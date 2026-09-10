"""User intent at the new desktop/native-editor boundary, without a game."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.bindings import CaptureBinding
from dolly.editor_actions import EditorBinding
from dolly.gui import DollyApp, _binding_event_key
from dolly.path import Keyframe
from dolly.settings import AppSettings
from test_gui_capture import CaptureHarness, Var


class Stage1GuiTests(unittest.TestCase):
    def harness(self):
        h = CaptureHarness()
        app = h.app
        app.game_path = Var('B:/SteamLibrary/Deadlock/game/bin/win64/deadlock.exe')
        app.demo_path = Var('B:/SteamLibrary/Deadlock/game/citadel/replays/shot.dem')
        app.replay_folder = Var('B:/SteamLibrary/Deadlock/game/citadel/replays')
        app.launch_options = Var('-windowed -w 1920 -h 1080')
        app.protocol = Var('Netconsole')
        app.startup_bar = Mock()
        app.startup_progress = Var('')
        app.startup_cancel = None
        app.cancel_startup_button = Mock()
        app._disable_external_input = Mock()
        app._close_paused_camera = Mock()
        app.controller.start_editing = Mock(return_value={'startup_stage': 'editing_ready'})
        app._editing_started = Mock()
        app._refresh_bindings = Mock()
        app.binding_feedback = Var('')
        return h, app

    def test_one_click_snapshots_paths_and_options_and_enters_native_editor(self):
        h, app = self.harness()
        app._start_editing_session()
        app.demo_path.set('different.dem')
        with patch('dolly.gui.save_settings') as save:
            h.finish()
        args, kwargs = app.controller.start_editing.call_args
        self.assertTrue(args[1].endswith('/shot.dem'))
        self.assertEqual(kwargs['protocol'], 'netcon')
        self.assertIs(kwargs['native'], True)
        self.assertEqual(kwargs['launch_options'], '-windowed -w 1920 -h 1080')
        self.assertFalse(kwargs['cancel_event'].is_set())
        self.assertEqual(save.call_args.args[0].demo_path, args[1])
        app._disable_external_input.assert_called_once()
        app._editing_started.assert_called_once()
        self.assertEqual(h.errors, [])

    def test_invalid_additional_options_never_launch_or_disable_current_input(self):
        h, app = self.harness()
        app.launch_options.set('-secure +playdemo other.dem')
        app._start_editing_session()
        self.assertTrue(h.errors)
        self.assertIsNone(h.pending)
        app.controller.start_editing.assert_not_called()
        app._disable_external_input.assert_not_called()

    def test_cancel_button_sets_the_same_live_signal_passed_to_startup(self):
        h, app = self.harness()
        app._start_editing_session()
        app._cancel_startup()
        with patch('dolly.gui.save_settings'):
            h.finish()
        self.assertTrue(app.controller.start_editing.call_args.kwargs['cancel_event'].is_set())
        self.assertIn('Cancelling', app.startup_progress.get())

    def test_native_event_starts_empty_path_and_uses_press_pose_and_tick(self):
        h = CaptureHarness()
        app = h.app
        snapshot = {'pose': [10, 20, 30, 4, 5, 6, 1.5], 'tick': 512, 'paused': True}
        app.controller.capture_native_snapshot = Mock(return_value=(Keyframe(0, 10, 20, 30, 4, 5, 6, aspect_ratio=1.5), 512))
        app._capture_view('append', native_snapshot=snapshot)
        h.finish()
        app.controller.capture_native_snapshot.assert_called_once_with(snapshot, time=0.0)
        self.assertEqual(app.project.start_tick, 512)
        self.assertEqual(app.project.keyframes[0].z, 30)
        self.assertEqual(app.project.keyframes[0].aspect_ratio, 1.5)
        self.assertEqual(app.controller.calls, [])
        self.assertEqual(h.errors, [])

    def test_native_replay_timing_uses_snapshot_tick_not_later_live_tick(self):
        h = CaptureHarness()
        app = h.app
        app.project.keyframes = [Keyframe(0, 1, 2, 3, 0, 0, 0)]
        app.project.start_tick = 512
        app.start_tick.set('512')
        app.capture_mode.set('Replay timing')
        app.controller.tick = 2000
        app.controller.capture_native_snapshot = Mock(return_value=(Keyframe(0, 10, 20, 30, 0, 0, 0), 640))
        app._capture_view('append', native_snapshot={'tick': 640})
        h.finish()
        self.assertEqual(app.project.keyframes[-1].time, 2.0)
        self.assertEqual(h.errors, [])

    def test_saving_keybind_retires_old_listener_and_keeps_launcher_preferences(self):
        h, app = self.harness()
        app.capture_hotkey = helper = Mock()
        settings = app.app_settings.with_capture_binding(CaptureBinding('Mouse5', False, False))
        app._persist_preferences(settings, 'Saved')
        with patch('dolly.gui.save_settings') as save, patch('dolly.gui.editor_session.configure'):
            h.finish()
        helper.stop.assert_called_once()
        self.assertIsNone(app.capture_hotkey)
        self.assertFalse(app.hotkey_enabled.get())
        self.assertTrue(save.call_args.args[0].game_path.startswith('B:/'))
        self.assertEqual(app.app_settings.capture_binding.key, 'Mouse5')

    def test_clearing_capture_binding_stops_legacy_listener_despite_retained_alias(self):
        h, app = self.harness()
        app.capture_hotkey = helper = Mock()
        bindings = dict(app.app_settings.action_bindings)
        bindings['capture'] = None
        settings = app.app_settings.with_action_bindings(bindings)
        app._persist_preferences(settings, 'Unbound')
        with patch('dolly.gui.save_settings'), patch('dolly.gui.editor_session.configure'):
            h.finish()
        helper.stop.assert_called_once()
        app._toggle_capture_hotkey()
        self.assertFalse(app.hotkey_enabled.get())
        self.assertIn('Unbound', app.hotkey_label.get())

    def test_native_paused_controls_do_not_start_external_keyboard_polling(self):
        h, app = self.harness()
        app.native_editor_active = True
        app.controller.enter_native_flight = Mock()
        bridge = Mock()
        app.controller._native_bridge = Mock(return_value=bridge)
        with patch('dolly.gui.CameraInput') as external, patch('dolly.gui.editor_session.configure'):
            app._open_paused_camera()
            h.finish()
        external.assert_not_called()
        app.controller.enter_native_flight.assert_called_once()
        bridge.configure_editor.assert_called_once_with(owner='panel')

    def test_binding_recorder_preserves_f7_and_side_mouse_buttons(self):
        self.assertEqual(_binding_event_key(SimpleNamespace(keysym='F7', num='??')), 'F7')
        self.assertEqual(_binding_event_key(SimpleNamespace(num=4)), 'Mouse4')
        self.assertEqual(_binding_event_key(SimpleNamespace(num=5)), 'Mouse5')
        self.assertIsNone(_binding_event_key(SimpleNamespace(num=1)))
        self.assertEqual(_binding_event_key(SimpleNamespace(keysym='k', num='??')), 'K')


if __name__ == '__main__':
    unittest.main()
