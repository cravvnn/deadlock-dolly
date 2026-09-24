"""Clipboard text and dispatch for the error dialog's copy button."""

import unittest
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


if __name__ == "__main__":
    unittest.main()
