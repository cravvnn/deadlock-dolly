import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly import edition, settings, update_ui


class EditionTests(unittest.TestCase):
    def test_separate_preferences_and_update_default(self):
        with patch.object(edition, '_environment', False):
            original = settings.settings_path()
            self.assertTrue(settings.AppSettings().auto_updates)
            edition.configure_environment()
            self.assertNotEqual(settings.settings_path(), original)
            self.assertEqual(settings.settings_path().parent.name, 'DollyEnvironmentCapture')
            self.assertFalse(settings.AppSettings().auto_updates)
            self.assertEqual(edition.application_name(), 'Dolly Environment Capture')

    def test_environment_updater_never_queries_normal_release(self):
        with patch.object(edition, '_environment', True), \
                patch.object(update_ui.updater, 'check_latest') as check:
            ui = object.__new__(update_ui.UpdateUI)
            ui.restarting = False
            ui.app = SimpleNamespace(closed=False, update_status=Mock())
            ui.check()
            check.assert_not_called()
            self.assertIn('separate ZIPs', ui.app.update_status.set.call_args.args[0])
