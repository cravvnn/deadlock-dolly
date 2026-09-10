"""Session mount cleanup boundaries and the UI-to-process-waiter handoff."""
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dolly import launcher, session_cleanup as cleanup
from dolly.controller import Controller
from test_launcher import fake_game, GAMEINFO


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.paths = fake_game(self.folder / "Deadlock")
        self.package = self.folder / "Dolly"
        process = MagicMock()
        process.pid = 9876
        process._handle = 1234
        process.poll.return_value = None
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.package), patch.object(launcher.subprocess, "Popen", return_value=process), patch.object(launcher.threading, "Thread"):
            self.session = launcher.launch(self.paths.root)

    def recover(self, root=None):
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", root or self.package):
            return launcher.recover_pending(self.paths.root)

    def test_restored_journal_is_retried_after_ui_closed_first(self):
        self.session.restore_gameinfo()
        self.assertTrue(self.session.overlay_dir.exists())
        self.assertEqual(self.recover(), [str(self.session.session_dir)])
        self.assertFalse(self.session.overlay_dir.exists())
        self.assertTrue((self.session.session_dir / "original.gameinfo.gi").is_file())
        self.assertEqual(self.recover(), [])

    def test_recovery_from_new_portable_folder_finds_old_mounted_journal(self):
        self.recover(self.folder / "New Dolly")
        self.assertFalse(self.session.overlay_dir.exists())
        self.assertEqual(self.paths.gameinfo.read_bytes(), GAMEINFO.encode())

    def test_recovery_after_old_portable_folder_removed_uses_marker_and_current_mounts(self):
        self.session.restore_gameinfo()
        shutil.rmtree(self.package)
        self.recover(self.folder / "New Dolly")
        self.assertFalse(self.session.overlay_dir.exists())

    def test_moved_portable_folder_with_restored_logs_discovers_old_mount(self):
        self.session.restore_gameinfo()
        moved = self.folder / "Moved Dolly"
        self.package.rename(moved)
        self.recover(moved)
        self.assertFalse(self.session.overlay_dir.exists())
        self.assertTrue((moved / "logs" / self.session.session_dir.name / "original.gameinfo.gi").is_file())

    def test_missing_backup_with_active_mount_stops_recovery_without_deleting(self):
        shutil.rmtree(self.package)
        mounted = self.paths.gameinfo.read_bytes()
        with self.assertRaisesRegex(launcher.LaunchError, "still references"):
            self.recover(self.folder / "New Dolly")
        self.assertEqual(self.paths.gameinfo.read_bytes(), mounted)
        self.assertTrue(self.session.overlay_dir.exists())

    def test_recover_without_game_argument_discovers_old_version_mounts(self):
        self.session.restore_gameinfo()
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"steam.exe"}), patch.object(launcher, "PACKAGE_ROOT", self.folder / "New Dolly"), patch.object(launcher, "discover_game", return_value=self.paths.root):
            launcher.recover_pending()
        self.assertFalse(self.session.overlay_dir.exists())

    def test_completed_journal_does_not_authorize_deleting_a_remounted_directory(self):
        self.session.restore_gameinfo()
        changed = launcher.make_gameinfo(GAMEINFO, self.session.overlay_dir.name).encode()
        self.paths.gameinfo.write_bytes(changed)
        with self.assertRaisesRegex(launcher.LaunchError, "still references"):
            self.recover()
        self.assertTrue(self.session.overlay_dir.exists())
        self.assertEqual(self.paths.gameinfo.read_bytes(), changed)

    def test_different_external_edit_survives_cleanup_after_original_restore(self):
        self.session.restore_gameinfo()
        edited = GAMEINFO.replace('game "citadel"', 'game "A new title"').encode()
        self.paths.gameinfo.write_bytes(edited)
        self.recover()
        self.assertEqual(self.paths.gameinfo.read_bytes(), edited)
        self.assertFalse(self.session.overlay_dir.exists())

    def test_wildcard_search_mount_prevents_deleting_a_referenced_directory(self):
        self.session.restore_gameinfo()
        self.paths.gameinfo.write_text(
            GAMEINFO.replace('Game "core"', 'Game "citadel_dolly_*/cvar_unlocker"'),
            encoding="utf-8")
        with self.assertRaisesRegex(launcher.LaunchError, "still references"):
            self.recover()
        self.assertTrue(self.session.overlay_dir.exists())

    def test_comment_mention_of_old_mount_does_not_prevent_cleanup(self):
        self.session.restore_gameinfo()
        self.paths.gameinfo.write_text(
            GAMEINFO + "\n// Previous mount " + self.session.overlay_dir.name,
            encoding="utf-8")
        self.recover()
        self.assertFalse(self.session.overlay_dir.exists())

    def test_unknown_user_content_preserves_the_whole_directory(self):
        self.session.restore_gameinfo()
        extra = self.session.overlay_dir / "notes.txt"
        extra.write_text("keep me")
        with self.assertRaisesRegex(launcher.LaunchError, "extra file"):
            self.recover()
        self.assertEqual(extra.read_text(), "keep me")
        self.assertTrue((self.session.overlay_dir / "cvar_unlocker/bin/win64/server.dll").is_file())

    def test_linked_child_is_not_followed_or_removed(self):
        self.session.restore_gameinfo()
        outside = self.folder / "outside"
        outside.mkdir()
        protected = outside / "server.dll"
        protected.write_bytes(b"keep me")
        child = self.session.overlay_dir / "cvar_unlocker"
        shutil.rmtree(child)
        try:
            child.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symbolic links are not available")
        with self.assertRaisesRegex(launcher.LaunchError, "link"):
            self.recover()
        self.assertEqual(protected.read_bytes(), b"keep me")
        self.assertTrue(child.is_symlink())

    def test_marker_for_another_installation_does_not_authorize_cleanup(self):
        self.session.restore_gameinfo()
        marker = self.session.overlay_dir / ".dolly-session.json"
        data = json.loads(marker.read_text())
        data["original_gameinfo"] = str(self.folder / "Other/game/citadel/gameinfo.gi")
        marker.write_text(json.dumps(data))
        with self.assertRaisesRegex(launcher.LaunchError, "unexpected game"):
            self.recover()
        self.assertTrue(self.session.overlay_dir.exists())

    def test_orphan_with_relative_or_mismatched_session_marker_is_ignored(self):
        self.session.restore_gameinfo()
        marker = self.session.overlay_dir / ".dolly-session.json"
        original = json.loads(marker.read_text())
        for value in ("../../logs/elsewhere", str(self.folder / "logs/elsewhere")):
            marker.write_text(json.dumps({**original, "session_dir": value}))
            self.assertEqual(self.recover(self.folder / "New Dolly"), [])
            self.assertTrue(self.session.overlay_dir.exists())

    def test_cleanup_deferral_keeps_marker_and_retries_after_locked_dll(self):
        self.session.restore_gameinfo()
        self.session.process.poll.return_value = 0
        unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path.name == "server.dll":
                raise PermissionError("still releasing the loaded module")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", locked):
            self.session.close()
        self.assertTrue((self.session.overlay_dir / ".dolly-session.json").is_file())
        self.assertIn("cleanup deferred", self.session.read_log())
        self.recover()
        self.assertFalse(self.session.overlay_dir.exists())

    def test_concurrent_cleanup_is_idempotent_after_both_validate_the_mount(self):
        self.session.restore_gameinfo()
        unlink = Path.unlink
        raced = False
        def another_cleanup_first(path, *args, **kwargs):
            nonlocal raced
            if path.name == "server.dll" and not raced:
                raced = True
                cleanup.remove_overlay(self.session.overlay_dir, self.paths, self.session.session_dir)
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", another_cleanup_first):
            cleanup.remove_overlay(self.session.overlay_dir, self.paths, self.session.session_dir)
        self.assertTrue(raced)
        self.assertFalse(self.session.overlay_dir.exists())

    def test_game_alive_never_removes_mounted_files(self):
        self.session.restore_gameinfo()
        self.assertFalse(self.session.close())
        self.assertTrue(self.session.overlay_dir.exists())
        with patch.object(launcher, "_check_runtime"), patch.object(launcher, "running_processes", return_value={"deadlock.exe"}), self.assertRaisesRegex(launcher.LaunchError, "Exit Deadlock"):
            launcher.recover_pending(self.paths.root)
        self.assertTrue(self.session.overlay_dir.exists())

    def test_handoff_is_once_only_and_does_not_kill_game(self):
        with patch.object(cleanup, "start_waiter") as start:
            self.assertTrue(self.session.handoff_cleanup())
            self.assertTrue(self.session.handoff_cleanup())
        start.assert_called_once_with(self.session)
        self.session.process.kill.assert_not_called()
        self.session.process.terminate.assert_not_called()

    def test_game_exit_saves_native_graphics_evidence_before_cleanup(self):
        evidence = {"graphics": {"latest": {"pending_count": 32767}}}
        self.session.native = MagicMock()
        self.session.native.diagnostics.return_value = evidence
        self.session.process.poll.return_value = 1
        self.session.close()
        saved = self.session.session_dir / "native_diagnostics.json"
        self.assertEqual(json.loads(saved.read_text()), evidence)
        self.assertFalse(self.session.overlay_dir.exists())
        self.assertEqual(self.paths.gameinfo.read_bytes(), GAMEINFO.encode())

    def test_diagnostic_save_failure_does_not_prevent_restoration_or_removal(self):
        self.session.native = MagicMock()
        self.session.native.diagnostics.return_value = {"graphics": {"samples": []}}
        self.session.process.poll.return_value = 1
        write = launcher._atomic_write
        def failing(path, *args, **kwargs):
            if path.name == "native_diagnostics.json":
                raise OSError("diagnostic write unavailable")
            return write(path, *args, **kwargs)
        with patch.object(launcher, "_atomic_write", side_effect=failing):
            self.session.close()
        self.assertFalse(self.session.overlay_dir.exists())
        self.assertEqual(self.paths.gameinfo.read_bytes(), GAMEINFO.encode())

    def test_failed_helper_creation_retains_retryable_mount(self):
        self.session.restore_gameinfo()
        with patch.object(cleanup, "start_waiter", side_effect=OSError("denied")):
            self.assertFalse(self.session.handoff_cleanup())
        self.assertTrue(self.session.overlay_dir.exists())
        self.assertIn("next Dolly launch", self.session.read_log())
        self.recover()
        self.assertFalse(self.session.overlay_dir.exists())

    def test_controller_close_hands_off_even_if_restore_reports_conflict(self):
        controller = Controller.__new__(Controller)
        controller.disconnect = MagicMock()
        controller._session = MagicMock()
        controller._session.restore_gameinfo.side_effect = launcher.LaunchError("conflict")
        with self.assertRaisesRegex(launcher.LaunchError, "conflict"):
            controller.close()
        controller.disconnect.assert_called_once()
        controller._session.handoff_cleanup.assert_called_once()

    def test_controller_close_restores_before_handoff(self):
        controller = Controller.__new__(Controller)
        controller.disconnect = MagicMock()
        controller._session = MagicMock()
        controller.close()
        self.assertEqual([call[0] for call in controller._session.method_calls], ["restore_gameinfo", "handoff_cleanup"])

    def test_detached_waiter_only_removes_after_exact_handle_signals(self):
        self.session.restore_gameinfo()
        api = MagicMock()
        def wait(handle, timeout):
            self.assertEqual((handle, timeout), (5678, 0xFFFFFFFF))
            self.assertTrue(self.session.overlay_dir.exists())
            return 0
        api.WaitForSingleObject.side_effect = wait
        with patch.object(cleanup.sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=api, create=True), patch.object(launcher, "running_processes", return_value={"steam.exe"}):
            self.assertEqual(cleanup.wait_and_cleanup(self.session.session_dir, 5678), 0)
        api.CloseHandle.assert_called_once_with(5678)
        self.assertFalse(self.session.overlay_dir.exists())

    def test_failed_wait_does_not_remove_anything(self):
        api = MagicMock()
        api.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch.object(cleanup.sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=api, create=True):
            self.assertEqual(cleanup.wait_and_cleanup(self.session.session_dir, 5678), 1)
        self.assertTrue(self.session.overlay_dir.exists())
        self.assertIn(self.session.overlay_dir.name, self.paths.gameinfo.read_text())

    def test_waiter_defers_if_another_game_started(self):
        self.session.restore_gameinfo()
        api = MagicMock()
        api.WaitForSingleObject.return_value = 0
        with patch.object(cleanup.sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=api, create=True), patch.object(launcher, "running_processes", return_value={"deadlock.exe"}):
            self.assertEqual(cleanup.wait_and_cleanup(self.session.session_dir, 5678), 0)
        self.assertTrue(self.session.overlay_dir.exists())

    def test_waiter_process_uses_inherited_wait_only_handle_and_frozen_entry(self):
        api = MagicMock()
        api.GetCurrentProcess.return_value = 99
        def duplicate(current, original, target, destination, access, inherit, flags):
            self.assertEqual((current, original, target, access, inherit, flags), (99, 1234, 99, 0x00100000, True, 0))
            destination._obj.value = 5678
            return 1
        api.DuplicateHandle.side_effect = duplicate
        startup = MagicMock()
        with patch.object(cleanup.sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=api, create=True), patch("dolly.runtime.is_frozen", return_value=True), patch.object(cleanup.subprocess, "STARTUPINFO", return_value=startup, create=True), patch.object(cleanup.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), patch.object(cleanup.subprocess, "Popen") as popen:
            cleanup.start_waiter(self.session)
        args, kwargs = popen.call_args
        # Windows TEMP may use an 8.3 alias; the helper receives the resolved
        # long path so it can reopen the same journal after Dolly exits.
        self.assertEqual(args[0][1:], ["--cleanup-session", str(self.session.session_dir.resolve()), "5678"])
        self.assertEqual(startup.lpAttributeList, {"handle_list": [5678]})
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(api.CloseHandle.call_args.args[0].value, 5678)

    def test_portable_entry_routes_helper_without_importing_gui(self):
        from dolly import desktop
        with patch.object(cleanup, "wait_and_cleanup", return_value=0) as helper, patch("dolly.__main__.main") as gui:
            self.assertEqual(desktop._run(["--cleanup-session", str(self.session.session_dir), "5678"]), 0)
        helper.assert_called_once_with(self.session.session_dir, 5678)
        gui.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Real process-handle inheritance requires Windows")
    def test_windows_detached_helper_waits_for_real_process_then_cleans_mount(self):
        if launcher._game_is_running(launcher.running_processes()):
            self.skipTest("A real Deadlock process is already running")
        self.session.restore_gameinfo()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def reap(child):
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        self.addCleanup(reap, process)
        self.session.process = process
        helpers = []
        spawn = subprocess.Popen
        def track_helper(*args, **kwargs):
            child = spawn(*args, **kwargs)
            helpers.append(child)
            self.addCleanup(reap, child)
            return child
        # Only this test owns and waits for the helper. Production deliberately
        # detaches it so closing Dolly never waits for the game to exit.
        with patch.object(cleanup.subprocess, "Popen", side_effect=track_helper):
            cleanup.start_waiter(self.session)
        self.assertEqual(len(helpers), 1)
        self.assertTrue(self.session.overlay_dir.exists())
        process.wait(timeout=10)
        self.assertEqual(helpers[0].wait(timeout=10), 0, self.session.read_log())
        self.assertFalse(self.session.overlay_dir.exists(), self.session.read_log())


if __name__ == "__main__":
    unittest.main()
