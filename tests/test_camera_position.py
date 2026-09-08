"""Camera-position regression tests against console-level engine simulations.

The native command/view offset reproduces the user's measured round trip.
These tests exercise the controller's real measurement and compensation; they
do not establish rendered-camera compatibility with a particular game build.
"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from dolly.controller import Controller
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT
from test_controller import CountedEvent, FakeClock, FakeConsole, make_project


class OffsetConsole(FakeConsole):
    """Report the view position, which can differ from spec_goto's origin."""

    def __init__(self, offset=(-1.1, -1.2, 57.4)):
        super().__init__()
        self.offset = tuple(offset)
        self.ignore_height = False
        self.unstable_readback = False
        self.position_reads = 0
        self.view_writes = []
        self.goto_output = 100
        self.delay_reads = 0
        self.stale_reads_remaining = 0
        self.stale_pose = None

    def _execute(self, command):
        for item in command.split(";"):
            item = item.strip()
            previous_pose = list(self.pose)
            super()._execute(item)
            if item.startswith("spec_goto "):
                for axis in range(3):
                    self.pose[axis] += self.offset[axis]
                if self.ignore_height:
                    self.pose[2] = previous_pose[2]
                self.view_writes.append(list(self.pose))
                self.stale_pose = previous_pose
                self.stale_reads_remaining = self.delay_reads

    def _request_item(self, command):
        if command == "spec_pos":
            self.position_reads += 1
            pose = list(self.pose)
            if self.stale_reads_remaining:
                pose = list(self.stale_pose)
                self.stale_reads_remaining -= 1
            if self.unstable_readback:
                pose[2] += 2 if self.position_reads % 2 else -2
            return "[Console] spec_goto " + " ".join(f"{value:.1f}" for value in pose)
        return super()._request_item(command)


class CameraPositionTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(lambda message: None)
        self.console = OffsetConsole()
        self.controller._console = self.console
        self.process = SimpleNamespace(poll=lambda: None)
        self.controller._session = SimpleNamespace(
            process=self.process, pid=1234, session_dir=Path("unused"),
            close=MagicMock(), restore_gameinfo=MagicMock(), owns_console_port=lambda: True)
        self.controller._demo = Path("/chosen/example.dem")
        self.controller._unlocker_pid = 1234
        self.controller._probe_result = {"capabilities": {
            "spec_pos": True, "spec_goto": True, "cl_citadel_forceangles": True,
            "r_aspectratio": True}}
        # Retain production sampling/verification, removing only the real wait.
        interval = patch("dolly.controller.CAMERA_SETTLE_INTERVAL", 0)
        interval.start()
        self.addCleanup(interval.stop)

    def assertPosition(self, expected):
        for actual, wanted in zip(self.console.pose[:3], expected):
            self.assertAlmostEqual(actual, wanted, places=5)

    def start_playback(self, project=None, **kwargs):
        project = project or make_project()
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch("dolly.controller.threading.Thread", return_value=worker):
            self.controller.play(project, **kwargs)
        return project

    def diagnostic_report(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = self.controller.export_diagnostics(Path(directory) / "position.zip")
            with zipfile.ZipFile(archive_path) as archive:
                return json.loads(archive.read("diagnostics.json"))

    def test_preview_corrects_the_reported_round_trip_without_changing_saved_key(self):
        project = Project(keyframes=[Keyframe(0, 240.1, 3816.2, 421.3, -10.5, 317.4, 0, 75)])
        saved = project.to_dict()
        self.console.pose = [240.1, 3816.2, 421.3, -10.5, 317.4]

        self.controller.apply(project, 0)

        self.assertPosition((240.1, 3816.2, 421.3))
        self.assertEqual(project.to_dict(), saved)
        self.assertEqual(self.console.view_writes[0][:3], [239.0, 3815.0, 478.7])
        self.assertEqual(self.console.view_writes[0][3], -10.5)
        self.assertAlmostEqual(self.console.view_writes[0][4] % 360, 317.4)
        for axis, expected in zip(("x", "y", "z"), self.console.offset):
            self.assertAlmostEqual(self.controller._camera_offset[axis], expected)
        self.assertTrue(self.controller._camera_calibration["verified"])
        self.assertTrue(self.controller._camera_calibration["height_response_verified"])
        self.assertTrue(self.console.paused)

    def test_seek_keeps_distinct_low_and_high_camera_keys_and_project_unchanged(self):
        project = make_project()
        saved = project.to_dict()
        for shot_time, expected in ((0, (0, 0, 200)), (10, (100, 200, 300)),
                                    (5, (50, 100, 250)), (0, (0, 0, 200))):
            with self.subTest(shot_time=shot_time):
                self.console.goto_output = int(project.start_tick + shot_time * project.tick_rate)
                self.controller.seek(project, shot_time)
                self.assertPosition(expected)
                self.assertEqual(project.to_dict(), saved)
                self.assertTrue(self.console.paused)

    def test_capture_retains_actual_view_coordinates_and_does_not_double_apply_offset(self):
        self.controller.apply(make_project(), 0)
        self.console.pose = [60.5, 71.2, 135.4, -21, 89]
        captured = self.controller.capture(0)
        self.assertEqual((captured.x, captured.y, captured.z), (60.5, 71.2, 135.4))
        project = Project(keyframes=[captured])
        for _ in range(2):
            self.controller.apply(project, 0)
            self.assertPosition((60.5, 71.2, 135.4))
            captured_again = self.controller.capture(0)
            self.assertEqual((captured_again.x, captured_again.y, captured_again.z),
                             (captured.x, captured.y, captured.z))

    def test_play_verifies_position_before_resume_with_lens_cvars_and_hud_applied(self):
        project = make_project()
        project.setup_values = {"r_citadel_depthoffield_enable": 1}
        project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
                                   [TrackKey(0, 900), TrackKey(10, 1200)])]
        self.start_playback(project, speed=0.5)

        self.assertEqual(len(self.console.resume_snapshots), 1)
        at_resume = self.console.resume_snapshots[0]
        self.assertEqual(at_resume["pose"][:3], [0, 0, 200])
        self.assertEqual(at_resume["tick"], 100)
        for name, expected in {"r_aspectratio": STANDARD_ASPECT,
                               "r_citadel_depthoffield_enable": 1,
                               "r_citadel_depthoffield_focus_distance": 900,
                               "citadel_hud_visible": 0, "demo_timescale": 0.5}.items():
            self.assertAlmostEqual(at_resume["values"][name], expected)
        resume_index = self.console.operations.index("demo_resume")
        position_checks = [index for index, command in enumerate(self.console.operations)
                           if command == "spec_pos"]
        self.assertTrue(position_checks)
        self.assertLess(max(position_checks), resume_index)
        self.assertTrue(self.controller._camera_calibration["verified"])

    def test_worker_keeps_compensation_across_interpolated_heights_without_extra_readbacks(self):
        project = self.start_playback()
        self.console.requests.clear()
        self.console.view_writes.clear()
        self.console.goto_outputs.extend([100, 125, 150, 175, 200, 200])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=8)
        with patch("dolly.controller.time.monotonic", side_effect=clock.monotonic):
            self.controller._run(project, 0, 1, 60, False)

        self.assertGreater(len(self.console.view_writes), 3)
        for pose in self.console.view_writes:
            self.assertAlmostEqual(pose[2], 200 + pose[0], places=5)
        self.assertAlmostEqual(self.console.view_writes[0][2], 200)
        self.assertAlmostEqual(self.console.view_writes[-1][2], 300)
        self.assertNotIn("spec_pos", self.console.requests)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertTrue(self.console.paused)

    def test_next_preview_remeasures_changed_native_offset(self):
        project = make_project()
        self.controller.apply(project, 0)
        self.console.offset = (2.5, 3.2, 32.0)
        self.controller.apply(project, 5)
        self.assertPosition((50, 100, 250))
        for axis, expected in zip(("x", "y", "z"), self.console.offset):
            self.assertAlmostEqual(self.controller._camera_offset[axis], expected)
        self.assertTrue(self.controller._camera_calibration["verified"])

    def test_negative_and_zero_offsets_are_measured_without_assumed_eye_height(self):
        for offset in ((0, 0, 0), (40, -75, -25)):
            with self.subTest(offset=offset):
                self.console.offset = offset
                self.controller.apply(make_project(), 0)
                self.assertPosition((0, 0, 200))
                for axis, expected in zip(("x", "y", "z"), offset):
                    self.assertAlmostEqual(self.controller._camera_offset[axis], expected)
                self.assertTrue(self.controller._camera_calibration["height_response_verified"])

    def test_ignored_height_is_rejected_even_when_initial_view_equals_requested_height(self):
        self.console.offset = (0, 0, 0)
        self.console.pose = [0, 0, 200, 0, 0]
        self.console.ignore_height = True
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaises(RuntimeError):
                self.controller.play(make_project())
        worker.assert_not_called()
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertPosition((0, 0, 200))
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertFalse(self.controller.status()["playing"])

    def test_unstable_position_is_rejected_before_resume_and_restores_hud(self):
        self.console.unstable_readback = True
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaises(RuntimeError):
                self.controller.play(make_project())
        worker.assert_not_called()
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertTrue(self.controller._camera_calibration["error"])

    def test_initial_stale_readbacks_are_not_mistaken_for_successful_positioning(self):
        self.console.delay_reads = 2
        self.console.pose = [0, 0, 200, 0, 0]
        self.start_playback()
        self.assertPosition((0, 0, 200))
        self.assertEqual(self.console.resume_snapshots[0]["pose"][:3], [0, 0, 200])
        self.assertTrue(self.controller._camera_calibration["verified"])
        attempts = self.controller._camera_calibration["attempts"]
        self.assertTrue(any(len(attempt["samples"]) >= 5 for attempt in attempts))
        self.assertAlmostEqual(self.controller._camera_offset["z"], 57.4)

    def _assert_cancelled_start(self, phase):
        original_request = self.console._request_item
        triggered = []

        def cancel_at_boundary(command):
            response = original_request(command)
            evidence = self.controller._camera_calibration
            attempts = evidence.get("attempts", [])
            final_sample = (command == "spec_pos" and attempts
                            and attempts[-1]["stage"] == "translation_return"
                            and len(attempts[-1]["samples"]) == 2)
            after_verification = command == "demo_goto" and evidence.get("verified", False)
            if not triggered and ((phase == "last_sample" and final_sample)
                                  or (phase == "last_demo_check" and after_verification)):
                triggered.append(command)
                self.controller._stop_event.set()
            return response

        with patch.object(self.console, "_request_item", side_effect=cancel_at_boundary):
            with patch("dolly.controller.threading.Thread") as worker:
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    self.controller.play(make_project())
        worker.assert_not_called()
        self.assertTrue(triggered)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.controller.status()["playing"])

    def test_cancellation_during_last_position_readback_cannot_resume(self):
        self._assert_cancelled_start("last_sample")
        self.assertFalse(self.controller._camera_calibration["verified"])

    def test_cancellation_after_verification_cannot_resume(self):
        self._assert_cancelled_start("last_demo_check")

    def test_failed_probe_readback_returns_to_requested_view_and_does_not_resume(self):
        original_request = self.console._request_item

        def fail_probe_readback(command):
            attempts = self.controller._camera_calibration.get("attempts", [])
            if command == "spec_pos" and attempts and attempts[-1]["stage"] == "translation_probe":
                raise RuntimeError("Simulated probe readback failure")
            return original_request(command)

        with patch.object(self.console, "_request_item", side_effect=fail_probe_readback):
            with patch("dolly.controller.threading.Thread") as worker:
                with self.assertRaisesRegex(RuntimeError, "Simulated probe readback failure"):
                    self.controller.play(make_project())
        worker.assert_not_called()
        self.assertPosition((0, 0, 200))
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertIn("return_after_probe_error", [attempt["stage"]
                      for attempt in self.controller._camera_calibration["attempts"]])

    def test_replay_tick_change_during_position_check_cannot_resume(self):
        original_request = self.console._request_item

        def advance_during_final_readback(command):
            response = original_request(command)
            attempts = self.controller._camera_calibration.get("attempts", [])
            if command == "spec_pos" and attempts and attempts[-1]["stage"] == "translation_return":
                self.console.goto_output = 110
                self.console.tick = 110
            return response

        with patch.object(self.console, "_request_item", side_effect=advance_during_final_readback):
            with patch("dolly.controller.threading.Thread") as worker:
                with self.assertRaisesRegex(RuntimeError, "replay moved"):
                    self.controller.play(make_project())
        worker.assert_not_called()
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.controller.status()["playing"])

    def test_implausibly_large_offset_is_rejected_and_diagnostics_preserve_failure(self):
        self.console.offset = (0, 0, 400)
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaises(RuntimeError):
                self.controller.play(make_project())
        worker.assert_not_called()
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        report = self.diagnostic_report()
        self.assertFalse(report["camera_calibration"]["verified"])
        self.assertTrue(report["camera_calibration"]["error"])
        self.assertEqual(report["camera_position_offset"], {"x": 0, "y": 0, "z": 0})

    def test_diagnostics_record_verified_measurement_and_disconnect_invalidates_it(self):
        self.controller.apply(make_project(), 0)
        report = self.diagnostic_report()
        calibration = report["camera_calibration"]
        self.assertTrue(calibration["verified"])
        self.assertTrue(calibration["height_response_verified"])
        self.assertTrue(calibration["attempts"])
        for axis, expected in zip(("x", "y", "z"), self.console.offset):
            self.assertAlmostEqual(report["camera_position_offset"][axis], expected)
            self.assertAlmostEqual(calibration["offset"][axis], expected)
        self.controller.disconnect()
        self.assertEqual(self.controller._camera_offset, {"x": 0, "y": 0, "z": 0})
        self.assertFalse(self.controller._camera_calibration.get("verified", False))


if __name__ == "__main__":
    unittest.main()
