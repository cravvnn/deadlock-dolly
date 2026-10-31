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
        # This fixture exercises packet boundaries; recovery lifecycle has its own suite.
        recovery = patch.object(self.controller, "_recover_replay_for_shot")
        recovery.start()
        self.addCleanup(recovery.stop)
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
        self.bridge.configure_editor = Mock()
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

    def test_reported_six_tick_overshoot_preserves_sparse_packet_and_camera_phase(self):
        # Mirror the reported Players take: shot starts at 95; the next packet
        # is 97; the forward seek briefly settles at 103. The correction must
        # reach 97 exactly, and the authored camera phase stays tied to 95.
        self.demo.write_bytes(synthetic_demo((1, 94, 97, 103, 3010)))
        self.console.total_ticks = 3010
        self.console.tick = self.console.goto_output = 94
        self.console.overshoot = 6
        self.console.goto_seek_output = (
            'Demo Skipping: skipping forward to demo tick 97 (game tick 38982) '
            'from current 94 (38979)')
        self.project.start_tick = 95
        saved = deepcopy(self.project.to_dict())
        info = self.controller._seek(self.project, 0)
        self.assertEqual([s['target'] for s in self.console.seek_attempts], [97, 97])
        self.assertEqual(info['tick'], 97)
        self.assertEqual(info['seek_boundary'], {'requested_tick': 95,
            'actual_tick': 97, 'reason': 'recorded_packet'})
        self.assertEqual(self.controller._seek_shot_time(self.project, 0, info), 2 / 64)
        self.assertEqual(self.project.to_dict(), saved)

    def _replay_camera_seek(self, target, pose, *, owner='panel', **options):
        # Camera restoration has its own native-flight lifecycle owner. Keep
        # the actual packet selection and paused-seek verification here.
        return self.controller._seek_tick(target, **options), None, None

    def test_timeline_backward_seek_uses_preceding_packet_without_timeout(self):
        self.bridge.start_flight = Mock()
        # A paused rewind cannot reconstruct the unrecorded requested tick.
        self.console.on_seek = lambda seek: setattr(
            self.console, "tick", 48394 if seek["target"] == 48393 else seek["target"])
        with patch.object(self.controller, "_seek_replay_camera", side_effect=self._replay_camera_seek):
            result = self.controller.seek_replay_tick(48393)
        self.assertEqual(result["tick"], 48391)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48391])
        self.assertTrue(self.controller._last_seek_details["verified"])
        self.assertLess(self.clock.now, 1)

    def test_relative_backward_seek_uses_the_recorded_replay_boundary(self):
        self.console.tick = self.console.goto_output = 48457
        with patch.object(self.controller, "_seek_replay_camera", side_effect=self._replay_camera_seek):
            self.controller.seek_relative(-1, tick_rate=64)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [48391])

    def test_timeline_forward_seek_uses_following_packet_and_exact_packet_stays_exact(self):
        self.bridge.start_flight = Mock()
        for requested, actual in ((48393, 48394), (48394, 48394)):
            with self.subTest(requested=requested):
                self.console.tick = self.console.goto_output = 48391
                self.console.seek_attempts.clear()
                with patch.object(self.controller, "_seek_replay_camera", side_effect=self._replay_camera_seek):
                    result = self.controller.seek_replay_tick(requested)
                self.assertEqual(result["tick"], actual)
                self.assertEqual([s["target"] for s in self.console.seek_attempts], [actual])

    def test_timeline_seek_rejects_an_index_for_a_different_live_replay_length(self):
        self.bridge.start_flight = Mock()
        self.console.total_ticks = 50000
        with patch.object(self.controller, "_seek_replay_camera", side_effect=self._replay_camera_seek):
            with self.assertRaisesRegex(RuntimeError, "differs from the loaded replay"):
                self.controller.seek_replay_tick(48393)
        self.assertFalse(self.console.seek_attempts)

    def test_timeline_unsupported_index_keeps_exact_seek_and_cancellation_prevents_write(self):
        self.bridge.start_flight = Mock()
        self.demo.write_bytes(b"unsupported")
        with patch.object(self.controller, "_seek_replay_camera", side_effect=self._replay_camera_seek):
            self.assertEqual(self.controller.seek_replay_tick(48393)["tick"], 48393)
        self.console.seek_attempts.clear()
        with patch.object(self.controller, "_check_position_cancelled", side_effect=RuntimeError("Seek cancelled")):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.controller.seek_replay_tick(48393)
        self.assertFalse(self.console.seek_attempts)

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

    def _reported_endpoint(self):
        # The reported replay has packets 175, 178; a paused camera can be
        # captured between them. Its exact authored endpoint must survive seek.
        self.demo.write_bytes(synthetic_demo((1, 97, 175, 178, 48142)))
        self.console.total_ticks = 48142
        self.console.tick = self.console.goto_output = 97
        self.project.start_tick = 97
        self.project.keyframes[-1].time = 80 / 64  # Requested tick 177.
        self.bridge.start_flight = Mock()  # Native manual-camera capability.
        self.project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
            [TrackKey(0, 200), TrackKey(80 / 64, 840)], interpolation="linear")]
        return deepcopy(self.project.to_dict())

    def test_paused_endpoint_seek_holds_saved_camera_and_effects_at_next_packet(self):
        saved = self._reported_endpoint()
        self.controller.seek(self.project, 80 / 64)
        self.assertEqual([s["target"] for s in self.console.seek_attempts], [178])
        self.assertEqual(self.console.tick, 178)
        self.assertTrue(self.console.paused)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.bridge.phase, 80 / 64)
        expected = self.project.evaluate(80 / 64)
        for axis in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio"):
            self.assertAlmostEqual(self.controller._paused_pose[axis], expected[axis])
        self.assertEqual(self.console.values["r_citadel_depthoffield_focus_distance"], 840)
        self.assertEqual(self.controller.status()["time"], 80 / 64)
        self.assertIn("Final camera held", self.controller.status()["message"])
        self.assertEqual(self.project.to_dict(), saved)

    def test_paused_endpoint_cancellation_prevents_camera_application(self):
        saved = self._reported_endpoint()
        self.bridge.prepare = Mock(wraps=self.bridge.prepare)
        self.console.on_seek = lambda seek: self.controller._stop_event.set()
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            self.controller.seek(self.project, 80 / 64)
        self.bridge.prepare.assert_not_called()
        self.assertFalse(self.console.camera_writes)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.project.to_dict(), saved)

    def test_paused_endpoint_changed_replay_prevents_camera_application(self):
        saved = self._reported_endpoint()
        self.bridge.prepare = Mock(wraps=self.bridge.prepare)
        self.console.on_seek = lambda seek: setattr(self.console, "demo_name", "other.dem")
        with self.assertRaisesRegex(RuntimeError, "selected|different|launched"):
            self.controller.seek(self.project, 80 / 64)
        self.bridge.prepare.assert_not_called()
        self.assertFalse(self.console.camera_writes)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.project.to_dict(), saved)

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
