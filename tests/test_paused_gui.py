"""Paused-camera UI intent, cancellation and capture handoff without a desktop."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly.navigation import CameraMotion
from dolly.path import Keyframe
from test_gui_capture import CaptureHarness, Var


class PausedHarness(CaptureHarness):
    def __init__(self):
        super().__init__()
        app = self.app
        app.project.keyframes = [Keyframe(0, 1, 2, 3, 0, 0, 0), Keyframe(3, 20, 30, 40, 10, 20, 0)]
        app.project.start_tick = 128
        app.start_tick.set("128")
        self.selected = 0
        app.controller.tick = 777
        app.controller.state = {"connected": True, "paused_camera": False, "paused_flight": False, "tick": 777}
        app.controller.status = lambda: dict(app.controller.state)
        app.controller.begin_paused_camera = Mock(side_effect=self.begin)
        app.controller.select_paused_camera = Mock(side_effect=self.select)
        app.controller.start_paused_flight = Mock(side_effect=self.start)
        app.controller.stop_paused_flight = Mock(side_effect=self.stop)
        app.controller.play = Mock()
        app.controller.seek = Mock()
        app.controller.disconnect = Mock()
        app.paused_dialog = Mock()
        app.paused_input = Mock()
        app.paused_input.sample.return_value = CameraMotion(forward=1)
        app.paused_cancel = None
        app.paused_requested = False
        app.paused_run_options = (240.0, 60.0)
        app.paused_move_speed = Var("240")
        app.paused_turn_speed = Var("60")
        app.paused_status = Var("")
        app.paused_view_text = Var("")
        app.selected_text = Var("Camera 01")
        app.paused_pad = Mock()
        app.paused_switch_buttons = [Mock(), Mock(), Mock()]
        app.paused_hold_buttons = [Mock() for _ in range(10)]
        app.paused_start_button = Mock()
        app.paused_stop_button = Mock()
        app.paused_capture_button = Mock()
        app.paused_focus_button = Mock()
        app.paused_move_entry = Mock()
        app.paused_turn_entry = Mock()
        app.camera_tree.selection_set = self.set_selection
        app.camera_tree.see = Mock()
        app._select_key = Mock()
        self.order = []

    def set_selection(self, index):
        self.selected = int(index)

    def begin(self, *, cancelled=None):
        self.order.append("begin")
        self.app.controller.state.update(paused_camera=True, paused_tick=777)
        return {}

    def select(self, project, time, *, cancelled=None):
        self.order.append(("select", time))
        self.app.controller.state.update(paused_camera=True, paused_tick=777, paused_flight=False)
        return {}

    def start(self, source, **options):
        self.order.append("flight")
        self.app.controller.state["paused_flight"] = True
        self.source = source
        self.options = options

    def stop(self):
        self.order.append("stop")
        self.app.controller.state["paused_flight"] = False

    def poll(self):
        self.app._poll_paused_camera(self.app.controller.status())

    def run(self):
        self.app._start_paused_controls()
        self.finish()
        self.poll()


class PausedGuiTests(unittest.TestCase):
    def setUp(self):
        self.h = PausedHarness()
        self.app = self.h.app

    def test_switch_uses_authored_pose_at_current_tick_without_retiming_project(self):
        before = copy.deepcopy(self.app.project)
        self.app._switch_paused_camera(1)
        self.h.finish()
        self.assertEqual(self.h.order, [("select", 3)])
        self.assertEqual(self.h.selected, 1)
        self.assertEqual(self.app.project, before)
        self.assertEqual(self.app.controller.status()["paused_tick"], 777)
        self.app.controller.seek.assert_not_called()
        self.app.controller.play.assert_not_called()
        self.assertEqual(self.h.errors, [])

    def test_next_and_previous_wrap_and_no_selection_can_choose_last(self):
        for selected, step, wanted in ((1, 1, 0), (0, -1, 1), (None, -1, 1), (None, 0, 0)):
            with self.subTest(selected=selected, step=step):
                self.h.selected = selected
                self.app._switch_paused_camera(step)
                self.h.finish()
                self.assertEqual(self.h.selected, wanted)

    def test_start_prepares_camera_once_then_runs_with_numeric_speed_snapshot(self):
        self.app.paused_move_speed.set("180")
        self.app.paused_turn_speed.set("45")
        self.app._start_paused_controls()
        self.app.paused_move_speed.set("999")
        self.h.finish()
        self.assertEqual(self.h.order, ["begin", "flight"])
        self.assertEqual(self.h.options, {"move_speed": 180.0, "turn_speed": 45.0})
        self.assertEqual(self.h.source(), CameraMotion(forward=1))
        self.app.paused_pad.focus_set.assert_called_once()

    def test_close_during_queued_start_cancels_it_before_camera_commands(self):
        helper, dialog = self.app.paused_input, self.app.paused_dialog
        self.app._start_paused_controls()
        self.app._close_paused_camera()
        self.h.finish()
        self.assertEqual(self.h.order, [])
        helper.close.assert_called_once()
        dialog.destroy.assert_called_once()
        self.assertIsNone(self.app.paused_input)
        self.assertFalse(self.app.paused_requested)

    def test_prepare_receives_live_cancellation_signal_when_panel_closes(self):
        def prepare(*, cancelled):
            self.assertFalse(cancelled())
            self.app._close_paused_camera()
            self.assertTrue(cancelled())
            raise RuntimeError("Paused camera preparation cancelled")
        self.app.controller.begin_paused_camera.side_effect = prepare
        self.app._start_paused_controls()
        self.h.finish()
        self.app.controller.start_paused_flight.assert_not_called()
        self.assertEqual(self.h.errors, [])

    def test_close_running_camera_cancels_sampler_before_stop_job_finishes(self):
        self.h.run()
        source = self.h.source
        self.app._close_paused_camera()
        self.assertTrue(source().stop)
        self.h.finish()
        self.assertEqual(self.h.order[-1], "stop")
        self.assertEqual(self.app.controller.status()["paused_tick"], 777)

    def test_stop_while_start_is_busy_prevents_late_flight_start(self):
        self.app._start_paused_controls()
        self.app._stop_paused_controls()
        self.h.finish()
        self.assertEqual(self.h.order, [])
        self.assertFalse(self.app.paused_requested)

    def test_switch_during_flight_restarts_only_after_positioning(self):
        self.h.run()
        old_source = self.h.source
        self.app._switch_paused_camera(1)
        self.assertTrue(old_source().stop)
        self.h.finish()
        self.assertEqual(self.h.order, ["begin", "flight", ("select", 3), "flight"])
        self.assertFalse(self.h.source().stop)
        self.app.controller.begin_paused_camera.assert_called_once()

    def test_capture_commits_view_then_resumes_prepared_camera_without_calibration(self):
        self.h.run()
        self.app.capture_here()
        self.app.controller.state["paused_flight"] = False  # Capture's backend halt.
        self.h.finish()
        self.assertEqual(len(self.app.project.keyframes), 3)
        self.assertIsNotNone(self.h.pending)  # Resume is a separate serialized operation.
        self.assertEqual(self.h.order, ["begin", "flight"])
        self.h.finish()
        self.assertEqual(self.h.order, ["begin", "flight", "flight"])
        self.app.controller.begin_paused_camera.assert_called_once()

    def test_failed_capture_leaves_flight_stopped_and_project_unchanged(self):
        self.h.run()
        before = copy.deepcopy(self.app.project)
        self.app.controller.failure = RuntimeError("Replay tick changed")
        self.app.capture_here()
        self.app.controller.state["paused_flight"] = False
        self.h.finish()
        self.h.poll()
        self.assertEqual(self.app.project, before)
        self.assertFalse(self.app.paused_requested)
        self.assertIsNone(self.h.pending)
        self.assertEqual(self.h.order, ["begin", "flight"])

    def test_close_during_capture_prevents_automatic_resume(self):
        self.h.run()
        self.app.capture_here()
        self.app._close_paused_camera()
        self.h.finish()
        self.assertIsNone(self.h.pending)
        self.assertEqual(self.h.order, ["begin", "flight"])

    def test_main_play_closes_panel_and_cancels_manual_source(self):
        self.h.run()
        source = self.h.source
        self.app._play()
        self.assertTrue(source().stop)
        self.assertIsNone(self.app.paused_dialog)
        self.h.finish()
        self.app.controller.play.assert_called_once()
        self.assertEqual(self.app.controller.play.call_args.kwargs["time"], 0)

    def test_escape_stops_and_blur_releases_all_gui_input(self):
        self.h.run()
        self.app._paused_key(SimpleNamespace(keysym="Escape"), True)
        self.assertTrue(self.h.source().stop)
        self.app.paused_input.set_gui_focus.assert_called_with(False)
        self.app.paused_input.clear.assert_called()
        self.h.finish()
        self.assertEqual(self.h.order[-1], "stop")

    def test_keyboard_pad_preserves_physical_modifier_identity(self):
        self.h.run()
        for key in ("Control_L", "Control_R", "Shift_L", "Shift_R"):
            self.app._paused_key(SimpleNamespace(keysym=key), True)
            self.app.paused_input.press.assert_called_with(key.lower())
            self.app._paused_key(SimpleNamespace(keysym=key), False)
            self.app.paused_input.release.assert_called_with(key.lower())

    def test_key_release_is_delivered_while_a_console_operation_is_busy(self):
        self.h.run()
        self.app.busy = True
        self.app._paused_key(SimpleNamespace(keysym="w"), False)
        self.app.paused_input.release.assert_called_with("w")
        self.app._paused_key(SimpleNamespace(keysym="Escape"), True)
        self.assertTrue(self.h.source().stop)

    def test_invalid_speed_does_not_prepare_or_start_camera(self):
        for value in ("nan", "0", "10001", "bad"):
            with self.subTest(value=value):
                self.app.paused_move_speed.set(value)
                self.app._start_paused_controls()
                self.assertIsNone(self.h.pending)
                self.assertEqual(self.h.order, [])

    def test_external_flight_stop_clears_intent_and_disables_movement(self):
        self.h.run()
        self.app.controller.state.update(paused_flight=False, paused_camera=False, paused_tick=None)
        self.h.poll()
        self.assertFalse(self.app.paused_requested)
        for button in self.app.paused_hold_buttons:
            button.configure.assert_called_with(state="disabled")


if __name__ == "__main__":
    unittest.main()
