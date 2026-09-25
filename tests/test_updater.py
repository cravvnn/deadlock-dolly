"""Updater trust boundaries, interrupted transactions and preference migration."""
import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
                           ("size", u.MAX_DOWNLOAD + 1), ("size", -1), ("state", "new")):
            data = self.release(); data["assets"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): u.select_release(data, "0.5.4")
        data = self.release(); data["assets"] *= 2
        with self.assertRaises(ValueError): u.select_release(data, "0.5.4")

    def test_metadata_request_is_latest_only(self):
        from io import BytesIO
        with patch.object(u, "open_url", return_value=BytesIO(json.dumps(self.release()).encode())) as opened:
            self.assertIsNotNone(u.check_latest("0.5.4-alpha"))
            opened.assert_called_once_with("https://api.github.com/repos/cravvnn/deadlock-dolly/releases/latest")

    def test_redirect_rejects_plain_http_and_other_hosts(self):
        from urllib.request import Request
        handler = u.ReleaseRedirect()
        for url in ("http://github.com/file", "https://github.com.evil.example/file", "file:///C:/file"):
            with self.assertRaises(ValueError):
                handler.redirect_request(Request(u.LATEST_URL), None, 302, "", {}, url)


class TransactionTests(unittest.TestCase):
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

    def test_abrupt_process_exit_can_be_recovered_from_journal(self):
        plan = self.work / "plan.json"; w.atomic_json(plan, self.plan)
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
        w.rollback(self.plan)
        self.assertEqual(self.snapshot(), self.before)

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
        (Path(work) / "verified.json").unlink()
        self.assertIsNone(u.staged_payload(self.target, "1.0.1"))

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
            raw.pop("ffmpeg_path"); raw.pop("auto_updates")
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
        from types import SimpleNamespace
        from dolly.update_ui import UpdateUI
        from unittest.mock import Mock
        manager = UpdateUI.__new__(UpdateUI)
        manager.app = SimpleNamespace(busy=False, playing=False, dirty=False, startup_cancel=None,
            controller=Mock(), video_export=Mock(), root=Mock(), update_status=Mock(), _on_close=Mock())
        manager.app.controller.status.return_value = {"game_running": False}
        manager.app.video_export.status.return_value = {"state": "idle"}
        manager.app.root.grab_current.return_value = None
        manager.ready = None; manager.restarting = False
        return manager

    def test_unsaved_work_game_recording_operations_and_dialogs_defer_restart(self):
        m = self.manager()
        self.assertTrue(m.safe_to_restart())
        for key in ("busy", "playing", "dirty"):
            setattr(m.app, key, True)
            self.assertFalse(m.safe_to_restart())
            setattr(m.app, key, False)
        m.app.controller.status.return_value = {"game_running": True}
        self.assertFalse(m.safe_to_restart())
        m.app.controller.status.return_value = {"game_running": False}
        m.app.video_export.status.return_value = {"state": "finalizing"}
        self.assertFalse(m.safe_to_restart())
        m.app.video_export.status.return_value = {"state": "idle"}
        m.app.root.grab_current.return_value = object()
        self.assertFalse(m.safe_to_restart())

    def test_restart_launches_in_install_helper_then_closes_app(self):
        from dolly import update_ui
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "install"; target.mkdir()
            work = target / ".dolly-update-verified"; work.mkdir()
            (target / "Dolly.exe").write_bytes(b"helper")
            m = self.manager(); m.ready = (work, "1.0.1")
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
        with patch.object(update_ui, "check_processes", side_effect=RuntimeError("game running")):
            m.restart()
        m.app._on_close.assert_not_called()
        self.assertFalse(m.restarting)
