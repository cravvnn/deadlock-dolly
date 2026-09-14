"""Public executable startup, safe handoff, recovery and package migration."""
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch
from dolly import release_launcher as boot, update_worker as worker, updater
from dolly.settings import AppSettings
from test_updater import package


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name) / "Dolly folder"
        self.target.mkdir()
        (self.target / updater.MANIFEST).write_text("fixture")

    def test_normal_launch_checks_before_opening_editor(self):
        with patch.object(boot, "load_settings", return_value=AppSettings()), \
             patch.object(worker, "pending_work", return_value=None), \
             patch.object(boot, "StartupWindow") as window, patch.object(boot, "open_editor") as editor:
            window.return_value.run.return_value = "open"
            self.assertEqual(boot.main([], target=self.target), 0)
            window.assert_called_once_with(self.target.resolve())
            editor.assert_called_once_with(self.target.resolve(), [])

    def test_opt_out_and_updated_launch_never_check_again(self):
        for args, automatic in (([], False), (["--updated"], True)):
            with self.subTest(args=args), patch.object(boot, "load_settings", return_value=AppSettings(auto_updates=automatic)), \
                 patch.object(worker, "pending_work", return_value=None), \
                 patch.object(boot, "StartupWindow") as window, patch.object(boot, "open_editor") as editor:
                boot.main(args, target=self.target)
                window.assert_not_called()
                editor.assert_called_once()

    def test_update_handoff_does_not_open_old_editor(self):
        with patch.object(boot, "load_settings", return_value=AppSettings()), \
             patch.object(worker, "pending_work", return_value=None), \
             patch.object(boot, "StartupWindow") as window, patch.object(boot, "open_editor") as editor:
            window.return_value.run.return_value = "updating"
            boot.main([], target=self.target)
            editor.assert_not_called()

    def test_recovery_runs_without_the_editor_runtime_and_waits_for_bootloader(self):
        work = self.target.parent / ".dolly-update-test"
        with patch.object(worker, "pending_work", return_value=work), \
             patch.object(boot, "launcher_process_ids", return_value=(123, 100)), \
             patch.object(worker, "check_processes") as check, patch.object(worker, "launch_worker") as launch, \
             patch.object(boot, "open_editor") as editor:
            boot.main([], target=self.target)
            check.assert_called_once_with(self.target, exclude_pid=(123, 100))
            launch.assert_called_once_with(self.target, work, recover=True, parent_pid=100)
            editor.assert_not_called()

    def test_install_health_check_bypasses_pending_recovery_and_network(self):
        args = ["--self-test", "report.json"]
        with patch.object(worker, "pending_work") as recovery, \
             patch.object(boot, "load_settings") as settings, patch.object(worker, "launch_program", return_value=0) as launch:
            self.assertEqual(boot.main(args, target=self.target), 0)
            self.assertEqual(launch.call_args.args[0], [str(self.target / "_internal/DollyApp.exe"), *args])
            self.assertTrue(launch.call_args.kwargs["wait"])
            recovery.assert_not_called(); settings.assert_not_called()

    def test_offline_or_running_game_opens_installed_editor_without_installing(self):
        for game in (False, True):
            events = queue.Queue()
            with patch.object(worker, "check_processes", side_effect=RuntimeError("game") if game else None), \
                 patch.object(updater, "check_latest", side_effect=OSError("offline")) as check, \
                 patch.object(updater, "download_update") as download:
                boot.check_for_update(self.target, events, (123, 100))
                self.assertEqual(events.get_nowait()[0], "open")
                download.assert_not_called()
                if game: check.assert_not_called()

    def test_verified_download_reports_ready_but_does_not_install_in_worker_thread(self):
        events = queue.Queue(); release = {"version": "1.2.3"}; work = self.target.parent / ".dolly-update-test"
        with patch.object(worker, "check_processes"), patch.object(updater, "check_latest", return_value=release), \
             patch.object(updater, "download_update", return_value=work), patch.object(worker, "launch_worker") as install:
            boot.check_for_update(self.target, events, (123,))
            self.assertEqual(events.get_nowait(), ("ready", work))
            install.assert_not_called()

    def test_skipped_window_ignores_late_ready_event(self):
        window = boot.StartupWindow.__new__(boot.StartupWindow)
        window.closed = True; window.events = queue.Queue(); window.events.put(("ready", self.target))
        with patch.object(worker, "launch_worker") as install:
            window.poll()
            install.assert_not_called()

    def test_editor_opened_during_download_defers_installation(self):
        window = boot.StartupWindow.__new__(boot.StartupWindow)
        window.closed = False; window.target = self.target; window.excluded = (123,)
        window.events = queue.Queue(); window.events.put(("ready", self.target))
        window.root = Mock(); window.status = Mock()
        with patch.object(worker, "check_processes", side_effect=RuntimeError("second editor")), \
             patch.object(worker, "launch_worker") as install:
            window.poll()
            install.assert_not_called()
            self.assertIn("deferred", window.status.set.call_args.args[0])

    def test_new_process_has_independent_pyinstaller_runtime(self):
        with patch.object(worker.subprocess, "Popen") as launch:
            worker.launch_program(["Dolly.exe"], self.target)
            self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_legacy_manifest_migrates_to_single_public_executable(self):
        package(self.target, "1.0.0")
        work = self.target.parent / ".dolly-update-migrate"
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
        work = self.target.parent / ".dolly-update-test"; work.mkdir()
        plan = {"target": str(self.target), "work": str(work)}
        worker.atomic_json(self.target / worker.PENDING, plan)
        worker.atomic_json(work / "plan.json", {**plan, "target": str(self.target.parent)})
        with self.assertRaisesRegex(ValueError, "disagrees"):
            worker.pending_work(self.target)
