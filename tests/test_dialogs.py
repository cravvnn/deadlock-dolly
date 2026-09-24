"""Clipboard text produced by the error dialog's copy button."""

import unittest

from dolly import __version__
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


if __name__ == "__main__":
    unittest.main()
