import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly import edition, settings, update_ui


class EditionTests(unittest.TestCase):
    def test_removed_actions_are_refused_before_touching_the_controller(self):
        from dolly.editor_dispatch import dispatch
        app = SimpleNamespace(status_text=Mock())
        with patch.object(edition, '_environment', True):
            for action in ('play_path', 'start_video', 'set_video_depth', 'open_bone_picker',
                           'start_game_follow', 'set_attach_target', 'prepare_cinematic',
                           'set_camera_curve_value', 'reshade', 'set_confetti_enabled'):
                with self.subTest(action=action):
                    self.assertTrue(dispatch(app, {'action': action}, None, publish=Mock()))
            for action in ('capture_environment', 'take_screenshot', 'capture', 'view_camera',
                           'flight', 'play_pause', 'seek_replay_tick', 'level_view'):
                self.assertTrue(edition.action_allowed(action), action)
        with patch.object(edition, '_environment', False):
            self.assertTrue(edition.action_allowed('start_video'))

    def test_panorama_id_follows_the_current_main_action(self):
        from dolly.editor_wire import EXTRA_ACTIONS
        self.assertEqual(EXTRA_ACTIONS.index('set_attach_vertical_aim_lock') + 26, 122)
        self.assertEqual(EXTRA_ACTIONS.index('capture_environment') + 26, 123)

    def test_environment_never_auto_enables_a_remembered_reshade_runtime(self):
        from dolly.gui import DollyApp
        with patch.object(edition, '_environment', True):
            self.assertFalse(DollyApp._configure_reshade(SimpleNamespace(), automatic=True))

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
