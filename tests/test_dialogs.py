"""Clipboard text and dispatch for the error dialog's copy button."""

import unittest
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

from dolly import __version__
from dolly import dialogs
from dolly.dialogs import COPY_LABEL, format_error_details


class FormatErrorDetailsTests(unittest.TestCase):
    def test_includes_version_and_operation(self):
        details = format_error_details("Play shot", "Camera position did not settle.")
        lines = details.splitlines()
        self.assertEqual(lines[0], f"Deadlock Dolly {__version__} | Play shot")
        self.assertEqual(lines[1], "Camera position did not settle.")

    def test_error_text_starts_on_the_second_line(self):
        details = format_error_details("Record video", "Launch a DirectX 11 replay through Dolly.")
        self.assertTrue(details.splitlines()[1].startswith("Launch a DirectX 11 replay"))

    def test_blank_title_omits_separator(self):
        details = format_error_details("", "Something failed.")
        self.assertEqual(details.splitlines()[0], f"Deadlock Dolly {__version__}")

    def test_blank_message_keeps_only_the_headline(self):
        details = format_error_details("Play shot", "   ")
        self.assertEqual(details, f"Deadlock Dolly {__version__} | Play shot")

    def test_multiline_message_is_preserved(self):
        details = format_error_details("Launching hideout", "first line\nsecond line")
        self.assertTrue(details.endswith("first line\nsecond line"))

    def test_copy_label_wording(self):
        self.assertEqual(COPY_LABEL, "Copy error details")


class ShowErrorDispatchTests(unittest.TestCase):
    def test_uses_native_dialog_on_windows(self):
        with mock.patch.object(dialogs, "_native_dialog", return_value=True) as native, \
                mock.patch.object(dialogs.messagebox, "showerror") as fallback:
            dialogs.show_error(None, "Play shot", "Camera failed.")
        native.assert_called_once()
        fallback.assert_not_called()

    def test_native_dialog_receives_message_and_details(self):
        with mock.patch.object(dialogs, "_native_dialog", return_value=True) as native:
            dialogs.show_error(None, "Play shot", "Camera failed.")
        title, message, details = native.call_args.args[1:]
        self.assertEqual(title, "Play shot")
        self.assertEqual(message, "Camera failed.")
        self.assertEqual(details, f"Deadlock Dolly {__version__} | Play shot\nCamera failed.")

    def test_falls_back_when_native_dialog_raises(self):
        with mock.patch.object(dialogs, "_native_dialog", side_effect=OSError("no comctl32")), \
                mock.patch.object(dialogs.messagebox, "showerror") as fallback:
            dialogs.show_error(None, "Play shot", "Camera failed.")
        fallback.assert_called_once()

    def test_falls_back_when_native_dialog_fails(self):
        with mock.patch.object(dialogs, "_native_dialog", return_value=False), \
                mock.patch.object(dialogs.messagebox, "showerror") as fallback:
            dialogs.show_error(None, "Play shot", "Camera failed.")
        fallback.assert_called_once()

    def test_non_windows_uses_message_box(self):
        with mock.patch.object(dialogs.sys, "platform", "linux"), \
                mock.patch.object(dialogs, "_native_dialog") as native, \
                mock.patch.object(dialogs.messagebox, "showerror") as fallback:
            dialogs.show_error(None, "Play shot", "Camera failed.")
        native.assert_not_called()
        fallback.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "native Windows/Tk regression")
class NativeDialogLifecycleTests(unittest.TestCase):
    def test_modal_dialog_survives_tk_events_and_copy_then_ok(self):
        # A separate process bounds a native abort/regression and its windows.
        # Copy is intercepted so this check never changes the user's clipboard.
        code = textwrap.dedent('''
            import ctypes
            import threading
            import time
            import tkinter as tk
            from dolly import dialogs
            root = tk.Tk()
            root.withdraw()
            owner = root.winfo_id()
            ticks = []
            copied = []
            errors = []
            dialogs._copy_text = lambda text: copied.append((text, threading.get_ident())) or True
            main_thread = threading.get_ident()
            user32 = ctypes.WinDLL("user32")
            user32.GetParent.argtypes = [ctypes.c_void_p]
            user32.GetParent.restype = ctypes.c_void_p
            user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            def external_events():
                deadline = time.monotonic() + 5
                while not dialogs._ACTIVE and time.monotonic() < deadline:
                    time.sleep(.01)
                if not dialogs._ACTIVE:
                    return
                hwnd = next(iter(dialogs._ACTIVE))
                time.sleep(.2)
                # Tk enables event servicing during a window move. The old
                # ctypes pump dispatches this with no saved Tk thread state.
                user32.PostMessageW(user32.GetParent(owner) or owner, 0x0231, 0, 0)
                user32.PostMessageW(owner, 0x000F, 0, 0)
                time.sleep(.1)
                user32.PostMessageW(hwnd, 0x0111, 1001, 0)
                time.sleep(.1)
                user32.PostMessageW(hwnd, 0x0111, 1, 0)
            def tick():
                ticks.append(1)
                root.after(20, tick)
            def show():
                root.after(20, tick)
                threading.Thread(target=external_events, daemon=True).start()
                try:
                    assert dialogs._native_dialog(root, "Dolly dialog regression", "Insufficient temporary space.", "expected details")
                    assert len(ticks) > 2, ticks
                    assert copied == [("expected details", copied[0][1])], copied
                    assert copied[0][1] != main_thread
                    assert not dialogs._ACTIVE
                except Exception as exc:
                    errors.append(exc)
                finally:
                    root.destroy()
            root.after(20, show)
            root.mainloop()
            if errors:
                raise errors[0]
            print("native dialog lifecycle passed")
        ''')
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("native dialog lifecycle passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
