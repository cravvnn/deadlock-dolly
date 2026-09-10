"""Integration guards for Tk polling versus asynchronous native transitions."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly import editor_session as session
from dolly.path import Project, Keyframe
from dolly.settings import AppSettings


class EditorSessionTests(unittest.TestCase):
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

    def test_seek_uses_project_tick_rate(self):
        self.app.project.tick_rate = 128
        session.dispatch(self.app, {"action": "seek_back", "value": 0}, self.bridge)
        self.app._submit.call_args.args[1]()
        self.controller.seek_relative.assert_called_once_with(-1, tick_rate=128)

    def test_bridge_failure_during_recovery_does_not_hide_original_error(self):
        self.controller.toggle_console.side_effect = RuntimeError("console command failed")
        self.bridge.editor_status.side_effect = RuntimeError("bridge disconnected")
        session.dispatch(self.app, {"action": "console", "value": 1}, self.bridge)
        with self.assertLogs(session.LOG, level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "console command failed"):
                self.app._submit.call_args.args[1]()


if __name__ == "__main__":
    unittest.main()
