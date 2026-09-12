"""Integration guards for Tk polling versus asynchronous native transitions."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly import editor_session as session
from dolly.path import Project, Keyframe
from dolly.settings import AppSettings


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class EditorSessionTests(unittest.TestCase):
    def test_wheel_framing_updates_only_the_selected_camera_lens(self):
        from copy import deepcopy
        keys = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(2, 7, 8, 9, 10, 11, 12)]
        self.app.project.keyframes = keys
        self.app._commit_camera = Mock()
        event = {"action": "set_framing", "value": 1, "pose": [90, 91, 92, 0, 0, 0, 1.25]}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        changed, selected_time = self.app._commit_camera.call_args.args
        expected = deepcopy(keys)
        expected[1].aspect_ratio = 1.25
        self.assertEqual(changed, expected)
        self.assertEqual(selected_time, 2)
        self.assertNotEqual(keys[1].aspect_ratio, 1.25)
        self.app._submit.assert_not_called()

    def test_wheel_before_capture_keeps_live_framing_without_creating_a_key(self):
        self.assertTrue(session.dispatch(self.app, {"action": "set_framing", "value": -1,
                        "pose": [0, 0, 0, 0, 0, 0, 1.2]}, self.bridge))
        self.assertFalse(self.app.project.keyframes)
        self.assertIn("Capture", self.app.status_text.set.call_args.args[0])

    def test_invalid_wheel_edit_cannot_mutate_project(self):
        for index, aspect in ((0, 1), (-2, 1), (.5, 1), (-1, 0), (-1, 4.1), (-1, float("nan"))):
            with self.subTest(index=index, aspect=aspect), self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "set_framing", "value": index,
                                 "pose": [0, 0, 0, 0, 0, 0, aspect]}, self.bridge)

    def test_dof_preview_finishes_before_the_project_is_committed(self):
        self.app.project.keyframes = [Keyframe(0, 0, 0, 0, 0, 0, 0)]
        self.app._mark_dirty = Mock()
        self.app._refresh_tracks = Mock()
        self.app._refresh_fixed = Mock()
        session.dispatch(self.app, {"action": "set_dof_0", "value": 1}, self.bridge)
        self.assertNotIn("r_dof_override", self.app.project.setup_values)
        work = self.app._submit.call_args.args
        result = work[1]()
        self.controller.preview_native_effects.assert_called_once()
        work[2](result)
        self.assertEqual(self.app.project.setup_values["r_dof_override"], 1)
        self.assertEqual(self.app.project.tracks[0].name, "r_dof_override_ranges")
        self.assertEqual(len(self.app.project.tracks[0].keys), 2)
        self.app._refresh_tracks.assert_called_once_with(0)
        self.app._mark_dirty.assert_called_once()

    def test_clear_ragdolls_dispatches_on_worker(self):
        self.assertTrue(session.dispatch(self.app, {"action": "destroy_ragdolls", "value": 0}, self.bridge))
        self.controller.destroy_ragdolls.assert_not_called()
        self.app._submit.call_args.args[1]()
        self.controller.destroy_ragdolls.assert_called_once_with()

    def setUp(self):
        self.bridge = Mock()
        self.bridge.editor_status.return_value = {"events": []}
        self.controller = Mock()
        self.controller._native_bridge.return_value = self.bridge
        self.controller.status.return_value = {"connected": True, "native_editor_active": False}
        self.app = SimpleNamespace(controller=self.controller, closed=False, busy=False,
            native_editor_active=False, app_settings=AppSettings(), project=Project(),
            camera_tree=object(), _selection_index=lambda _: None, shot_time=0,
            status_text=Mock(), _disable_external_input=Mock(), _capture_view=Mock(),
            _error=Mock(), _submit=Mock(return_value=True))
        self.app.status_text.get.return_value = "Ready"
        self.app.speed = Value("1")
        self.app.rate = Value("60")

    def test_poll_during_startup_does_not_disable_the_arming_native_camera(self):
        self.app.busy = True
        session.poll(self.app)
        self.bridge.configure_editor.assert_not_called()
        self.bridge.editor_status.assert_not_called()
        self.controller.status.return_value["native_editor_active"] = True
        session.poll(self.app)
        self.assertTrue(self.app.native_editor_active)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["enabled"])
        self.app._disable_external_input.assert_called_once()

    def test_path_playback_retains_editor_then_disconnect_disables_it(self):
        self.app.native_editor_active = True
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["enabled"])
        self.assertNotIn("owner", self.bridge.configure_editor.call_args.kwargs)
        self.controller.status.return_value["connected"] = False
        session.configure(self.app)
        self.assertFalse(self.app.native_editor_active)
        self.assertFalse(self.bridge.configure_editor.call_args.kwargs["enabled"])

    def test_failed_startup_keeps_live_panel_console_and_retry_actions_working(self):
        event = {"sequence": 1, "action": "console", "value": 1}
        self.bridge.editor_status.return_value = {"enabled": True, "events": [event]}
        session.poll(self.app)
        self.assertTrue(self.app.native_editor_active)
        self.app._disable_external_input.assert_called_once()
        self.app._submit.call_args.args[1]()
        self.controller.toggle_console.assert_called_once_with(enabled=True)
        self.bridge.acknowledge_editor_event.assert_called_once_with(1)

    def test_disconnected_bridge_cannot_reactivate_native_input(self):
        self.controller.status.return_value["connected"] = False
        self.bridge.editor_status.return_value = {"enabled": True, "events": []}
        session.poll(self.app)
        self.assertFalse(self.app.native_editor_active)
        self.bridge.editor_status.assert_not_called()

    def test_busy_retains_event_then_acknowledges_the_exact_capture_once(self):
        event = {"sequence": 1, "action": "capture", "value": 0,
                 "pose": [1, 2, 3, 4, 5, 6, 16/9], "tick": 64, "paused": True}
        self.app.native_editor_active = True
        self.bridge.editor_status.return_value = {"events": [event]}
        self.app.busy = True
        session.poll(self.app)
        self.bridge.acknowledge_editor_event.assert_not_called()
        self.app.busy = False
        session.poll(self.app)
        self.app._capture_view.assert_called_once_with("start", native_snapshot=event)
        self.bridge.acknowledge_editor_event.assert_called_once_with(1)
        self.bridge.editor_status.return_value = {"events": []}
        session.poll(self.app)
        self.app._capture_view.assert_called_once()

    def test_console_and_game_ui_use_desired_state_not_delayed_toggle(self):
        for action, method in (("console", self.controller.toggle_console),
                               ("game_ui", self.controller.toggle_game_ui)):
            for desired in (0, 1):
                session.dispatch(self.app, {"action": action, "value": desired}, self.bridge)
                self.app._submit.call_args.args[1]()
                method.assert_called_with(enabled=bool(desired))

    def test_panel_from_game_ui_returns_camera_before_opening_panel(self):
        events = []
        self.controller.toggle_game_ui.side_effect = lambda **_: events.append("camera")
        self.bridge.configure_editor.side_effect = lambda **_: events.append("panel")
        session.dispatch(self.app, {"action": "panel", "value": 1}, self.bridge)
        self.app._submit.call_args.args[1]()
        self.assertEqual(events, ["camera", "panel"])
        self.controller.toggle_game_ui.assert_called_once_with(enabled=False)

    def test_video_buttons_use_same_handlers_as_desktop_without_camera_stop(self):
        self.app._start_video_recording = Mock()
        self.app._stop_video_recording = Mock()
        session.dispatch(self.app, {"action": "start_video", "value": 0}, self.bridge)
        session.dispatch(self.app, {"action": "stop_video", "value": 0}, self.bridge)
        self.app._start_video_recording.assert_called_once_with()
        self.app._stop_video_recording.assert_called_once_with(cancel=False)
        self.controller.stop.assert_not_called()
        self.app._submit.assert_not_called()

    def test_reshade_binding_published_without_changing_input_owner(self):
        from dolly.editor_actions import EditorBinding
        self.app.native_editor_active = True
        self.app.app_settings = self.app.app_settings.with_reshade_binding(EditorBinding("Mouse5"))
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual(config["reshade_binding"], EditorBinding("Mouse5"))
        self.assertNotIn("owner", config)

    def test_seek_uses_project_tick_rate(self):
        self.app.project.tick_rate = 128
        session.dispatch(self.app, {"action": "seek_back", "value": 0}, self.bridge)
        self.app._submit.call_args.args[1]()
        self.controller.seek_relative.assert_called_once_with(-1, tick_rate=128)

    def test_playback_options_sync_both_directions_without_game_commands(self):
        self.app.native_editor_active = True
        self.app.speed.set("0.1")
        self.app.rate.set("120")
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["playback_speed"], config["playback_rate"]), (.1, 120))
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_speed", "value": .25}, self.bridge))
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_rate", "value": 30}, self.bridge))
        self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("0.25", "30"))
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["playback_speed"], config["playback_rate"]), (.25, 30))
        self.app._submit.assert_not_called()

    def test_video_export_options_sync_both_directions(self):
        self.app.native_editor_active = True
        self.app.video_fps = Value("120")
        self.app.video_bitrate = Value("40 Mbps")
        self.app.video_codec = Value("NVIDIA HEVC (NVENC)")
        self.app.video_fixed_step = Value(True)
        self.app.video_export_speed = Value("0.1")
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["video_fps"], config["video_bitrate_mbps"], config["video_encoder"],
                          config["video_fixed_step"], config["video_speed"]), (120, 40, 2, True, .1))
        for action, value in (("set_video_fps", 300), ("set_video_bitrate", 40),
                              ("set_video_encoder", 1), ("set_video_fixed_step", 0),
                              ("set_video_speed", .25)):
            self.assertTrue(session.dispatch(self.app, {"action": action, "value": value}, self.bridge))
        self.assertEqual((self.app.video_fps.get(), self.app.video_bitrate.get(),
                          self.app.video_codec.get(), self.app.video_fixed_step.get(),
                          self.app.video_export_speed.get()),
                         ("300", "40 Mbps", "NVIDIA H.264 (NVENC)", False, "0.25"))

    def test_invalid_in_game_video_options_do_not_change_desktop_state(self):
        self.app.video_fps = Value("60")
        self.app.video_bitrate = Value("20 Mbps")
        self.app.video_codec = Value("Auto (hardware when available)")
        self.app.video_fixed_step = Value(False)
        self.app.video_export_speed = Value("1")
        for action, invalid in (("set_video_fps", (0, 24, 200, 60.5, True)),
                                ("set_video_bitrate", (0, 15, 50)),
                                ("set_video_encoder", (-1, 11)),
                                ("set_video_speed", (0, .01, 4.1, float("nan"), True))):
            for value in invalid:
                with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                    session.dispatch(self.app, {"action": action, "value": value}, self.bridge)
        self.bridge.configure_editor.assert_not_called()

    def test_unfinished_speed_edit_does_not_block_editor_configuration(self):
        self.app.native_editor_active = True
        self.app.speed.set(".5")
        session.configure(self.app)
        for text in ("", "-", "nan", "99"):
            self.app.speed.set(text)
            self.app.status_text.get.return_value = "Editing " + text
            session.configure(self.app)
            config = self.bridge.configure_editor.call_args.kwargs
            self.assertEqual(config["playback_speed"], .5)
            self.assertEqual(config["message"], "Editing " + text)
            self.assertEqual(self.app.speed.get(), text)

    def test_invalid_in_game_playback_options_do_not_change_desktop_state(self):
        for action, invalid in (("set_playback_speed", (0, .01, 4.1, float("nan"), True)),
                                ("set_playback_rate", (0, 60.1, 90, float("inf"), True))):
            for value in invalid:
                with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                    session.dispatch(self.app, {"action": action, "value": value}, self.bridge)
                self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("1", "60"))
        self.bridge.configure_editor.assert_not_called()

    def test_busy_or_playing_cannot_change_running_shot_settings(self):
        for action, value in (("set_playback_speed", .1), ("set_playback_rate", 120)):
            self.app.busy = True
            self.assertFalse(session.dispatch(self.app, {"action": action, "value": value}, self.bridge))
            self.app.busy = False
            self.controller.status.return_value["playing"] = True
            # Consume a stale event without applying it after a shot started.
            self.assertTrue(session.dispatch(self.app, {"action": action, "value": value}, self.bridge))
            self.controller.status.return_value["playing"] = False
            self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("1", "60"))

    def test_in_game_options_are_acknowledged_once_after_application(self):
        self.app.native_editor_active = True
        self.bridge.editor_status.return_value = {"events": [
            {"sequence": 1, "action": "set_playback_speed", "value": .1},
            {"sequence": 2, "action": "set_playback_rate", "value": 120}]}
        session.poll(self.app)
        self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("0.1", "120"))
        self.assertEqual([call.args[0] for call in self.bridge.acknowledge_editor_event.call_args_list], [1, 2])

    def test_path_guides_publish_only_when_camera_shape_or_selection_changes(self):
        self.app.native_editor_active = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(1, 7, 8, 9, 10, 11, 12)]
        session.configure(self.app)
        self.bridge.publish_visualization.assert_called_once_with(self.app.project, enabled=True, selected_camera=0)
        self.app.shot_time = .5
        self.app.speed.set(".1")
        session.configure(self.app)
        self.bridge.publish_visualization.assert_called_once()
        # Mutable camera objects must not mutate the cached comparison too.
        self.app.project.keyframes[0].z += 10
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 2)
        self.app._selection_index = lambda _: 1
        session.configure(self.app)
        self.bridge.publish_visualization.assert_called_with(self.app.project, enabled=True, selected_camera=1)
        self.app.project.rotation_mode = "unwrapped"
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 4)

    def test_optional_viewer_failure_does_not_interrupt_editor_or_retry_every_poll(self):
        self.app.native_editor_active = True
        self.bridge.publish_visualization.return_value = False
        self.bridge.visualization_diagnostics.return_value = {"error": "viewer unavailable"}
        with self.assertLogs(session.LOG, level="WARNING") as logs:
            session.configure(self.app)
            self.app.status_text.get.return_value = "Camera ready"
            session.configure(self.app)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("viewer unavailable", logs.output[0])
        self.assertEqual(self.bridge.configure_editor.call_args.kwargs["message"], "Camera ready")
        self.bridge.publish_visualization.assert_called_once()
        self.app._error.assert_not_called()

    def test_clear_and_new_bridge_refresh_guides_without_changing_input_owner(self):
        self.app.native_editor_active = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        session.configure(self.app)
        self.app.project.keyframes.clear()
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 2)
        replacement = Mock()
        self.controller._native_bridge.return_value = replacement
        session.configure(self.app)
        replacement.publish_visualization.assert_called_once_with(self.app.project, enabled=True, selected_camera=0)
        self.controller.status.return_value["connected"] = False
        session.configure(self.app)
        replacement.publish_visualization.assert_called_with(self.app.project, enabled=False, selected_camera=0)
        self.assertNotIn("owner", replacement.configure_editor.call_args.kwargs)

    def test_bridge_failure_during_recovery_does_not_hide_original_error(self):
        self.controller.toggle_console.side_effect = RuntimeError("console command failed")
        self.bridge.editor_status.side_effect = RuntimeError("bridge disconnected")
        session.dispatch(self.app, {"action": "console", "value": 1}, self.bridge)
        with self.assertLogs(session.LOG, level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "console command failed"):
                self.app._submit.call_args.args[1]()


if __name__ == "__main__":
    unittest.main()
