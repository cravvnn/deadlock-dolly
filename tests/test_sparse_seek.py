"""Native shots captured between recorded packets retain their authored phase."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dolly.path import CvarTrack, Keyframe, Project, TrackKey
from test_demo_packets import synthetic_demo
from test_native_controller import Bridge
import test_seek_overshoot as seek_fixture


class SparseConsole(seek_fixture.ForwardOvershootConsole):
    total_ticks = 51585

    def _request_item(self, command):
        output = super()._request_item(command)
        if command == "demo_goto":
            output = output.replace("of 161418 ticks", f"of {self.total_ticks} ticks")
        return output


class SparseSeekTests(unittest.TestCase):
    def setUp(self):
        seek_fixture.SeekOvershootTests.setUp(self)
        self.console = SparseConsole()
        self.controller._console = self.console
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.demo = Path(folder.name) / "dolly1.dem"
        self.demo.write_bytes(synthetic_demo((1, 22631, 22634, 22637, 48391, 48394, 48397, 48400, 51585)))
        self.controller._demo = self.demo
        self.console.demo_name = self.demo.name
        self.console.tick = self.console.goto_output = 49978
        self.console.overshoot = 0
        self.project = Project(start_tick=48393, tick_rate=64, interpolation="linear",
            lens_interpolation="linear", keyframes=[
                Keyframe(0, 0, 0, 100, 0, 0, 0, aspect_ratio=1.5),
                Keyframe(1, 640, 128, 228, 32, 64, 0, aspect_ratio=2)])
        def advance(seconds):
            self.clock.now += seconds
            return False
        self.clock.advance = advance
        self.bridge = Bridge(self.console, self.clock)
        self.controller._session.native = self.bridge

    def test_native_seek_targets_verified_following_packet_and_requires_stable_pause(self):
        info = self.controller._seek(self.project, 0)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48394])
        self.assertGreaterEqual(len(self.console.status_samples), 6)
        self.assertEqual(info["seek_boundary"], {"requested_tick": 48393,
            "actual_tick": 48394, "reason": "recorded_packet"})
        self.assertTrue(self.console.paused)
        self.assertTrue(self.controller._last_seek_details["verified"])
        self.assertEqual(self.controller._last_seek_details["commanded_tick"], 48394)

    def test_exact_recorded_tick_stays_exact(self):
        self.project.start_tick = 48394
        info = self.controller._seek(self.project, 0)
        self.assertNotIn("seek_boundary", info)
        self.assertEqual(info["tick"], 48394)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48394])

    def test_native_play_passes_original_camera_and_effect_phase_without_moving_keys(self):
        self.project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
            [TrackKey(0, 200), TrackKey(1, 840)], interpolation="linear")]
        saved = deepcopy(self.project.to_dict())
        with patch("dolly.controller.threading.Thread", return_value=Mock()) as worker:
            self.controller.play(self.project)
        self.assertEqual(self.bridge.phase, 1 / 64)
        self.assertEqual(self.bridge.project.to_dict(), saved)
        self.assertEqual(self.project.to_dict(), saved)
        self.assertEqual(worker.call_args.kwargs["args"][1], 1 / 64)
        initial = self.console.resume_snapshots[-1]
        self.assertEqual(initial["tick"], 48394)
        self.assertEqual(initial["values"]["r_citadel_depthoffield_focus_distance"], 210)
        self.assertEqual(self.controller._playback_details["startup_seek"]["actual_tick"], 48394)

    def test_native_seek_ui_evaluates_camera_at_original_phase(self):
        saved = deepcopy(self.project.to_dict())
        self.controller.seek(self.project, 0)
        expected = self.project.evaluate(1 / 64)
        for index, axis in enumerate(("x", "y", "z", "pitch", "yaw")):
            self.assertAlmostEqual(self.console.pose[index], expected[axis])
        self.assertAlmostEqual(self.console.values["r_aspectratio"], expected["aspect_ratio"], places=5)
        self.assertEqual(self.controller.status()["time"], 1 / 64)
        self.assertEqual(self.project.to_dict(), saved)

    def test_packet_beyond_shot_end_fails_before_any_seek_or_camera_write(self):
        self.project.keyframes[-1].time = .005
        saved = deepcopy(self.project.to_dict())
        with self.assertRaisesRegex(RuntimeError, "next recorded replay packet is tick 48394, after this shot ends"):
            self.controller.play(self.project)
        self.assertEqual(self.project.to_dict(), saved)
        self.assertFalse(self.console.seek_attempts)
        self.assertFalse(self.console.camera_writes)
        self.assertNotIn("demo_resume", self.console.operations)

    def test_generic_exact_seek_and_console_backend_do_not_opt_in(self):
        info = self.controller._seek_tick(48393)
        self.assertEqual(info["tick"], 48393)
        self.assertNotIn("seek_boundary", info)
        self.controller._session.native = None
        info = self.controller._seek(self.project, 0)
        self.assertEqual(info["tick"], 48393)
        self.assertNotIn("seek_boundary", info)

    def test_malformed_or_incomplete_file_does_not_authorize_tick_change(self):
        self.demo.write_bytes(b"PBDEMS2\0" + b"\0" * 16)
        info = self.controller._seek(self.project, 0)
        self.assertEqual(info["tick"], 48393)
        self.assertNotIn("seek_boundary", info)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48393])

    def test_different_replay_is_still_rejected(self):
        self.console.demo_name = "other.dem"
        with self.assertRaisesRegex(RuntimeError, "selected|different|launched"):
            self.controller._seek(self.project, 0)
        self.assertFalse(self.console.camera_writes)
        self.assertFalse(self.console.seek_attempts)
        self.assertNotIn("demo_resume", self.console.operations)

    def test_different_live_length_does_not_use_replaced_file_index(self):
        self.console.total_ticks = 50000
        info = self.controller._seek(self.project, 0)
        self.assertEqual(info["tick"], 48393)
        self.assertNotIn("seek_boundary", info)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48393])

    def test_wandering_after_seek_or_pause_never_counts_as_boundary_success(self):
        self.console.permanent = True
        self.console.overshoot = 1
        with self.assertRaisesRegex(RuntimeError, "did not reach tick 48394"):
            self.controller._seek(self.project, 0)
        self.assertFalse(self.controller._last_seek_details["verified"])
        self.assertNotIn("demo_resume", self.console.operations)

    def test_native_frozen_preview_keeps_nonpacket_tick_without_console_calibration(self):
        self.console.tick = self.console.goto_output = 48393
        with patch("dolly.controller.threading.Thread", return_value=Mock()):
            self.controller.play(self.project, frozen=True)
        self.assertEqual(self.console.tick, 48393)
        self.assertEqual(self.bridge.phase, 0)
        self.assertTrue(self.bridge.frozen)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.console.seek_attempts)
        self.assertFalse(self.console.camera_writes)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertIsNone(self.controller._playback_details["startup_seek"])

    def test_native_frozen_preview_rejects_tick_change_during_preparation(self):
        self.console.tick = self.console.goto_output = 48393
        prepare = self.bridge.prepare
        def change_tick(*args, **kwargs):
            result = prepare(*args, **kwargs)
            self.console.tick = 48394
            return result
        self.bridge.prepare = change_tick
        with patch("dolly.controller.threading.Event", return_value=Mock(wait=self.clock.advance)):
            with self.assertRaisesRegex(RuntimeError, "replay moved while preparing frozen"):
                self.controller.play(self.project, frozen=True)
        self.assertNotIn("native.play", self.bridge.events)
        self.assertFalse(self.console.seek_attempts)
        self.assertNotIn("demo_resume", self.console.operations)


if __name__ == "__main__":
    unittest.main()
