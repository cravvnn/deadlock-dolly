"""Choosing the ignore action on Deadlock's assertion dialogs."""
import unittest

from dolly.assert_watcher import choose_assert_action


class ChooseAssertAction(unittest.TestCase):
    def test_rich_dialog_prefers_the_quiet_ignore(self):
        self.assertEqual(choose_assert_action(
            ["Break", "Ignore", "Ignore For 24 Hours", "Exit"]), "Ignore For 24 Hours")

    def test_fallback_message_box_buttons_with_mnemonics_match(self):
        self.assertEqual(choose_assert_action(["Cancel", "&Try Again", "&Continue"]), "Continue")
        self.assertEqual(choose_assert_action(["Cancel", "&Try Again"]), "Try Again")

    def test_unknown_or_debugger_only_dialogs_are_left_alone(self):
        self.assertIsNone(choose_assert_action(["&Debug", "E&xit", "", "  "]))


if __name__ == "__main__":
    unittest.main()
