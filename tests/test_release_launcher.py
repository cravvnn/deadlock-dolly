"""Public executable startup, safe handoff, recovery and package migration."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from dolly import release_launcher as boot, update_worker as worker, updater
from test_updater import package


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / "Dolly folder"
        self.target.mkdir()
        (self.target / updater.MANIFEST).write_text("fixture")

    def _start_writer(self, work, control, mode):
        # Fake install only. Successful process admission is scheduled before
        # launcher start; these Python processes are not packaged Dolly PIDs.
        script = '''
import json, os, sys, time
from pathlib import Path
from dolly import update_worker as w
target, work, control = map(Path, sys.argv[1:4])
mode = sys.argv[4]
deadline = time.monotonic() + 15
def wait(name):
    while not (control / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(.005)
real = w._replace_retrying
def replace(temporary, destination):
    if Path(destination).name == 'Dolly.exe':
        if mode == 'crash':
            real(temporary, destination)
        w.atomic_json(control / 'ready.json', {'ready': True})
        wait('release')
        if mode == 'crash':
            os._exit(77)
    return real(temporary, destination)
w._replace_retrying = replace
try:
    with w.install_lock(target):
        if mode == 'contend':
            w.atomic_json(control / 'ready.json', {'acquired': True})
        else:
            w.atomic_json(control / 'admitted.json', {'admitted': True})
            wait('go')
            w.install({'target': str(target), 'work': str(work)})
except OSError:
    if mode != 'contend':
        raise
    w.atomic_json(control / 'ready.json', {'acquired': False})
'''
        return subprocess.Popen([sys.executable, "-c", script, str(self.target),
                                 str(work), str(control), mode],
                                cwd=Path(__file__).resolve().parents[1],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _wait_writer(self, path, child):
        deadline = time.monotonic() + 10
        while not path.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                self.fail("Writer did not reach " + path.name)
            time.sleep(.005)

    def test_admitted_update_keeps_recovery_resources_through_launcher_start(self):
        # Include a commit delayed until after launcher return, as a mapped
        # public executable can delay replacement while cleanup proceeds.
        for mode in ("complete", "crash"):
            with self.subTest(mode=mode):
                package(self.target, "1.0.0")
                work = self.target / (".dolly-update-" + mode)
                package(work / "payload", "1.0.1")
                worker.atomic_json(work / "plan.json", {"target": str(self.target), "work": str(work)})
                os.utime(work, (0, 0))
                before = {name: (self.target / name).read_bytes()
                          for name in (*updater.read_manifest(self.target)["files"], updater.MANIFEST)}
                with tempfile.TemporaryDirectory() as folder:
                    control = Path(folder)
                    child = self._start_writer(work, control, mode)
                    try:
                        self._wait_writer(control / "admitted.json", child)
                        finished = worker._workspace_finished

                        def interleave(selected, keep):
                            allowed = finished(selected, keep)
                            if selected == work and allowed:
                                (control / "go").touch()
                                self._wait_writer(control / "ready.json", child)
                            return allowed

                        with patch.object(worker, "_workspace_finished", side_effect=interleave), \
                             patch.object(boot, "open_editor") as editor, \
                             patch.object(worker, "notify") as notify:
                            code = boot.main([], target=self.target)
                        (control / "go").touch()
                        self._wait_writer(control / "ready.json", child)
                        preserved = (work / "backup/Dolly.exe").is_file() and (work / "journal.json").is_file()
                    finally:
                        (control / "go").touch()
                        (control / "release").touch()
                        try:
                            stdout, stderr = child.communicate(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.communicate()
                            raise
                    self.assertEqual(code, 1)
                    editor.assert_not_called()
                    notify.assert_called_once()
                    self.assertTrue(preserved, "Startup removed active recovery resources")
                    self.assertEqual(child.returncode, 77 if mode == "crash" else 0, stdout + stderr)
                if mode == "crash":
                    worker.rollback({"target": str(self.target), "work": str(work)})
                    self.assertEqual({name: (self.target / name).read_bytes() for name in before}, before)
                else:
                    manifest = updater.read_manifest(self.target)
                    self.assertEqual(manifest["version"], "1.0.1")
                    self.assertTrue(all(updater.digest_file(self.target / name) == digest
                                        for name, digest in manifest["files"].items()))
                self.assertIsNone(worker.pending_work(self.target))

    def test_startup_excludes_writer_until_editor_handoff_returns(self):
        work = self.target / ".dolly-update-old"
        work.mkdir()
        os.utime(work, (0, 0))
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder)
            observations = []

            def contender(*args):
                child = self._start_writer(work, control, "contend")
                try:
                    stdout, stderr = child.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
                    raise
                self.assertEqual(child.returncode, 0, stdout + stderr)
                observations.append(json.loads((control / "ready.json").read_text())["acquired"])
                return True

            with patch.object(worker, "_workspace_finished", side_effect=contender), \
                 patch.object(boot, "open_editor", side_effect=contender):
                self.assertEqual(boot.main([], target=self.target), 0)
            self.assertEqual(observations, [False, False])
        self.assertFalse(work.exists())
        with worker.install_lock(self.target):
            pass  # Startup released ownership after process handoff.

    def test_unwritable_startup_lock_defers_without_cleanup_or_editor(self):
        with patch.object(worker, "install_lock", side_effect=PermissionError("not writable")), \
             patch.object(worker, "notify") as notify, \
             patch.object(worker, "pending_work", return_value=None) as pending, \
             patch.object(worker, "cleanup_workspaces") as cleanup, \
             patch.object(boot, "open_editor") as editor:
            self.assertEqual(boot.main([], target=self.target), 1)
            self.assertIn("not writable", notify.call_args.args[0])
            pending.assert_not_called()
            cleanup.assert_not_called()
            editor.assert_not_called()

    def test_normal_launch_cleans_up_and_opens_without_discovery_download_or_install(self):
        for arguments in ([], ["--updated"], ["--open", "shot.dolly"]):
            with self.subTest(arguments=arguments), \
                 patch.object(worker, "pending_work", return_value=None) as recovery, \
                 patch.object(worker, "cleanup_workspaces") as cleanup, \
                 patch.object(worker, "launch_program") as editor, \
                 patch.object(worker, "check_processes") as processes, \
                 patch.object(worker, "launch_worker") as install, \
                 patch.object(updater, "check_latest") as discovery, \
                 patch.object(updater, "download_update") as download, \
                 patch.object(updater, "open_url") as network:
                calls = Mock()
                calls.attach_mock(recovery, "recovery")
                calls.attach_mock(cleanup, "cleanup")
                calls.attach_mock(editor, "editor")
                self.assertEqual(boot.main(arguments, target=self.target), 0)
                self.assertEqual([call[0] for call in calls.mock_calls], ["recovery", "cleanup", "editor"])
                editor.assert_called_once_with(
                    [str(self.target.resolve() / "_internal/DollyApp.exe"), *arguments],
                    self.target.resolve(), wait=False)
                discovery.assert_not_called()
                download.assert_not_called()
                network.assert_not_called()
                processes.assert_not_called()
                install.assert_not_called()

    def test_relaunch_from_new_extracted_folder_keeps_the_same_layout_profile(self):
        profile = self.target.parent / "Local profile" / "DeadlockDolly"
        profile.mkdir(parents=True)
        layout = profile / "ui-layout.ini"
        layout.write_bytes(b"[DollyUI][Preferences]\nEditorCustom=1\n")
        override = self.target.parent / "custom layout.ini"
        override.write_bytes(b"[DollyUI][Preferences]\nReducedMotion=1\n")
        before = layout.read_bytes(), override.read_bytes()
        new_target = self.target.parent / "New Dolly version"
        new_target.mkdir()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(profile.parent),
                                     "DOLLY_UI_LAYOUT_FILE": str(override)}):
            for target in (self.target, new_target):
                with self.subTest(target=target), patch.object(worker.subprocess, "Popen") as launch:
                    boot.main(["--updated"], target=target)
                    environment = launch.call_args.kwargs["env"]
                    self.assertEqual(environment["LOCALAPPDATA"], str(profile.parent))
                    self.assertEqual(environment["DOLLY_UI_LAYOUT_FILE"], str(override))
                    self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                    self.assertEqual(launch.call_args.kwargs["cwd"], target.resolve())
        self.assertEqual((layout.read_bytes(), override.read_bytes()), before)

    def test_ordinary_open_does_not_mark_the_editor_as_just_updated(self):
        with patch.object(worker, "launch_program") as launch:
            boot.open_editor(self.target)
            launch.assert_called_once_with(
                [str(self.target / "_internal/DollyApp.exe")], self.target, wait=False)

    def test_actual_update_argument_is_forwarded_unchanged(self):
        with patch.object(worker, "launch_program") as launch:
            boot.open_editor(self.target, ["--updated"])
            launch.assert_called_once_with(
                [str(self.target / "_internal/DollyApp.exe"), "--updated"], self.target, wait=False)

    def test_recovery_runs_without_the_editor_runtime_and_waits_for_bootloader(self):
        work = self.target / ".dolly-update-test"
        with patch.object(worker, "pending_work", return_value=work), \
             patch.object(boot, "launcher_process_ids", return_value=(123, 100)), \
             patch.object(worker, "check_processes") as check, patch.object(worker, "launch_worker") as launch, \
             patch.object(boot, "open_editor") as editor, \
             patch.object(worker, "cleanup_workspaces") as cleanup, \
             patch.object(updater, "check_latest") as discovery, \
             patch.object(updater, "download_update") as download:
            boot.main([], target=self.target)
            check.assert_called_once_with(self.target.resolve(), exclude_pid=(123, 100))
            launch.assert_called_once_with(self.target.resolve(), work, recover=True, parent_pid=100)
            editor.assert_not_called()
            cleanup.assert_not_called()
            discovery.assert_not_called()
            download.assert_not_called()

    def test_recovery_process_guard_still_blocks_an_unsafe_handoff(self):
        with patch.object(worker, "pending_work", return_value=self.target / ".dolly-update-test"), \
             patch.object(boot, "launcher_process_ids", return_value=(123, 100)), \
             patch.object(worker, "check_processes", side_effect=RuntimeError("Game is running")), \
             patch.object(worker, "launch_worker") as install, patch.object(boot, "open_editor") as editor:
            with self.assertRaisesRegex(RuntimeError, "Game is running"):
                boot.main([], target=self.target)
            install.assert_not_called()
            editor.assert_not_called()

    def test_install_health_check_bypasses_pending_recovery_and_network(self):
        for args in (["--self-test", "report.json"], ["--self-test-hidden", "report.json"],
                     ["--cleanup-session", "session.json"], ["--help"]):
            with self.subTest(args=args), patch.object(worker, "pending_work") as recovery, \
                 patch.object(worker, "cleanup_workspaces") as cleanup, \
                 patch.object(updater, "check_latest") as discovery, \
                 patch.object(worker, "launch_program", return_value=0) as launch:
                self.assertEqual(boot.main(args, target=self.target), 0)
                launch.assert_called_once_with(
                    [str(self.target.resolve() / "_internal/DollyApp.exe"), *args], self.target.resolve(), wait=True)
                recovery.assert_not_called()
                cleanup.assert_not_called()
                discovery.assert_not_called()

    def test_update_worker_entrypoints_still_bypass_editor_startup(self):
        for arguments, expected in ((["--plan", "plan.json"], ["--plan", "plan.json"]),
                                    (["--updater-self-test", "report.json"], ["--self-test", "report.json"])):
            with self.subTest(arguments=arguments), patch.object(worker, "main", return_value=7) as handoff, \
                 patch.object(worker, "pending_work") as recovery, patch.object(boot, "open_editor") as editor:
                self.assertEqual(boot.main(arguments, target=self.target), 7)
                handoff.assert_called_once_with(expected)
                recovery.assert_not_called()
                editor.assert_not_called()

    def test_new_process_has_independent_pyinstaller_runtime(self):
        with patch.object(worker.subprocess, "Popen") as launch:
            worker.launch_program(["Dolly.exe"], self.target)
            self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_legacy_manifest_migrates_to_single_public_executable(self):
        package(self.target, "1.0.0")
        work = self.target / ".dolly-update-migrate"
        package(work / "payload", "1.0.1")
        runtime = work / "payload/_internal/DollyApp.exe"
        runtime.write_text("new editor")
        (work / "payload/DollyUpdater.exe").unlink()
        updater.write_manifest(work / "payload", "1.0.1")
        self.assertEqual(updater.read_manifest(self.target)["schema"], 1)
        worker.install({"target": str(self.target), "work": str(work)})
        self.assertEqual(updater.read_manifest(self.target)["schema"], 2)
        self.assertFalse((self.target / "DollyUpdater.exe").exists())
        self.assertEqual((self.target / "_internal/DollyApp.exe").read_text(), "new editor")

    def test_pending_recovery_requires_matching_plan(self):
        work = self.target / ".dolly-update-test"; work.mkdir()
        plan = {"target": str(self.target), "work": str(work)}
        worker.atomic_json(self.target / worker.PENDING, plan)
        worker.atomic_json(work / "plan.json", {**plan, "target": str(self.target.parent)})
        with self.assertRaisesRegex(ValueError, "disagrees"):
            worker.pending_work(self.target)

    def test_normal_open_ignores_a_verified_staged_release_without_a_pending_transaction(self):
        def release(folder, version):
            package(folder, version)
            (folder / "DollyUpdater.exe").unlink()
            (folder / "_internal/DollyApp.exe").write_text(version)
            updater.write_manifest(folder, version)

        release(self.target, "0.6.52-alpha")
        staged = self.target / ".dolly-update-next"
        release(staged / "payload", "0.6.53-alpha")
        worker.atomic_json(staged / "plan.json", {"target": str(self.target), "work": str(staged)})
        worker.atomic_json(staged / "verified.json", {"version": "0.6.53-alpha", "sha256": "a" * 64})
        before = {name: (self.target / name).read_bytes()
                  for name in (*updater.read_manifest(self.target)["files"], updater.MANIFEST)}

        with patch.object(worker, "launch_program") as editor, \
             patch.object(worker, "launch_worker") as install, \
             patch.object(worker, "check_processes") as processes, \
             patch.object(updater, "open_url") as network, \
             patch.object(updater, "download_update") as download:
            for arguments in (["--updated"], [], []):
                self.assertEqual(boot.main(arguments, target=self.target), 0)
            self.assertEqual(editor.call_count, 3)
            for call in editor.call_args_list:
                self.assertEqual(call.args[0][0], str(self.target / "_internal/DollyApp.exe"))
            install.assert_not_called()
            processes.assert_not_called()
            network.assert_not_called()
            download.assert_not_called()
        self.assertIsNone(worker.pending_work(self.target))
        self.assertEqual(updater.read_manifest(self.target)["version"], "0.6.52-alpha")
        self.assertEqual({name: (self.target / name).read_bytes() for name in before}, before)
        self.assertTrue((staged / "payload" / updater.MANIFEST).is_file())


class UpdateDiscoveryTests(unittest.TestCase):
    def release(self, tag="v0.6.50-alpha"):
        version = tag.removeprefix("v")
        name = f"Deadlock_Dolly_{version}_Windows_x64.zip"
        return {"tag_name": tag, "draft": False, "prerelease": False,
                "html_url": "https://untrusted.example/release-notes",
                "assets": [{"name": name, "state": "uploaded", "size": 100,
                            "digest": "sha256:" + "a" * 64,
                            "browser_download_url": f"https://github.com/{updater.REPOSITORY}/releases/download/{tag}/{name}"}]}

    def test_notes_url_is_bound_to_the_validated_official_release_tag(self):
        for tag in ("v0.6.50-alpha", "0.6.50-alpha", "v1.2.3-rc.4"):
            with self.subTest(tag=tag):
                selected = updater.select_release(self.release(tag), "0.6.49-alpha")
                self.assertEqual(selected["notes_url"],
                                 f"https://github.com/cravvnn/deadlock-dolly/releases/tag/{tag}")
                self.assertEqual(selected["sha256"], "a" * 64)

    def test_api_html_url_never_controls_the_notes_destination(self):
        for url in (None, "javascript:alert(1)", "file:///C:/notes.html",
                    "https://github.com/another/publisher/releases/tag/v0.6.50-alpha"):
            with self.subTest(url=url):
                release = self.release()
                release["html_url"] = url
                self.assertEqual(updater.select_release(release, "0.6.49-alpha")["notes_url"],
                                 "https://github.com/cravvnn/deadlock-dolly/releases/tag/v0.6.50-alpha")

    def test_unsupported_tags_cannot_become_notes_links(self):
        for tag in ("latest", "../v0.6.50-alpha", "v0.6.50-alpha?redirect=evil", "https://example.com"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                updater.select_release(self.release(tag), "0.6.49-alpha")

    def test_discovery_fetches_only_metadata_and_never_stages_an_update(self):
        metadata = BytesIO(json.dumps(self.release()).encode())
        with patch.object(updater, "open_url", return_value=metadata) as network, \
             patch.object(updater, "download_update") as download, \
             patch.object(updater, "extract_verified") as extract:
            selected = updater.check_latest("0.6.49-alpha")
            self.assertEqual(selected["version"], "0.6.50-alpha")
            self.assertTrue(selected["notes_url"].endswith("/tag/v0.6.50-alpha"))
            network.assert_called_once_with("https://api.github.com/repos/cravvnn/deadlock-dolly/releases/latest")
            download.assert_not_called()
            extract.assert_not_called()

    def test_discovery_keeps_the_metadata_size_limit(self):
        with patch.object(updater, "open_url", return_value=BytesIO(b" " * (2 * 1024 * 1024 + 1))), \
             patch.object(updater, "download_update") as download:
            with self.assertRaisesRegex(ValueError, "metadata is too large"):
                updater.check_latest("0.6.49-alpha")
            download.assert_not_called()
