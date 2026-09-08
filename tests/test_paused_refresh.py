"""Regressions for the executable's stale paused spectator camera.

This console model preserves the diagnostic distinction between a same-tick
seek (a no-op) and rebuilding a different replay tick. Passing it establishes
controller behavior, not compatibility with the native game renderer.
"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from dolly.navigation import CameraMotion
from dolly.path import Keyframe, Project, STANDARD_ASPECT
import test_camera_position as camera_fixture
from test_camera_position import OffsetConsole


class StaleSpectatorConsole(OffsetConsole):
    """The paused view applies only 0.245 of each commanded displacement."""

    def __init__(self, *, permanent=False):
        super().__init__(offset=(0, 0, 94.3))
        self.tick = self.goto_output = 112249
        self.total_ticks = 161418
        self.pose = [-202.6, 1695.7, 412.7, -9.7, 245.9]
        self.anchor = list(self.pose)
        self.weak = True
        self.permanent = permanent
        self.seek_pairs = []
        self.on_seek = None

    def _execute(self, command):
        for item in command.split(";"):
            item = item.strip()
            previous_tick = self.tick
            super()._execute(item)
            if item.startswith("demo_gototick "):
                self.seek_pairs.append((previous_tick, self.tick))
                if self.tick != previous_tick:
                    self.weak = self.permanent
                    # Replay rebuilding may replace the spectator view. The
                    # controller must restore the view captured before seeking.
                    self.pose = [40, 60, 350, 0, 90]
                    self.anchor = list(self.pose)
                if self.on_seek is not None:
                    self.on_seek(previous_tick, self.tick)
            elif item.startswith("spec_goto ") and self.weak:
                for axis in range(3):
                    self.pose[axis] = self.anchor[axis] + .245 * (
                        self.pose[axis] - self.anchor[axis])
                self.view_writes[-1] = list(self.pose)

    def _request_item(self, command):
        if command == "demo_goto":
            return (f"Currently playing {self.tick} of {self.total_ticks} ticks. "
                    f"Minutes:42.04 File:{self.demo_name}")
        return super()._request_item(command)


class PausedRefreshTests(unittest.TestCase):
    def setUp(self):
        camera_fixture.CameraPositionTests.setUp(self)
        self.console = StaleSpectatorConsole()
        self.controller._console = self.console

    def prepare(self):
        with patch("dolly.controller.client_aspect_ratio", return_value=STANDARD_ASPECT):
            return self.controller.begin_paused_camera()

    def assert_pose(self, frame):
        for actual, axis in zip(self.console.pose, ("x", "y", "z", "pitch", "yaw")):
            wanted = frame[axis]
            error = (actual - wanted + 180) % 360 - 180 if axis == "yaw" else actual - wanted
            self.assertLessEqual(abs(error), .5, axis)

    def assert_restored_tick(self, tick):
        real_seeks = [(old, new) for old, new in self.console.seek_pairs if old != new]
        self.assertTrue(real_seeks)
        self.assertEqual(real_seeks[-1][1], tick)
        self.assertTrue(all(0 <= new <= self.console.total_ticks for _, new in real_seeks))
        self.assertEqual(self.console.tick, tick)
        self.assertTrue(self.console.paused)
        self.assertNotIn("demo_resume", self.console.operations)

    def test_current_view_survives_actual_tick_refresh_before_paused_movement(self):
        original = list(self.console.pose)
        frame = self.prepare()
        self.assertEqual([frame[a] for a in ("x", "y", "z", "pitch", "yaw")], original)
        self.assert_pose(frame)
        self.assert_restored_tick(112249)
        self.assertTrue(self.controller.status()["paused_camera"])
        self.assertTrue(self.controller._camera_calibration["direct_response_verified"])
        self.assertEqual(self.controller._camera_calibration["translation_response"]["gain"],
                         {"x": 1, "y": 1, "z": 1})

    def test_distant_saved_view_preserves_current_replay_moment_for_select_and_preview(self):
        project = Project(start_tick=1000, keyframes=[
            Keyframe(0, -2400, 9200, 800, -35, 317, 7, aspect_ratio=1.2)])
        original_project = deepcopy(project.to_dict())
        for operation in ("select_paused_camera", "apply"):
            with self.subTest(operation=operation):
                self.console.weak = True
                self.console.anchor = list(self.console.pose)
                self.console.seek_pairs.clear()
                getattr(self.controller, operation)(project, 0)
                self.assert_pose(project.evaluate(0))
                self.assert_restored_tick(112249)
                self.assertEqual(self.console.values["r_aspectratio"], 1.2)
                self.assertEqual(project.to_dict(), original_project)
                self.assertTrue(self.controller._camera_calibration["verified"])

    def test_nonrefresh_position_can_recover_once_without_resuming(self):
        frame = Project(keyframes=[Keyframe(0, -2400, 9200, 800, 0, 10, 0)]).evaluate(0)
        self.controller._position_direct_frame(frame, 112249, refresh=False)
        self.assert_pose(frame)
        self.assert_restored_tick(112249)
        self.assertEqual(len([p for p in self.console.seek_pairs if p[0] != p[1]]), 2)
        self.assertTrue(self.controller._camera_calibration["direct_response_verified"])

    def test_first_tick_uses_forward_neighbor_then_returns_to_zero(self):
        self.console.tick = self.console.goto_output = 0
        frame = self.prepare()
        self.assertEqual(self.console.seek_pairs, [(0, 1), (1, 0)])
        self.assert_pose(frame)
        self.assert_restored_tick(0)

    def test_last_tick_uses_previous_neighbor_without_crossing_replay_end(self):
        self.console.tick = self.console.goto_output = self.console.total_ticks
        frame = self.prepare()
        end = self.console.total_ticks
        self.assertEqual(self.console.seek_pairs, [(end, end - 1), (end - 1, end)])
        self.assert_pose(frame)
        self.assert_restored_tick(end)

    def test_no_neighbor_at_empty_replay_boundary_never_seeks_outside_demo(self):
        self.console.tick = self.console.goto_output = self.console.total_ticks = 0
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertEqual(self.console.seek_pairs, [])
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller.status()["paused_camera"])

    def test_permanently_weak_camera_remains_blocked_after_bounded_refresh(self):
        self.console.permanent = True
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertLessEqual(len([p for p in self.console.seek_pairs if p[0] != p[1]]), 2)
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.tick, 112249)

    def test_cancel_after_outbound_seek_stops_before_camera_writes(self):
        self.console.on_seek = lambda old, new: self.controller._stop_event.set()
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            self.prepare()
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.seek_pairs, [(112249, 112248)])

    def test_changed_demo_after_outbound_seek_is_not_rewritten(self):
        self.console.on_seek = lambda old, new: setattr(self.console, "demo_name", "another.dem")
        with self.assertRaisesRegex(RuntimeError, "different"):
            self.prepare()
        self.assertEqual(self.console.camera_writes, [])
        self.assertEqual(len(self.console.seek_pairs), 1)
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertNotIn("demo_resume", self.console.operations)

    def test_lens_failure_cannot_trigger_position_recovery_seek(self):
        original_request = self.console._request_item
        def bad_lens(command):
            if command == "r_aspectratio":
                return "r_aspectratio = 1.0"
            return original_request(command)
        frame = Project(keyframes=[Keyframe(0, -2400, 9200, 800, 0, 10, 0)]).evaluate(0)
        with patch.object(self.console, "_request_item", side_effect=bad_lens):
            with self.assertRaisesRegex(RuntimeError, "aspect"):
                self.controller._position_direct_frame(frame, 112249, refresh=False)
        self.assertEqual(self.console.seek_pairs, [])
        self.assertEqual(self.console.camera_writes, [])

    def test_capture_after_external_seek_succeeds_first_try_and_retires_old_movement(self):
        self.prepare()
        self.controller.stop_paused_flight()
        self.console.tick = self.console.goto_output = 112310
        self.console.pose = [-347.2, 960.5, 400.4, -10.9, 43.4]
        self.console.requests.clear()
        self.console.seek_pairs.clear()
        captured = self.controller.capture_at_replay(112249, 64)
        self.assertEqual(captured.time, 61 / 64)
        self.assertEqual((captured.x, captured.y, captured.z), (-347.2, 960.5, 400.4))
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertIsNone(self.controller._paused_tick)
        self.assertEqual(self.console.seek_pairs, [])
        self.assertEqual(self.console.camera_writes, [])
        with self.assertRaises(RuntimeError):
            self.controller.nudge_paused_camera(CameraMotion(up=1))
        self.assertEqual(self.console.camera_writes, [])

    def test_tick_change_while_capture_is_measuring_still_rejects_mixed_frame(self):
        self.prepare()
        original_request = self.console._request_item
        def advancing_capture(command):
            result = original_request(command)
            if command == "spec_pos":
                self.console.tick += 1
            return result
        with patch.object(self.console, "_request_item", side_effect=advancing_capture):
            with self.assertRaisesRegex(RuntimeError, "replay moved|Replay moved"):
                self.controller.capture_at_replay(112249, 64)
        self.assertFalse(self.controller.status()["paused_camera"])


if __name__ == "__main__":
    unittest.main()
