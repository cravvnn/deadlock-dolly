import queue
import unittest
from unittest.mock import patch
from dolly import edition, settings, release_launcher


class EditionTests(unittest.TestCase):
    def test_separate_preferences_and_update_default(self):
        with patch.object(edition,'_environment',False):
            original=settings.settings_path()
            self.assertTrue(settings.AppSettings().auto_updates)
            edition.configure_environment()
            self.assertNotEqual(settings.settings_path(),original)
            self.assertEqual(settings.settings_path().parent.name,'DollyEnvironmentCapture')
            self.assertFalse(settings.AppSettings().auto_updates)
            self.assertEqual(edition.application_name(),'Dolly Environment Capture')

    def test_environment_updater_never_queries_normal_release(self):
        with patch.object(edition,'_environment',True), patch.object(release_launcher.updater,'check_latest') as check:
            events=queue.Queue()
            release_launcher.check_for_update(None,events,[])
            check.assert_not_called()
            self.assertIn('separate ZIPs',events.get_nowait()[1])
