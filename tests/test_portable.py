"""Portable data, direct startup and game DLL-search isolation regressions."""
from contextlib import ExitStack
import ctypes
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import runtime, desktop, bootstrap


class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Dolly portable ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.app = self.base / "Folder with spaces"
        self.bundle = self.app / "_internal"

    def frozen(self):
        stack = ExitStack()
        stack.enter_context(patch.object(sys, "frozen", True, create=True))
        stack.enter_context(patch.object(sys, "_MEIPASS", str(self.bundle), create=True))
        stack.enter_context(patch.object(sys, "executable", str(self.app / "Dolly.exe")))
        return stack

    def native_api(self, *, succeeds=True):
        def get(size, buffer):
            value = str(self.bundle)
            if buffer is not None:
                buffer.value = value
            return len(value)
        return SimpleNamespace(GetDllDirectoryW=Mock(side_effect=get),
                               SetDllDirectoryW=Mock(return_value=int(succeeds)))

    def test_source_and_portable_have_distinct_resource_and_persistent_roots(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(runtime.application_root(self.app), self.app)
            self.assertEqual(runtime.resource_root(self.app), self.app)
        with self.frozen():
            # sys.executable can contain a Windows short-name folder alias.
            self.assertEqual(runtime.application_root(), self.app.resolve())
            self.assertEqual(runtime.resource_root(), self.bundle)
            self.assertNotEqual(runtime.application_root() / "logs", self.bundle / "logs")

    def test_source_game_launch_leaves_environment_and_native_api_untouched(self):
        with patch.object(sys, "frozen", False, create=True), \
                patch.object(ctypes, "WinDLL", create=True) as api:
            with runtime.external_program_environment() as env:
                self.assertIsNone(env)
        api.assert_not_called()

    def test_frozen_game_launch_clears_dll_directory_and_filters_only_bundle_path(self):
        api = self.native_api()
        outside = str(self.base / "Other libraries")
        bundle_path = str(self.bundle / "lib")
        with self.frozen(), patch.object(sys, "platform", "win32"), \
                patch.object(ctypes, "WinDLL", return_value=api, create=True), \
                patch.dict(runtime.os.environ, {"PATH": bundle_path + ";" + outside}):
            with runtime.external_program_environment() as env:
                self.assertEqual(api.SetDllDirectoryW.call_args.args, (None,))
                self.assertEqual(env["PATH"], outside)
                self.assertEqual(runtime.os.environ["PATH"], bundle_path + ";" + outside)
        self.assertEqual([c.args for c in api.SetDllDirectoryW.call_args_list], [(None,), (str(self.bundle),)])

    def test_native_path_restored_when_game_spawn_raises(self):
        api = self.native_api()
        with self.frozen(), patch.object(sys, "platform", "win32"), \
                patch.object(ctypes, "WinDLL", return_value=api, create=True):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                with runtime.external_program_environment():
                    raise OSError("spawn failed")
        self.assertEqual(api.SetDllDirectoryW.call_args.args, (str(self.bundle),))

    def test_failed_native_preparation_does_not_allow_game_spawn(self):
        api = self.native_api(succeeds=False)
        spawned = False
        with self.frozen(), patch.object(sys, "platform", "win32"), \
                patch.object(ctypes, "WinDLL", return_value=api, create=True):
            with self.assertRaisesRegex(OSError, "DLL search path"):
                with runtime.external_program_environment():
                    spawned = True
        self.assertFalse(spawned)

    def test_direct_launcher_writes_logs_with_no_console_and_no_child_python(self):
        with self.frozen(), patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                patch("dolly.__main__.main", return_value=0) as gui, \
                patch("subprocess.Popen") as spawn:
            self.assertEqual(desktop.main([]), 0)
        gui.assert_called_once_with()
        spawn.assert_not_called()
        self.assertIn("frozen=True", (self.app / "logs/Dolly_startup.log").read_text())
        self.assertFalse((self.bundle / "logs").exists())

    def test_hidden_startup_exception_keeps_traceback_in_app_folder(self):
        with self.frozen(), patch("dolly.__main__.main", side_effect=RuntimeError("cannot open editor")), \
                patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main([]), 1)
        self.assertIn("cannot open editor", (self.app / "logs/Dolly_startup.log").read_text())
        self.assertIn(str((self.app / "logs/Dolly_startup.log").resolve()), message.call_args.args[0])

    def test_unwritable_log_reports_error_before_gui_start(self):
        self.app.mkdir(parents=True)
        (self.app / "logs").write_text("This is not a directory")
        with self.frozen(), patch("dolly.__main__.main") as gui, \
                patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main([]), 1)
        gui.assert_not_called()
        self.assertTrue(message.call_args.kwargs["error"])

    def test_recovery_uses_existing_journals_without_starting_editor(self):
        with self.frozen(), patch("dolly.launcher.recover_pending", return_value=["session"]) as recover, \
                patch("dolly.__main__.main") as gui, patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main(["--recover"]), 0)
        recover.assert_called_once_with()
        gui.assert_not_called()
        self.assertIn("Recovered 1", message.call_args.args[0])

    def test_recovery_failure_keeps_error_visible_and_logged(self):
        with self.frozen(), patch("dolly.launcher.recover_pending", side_effect=RuntimeError("Close Deadlock")), \
                patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main(["--recover"]), 1)
        self.assertIn("Close Deadlock", message.call_args.args[0])

    def test_self_test_dispatch_does_not_enter_interactive_editor(self):
        report = self.base / "check.json"
        with self.frozen(), patch.object(desktop, "bundle_self_test", return_value=0) as check, \
                patch("dolly.__main__.main") as gui:
            self.assertEqual(desktop.main(["--self-test", str(report)]), 0)
        check.assert_called_once_with(report)
        gui.assert_not_called()

    def test_python_bootstrap_cannot_recursively_launch_frozen_executable(self):
        with self.frozen(), patch.object(sys, "platform", "win32"), patch("subprocess.Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "directly"):
                bootstrap.launch_gui(self.app)
        spawn.assert_not_called()

    def test_gui_recovery_uses_serial_worker_and_existing_guarded_recovery(self):
        from dolly.gui import DollyApp, recover_pending
        app = SimpleNamespace(_submit=Mock(), root=object())
        DollyApp._recover_game_config(app)
        label, operation, callback = app._submit.call_args.args
        self.assertIs(operation, recover_pending)
        with patch("dolly.gui.messagebox.showinfo") as info:
            callback([])
        self.assertIn("No pending", info.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
