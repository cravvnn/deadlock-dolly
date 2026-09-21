"""POV ownership and failure cleanup; these are offline, not game observations."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from dolly.controller import Controller
from dolly.path import Keyframe, Project


class PovControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = c = Controller()
        self.bridge = Mock()
        self.bridge.status.return_value = {"paused": True, "frame_count": 20}
        c._native_bridge = Mock(return_value=self.bridge)
        c._require_probe = Mock()
        c._require_demo = Mock(return_value={"tick": 100})
        c._recorder_active = Mock(return_value=False)
        c._game_ui_visible = True
        c._demo = Path("example.dem")
        c._request = Mock()
        c.stop = Mock()
        c._wait_paused_native_view = Mock()
        c._remember_game_ui_settings = Mock()
        c._hide_game_ui = Mock()
        c.enter_native_flight = Mock()
        c._recover_replay_for_shot = Mock()
        c._position_direct_frame = Mock()
        self.project = Project(start_tick=100, keyframes=[
            Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(2, 0, 0, 0, 0, 0, 0)])

    def test_preparation_preserves_selected_view_without_reload_or_camera_write(self):
        c = self.controller
        c.prepare_pov_recording(self.project)
        c._request.assert_called_once_with("demo_pause")
        c.enter_native_flight.assert_not_called()
        c._recover_replay_for_shot.assert_not_called()
        c._position_direct_frame.assert_not_called()
        c._hide_game_ui.assert_called_once()
        self.assertEqual(c._recording_replay[2], 100)

    def test_requires_explicit_game_camera_and_paused_start(self):
        c = self.controller
        c._game_ui_visible = False
        with self.assertRaisesRegex(RuntimeError, "F9"):
            c.prepare_pov_recording(self.project)
        c.stop.assert_not_called()
        c._game_ui_visible = True
        self.bridge.status.return_value["paused"] = False
        with self.assertRaisesRegex(RuntimeError, "Pause"):
            c.prepare_pov_recording(self.project)
        c.stop.assert_not_called()

    def test_failed_hud_hide_restores_and_does_not_reserve_recording(self):
        c = self.controller
        c._hide_game_ui.side_effect = RuntimeError("HUD readback failed")
        c.finish_pov_recording = Mock()
        with self.assertRaisesRegex(RuntimeError, "HUD readback"):
            c.prepare_pov_recording(self.project)
        c.finish_pov_recording.assert_called_once()
        self.assertIsNone(c._recording_replay)

    def test_opening_panel_does_not_reclaim_camera(self):
        c = self.controller
        c._console_open = False
        c.open_pov_panel()
        self.bridge.configure_editor.assert_called_once_with(owner="panel")
        c.enter_native_flight.assert_not_called()

    def test_play_arms_clock_before_resume_and_never_calibrates_camera(self):
        c = self.controller
        c._pov_active = True
        c._prepare_native_shot_replay = Mock()
        c._wait_for_recorder_ready = Mock()
        ordered = Mock()
        ordered.attach_mock(self.bridge.prepare, "prepare")
        ordered.attach_mock(self.bridge.play, "play")
        ordered.attach_mock(c._request, "request")
        with patch("dolly.controller.threading.Thread"):
            c.play_pov(self.project, .5)
        self.bridge.prepare.assert_called_once_with(self.project, 0, .5, False, "example.dem", pov=True)
        self.assertEqual([call[0] for call in ordered.mock_calls], ["prepare", "play", "request"])
        c._position_direct_frame.assert_not_called()

    def test_completion_keeps_hud_hidden_until_recorder_is_drained(self):
        c = self.controller
        c._pov_active = True
        c._restore_game_ui_settings = Mock()
        c._handoff_native_camera = Mock()
        self.assertIsNone(c._finish_native_playback())
        c._request.assert_called_with("demo_pause")
        c._restore_game_ui_settings.assert_not_called()
        c._handoff_native_camera.assert_not_called()

    def test_finish_releases_before_restoring_ui(self):
        c = self.controller
        c._pov_active = True
        c._alive = Mock(return_value=True)
        c._halt = Mock()
        c._finish_playback = Mock()
        c._restore_game_ui_settings = Mock(return_value=None)
        ordered = Mock()
        for name in ("_halt", "_finish_playback", "_restore_game_ui_settings"):
            ordered.attach_mock(getattr(c, name), name)
        c.finish_pov_recording()
        self.assertEqual([call[0] for call in ordered.mock_calls],
                         ["_halt", "_finish_playback", "_restore_game_ui_settings"])
        self.assertFalse(c._pov_active)
        self.assertTrue(c._game_ui_visible)


if __name__ == "__main__":
    unittest.main()
