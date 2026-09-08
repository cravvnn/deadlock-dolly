"""Exact paused-seek recovery for the forward overshoot in the 0.3.4 logs.

The simulated engine freezes two ticks past a forward seek, while seeking the
same target backward succeeds. These tests exercise real controller parsing,
seek verification, camera restoration and translation, not a native renderer.
"""

from collections import deque
from copy import deepcopy
import unittest
from unittest.mock import patch

from dolly.navigation import CameraMotion
from dolly.path import Keyframe, Project, STANDARD_ASPECT
import test_camera_position as camera_fixture
from test_camera_position import OffsetConsole
from test_controller import CountedEvent, FakeClock


class LatchingCountedEvent(CountedEvent):
    """Keep explicit cancellation set, like threading.Event.wait does."""

    def wait(self, timeout):
        if self.is_set():
            return True
        return super().wait(timeout)


class ForwardOvershootConsole(OffsetConsole):
    """Replay seeks can reset the view and freeze beyond a forward target."""

    def __init__(self, *, overshoot=2, permanent=False, weak_permanent=False):
        super().__init__(offset=(0, 0, 94.3))
        self.tick = self.goto_output = 112249
        self.pose = [-202.6, 1695.7, 412.7, -9.7, 245.9]
        self.anchor = list(self.pose)
        self.weak = True
        self.weak_permanent = weak_permanent
        self.overshoot = overshoot
        self.permanent = permanent
        self.seek_attempts = []
        self.status_samples = []
        self.on_status = None
        self.on_seek = None

    def _execute(self, command):
        for item in command.split(";"):
            item = item.strip()
            before = self.tick
            super()._execute(item)
            if item.startswith("demo_gototick "):
                target = int(item.split()[1])
                if self.permanent or target > before:
                    self.tick = target + self.overshoot
                self.seek_attempts.append({"before": before, "target": target,
                                           "landed": self.tick,
                                           "samples_before": len(self.status_samples)})
                self.weak = self.weak_permanent
                # A failed forward return leaves this reset view visible in
                # 0.3.4. Successful preparation must replace it with the view
                # captured before either seek, not recapture this position.
                self.pose = [40, 60, 350, 0, 90]
                self.anchor = list(self.pose)
                if self.on_seek is not None:
                    self.on_seek(self.seek_attempts[-1])
            elif item.startswith("spec_goto ") and self.weak:
                self.pose[:3] = [self.anchor[a] + .245 * (
                    self.pose[a] - self.anchor[a]) for a in range(3)]
                self.view_writes[-1] = list(self.pose)

    def _request_item(self, command):
        output = super()._request_item(command)
        if command == "demo_goto":
            self.status_samples.append(self.tick)
            if self.on_status is not None:
                self.on_status(self.tick)
        return output


class SeekOvershootTests(unittest.TestCase):
    def setUp(self):
        camera_fixture.CameraPositionTests.setUp(self)
        self.console = ForwardOvershootConsole()
        self.controller._console = self.console
        self.clock = FakeClock()
        self.controller._stop_event = LatchingCountedEvent(self.clock, 2000)
        for setting in (
            patch("dolly.controller.time.perf_counter", self.clock.monotonic),
            patch("dolly.controller.SEEK_SETTLE_INTERVAL", .04),
            patch("dolly.controller.client_aspect_ratio", return_value=STANDARD_ASPECT),
        ):
            setting.start()
            self.addCleanup(setting.stop)

    def prepare(self):
        return self.controller.begin_paused_camera()

    def assert_pose(self, frame):
        for actual, axis in zip(self.console.pose, ("x", "y", "z", "pitch", "yaw")):
            delta = actual - frame[axis]
            if axis == "yaw":
                delta = (delta + 180) % 360 - 180
            self.assertLessEqual(abs(delta), .05, axis)

    def assert_failed_without_camera_writes(self):
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertFalse(self.controller._last_seek_details["verified"])
        self.assertEqual(self.console.camera_writes, [])
        self.assertNotIn("demo_resume", self.console.operations)

    def test_forward_return_overshoot_restores_original_view_and_enables_translation(self):
        tick, original = self.console.tick, list(self.console.pose)
        self.console.values["demo_timescale"] = .1
        frame = self.prepare()

        self.assertEqual([frame[a] for a in ("x", "y", "z", "pitch", "yaw")], original)
        self.assertEqual([(s["target"], s["landed"]) for s in self.console.seek_attempts],
                         [(tick - 1, tick - 1), (tick, tick + 2), (tick, tick)])
        self.assertEqual(self.console.tick, tick)
        self.assertTrue(self.console.paused)
        self.assertTrue(self.controller.status()["paused_camera"])
        self.assertTrue(self.controller._camera_calibration["direct_response_verified"])
        self.assertEqual(self.controller._camera_calibration["translation_response"]["gain"],
                         {"x": 1, "y": 1, "z": 1})
        self.assert_pose(frame)

        # Exercise the actual manual writes in both directions, rather than
        # treating working arrow-key rotation as proof that XYZ is usable.
        for motion in (CameraMotion(up=1), CameraMotion(up=-1),
                       CameraMotion(right=1), CameraMotion(right=-1),
                       CameraMotion(forward=1), CameraMotion(forward=-1)):
            with self.subTest(motion=motion):
                previous = list(self.console.pose[:3])
                moved = self.controller.nudge_paused_camera(motion, seconds=.1)
                self.assertNotEqual(previous, self.console.pose[:3])
                self.assert_pose(moved)
                self.assertEqual(self.console.tick, tick)
        self.assert_pose(frame)
        self.assertEqual(self.console.values["demo_timescale"], .1)
        self.assertNotIn("demo_resume", self.console.operations)

    def test_saved_camera_switch_restores_exact_current_tick_and_authored_lens(self):
        project = Project(start_tick=1000, keyframes=[
            Keyframe(0, -2400, 9200, 800, -35, 317, 7, aspect_ratio=1.2)])
        original = deepcopy(project.to_dict())
        frame = self.controller.select_paused_camera(project, 0)
        self.assert_pose(frame)
        self.assertEqual(self.console.tick, 112249)
        self.assertEqual([s["target"] for s in self.console.seek_attempts],
                         [112248, 112249, 112249])
        self.assertEqual(self.console.values["r_aspectratio"], 1.2)
        self.assertEqual(project.to_dict(), original)
        self.assertTrue(self.controller.status()["paused_camera"])

    def test_general_seek_corrects_one_or_two_tick_overshoot_without_fudging_target(self):
        for overshoot in (1, 2):
            with self.subTest(overshoot=overshoot):
                self.console.overshoot = overshoot
                self.console.tick = 100
                self.console.seek_attempts.clear()
                self.console.status_samples.clear()
                info = self.controller._seek_tick(123)
                self.assertEqual(info["tick"], 123)
                self.assertEqual([s["target"] for s in self.console.seek_attempts], [123, 123])
                correction = self.console.seek_attempts[1]
                self.assertEqual(correction["before"], 123 + overshoot)
                self.assertGreaterEqual(correction["samples_before"], 3)
                samples_after = self.console.status_samples[correction["samples_before"]:]
                self.assertGreaterEqual(len(samples_after), 6)
                self.assertEqual(samples_after, [123] * len(samples_after))
                operations = self.console.operations
                retry_index = max(i for i, command in enumerate(operations)
                                  if command == "demo_gototick 123 0 1")
                earlier_state_writes = [command for command in operations[:retry_index]
                                        if command.startswith(("demo_pause", "demo_gototick"))]
                self.assertEqual(earlier_state_writes[-1], "demo_pause")
                after_retry = operations[retry_index + 1:]
                pause_index = after_retry.index("demo_pause")
                self.assertGreaterEqual(after_retry[:pause_index].count("demo_goto"), 3)
                self.assertGreaterEqual(after_retry[pause_index + 1:].count("demo_goto"), 3)
                self.assertTrue(self.controller._last_seek_details["verified"])
                self.assertEqual(self.controller._last_seek_details["target_tick"], 123)
                evidence = self.controller._last_seek_details["corrections"]
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0]["from_tick"], 123 + overshoot)
                self.assertEqual(evidence[0]["target_tick"], 123)
                self.assertEqual([s["tick"] for s in evidence[0]["samples_before"]][-3:],
                                 [123 + overshoot] * 3)
                self.assertNotIn("demo_resume", self.console.operations)

    def test_transient_overshoot_does_not_interrupt_normal_settling(self):
        self.console.goto_outputs = deque([124, 123, 123, 123, 123, 123, 123])
        info = self.controller._seek_tick(123)
        self.assertEqual(info["tick"], 123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assertGreaterEqual(self.console.status_samples.count(123), 6)

    def test_stable_overshoot_that_settles_after_pause_needs_no_corrective_seek(self):
        self.console.tick = 100
        self.console.goto_outputs = deque([125, 125, 125, 123] + [123] * 6)
        info = self.controller._seek_tick(123)
        self.assertEqual(info["tick"], 123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assertEqual(self.controller._last_seek_details["corrections"], [])
        self.assertTrue(self.controller._last_seek_details["verified"])
        self.assertGreaterEqual(self.console.operations.count("demo_pause"), 3)
        self.assertGreaterEqual(self.console.status_samples.count(123), 6)

    def test_changed_tick_on_fresh_pause_confirmation_is_not_chased(self):
        for overshoot, confirmed in ((2, 124), (1, 125), (2, 700), (2, 122)):
            with self.subTest(overshoot=overshoot, confirmed=confirmed):
                self.console.tick = 100
                self.console.seek_attempts.clear()
                self.console.goto_outputs = deque([123 + overshoot] * 3 + [confirmed])
                with self.assertRaisesRegex(RuntimeError, "replay moved during seek correction"):
                    self.controller._seek_tick(123)
                self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
                self.assertEqual(self.controller._last_seek_details["corrections"], [])
                self.assert_failed_without_camera_writes()

    def test_dialog_cancellation_during_fresh_confirmation_prevents_corrective_seek(self):
        self.console.tick = 100
        cancelled = []
        def cancel_on_confirmation(tick):
            if len(self.console.status_samples) == 4:
                cancelled.append(True)
        self.console.on_status = cancel_on_confirmation
        with self.controller._paused_preparation(lambda: bool(cancelled)):
            with self.assertRaisesRegex(RuntimeError, "cancel"):
                self.controller._seek_tick(123)
        self.assertEqual(len(self.console.status_samples), 4)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assertEqual(self.controller._last_seek_details["corrections"], [])
        self.assert_failed_without_camera_writes()

    def test_changed_demo_on_fresh_confirmation_prevents_corrective_seek(self):
        self.console.tick = 100
        original_request = self.console._request_item
        def change_demo_before_confirmation(command):
            if command == "demo_goto" and len(self.console.status_samples) == 3:
                self.console.demo_name = "another.dem"
            return original_request(command)
        with patch.object(self.console, "_request_item", side_effect=change_demo_before_confirmation):
            with self.assertRaisesRegex(RuntimeError, "different"):
                self.controller._seek_tick(123)
        self.assertEqual(len(self.console.status_samples), 4)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assertEqual(self.controller._last_seek_details["corrections"], [])
        self.assert_failed_without_camera_writes()

    def test_alternating_nearby_ticks_are_not_mistaken_for_a_stuck_overshoot(self):
        self.console.goto_outputs = deque([124, 125] * 400)
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 123"):
            self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assert_failed_without_camera_writes()

    def test_permanent_small_overshoot_gets_only_one_correction_and_one_timeout_budget(self):
        self.console.permanent = True
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 123"):
            self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123, 123])
        self.assertGreaterEqual(self.clock.now, 15)
        self.assertLess(self.clock.now, 15.1)
        self.assert_failed_without_camera_writes()

    def test_large_overshoot_and_stuck_lower_tick_are_never_retargeted(self):
        for landed in (122, 126, 700):
            with self.subTest(landed=landed):
                self.console.seek_attempts.clear()
                self.console.goto_outputs = deque([landed] * 500)
                self.controller._stop_event.clear()
                started = self.clock.now
                with self.assertRaisesRegex(RuntimeError, "did not reach tick 123"):
                    self.controller._seek_tick(123)
                self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
                self.assertLess(self.clock.now - started, 15.1)
                self.assert_failed_without_camera_writes()

    def test_cancel_while_overshoot_is_settling_issues_no_corrective_seek(self):
        self.console.tick = 100
        self.controller._stop_event = CountedEvent(self.clock, 2)
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assert_failed_without_camera_writes()

    def test_cancel_after_corrective_seek_prevents_pose_restoration_and_manual_enable(self):
        self.console.on_seek = lambda seek: (
            self.controller._stop_event.set() if len(self.console.seek_attempts) == 3 else None)
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            self.prepare()
        self.assertEqual(len(self.console.seek_attempts), 3)
        self.assert_failed_without_camera_writes()

    def test_dialog_cancellation_on_final_overshoot_sample_prevents_correction(self):
        self.console.tick = 100
        cancelled = []
        def cancel_after_three_samples(tick):
            if len(self.console.status_samples) == 3:
                cancelled.append(True)
        self.console.on_status = cancel_after_three_samples
        with self.controller._paused_preparation(lambda: bool(cancelled)):
            with self.assertRaisesRegex(RuntimeError, "cancel"):
                self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assert_failed_without_camera_writes()

    def test_changed_demo_during_settling_never_receives_corrective_seek(self):
        self.console.tick = 100
        self.console.on_status = lambda tick: setattr(self.console, "demo_name", "another.dem")
        with self.assertRaisesRegex(RuntimeError, "different"):
            self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123])
        self.assert_failed_without_camera_writes()

    def test_dialog_cancellation_on_last_exact_confirmation_cannot_report_success(self):
        self.console.tick = 100
        cancelled, confirmations = [], []
        def cancel_on_last_confirmation(tick):
            if len(self.console.seek_attempts) == 2 and tick == 123:
                state_writes = [c for c in self.console.operations
                                if c.startswith(("demo_pause", "demo_gototick"))]
                if state_writes[-1] == "demo_pause":
                    confirmations.append(tick)
                    if len(confirmations) == 3:
                        cancelled.append(True)
        self.console.on_status = cancel_on_last_confirmation
        with self.controller._paused_preparation(lambda: bool(cancelled)):
            with self.assertRaisesRegex(RuntimeError, "cancel"):
                self.controller._seek_tick(123)
        self.assertEqual(confirmations, [123] * 3)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123, 123])
        self.assert_failed_without_camera_writes()

    def test_changed_demo_during_final_pause_confirmation_cannot_report_success(self):
        self.console.tick = 100
        def change_demo_during_confirmation(tick):
            if len(self.console.seek_attempts) == 2 and tick == 123:
                state_writes = [c for c in self.console.operations
                                if c.startswith(("demo_pause", "demo_gototick"))]
                if state_writes[-1] == "demo_pause":
                    self.console.demo_name = "another.dem"
        self.console.on_status = change_demo_during_confirmation
        with self.assertRaisesRegex(RuntimeError, "different"):
            self.controller._seek_tick(123)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [123, 123])
        self.assert_failed_without_camera_writes()

    def test_corrected_tick_does_not_bypass_weak_translation_guard(self):
        self.console.weak_permanent = True
        # Keep the starting target close enough to reach the translation
        # witness; an unrelated huge offset would instead hit its size guard.
        self.console.pose = [40, 60, 350, 0, 90]
        self.console.anchor = list(self.console.pose)
        self.console.offset = (0, 0, 0)
        with self.assertRaisesRegex(RuntimeError, "only part"):
            self.prepare()
        self.assertEqual(self.console.tick, 112249)
        self.assertEqual(len(self.console.seek_attempts), 3)
        self.assertFalse(self.controller.status()["paused_camera"])
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertNotIn("demo_resume", self.console.operations)


if __name__ == "__main__":
    unittest.main()
