"""Exercise console-free editor startup without launching a real GUI."""

import ctypes
import io
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import bootstrap
from dolly import __main__ as editor_main


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="Dolly launcher ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Dolly folder with spaces"

    def test_windows_uses_same_interpreter_and_no_console_with_live_log_handles(self):
        captured = {}

        def spawn(command, **options):
            captured.update(command=command, **options)
            self.assertFalse(options["stdout"].closed)
            # Windows may expand a temporary folder's 8.3 alias (RUNNER~1).
            self.assertEqual(Path(options["stdout"].name),
                             (self.root / "logs" / "Dolly_startup.log").resolve())
            options["stdout"].write(b"child startup output\r\n")
            return SimpleNamespace(pid=4567)

        with patch.object(bootstrap.sys, "platform", "win32"), \
                patch.object(bootstrap.sys, "executable", r"B:\Python Install\python.exe"), \
                patch.object(bootstrap.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch.object(bootstrap.subprocess, "Popen", side_effect=spawn) as popen:
            self.assertEqual(bootstrap.launch_gui(self.root), 4567)

        popen.assert_called_once()
        self.assertEqual(captured["command"], [r"B:\Python Install\python.exe", "-u", "-m", "dolly"])
        self.assertEqual(captured["cwd"], str(self.root.resolve()))
        self.assertEqual(captured["creationflags"], 0x08000000)
        self.assertIs(captured["stdin"], subprocess.DEVNULL)
        self.assertIs(captured["stderr"], subprocess.STDOUT)
        self.assertTrue(captured["close_fds"])
        self.assertNotIn("shell", captured)
        self.assertTrue(captured["stdout"].closed)
        self.assertEqual((self.root / "logs" / "Dolly_startup.log").read_bytes(),
                         b"child startup output\r\nDolly editor started (PID 4567).\r\n")

    def test_bootstrap_appends_without_erasing_batch_validation_log(self):
        logs = self.root / "logs"
        logs.mkdir(parents=True)
        path = logs / "Dolly_startup.log"
        path.write_text("Interpreter validation passed\n", encoding="utf-8")
        with patch.object(bootstrap.sys, "platform", "win32"), \
                patch.object(bootstrap.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch.object(bootstrap.subprocess, "Popen", return_value=SimpleNamespace(pid=8901)):
            bootstrap.launch_gui(self.root)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "Interpreter validation passed\nDolly editor started (PID 8901).\n")

    def test_non_windows_fails_before_creating_logs_or_starting_process(self):
        with patch.object(bootstrap.sys, "platform", "linux"), \
                patch.object(bootstrap.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                bootstrap.launch_gui(self.root)
        popen.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_native_spawn_failure_propagates_and_closes_log_handle(self):
        handles = []

        def fail(*args, **options):
            handles.append(options["stdout"])
            raise OSError("Windows could not create the editor process")

        with patch.object(bootstrap.sys, "platform", "win32"), \
                patch.object(bootstrap.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch.object(bootstrap.subprocess, "Popen", side_effect=fail):
            with self.assertRaisesRegex(OSError, "could not create"):
                bootstrap.launch_gui(self.root)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        self.assertNotIn(b"started", (self.root / "logs" / "Dolly_startup.log").read_bytes())

    def test_main_returns_failure_and_actionable_message_for_spawn_error(self):
        output = io.StringIO()
        logs = self.root / "logs"
        logs.mkdir(parents=True)
        startup_log = logs / "Dolly_startup.log"
        startup_log.write_text("Interpreter validation passed\n", encoding="utf-8")
        with patch.object(bootstrap, "__file__", str(self.root / "dolly" / "bootstrap.py")), \
                patch.object(bootstrap, "launch_gui", side_effect=OSError("Access denied")), \
                patch.object(bootstrap.sys, "stderr", output):
            self.assertEqual(bootstrap.main(), 1)
        self.assertIn("Access denied", output.getvalue())
        self.assertIn("Dolly_startup.log", output.getvalue())
        self.assertEqual(startup_log.read_text(encoding="utf-8"),
                         "Interpreter validation passed\nCould not start the Dolly editor: Access denied\n")

    def test_main_keeps_original_error_visible_when_log_cannot_be_opened(self):
        output = io.StringIO()
        with patch.object(bootstrap, "launch_gui", side_effect=PermissionError("Startup log is locked")), \
                patch.object(Path, "open", side_effect=PermissionError("Still locked")), \
                patch.object(bootstrap.sys, "stderr", output):
            self.assertEqual(bootstrap.main(), 1)
        self.assertIn("Startup log is locked", output.getvalue())
        self.assertIn("Dolly_startup.log", output.getvalue())

    def test_batch_closes_its_log_redirection_before_both_bootstrap_branches(self):
        # Windows cmd.exe keeps a redirected log open for the full command.
        # Reopening it inside bootstrap caused the reported Errno 13 failure.
        batch = (Path(__file__).resolve().parents[1] / "Launch_Dolly.bat").read_text()
        launches = [line.strip() for line in batch.splitlines()
                    if re.match(r"^(?:py|python)\s+.*-m dolly\.bootstrap\b", line)]
        self.assertEqual(launches, ["py -3 -u -m dolly.bootstrap", "python -u -m dolly.bootstrap"])
        validations = [line for line in batch.splitlines() if " -c " in line]
        self.assertEqual(len(validations), 2)
        self.assertTrue(all('>> "%DOLLY_STARTUP_LOG%" 2>&1' in line for line in validations))

    def test_main_returns_success_after_spawn_without_waiting_for_gui(self):
        with patch.object(bootstrap, "launch_gui", return_value=2345) as start:
            self.assertEqual(bootstrap.main(), 0)
        start.assert_called_once_with()

    def assert_hidden_startup_error_is_visible(self, *, logging_failure):
        message_box = Mock(return_value=1)
        user32 = SimpleNamespace(MessageBoxW=message_box)
        output = io.StringIO()
        failure = PermissionError("Log file is locked") if logging_failure else None
        with patch.object(editor_main, "__file__", str(self.root / "dolly" / "__main__.py")), \
                patch.object(editor_main, "os", SimpleNamespace(name="nt")), \
                patch.object(editor_main.logging, "getLogger", return_value=Mock()), \
                patch.object(editor_main, "RotatingFileHandler", side_effect=failure), \
                patch.object(editor_main.sys, "stderr", output), \
                patch.object(ctypes, "WinDLL", return_value=user32, create=True), \
                patch("dolly.branding.set_taskbar_identity"), \
                patch("dolly.gui.main", side_effect=RuntimeError("Tk could not create a window")) as gui:
            self.assertEqual(editor_main.main(), 1)

        message_box.assert_called_once()
        parent, message, title, flags = message_box.call_args.args
        self.assertIsNone(parent)
        self.assertIn(str((self.root / "logs" / "Dolly_startup.log").resolve()), message)
        self.assertIn(str((self.root / "logs" / "Dolly.log").resolve()), message)
        self.assertIn("startup error", title)
        self.assertEqual(flags, 0x10)
        if logging_failure:
            gui.assert_not_called()
            self.assertIn("Log file is locked", output.getvalue())
        else:
            gui.assert_called_once_with()
            self.assertIn("Tk could not create a window", output.getvalue())

    def test_hidden_gui_startup_failure_shows_windows_error_dialog_with_logs(self):
        self.assert_hidden_startup_error_is_visible(logging_failure=False)

    def test_hidden_log_handler_failure_shows_windows_error_dialog_with_logs(self):
        self.assert_hidden_startup_error_is_visible(logging_failure=True)


if __name__ == "__main__":
    unittest.main()
