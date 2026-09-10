"""Replay tick 0 can precede the first seekable full packet (user log 0.4.1).

Use the actual seek-response shape and distinct camera/lens keys. The boundary
exception must never turn generic off-by-one failures into successful seeks.
"""
from collections import deque
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from dolly.path import CvarTrack, Keyframe, Project, TrackKey
from test_native_controller import Bridge
import test_seek_overshoot as seek_fixture


class FirstPacketConsole(seek_fixture.ForwardOvershootConsole):
    def __init__(self):
        super().__init__(overshoot=1, permanent=True)
        self.packet_reports = deque()
        self.packet_report = True

    def _request_item(self, command):
        result = super()._request_item(command)
        if command == "demo_gototick 0 0 1":
            report = self.packet_reports.popleft() if self.packet_reports else self.packet_report
            if report:
                return ("Demo Skipping: skipping to demo tick 0 (game tick 1129) "
                        "from full packet 1 (1130).  Current playback is 545 (1674)")
        return result


class SeekBoundaryTests(unittest.TestCase):
    def setUp(self):
        seek_fixture.SeekOvershootTests.setUp(self)
        self.console = FirstPacketConsole()
        self.controller._console = self.console
        self.project = Project(start_tick=0, tick_rate=64, interpolation="linear",
            lens_interpolation="linear", keyframes=[
                Keyframe(0, 0, 0, 100, 0, 0, 0, aspect_ratio=1.5),
                Keyframe(1, 640, 128, 228, 32, 64, 0, aspect_ratio=2)])

    def test_packet_one_floor_is_returned_honestly_after_retry_and_paused_confirmation(self):
        info = self.controller._seek(self.project, 0)
        self.assertEqual(info["tick"], 1)
        self.assertEqual(info["seek_boundary"], {"requested_tick": 0,
            "actual_tick": 1, "reason": "first_full_packet"})
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [0, 0])
        self.assertGreaterEqual(len(self.console.status_samples), 10)
        self.assertTrue(self.console.paused)
        self.assertNotIn("demo_resume", self.console.operations)
        details = self.controller._last_seek_details
        self.assertEqual((details["target_tick"], details["actual_tick"]), (0, 1))
        self.assertTrue(details["verified"])
        self.assertLess(self.clock.now, 2)

    def test_exact_callers_do_not_opt_into_boundary_reconstruction(self):
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 0"):
            self.controller._seek_tick(0)
        self.assertFalse(self.controller._last_seek_details["verified"])

    def test_both_seek_responses_must_report_the_packet_one_floor(self):
        for reports in ((False, True), (True, False), (False, False)):
            with self.subTest(reports=reports):
                self.console.packet_reports = deque(reports)
                self.console.seek_attempts.clear()
                with self.assertRaisesRegex(RuntimeError, "did not reach tick 0"):
                    self.controller._seek(self.project, 0)
                self.assertFalse(self.controller._last_seek_details["verified"])

    def test_tick_two_is_not_accepted_even_with_packet_one_report(self):
        self.console.overshoot = 2
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 0"):
            self.controller._seek(self.project, 0)
        self.assertFalse(self.controller._last_seek_details["verified"])

    def test_nonzero_one_tick_miss_is_still_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 64"):
            self.controller._seek(self.project, 1)
        self.assertFalse(self.controller._last_seek_details["verified"])

    def test_actual_zero_succeeds_normally_when_engine_can_reach_it(self):
        self.console.overshoot = 0
        info = self.controller._seek(self.project, 0)
        self.assertEqual(info["tick"], 0)
        self.assertNotIn("seek_boundary", info)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [0])

    def test_boundary_tick_must_remain_still_after_fresh_pause(self):
        self.console.goto_outputs = deque([1] * 7 + [2] * 500)
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 0"):
            self.controller._seek(self.project, 0)
        self.assertFalse(self.controller._last_seek_details["verified"])

    def test_cancellation_on_final_boundary_sample_never_reports_success(self):
        self.console.on_status = lambda tick: (
            self.controller._stop_event.set() if len(self.console.status_samples) == 10 else None)
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            self.controller._seek(self.project, 0)
        self.assertFalse(self.controller._last_seek_details["verified"])

    def test_seek_from_saved_tick_zero_keeps_every_authored_key_and_lens_time(self):
        saved = deepcopy(self.project.to_dict())
        reopened = Project.from_dict(saved)
        self.controller.seek(reopened, 0)
        expected = reopened.evaluate(1 / 64)
        for i, axis in enumerate(("x", "y", "z", "pitch", "yaw")):
            self.assertAlmostEqual(self.console.pose[i], expected[axis])
        self.assertAlmostEqual(self.console.values["r_aspectratio"], expected["aspect_ratio"], places=5)
        self.assertEqual(self.controller.status()["time"], 1 / 64)
        self.assertEqual(self.console.tick, 1)
        self.assertEqual(reopened.to_dict(), saved)
        self.assertEqual(self.controller._camera_calibration["prepared_tick"], 1)

    def test_console_playback_starts_at_actual_tick_without_delaying_subsequent_keys(self):
        saved = deepcopy(self.project.to_dict())
        with patch("dolly.controller.threading.Thread", return_value=Mock()):
            self.controller.play(self.project)
        initial = self.console.resume_snapshots[-1]
        self.assertEqual(initial["tick"], 1)
        self.assertEqual(initial["pose"][:3], [10, 2, 102])
        self.assertEqual(self.controller._playback_details["requested_start_time"], 0)
        self.assertEqual(self.controller._playback_details["start_time"], 1 / 64)
        self.assertEqual(self.project.to_dict(), saved)

    def test_native_camera_and_effects_share_actual_replay_phase_after_reopen(self):
        def advance(seconds):
            self.clock.now += seconds
            return False
        self.clock.advance = advance
        bridge = Bridge(self.console, self.clock)
        self.controller._session.native = bridge
        self.project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
            [TrackKey(0, 200), TrackKey(1, 840)], interpolation="linear")]
        saved = deepcopy(self.project.to_dict())
        reopened = Project.from_dict(saved)
        with patch("dolly.controller.threading.Thread", return_value=Mock()) as worker:
            self.controller.play(reopened)
        self.assertEqual(bridge.phase, 1 / 64)
        self.assertEqual(bridge.project.to_dict(), saved)
        self.assertEqual(reopened.to_dict(), saved)
        self.assertEqual(self.console.resume_snapshots[-1]["values"]["r_citadel_depthoffield_focus_distance"], 210)
        self.assertEqual(self.console.resume_snapshots[-1]["tick"], 1)
        self.assertEqual(worker.call_args.kwargs["args"][1], 1 / 64)
        self.assertEqual(self.controller._playback_details["startup_seek"]["actual_tick"], 1)

    def test_shot_ending_before_first_packet_fails_without_mutating_or_resuming(self):
        self.project.keyframes[-1].time = .005
        saved = deepcopy(self.project.to_dict())
        with self.assertRaisesRegex(RuntimeError, "first seekable tick is 1, after this shot ends"):
            self.controller.play(self.project)
        self.assertEqual(self.project.to_dict(), saved)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.camera_writes, [])


if __name__ == "__main__":
    unittest.main()
