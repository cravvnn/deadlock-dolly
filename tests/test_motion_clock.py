"""Exercise camera writers with distinct Windows 3.12 clock models.

The coarse clock intentionally has a different epoch and 15.625 ms steps.
These tests check produced camera movement, not which clock function is called.
They simulate the console transport and cannot verify game rendering.
"""

from contextlib import contextmanager
import math
import unittest
from unittest.mock import patch

from dolly.navigation import CameraMotion
from dolly.path import Keyframe, Project
import test_controller as controller_fixture
from test_controller import CountedEvent, FakeConsole


class _WindowsClocks:
    def __init__(self):
        self.now = 0.0

    def precise(self):
        return 4000.0 + self.now

    def coarse(self):
        return 9000.0 + math.floor(self.now * 64) / 64

    @contextmanager
    def installed(self):
        with patch("dolly.controller.time.perf_counter", self.precise), \
                patch("dolly.controller.time.monotonic", self.coarse):
            yield


class _TimedConsole(FakeConsole):
    def __init__(self, clock, *, replay_speed=0.0):
        super().__init__()
        self.clock = clock
        self.replay_speed = replay_speed
        self.start_tick = self.tick = self.goto_output = 1000
        self.paused = not replay_speed

    def request(self, command, timeout=3, completion_patterns=None):
        if command.startswith("spec_goto "):
            # A sub-clock-quantum round trip, as in the uploaded trace.
            self.clock.now += 0.002
        return super().request(command, timeout, completion_patterns)

    def _request_item(self, command):
        if command in ("demo_info", "demo_goto") and not self.paused:
            self.tick = self.start_tick + math.floor(self.clock.now * 64 * self.replay_speed)
            self.goto_output = self.tick
        return super()._request_item(command)


class MotionClockTests(unittest.TestCase):
    def setUp(self):
        controller_fixture.ControllerTests.setUp(self)
        self.clock = _WindowsClocks()
        self.console = _TimedConsole(self.clock)
        self.controller._console = self.console
        self.project = Project(start_tick=1000, tick_rate=64,
            interpolation="linear", rotation_mode="shortest", keyframes=[
                Keyframe(0, 10, 20, 300, 0, 0, 0),
                Keyframe(0.25, 10, 20, 300, 0, 30, 0),
            ])

    def yaw_values(self):
        return [float(command.split(";")[0].split()[5])
                for command in self.console.camera_writes]

    def prepare_loop(self, *, waits=1000):
        self.controller._stop_event = CountedEvent(self.clock, waits)
        initial = self.project.evaluate(0)
        self.controller._reset_motion_observations(initial)
        self.controller._playback_details = {
            "initial_tick": 1000,
            "camera_prepared_at": self.clock.precise(),
        }
        self.controller._state["playing"] = True

    def test_frozen_rotation_advances_every_120hz_frame_and_finishes_exactly(self):
        with self.clock.installed():
            self.prepare_loop(waits=100)
            self.controller._run(self.project, 0, 1, 120, True)

        yaw = self.yaw_values()
        self.assertGreaterEqual(len(yaw), 31)
        self.assertLessEqual(len(yaw), 32)
        self.assertAlmostEqual(yaw[0], 0)
        self.assertAlmostEqual(yaw[-1], 30)
        increments = [b - a for a, b in zip(yaw, yaw[1:])]
        for increment in increments[:-1]:
            self.assertAlmostEqual(increment, 1.0, places=5)
        # An almost-terminal sample may already round to the exact angle in
        # the console command. Only the terminal write may repeat that angle.
        self.assertGreaterEqual(increments[-1], 0)
        self.assertLessEqual(increments[-1], 1.00001)
        self.assertAlmostEqual(self.controller.status()["time"], 0.25)
        self.assertLess(abs(self.clock.now - 0.25), 1 / 120 + 0.0021)
        self.assertEqual(self.console.tick, 1000)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertIn("Shot finished", self.controller.status()["message"])

    def test_tenth_speed_rotation_is_continuous_between_ticks_and_keeps_duration(self):
        self.console = _TimedConsole(self.clock, replay_speed=0.1)
        self.controller._console = self.console
        with self.clock.installed():
            self.prepare_loop(waits=400)
            self.controller._run(self.project, 0, 0.1, 120, False)

        yaw = self.yaw_values()
        self.assertGreaterEqual(len(yaw), 300)
        self.assertLessEqual(len(yaw), 304)
        self.assertAlmostEqual(yaw[0], 0)
        self.assertAlmostEqual(yaw[-1], 30)
        # Integer tick acknowledgements can adjust speed slightly; they must
        # not create the repeated zero-angle increments seen in the EXE log.
        increments = [b - a for a, b in zip(yaw, yaw[1:])]
        # Permit initial clock settling and the exact final-key write; inspect
        # the sustained motion after the first two replay tick observations.
        self.assertGreater(min(increments[40:-1]), 0.075)
        self.assertLess(max(increments[40:-1]), 0.125)
        self.assertGreater(increments[-1], 0)
        self.assertLess(abs(self.clock.now - 2.5), 2 / 120 + 0.0021)
        self.assertGreaterEqual(self.console.tick, 1016)
        self.assertTrue(self.console.paused)
        self.assertAlmostEqual(self.controller.status()["time"], 0.25)
        self.assertIn("Shot finished", self.controller.status()["message"])
        self.assertAlmostEqual(self.controller._playback_metrics["mean_round_trip_ms"], 2)

    def test_paused_rotation_uses_small_even_steps_and_cancellation_keeps_pose(self):
        with self.clock.installed():
            self.prepare_loop(waits=61)
            self.controller._set_paused_pose(self.project.evaluate(0), 1000)
            self.controller._state.update(playing=False, paused_flight=True)
            self.controller._run_paused_flight(
                lambda: CameraMotion(yaw=1), 240, 60, 120)

        yaw = [0.0, *self.yaw_values()]
        self.assertEqual(len(yaw), 61)
        for first, second in zip(yaw, yaw[1:]):
            self.assertAlmostEqual(second - first, 0.5, places=5)
        self.assertAlmostEqual(yaw[-1], 30)
        self.assertAlmostEqual(self.controller._paused_pose["yaw"], 30)
        self.assertEqual(self.controller._paused_details["updates"], 60)
        self.assertEqual(self.console.tick, 1000)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertFalse(any(item.startswith("demo_gototick ")
                             for item in self.console.operations))
        self.assertTrue(self.controller.status()["paused_camera"])
        self.assertFalse(self.controller.status()["paused_flight"])


if __name__ == "__main__":
    unittest.main()
