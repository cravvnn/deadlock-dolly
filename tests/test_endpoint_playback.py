"""Check the last pan against the same continuous clock as the rest of a shot.

The console reports integer ticks at 0.1x, while camera commands run at 120 Hz.
These simulations validate command continuity and cleanup, not game rendering.
"""

from copy import deepcopy
import unittest

from dolly.path import Keyframe, Project
import test_controller as controller_fixture
from test_controller import CountedEvent
from test_motion_clock import _TimedConsole, _WindowsClocks


class _EndpointConsole(_TimedConsole):
    def __init__(self, clock):
        super().__init__(clock, replay_speed=0.1)
        self.camera_hud = []
        self.switch_at = None
        self.tick_ceiling = None
        self.jump_to_end = False
        self.fail_final_write = False
        self.status_queries = 0

    def request(self, command, timeout=3, completion_patterns=None):
        if command.startswith("spec_goto "):
            self.camera_hud.append(self.values["citadel_hud_visible"])
            if self.fail_final_write and command.startswith("spec_goto 20 40 320 15 30;"):
                raise RuntimeError("Simulated final camera write failure")
        return super().request(command, timeout, completion_patterns)

    def _request_item(self, command):
        if command == "demo_goto":
            self.status_queries += 1
            if self.switch_at is not None and self.clock.now >= self.switch_at:
                self.demo_name = "other.dem"
            result = super()._request_item(command)
            if self.tick_ceiling is not None:
                self.tick = self.goto_output = min(self.tick, self.tick_ceiling)
            if self.jump_to_end and self.status_queries > 1:
                self.tick = self.goto_output = self.start_tick + 16
            if self.tick_ceiling is not None or self.jump_to_end:
                return (f"Currently playing {self.tick} of 161418 ticks. "
                        f"Minutes:42.04 File:{self.demo_name}")
            return result
        return super()._request_item(command)


class EndpointPlaybackTests(unittest.TestCase):
    def setUp(self):
        controller_fixture.ControllerTests.setUp(self)
        self.clock = _WindowsClocks()
        self.console = _EndpointConsole(self.clock)
        self.controller._console = self.console
        self.project = Project(start_tick=1000, tick_rate=64,
            interpolation="linear", keyframes=[
                Keyframe(0, 10, 20, 300, 0, 0, 0),
                Keyframe(.25, 20, 40, 320, 15, 30, 0),
            ])

    def run_shot(self, waits=500):
        with self.clock.installed():
            self.controller._stop_event = CountedEvent(self.clock, waits)
            self.controller._reset_motion_observations(self.project.evaluate(0))
            self.controller._playback_details = {
                "initial_tick": 1000,
                "camera_prepared_at": self.clock.precise(),
            }
            self.controller._state["playing"] = True
            self.controller._playback_restore = {"citadel_hud_visible": 1.0}
            self.console.values["citadel_hud_visible"] = 0.0
            self.controller._run(self.project, 0, .1, 120, False)

    def poses(self):
        return [[float(value) for value in command.split(";")[0].split()[1:6]]
                for command in self.console.camera_writes]

    def test_all_diagonal_pan_directions_finish_without_a_larger_last_step(self):
        for pitch_sign, yaw_sign in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            with self.subTest(pitch=pitch_sign, yaw=yaw_sign):
                self.setUp()
                self.project.keyframes[-1].pitch *= pitch_sign
                self.project.keyframes[-1].yaw *= yaw_sign
                original = deepcopy(self.project.to_dict())
                self.run_shot()
                poses = self.poses()
                for axis in range(5):
                    steps = [abs(b[axis] - a[axis]) for a, b in zip(poses, poses[1:])]
                    self.assertLessEqual(steps[-1], max(steps[-10:-1]) * 1.25)
                self.assertEqual(poses[-1], [20, 40, 320, 15 * pitch_sign, 30 * yaw_sign])
                self.assertEqual(self.project.to_dict(), original)
                self.assertTrue(all(value == 0 for value in self.console.camera_hud))
                self.assertEqual(self.console.values["citadel_hud_visible"], 1)
                self.assertTrue(self.console.paused)
                self.assertIn("Shot finished", self.controller.status()["message"])
                self.assertTrue(self.controller._playback_details["endpoint_settle"]["verified"])
                self.assertLess(self.clock.now - 2.5, 2 / (64 * .1))
                self.assertFalse(any(item.startswith("demo_gototick ") or item == "demo_resume"
                                     for item in self.console.operations))

    def test_cancel_during_fractional_tail_does_not_snap_to_the_final_view(self):
        self.run_shot(waits=302)
        self.assertNotEqual(self.poses()[-1], [20, 40, 320, 15, 30])
        self.assertNotIn("Shot finished", self.controller.status().get("message", ""))
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_changed_replay_during_tail_stops_camera_writes_and_restores_hud(self):
        self.console.switch_at = 2.515
        with self.assertLogs("dolly", level="ERROR"):
            self.run_shot()
        self.assertNotEqual(self.poses()[-1], [20, 40, 320, 15, 30])
        self.assertNotIn("Shot finished", self.controller.status().get("message", ""))
        self.assertIn("different", self.controller.status().get("message", ""))
        # Cleanup may restore our own process's HUD after any replay ends or
        # changes, but it must not reposition or pause a different replay.
        self.assertNotIn("demo_pause", self.console.operations)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_large_phase_lag_at_endpoint_stops_within_the_settle_budget(self):
        # A 16-tick status leap is under the external-seek threshold, but must
        # not teleport to the endpoint or cause a multi-second catch-up tail.
        self.console.jump_to_end = True
        with self.assertLogs("dolly", level="ERROR"):
            self.run_shot()
        self.assertNotEqual(self.poses()[-1], [20, 40, 320, 15, 30])
        self.assertIn("settle", self.controller.status().get("message", "").lower())
        self.assertLess(self.clock.now, .4)
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_replay_acknowledgement_is_still_required_even_if_camera_reaches_end(self):
        self.console.tick_ceiling = 1015
        self.run_shot(waits=400)
        self.assertEqual(self.poses()[-1], [20, 40, 320, 15, 30])
        self.assertNotIn("Shot finished", self.controller.status().get("message", ""))
        self.assertEqual(self.console.tick, 1015)
        self.assertTrue(all(value == 0 for value in self.console.camera_hud))
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_failed_final_write_is_not_reported_as_a_verified_endpoint(self):
        self.console.fail_final_write = True
        with self.assertLogs("dolly", level="ERROR"):
            self.run_shot()
        self.assertIn("final camera write failure", self.controller.status()["message"])
        settle = self.controller._playback_details["endpoint_settle"]
        self.assertTrue(settle["clock_ready"])
        self.assertFalse(settle["verified"])
        self.assertNotIn("camera_completed_at", settle)
        self.assertNotEqual(self.poses()[-1], [20, 40, 320, 15, 30])
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)


if __name__ == "__main__":
    unittest.main()
