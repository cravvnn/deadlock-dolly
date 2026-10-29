"""Updater trust boundaries, interrupted transactions and preference migration."""
from contextlib import nullcontext
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import patch
from dolly import updater as u
from dolly import update_worker as w
from dolly.settings import AppSettings, load_settings, save_settings

REQUIRED = ("Dolly.exe", "DollyUpdater.exe", "_internal/native/bin/win64/DollyNative.dll",
            "_internal/third_party/ffmpeg/bin/ffmpeg.exe")


def package(folder, version):
    for name in REQUIRED:
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(version)
    u.write_manifest(folder, version)


class ReleaseTests(unittest.TestCase):
    def release(self):
        return {"tag_name": "v0.5.5-alpha", "draft": False, "prerelease": False,
                "assets": [{"name": "Deadlock_Dolly_0.5.5-alpha_Windows_x64.zip", "state": "uploaded",
                  "size": 100, "digest": "sha256:" + "a" * 64,
                  "browser_download_url": "https://github.com/cravvnn/deadlock-dolly/releases/download/v0.5.5-alpha/Deadlock_Dolly_0.5.5-alpha_Windows_x64.zip"}]}

    def test_public_alpha_can_update_but_pre_release_and_draft_cannot(self):
        raw = self.release()
        self.assertEqual(u.select_release(raw, "0.5.4-alpha")["version"], "0.5.5-alpha")
        for flag in ("prerelease", "draft"):
            self.assertIsNone(u.select_release({**raw, flag: True}, "0.5.4-alpha"))
        self.assertIsNone(u.select_release(raw, "0.5.5-alpha"))
        self.assertIsNone(u.select_release(raw, "0.5.6-alpha"))

    def test_numeric_versions_and_prerelease_order(self):
        self.assertGreater(u.version_key("0.5.10-alpha"), u.version_key("0.5.9"))
        self.assertGreater(u.version_key("1.0.0"), u.version_key("1.0.0-rc.3"))
        self.assertGreater(u.version_key("1.0.0-alpha.10"), u.version_key("1.0.0-alpha.2"))
        for version in ("latest", "0.5", "1.2.3-branch-name", "../../x"):
            with self.assertRaises(ValueError): u.version_key(version)

    def test_asset_origin_digest_size_and_ambiguity(self):
        for key, value in (("browser_download_url", "https://evil.example/dolly.zip"), ("digest", None),
                           ("size", u.MAX_DOWNLOAD + 1), ("size", -1), ("size", True), ("state", "new")):
            data = self.release(); data["assets"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): u.select_release(data, "0.5.4")
        data = self.release(); data["assets"] *= 2
        with self.assertRaises(ValueError): u.select_release(data, "0.5.4")

    def test_redirect_rejects_plain_http_and_other_hosts(self):
        from urllib.request import Request
        handler = u.ReleaseRedirect()
        for url in ("http://github.com/file", "https://github.com.evil.example/file", "file:///C:/file"):
            with self.assertRaises(ValueError):
                handler.redirect_request(Request(u.LATEST_URL), None, 302, "", {}, url)


class TransactionTests(unittest.TestCase):
    def test_update_success_and_rollback_preserve_shared_layout_and_recovery_copy(self):
        # The native UI uses this shared profile, independent of each extracted
        # installation. Exercise actual install/rollback while it contains both
        # an authored layout and the recovery copy used after an interrupted save.
        profile = self.root / "Local profile" / "DeadlockDolly"
        profile.mkdir(parents=True)
        files = {
            "ui-layout.ini": b"[Window][Deadlock Dolly]\r\nPos=36,84\r\nSize=800,520\r\n\r\n[DollyUI][Preferences]\r\nCustom=1\r\nEditorCustom=1\r\n",
            "ui-layout.ini.bak": b"[Window][Deadlock Dolly]\nPos=48,96\n\n[DollyUI][Preferences]\nCustom=1\n",
        }
        for name, data in files.items():
            (profile / name).write_bytes(data)
        for failed_health in (False, True):
            with self.subTest(failed_health=failed_health):
                case = self.root / ("failed update" if failed_health else "successful update")
                target, work = case / "Dolly", case / ".dolly-update-test"
                package(target, "1.0.0")
                package(work / "payload", "1.0.1")
                plan = {"target": str(target), "work": str(work)}
                def health_check(_):
                    if failed_health:
                        raise RuntimeError("simulated startup failure")
                with patch.dict(os.environ, {"LOCALAPPDATA": str(profile.parent)}):
                    if failed_health:
                        with self.assertRaisesRegex(RuntimeError, "simulated startup failure"):
                            w.install(plan, health_check)
                    else:
                        w.install(plan, health_check)
                self.assertEqual({p.name: p.read_bytes() for p in profile.iterdir()}, files)
                journal = json.loads((work / "journal.json").read_text(encoding="utf-8"))
                self.assertFalse(any("ui-layout" in name for name in journal["before"]))
                self.assertEqual((target / "Dolly.exe").read_text(), "1.0.0" if failed_health else "1.0.1")

    def test_update_preserves_user_export_folder_preference(self):
        settings_path = self.root / 'Roaming' / 'DeadlockDolly' / 'settings.json'
        settings = AppSettings(export_folder=str(self.root / 'My exports'), full_editor=True)
        save_settings(settings, settings_path)
        before = settings_path.read_bytes()
        with patch.dict(os.environ, {"APPDATA": str(settings_path.parent.parent)}):
            w.install(self.plan)
        self.assertEqual(settings_path.read_bytes(), before)
        self.assertEqual(load_settings(settings_path), settings)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "Dolly with spaces"
        self.work = self.root / ".dolly-update-test"
        self.payload = self.work / "payload"
        package(self.target, "1.0.0")
        package(self.payload, "1.0.1")
        (self.target / "_internal/obsolete.dll").write_text("obsolete")
        u.write_manifest(self.target, "1.0.0")
        (self.target / "shots").mkdir()
        (self.target / "shots/my-shot.json").write_text("unsullied shot")
        (self.target / "_internal/custom-user-shader.fx").write_text("user shader")
        self.plan = {"target": str(self.target), "work": str(self.work)}
        self.before = self.snapshot()

    def snapshot(self):
        return {p.relative_to(self.target).as_posix(): p.read_bytes()
                for p in self.target.rglob("*") if p.is_file()}

    def test_success_installs_matching_bundle_and_preserves_unknown_user_files(self):
        w.install(self.plan)
        self.assertEqual((self.target / "Dolly.exe").read_text(), "1.0.1")
        self.assertFalse((self.target / "_internal/obsolete.dll").exists())
        self.assertEqual((self.target / "shots/my-shot.json").read_text(), "unsullied shot")
        self.assertEqual((self.target / "_internal/custom-user-shader.fx").read_text(), "user shader")
        self.assertFalse((self.target / w.PENDING).exists())

    def test_failed_startup_rolls_back_every_file_and_manifest(self):
        def fail(_): raise RuntimeError("bad new build")
        with self.assertRaisesRegex(RuntimeError, "bad new build"): w.install(self.plan, fail)
        self.assertEqual(self.snapshot(), self.before)
        w.rollback(self.plan)
        self.assertEqual(self.snapshot(), self.before)

    def test_atomic_json_failure_leaves_no_temporary_files(self):
        target = self.root / "state.json"
        with patch.object(w.json, "dumps", side_effect=ValueError("encode failed")):
            with self.assertRaises(ValueError):
                w.atomic_json(target, {"state": "applying"})
        self.assertFalse(target.exists())
        self.assertEqual([item for item in self.root.glob(".dolly-*") if item != self.work], [])

    def test_failed_file_replacement_rolls_back(self):
        count = 0
        def fail(source, target):
            nonlocal count
            count += 1
            if count == 3: raise PermissionError("locked DLL")
            w.replace_file(source, target)
        with self.assertRaises(PermissionError): w.install(self.plan, replace=fail)
        self.assertEqual(self.snapshot(), self.before)

    def test_transient_replace_lock_is_retried(self):
        source = self.root / "new"; source.write_text("new")
        target = self.root / "target"; target.write_text("old")
        real = os.replace
        calls = {"n": 0}
        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                error = PermissionError("held by a scanner"); error.winerror = 5
                raise error
            return real(src, dst)
        with patch.object(w.os, "replace", side_effect=flaky), patch.object(w.time, "sleep") as sleep:
            w._replace_retrying(source, target)
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_persistent_replace_lock_fails_closed(self):
        source = self.root / "new"; source.write_text("new")
        target = self.root / "target"; target.write_text("old")
        def locked(src, dst):
            error = PermissionError("protected folder"); error.winerror = 5
            raise error
        with patch.object(w.os, "replace", side_effect=locked) as replace, \
             patch.object(w.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                w._replace_retrying(source, target)
        self.assertEqual(target.read_text(), "old")
        self.assertTrue(1 < replace.call_count <= 6, "Persistent locks need a bounded retry budget")
        self.assertEqual(sleep.call_count, replace.call_count - 1)

    def test_readonly_target_attribute_is_cleared(self):
        source = self.root / "new"; source.write_text("new")
        target = self.root / "target"; target.write_text("old")
        os.chmod(target, 0o444)
        self.addCleanup(lambda: os.chmod(target, 0o666) if target.exists() else None)
        self.assertFalse(os.access(target, os.W_OK))
        w._replace_retrying(source, target)
        self.assertEqual(target.read_text(), "new")

    def test_abrupt_process_exit_can_be_recovered_from_journal(self):
        from dolly import release_launcher as boot
        work = self.target / ".dolly-update-interrupted"
        package(work / "payload", "1.0.1")
        plan = work / "plan.json"
        w.atomic_json(plan, {"target": str(self.target), "work": str(work)})
        script = '''import json,os,sys
from pathlib import Path
from dolly.update_worker import install,replace_file
count=0
def cut_power(source,target):
 global count
 replace_file(source,target)
 count+=1
 if count==2:os._exit(77)
install(json.loads(Path(sys.argv[1]).read_text()),replace=cut_power)
'''
        result = subprocess.run([sys.executable, "-c", script, str(plan)], timeout=30)
        self.assertEqual(result.returncode, 77)
        self.assertTrue((self.target / w.PENDING).exists())
        with patch.object(w, "check_processes"), \
             patch.object(boot, "launcher_process_ids", return_value=(123,)), \
             patch.object(w, "launch_worker") as handoff, patch.object(boot, "open_editor") as editor:
            boot.main([], target=self.target)
            handoff.assert_called_once_with(self.target, work, recover=True, parent_pid=123)
            editor.assert_not_called()
        with patch.object(w, "install_lock", side_effect=lambda target: nullcontext()), \
             patch.object(w, "check_processes"), patch.object(w, "install") as install, \
             patch.object(w, "launch_program") as launch, patch.object(u, "open_url") as network:
            self.assertEqual(w.main(["--plan", str(plan), "--recover", "--no-relaunch"]), 0)
            install.assert_not_called()
            launch.assert_not_called()
            network.assert_not_called()
        self.assertEqual({name: data for name, data in self.snapshot().items()
                          if not name.startswith(work.name + "/")}, self.before)
        self.assertIsNone(w.pending_work(self.target))
        self.assertEqual(json.loads((work / "journal.json").read_text())["state"], "rolled_back")

    def test_modified_owned_file_is_backed_up_then_replaced(self):
        (self.target / "Dolly.exe").write_text("customized")
        w.install(self.plan)
        self.assertEqual((self.target / "Dolly.exe").read_text(), "1.0.1")
        self.assertEqual((self.work / "backup/Dolly.exe").read_text(), "customized")
        state = json.loads((self.work / "journal.json").read_text())
        self.assertEqual(state["modified"], ["Dolly.exe"])

    def test_modified_owned_file_is_restored_when_startup_check_fails(self):
        (self.target / "Dolly.exe").write_text("customized")
        def fail(_): raise RuntimeError("bad new build")
        with self.assertRaisesRegex(RuntimeError, "bad new build"): w.install(self.plan, fail)
        self.assertEqual((self.target / "Dolly.exe").read_text(), "customized")

    def test_download_stages_inside_install_and_reuses_verified_payload(self):
        archive, release = self.make_zip()
        release.update(url="https://github.com/cravvnn/deadlock-dolly/releases/download/v1.0.1/package.zip",
                       size=archive.stat().st_size)
        with patch.object(u, "open_url", side_effect=[BytesIO(archive.read_bytes())]) as opened:
            work = u.download_update(release, self.target)
            reused = u.download_update(release, self.target)
        self.assertEqual(Path(work), Path(reused))
        self.assertEqual(Path(work).parent, self.target)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual([p.name for p in self.target.glob(".dolly-update-*")], [Path(work).name])
        self.assertTrue((Path(work) / "payload/UPDATE_MANIFEST.json").is_file())
        self.assertEqual(u.staged_payload(self.target, "1.0.1", release["sha256"]), work)
        self.assertIsNone(u.staged_payload(self.target, "1.0.1"))
        (Path(work) / "verified.json").unlink()
        self.assertIsNone(u.staged_payload(self.target, "1.0.1", release["sha256"]))

    def test_same_version_republished_asset_cannot_reuse_another_digest(self):
        archive, original = self.make_zip()
        original.update(url="https://github.com/cravvnn/deadlock-dolly/releases/download/v1.0.1/package.zip",
                        size=archive.stat().st_size)
        with patch.object(u, "open_url", return_value=BytesIO(archive.read_bytes())):
            old_work = u.download_update(original, self.target)
        (self.payload / "Dolly.exe").write_text("republished 1.0.1")
        u.write_manifest(self.payload, "1.0.1")
        archive, approved = self.make_zip()
        approved.update(url=original["url"], size=archive.stat().st_size)
        self.assertNotEqual(approved["sha256"], original["sha256"])
        with patch.object(u, "open_url", return_value=BytesIO(archive.read_bytes())) as network:
            new_work = u.download_update(approved, self.target)
        self.assertNotEqual(new_work, old_work, "The approved replacement digest reused the older package")
        network.assert_called_once_with(approved["url"])
        self.assertEqual((new_work / "payload/Dolly.exe").read_text(), "republished 1.0.1")
        self.assertEqual((old_work / "payload/Dolly.exe").read_text(), "1.0.1")
        self.assertEqual(json.loads((new_work / "verified.json").read_text())["sha256"], approved["sha256"])

    def test_old_cache_without_verified_digest_is_not_reused(self):
        archive, approved = self.make_zip()
        approved.update(url="https://github.com/cravvnn/deadlock-dolly/releases/download/v1.0.1/package.zip",
                        size=archive.stat().st_size)
        content = archive.read_bytes()
        with patch.object(u, "open_url", return_value=BytesIO(content)):
            old_work = u.download_update(approved, self.target)
        (old_work / "verified.json").write_text(json.dumps({"version": approved["version"]}))
        with patch.object(u, "open_url", return_value=BytesIO(content)) as network:
            new_work = u.download_update(approved, self.target)
        self.assertNotEqual(new_work, old_work, "An old marker without digest bypassed exact-release approval")
        network.assert_called_once_with(approved["url"])
        self.assertEqual(json.loads((new_work / "verified.json").read_text())["sha256"], approved["sha256"])

    def test_cleanup_removes_stale_workspaces_but_keeps_live_ones(self):
        stale = self.root / ".dolly-update-stale"; (stale / "payload").mkdir(parents=True)
        os.utime(stale, (0, 0))
        failed = self.root / ".dolly-update-failed"; failed.mkdir()
        (failed / "result.json").write_text('{"passed": false}')
        recovered = self.root / ".dolly-update-recovered"; recovered.mkdir()
        (recovered / "journal.json").write_text('{"state": "rolled_back"}')
        applying = self.root / ".dolly-update-applying"; applying.mkdir()
        (applying / "journal.json").write_text('{"state": "applying"}')
        active = self.root / ".dolly-update-active"; (active / "payload").mkdir(parents=True)
        w.cleanup_workspaces(self.root, keep=active)
        self.assertFalse(stale.exists())
        self.assertFalse(failed.exists())
        self.assertFalse(recovered.exists())
        self.assertTrue(applying.exists())
        self.assertTrue(active.exists())

    def test_cleanup_defers_while_recovery_is_pending(self):
        stale = self.root / ".dolly-update-stale"; stale.mkdir()
        (self.root / w.PENDING).write_text("{}")
        w.cleanup_workspaces(self.root)
        self.assertTrue(stale.exists())

    def test_cleanup_removes_legacy_sidecar_workspace_for_this_install_only(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            install = parent / "install"; install.mkdir()
            legacy = parent / ".dolly-update-legacy"; legacy.mkdir()
            (legacy / "plan.json").write_text(json.dumps({"target": str(install), "work": str(legacy)}))
            (legacy / "journal.json").write_text(json.dumps({"state": "complete", "target": str(install)}))
            other = parent / ".dolly-update-other"; other.mkdir()
            (other / "plan.json").write_text(json.dumps({"target": str(parent / "elsewhere"), "work": str(other)}))
            w.cleanup_workspaces(install)
            self.assertFalse(legacy.exists())
            self.assertTrue(other.exists())

    def test_new_package_cannot_overwrite_unowned_file(self):
        name = "_internal/custom-user-shader.fx"
        (self.payload / name).write_text("new bundled shader")
        u.write_manifest(self.payload, "1.0.1")
        with self.assertRaisesRegex(ValueError, "unowned"): w.install(self.plan)
        self.assertEqual(self.snapshot(), self.before)

    def test_staged_file_tampering_and_downgrade_abort(self):
        (self.payload / "Dolly.exe").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "changed"): w.install(self.plan)
        self.assertEqual(self.snapshot(), self.before)
        u.write_manifest(self.payload, "0.9.0")
        with self.assertRaisesRegex(ValueError, "downgrade"): w.install(self.plan)

    def test_target_symlink_outside_install_is_rejected(self):
        file = self.target / "Dolly.exe"; file.unlink()
        outside = self.root / "outside.exe"; outside.write_text("outside")
        try: file.symlink_to(outside)
        except OSError as error: self.skipTest(str(error))
        with self.assertRaises(ValueError): w.install(self.plan)
        self.assertEqual(outside.read_text(), "outside")

    def make_zip(self, extra=None):
        archive = self.root / "update.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for p in self.payload.rglob("*"):
                if p.is_file(): z.write(p, "DeadlockDolly/" + p.relative_to(self.payload).as_posix())
            if extra: z.writestr(*extra)
        return archive, {"sha256": u.digest_file(archive), "version": "1.0.1"}

    def test_valid_zip_is_verified_and_staged(self):
        archive, release = self.make_zip()
        self.assertEqual(u.extract_verified(archive, self.root / "staged", release)["version"], "1.0.1")

    def test_zip_traversal_case_collision_and_hash_fail_closed(self):
        for i, name in enumerate(("DeadlockDolly/../../outside", "DeadlockDolly/dolly.exe", "DeadlockDolly/_internal/NUL.txt")):
            archive, release = self.make_zip((name, "bad"))
            with self.subTest(name=name), self.assertRaises(ValueError):
                u.extract_verified(archive, self.root / ("bad" + str(i)), release)
        archive, release = self.make_zip(); release["sha256"] = "0" * 64
        with self.assertRaises(ValueError): u.extract_verified(archive, self.root / "hash", release)
        self.assertEqual(self.snapshot(), self.before)

    def test_manifest_cannot_claim_shot_files(self):
        path = self.payload / u.MANIFEST; data = json.loads(path.read_text())
        data["files"]["shots/my-shot.json"] = "a" * 64
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): u.read_manifest(self.payload)


class PreferenceTests(unittest.TestCase):
    def test_v4_migration_and_custom_paths(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings.json"
            original = AppSettings(full_editor=True, reshade_runtime_path="C:/user/ReShade64.dll")
            save_settings(original, path)
            raw = json.loads(path.read_text()); raw["version"] = 4
            raw.pop("ffmpeg_path"); raw.pop("auto_updates"); raw.pop("framing_grid_enabled"); raw.pop("export_folder")
            raw.pop("pause_after_capture")
            raw.pop("allow_mod_overrides")
            raw.pop("discord_presence")
            path.write_text(json.dumps(raw)); before = path.read_bytes()
            self.assertEqual(load_settings(path), original)
            self.assertEqual(path.read_bytes(), before)
            custom = AppSettings(ffmpeg_path="D:/tools/ffmpeg.exe", auto_updates=False,
                                 reshade_runtime_path=original.reshade_runtime_path)
            save_settings(custom, path)
            self.assertEqual(load_settings(path), custom)

    def test_bad_update_preferences_rejected(self):
        for value in ("yes", 1, None):
            with self.assertRaises(ValueError): AppSettings(auto_updates=value)
        with self.assertRaises(ValueError): AppSettings(ffmpeg_path="bad\npath")

class UpdateUITests(unittest.TestCase):
    def manager(self):
        from dolly.update_ui import UpdateUI
        from unittest.mock import Mock
        manager = UpdateUI.__new__(UpdateUI)
        manager.app = SimpleNamespace(busy=False, playing=False, dirty=False, startup_cancel=None, closed=False,
            controller=Mock(), video_export=Mock(), root=Mock(), update_status=Mock(), _on_close=Mock())
        manager.app.controller.status.return_value = {"game_running": False}
        manager.app.video_export.status.return_value = {"state": "idle"}
        manager.app.root.grab_current.return_value = None
        manager.available = {"version": "1.0.1", "url": "https://github.com/cravvnn/deadlock-dolly/releases/download/v1.0.1/package.zip",
                             "sha256": "a" * 64, "size": 100,
                             "notes_url": "https://github.com/cravvnn/deadlock-dolly/releases/tag/v1.0.1"}
        manager.ready = None; manager.ready_release = None; manager.approved = None
        manager.restarting = False; manager.dismissed = False; manager.running = False
        manager.notice = Mock(); manager.message = ""
        return manager

    def test_restart_launches_in_install_helper_then_closes_app(self):
        from dolly import update_ui
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "install"; target.mkdir()
            work = target / ".dolly-update-verified"; work.mkdir()
            (target / "Dolly.exe").write_bytes(b"helper")
            m = self.manager(); m.ready = (work, "1.0.1")
            m.approved = m.ready_release = m.release_key(m.available)
            with patch.object(update_ui, "application_root", return_value=target), \
                 patch.object(update_ui, "check_processes"), patch.object(w.subprocess, "Popen") as launch:
                m.restart()
            self.assertEqual(Path(launch.call_args.args[0][0]), work.resolve() / "DollyUpdater.exe")
            self.assertEqual(json.loads((work / "plan.json").read_text())["target"], str(target.resolve()))
            m.app._on_close.assert_called_once()
            self.assertTrue(m.restarting)

    def test_other_active_game_keeps_desktop_open(self):
        from dolly import update_ui
        m = self.manager(); m.ready = (Path("staging"), "1.0.1")
        m.approved = m.ready_release = m.release_key(m.available)
        with patch.object(update_ui, "check_processes", side_effect=RuntimeError("game running")):
            m.restart()
        m.app._on_close.assert_not_called()
        self.assertFalse(m.restarting)
        self.assertIsNone(m.approved)


class UpdateProgressWindowTests(unittest.TestCase):
    def test_successful_install_with_failed_relaunch_shows_dismissible_diagnostic(self):
        from unittest.mock import Mock

        class ImmediateThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        callbacks = []
        window = Mock()
        window.closed = False
        window.root.after.side_effect = lambda _delay, callback: callbacks.append(callback)
        window.root.mainloop.side_effect = lambda: callbacks.pop(0)()
        target = Path("Dolly install")
        with patch.object(w, "UpdateWindow", return_value=window), \
                patch.object(w.threading, "Thread", ImmediateThread), \
                patch.object(w, "_apply_plan"), \
                patch.object(w, "launch_program", side_effect=OSError("access denied")):
            code = w._progress_helper_window(target / "work", target, recover=False, parent_pid=0)
        self.assertEqual(code, 1)
        message = window.show_failure.call_args.args[0]
        self.assertIn("updated, but could not reopen", message)
        self.assertIn("open Dolly.exe", message)
        self.assertIn("access denied", message)
        window._close.assert_not_called()

    def test_user_close_waits_until_failure_but_success_can_close_programmatically(self):
        from unittest.mock import Mock
        window = w.UpdateWindow.__new__(w.UpdateWindow)
        window.closed = False
        window._close_allowed = False
        window.root = Mock()
        window.status = Mock()
        window.progress = Mock()
        window.close_button = Mock()
        window.scale = 1.0

        # A window-manager close while the worker is replacing files must not
        # terminate the daemon worker or abandon its transaction.
        window._request_close()
        self.assertFalse(window.closed)
        window.root.destroy.assert_not_called()

        # After a failure, both the close button and the window manager can
        # dismiss the diagnostic. Success retains the internal _close path.
        window.show_failure("Update failed")
        window._request_close()
        self.assertTrue(window.closed)
        window.root.destroy.assert_called_once()
        success = w.UpdateWindow.__new__(w.UpdateWindow)
        success.closed = False
        success._close_allowed = False
        success.root = Mock()
        success._close()
        self.assertTrue(success.closed)
        success.root.destroy.assert_called_once()

    def test_launch_worker_requests_the_progress_window_for_a_normal_install(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory); work = target / ".dolly-update-abc"; work.mkdir()
            (target / "Dolly.exe").write_bytes(b"helper")
            with patch.object(w, "launch_program") as launch:
                w.launch_worker(target, work, parent_pid=4321)
            command = launch.call_args.args[0]
            self.assertIn("--progress", command)
            self.assertNotIn("--recover", command)

    def test_recovery_does_not_request_the_progress_window(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory); work = target / ".dolly-update-abc"; work.mkdir()
            (target / "Dolly.exe").write_bytes(b"helper")
            (work / "DollyUpdater.exe").write_bytes(b"helper")
            with patch.object(w, "launch_program") as launch:
                w.launch_worker(target, work, recover=True)
            command = launch.call_args.args[0]
            self.assertIn("--recover", command)
            self.assertNotIn("--progress", command)

    def test_install_smoke_check_runs_hidden_and_requires_a_fresh_healthy_report(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Dolly with spaces"
            work = target / ".dolly-update-smoke"; work.mkdir(parents=True)
            report = work / "installed-smoke.json"
            healthy = {"passed": True, "frozen": True, "game_launched": False}
            for returncode, receipt in ((0, healthy), (1, healthy), (0, None), (0, {**healthy, "passed": False}),
                                        (0, {**healthy, "frozen": False}), (0, {**healthy, "game_launched": True})):
                with self.subTest(returncode=returncode, receipt=receipt):
                    report.write_text(json.dumps(healthy))  # Stale success must be discarded.
                    def run(command, **kwargs):
                        self.assertEqual(command, [str(target / "Dolly.exe"), "--self-test-hidden", str(report)])
                        self.assertEqual(kwargs["cwd"], work)
                        self.assertFalse(report.exists())
                        if receipt is not None:
                            report.write_text(json.dumps(receipt))
                        return SimpleNamespace(returncode=returncode)
                    with patch.object(w.subprocess, "run", side_effect=run), \
                         (nullcontext() if not returncode and receipt == healthy else
                          self.assertRaisesRegex(RuntimeError, "startup check")):
                        w.smoke(target, work)

    def test_progress_window_keeps_failure_on_screen(self):
        # A failure is shown inline and the window stays until the user closes it.
        from unittest.mock import Mock
        window = w.UpdateWindow.__new__(w.UpdateWindow)
        window.closed = False
        window.status = Mock()
        window.progress = Mock()
        window.close_button = Mock()
        window.show_failure("Dolly could not finish updating.")
        window.status.set.assert_called_once_with("Dolly could not finish updating.")
        window.progress.stop.assert_called_once()
        window.progress.pack_forget.assert_called_once()
        window.close_button.pack.assert_called_once()
        self.assertFalse(window.closed)

    def test_main_ignores_progress_for_a_self_test(self):
        # --self-test is a hidden startup gate; --progress must not change it.
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "self.json"
            with patch.object(w, "self_test", return_value=0) as self_test:
                self.assertEqual(w.main(["--self-test", str(report), "--progress"]), 0)
            self_test.assert_called_once_with(report)

    def test_main_routes_progress_install_to_the_windowed_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory); work = target / ".dolly-update-abc"; work.mkdir()
            plan = work / "plan.json"
            plan.write_text(json.dumps({"work": str(work), "target": str(target)}))
            with patch.object(w, "_progress_helper_window", return_value=0) as windowed, \
                 patch.object(w, "install") as install:
                code = w.main(["--plan", str(plan), "--progress", "--parent", "0"])
            self.assertEqual(code, 0)
            windowed.assert_called_once()
            install.assert_not_called()

    def test_main_without_progress_installs_headless(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory); work = target / ".dolly-update-abc"; work.mkdir()
            plan = work / "plan.json"
            plan.write_text(json.dumps({"work": str(work), "target": str(target)}))
            with patch.object(w, "_progress_helper_window") as windowed, \
                 patch.object(w, "_apply_plan", return_value=0) as apply, \
                 patch.object(w, "wait_parent"):
                code = w.main(["--plan", str(plan), "--no-relaunch", "--parent", "0"])
            self.assertEqual(code, 0)
            windowed.assert_not_called()
            apply.assert_called_once()
