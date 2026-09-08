"""Optional playback smoothing at the console boundary.

These tests exercise emitted poses, lens/cvar timing, replay ownership and
cleanup. The timed console is a deterministic simulation, not a rendering test.
"""

from copy import deepcopy
import inspect
import unittest
from unittest.mock import MagicMock, patch

from dolly.path import CvarTrack, Keyframe, Project, TrackKey
import test_controller as controller_fixture
from test_controller import CountedEvent, FakeConsole
from test_endpoint_playback import _EndpointConsole
from test_motion_clock import _WindowsClocks


_DEFAULT_MODE = object()


class _SmoothedConsole(_EndpointConsole):
    def __init__(self, clock):
        super().__init__(clock)
        self.frames = []
        self.external_seek_at = None

    def request(self, command, timeout=3, completion_patterns=None):
        camera = command.startswith("spec_goto ")
        sent_at = self.clock.now
        previous_tick = self.tick
        output = super().request(command, timeout, completion_patterns)
        if camera:
            force = next(item.strip().split()[1:] for item in command.split(";")
                         if item.strip().startswith("cl_citadel_forceangles "))
            self.frames.append({"sent_at": sent_at, "tick_before": previous_tick,
                                "pose": list(self.pose), "angles": list(map(float, force)),
                                "values": dict(self.values)})
        return output

    def _request_item(self, command):
        output = super()._request_item(command)
        if (command == "demo_goto" and self.external_seek_at is not None
                and self.clock.now >= self.external_seek_at):
            self.tick = self.goto_output = 10000
            return (f"Currently playing {self.tick} of 161418 ticks. "
                    f"Minutes:42.04 File:{self.demo_name}")
        return output


class SmoothedPlaybackTests(unittest.TestCase):
    def setUp(self):
        controller_fixture.ControllerTests.setUp(self)
        self.clock = _WindowsClocks()
        self.console = _SmoothedConsole(self.clock)
        self.controller._console = self.console
        self.project = Project(start_tick=1000, tick_rate=64,
            interpolation="linear", lens_interpolation="linear", keyframes=[
                Keyframe(0, 10, 20, 300, 0, 0, 0, aspect_ratio=16/9),
                Keyframe(.25, 20, 40, 320, 15, 30, 6, aspect_ratio=1.0),
            ], tracks=[
                CvarTrack("r_citadel_depthoffield_focus_distance",
                          [TrackKey(0, 600), TrackKey(.25, 1200)]),
                CvarTrack("r_citadel_depthoffield_enable",
                          [TrackKey(0, 0), TrackKey(.125, 1)], interpolation="step"),
            ])

    def run_shot(self, *, mode="balanced", waits=700, frozen=False, speed=.1):
        self.console.paused = frozen
        self.console.replay_speed = 0 if frozen else speed
        with self.clock.installed():
            self.controller._stop_event = CountedEvent(self.clock, waits)
            self.controller._reset_motion_observations(self.project.evaluate(0))
            self.controller._playback_details = {
                "initial_tick": 1000, "camera_prepared_at": self.clock.precise()}
            self.controller._state["playing"] = True
            self.controller._playback_restore = {
                "citadel_hud_visible": 1.0, "engine_no_focus_sleep": 20.0}
            self.console.values.update(citadel_hud_visible=0.0, engine_no_focus_sleep=0.0)
            args = (self.project, 0, speed, 120, frozen)
            if mode is _DEFAULT_MODE:
                self.controller._run(*args)
            else:
                self.controller._run(*args, smoothing=mode)

    def assert_clean_finish(self):
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertFalse(self.controller.status()["playing"])
        self.assertIn("Shot finished", self.controller.status()["message"])

    def test_tenth_speed_diagonal_paths_share_one_phase_for_xyz_angles_zoom_and_dof(self):
        for pitch_sign, yaw_sign in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            with self.subTest(pitch=pitch_sign, yaw=yaw_sign):
                self.setUp()
                self.project.keyframes[-1].pitch *= pitch_sign
                self.project.keyframes[-1].yaw *= yaw_sign
                saved = deepcopy(self.project.to_dict())
                self.run_shot()
                frames = self.console.frames
                self.assertGreater(len(frames), 300)
                self.assertEqual(frames[0]["pose"], [10, 20, 300, 0, 0])
                self.assertEqual(frames[-1]["pose"],
                                 [20, 40, 320, 15*pitch_sign, 30*yaw_sign])
                for frame in frames:
                    # Infer progress from emitted X, then check independent
                    # camera and effect outputs against the authored shot.
                    phase = (frame["pose"][0] - 10) / 10
                    expected = self.project.evaluate(phase * self.project.duration)
                    for actual, axis in zip(frame["pose"], ("x", "y", "z", "pitch", "yaw")):
                        self.assertAlmostEqual(actual, expected[axis], delta=5e-5)
                    self.assertAlmostEqual(frame["angles"][2], expected["roll"], delta=5e-5)
                    self.assertAlmostEqual(frame["values"]["r_aspectratio"],
                                           expected["aspect_ratio"], delta=5e-6)
                    self.assertAlmostEqual(frame["values"]["r_citadel_depthoffield_focus_distance"],
                                           expected["cvars"]["r_citadel_depthoffield_focus_distance"], delta=.003)
                    if abs(phase - .5) > 1e-5:
                        self.assertEqual(frame["values"]["r_citadel_depthoffield_enable"], int(phase >= .5))
                    self.assertEqual(frame["values"]["citadel_hud_visible"], 0)
                self.assertEqual(self.project.to_dict(), saved)
                self.assert_clean_finish()

    def test_each_enabled_mode_flushes_exact_endpoint_without_a_larger_final_pan_step(self):
        for mode, window in (("light", .08), ("balanced", .16), ("strong", .28)):
            with self.subTest(mode=mode):
                self.setUp()
                self.run_shot(mode=mode)
                yaw = [frame["pose"][4] for frame in self.console.frames]
                steps = [b-a for a, b in zip(yaw, yaw[1:])]
                self.assertTrue(all(step >= -1e-7 for step in steps))
                self.assertLessEqual(steps[-1], max(steps[-15:-1]) * 1.25 + 1e-6)
                self.assertEqual(yaw[-1], 30)
                self.assertGreater(self.clock.now, 2.5 + window / 2)
                self.assertLess(self.clock.now, 2.5 + 2 / (64 * .1) + window + .02)
                settle = self.controller._playback_details["endpoint_settle"]
                self.assertTrue(settle["verified"])
                self.assertLessEqual(settle["elapsed_seconds"], settle["budget_seconds"])
                self.assert_clean_finish()

    def test_filter_cannot_predict_ahead_when_replay_stops_advancing(self):
        self.console.tick_ceiling = 1004
        self.run_shot(mode="strong", waits=300)
        # The existing clock may lead the acknowledged replay by ONE tick;
        # the filter must never increase that limit or creep past it at rest.
        cap_phase = 5 / 16
        for frame in self.console.frames:
            phase = (frame["pose"][0] - 10) / 10
            self.assertLessEqual(phase, cap_phase + 1e-6)
        last = self.console.frames[-1]["pose"]
        self.assertEqual(self.console.frames[-20]["pose"], last)
        self.assertNotIn("Shot finished", self.controller.status().get("message", ""))
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_filtered_diagnostics_remain_causal_and_report_real_delay(self):
        self.run_shot()
        samples = list(self.controller._playback_samples)
        delayed = []
        for sample in samples:
            self.assertGreaterEqual(sample["time"], 0)
            self.assertLessEqual(sample["time"], sample["raw_time"] + 1e-12)
            self.assertLessEqual(sample["raw_time"], self.project.duration)
            self.assertEqual(sample["smoothing_mode"], "balanced")
            # A shot-time difference must be divided by the playback speed
            # before it is labelled as a real-time lag in diagnostics.
            expected_delay = (sample["raw_time"] - sample["time"]) / .1
            self.assertAlmostEqual(sample["smoothing_delay_ms"], expected_delay * 1000, delta=1e-7)
            self.assertAlmostEqual(sample["smoothing_delay_shot_seconds"], expected_delay * .1, delta=1e-10)
            self.assertAlmostEqual(sample["camera_estimate_tick"],
                                   self.project.start_tick + sample["time"] * self.project.tick_rate)
            self.assertGreaterEqual(sample["clock_estimate_tick"], sample["camera_estimate_tick"] - 1e-9)
            self.assertAlmostEqual(min(self.project.duration,
                                       (sample["clock_estimate_tick"] - self.project.start_tick)
                                       / self.project.tick_rate), sample["raw_time"])
            delayed.append(expected_delay)
        self.assertGreater(max(delayed), .06)
        self.assertEqual(samples[-1]["time"], self.project.duration)
        self.assertEqual(samples[-1]["raw_time"], self.project.duration)
        self.assertEqual(samples[-1]["smoothing_delay_ms"], 0)
        details = self.controller._playback_details["smoothing"]
        self.assertEqual(details["mode"], "balanced")
        self.assertAlmostEqual(details["window_seconds"], .16)
        self.assertAlmostEqual(details["nominal_delay_seconds"], .08)

    def test_frozen_preview_flushes_smoothing_without_resuming_or_seeking_demo(self):
        self.run_shot(mode="strong", frozen=True, speed=1)
        self.assertEqual(self.console.tick, 1000)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertFalse(any(command.startswith("demo_gototick ") for command in self.console.operations))
        self.assertEqual(self.console.frames[0]["pose"], [10, 20, 300, 0, 0])
        self.assertEqual(self.console.frames[-1]["pose"], [20, 40, 320, 15, 30])
        self.assertGreaterEqual(self.clock.now, .25 + .28)
        # The raw endpoint and its finite flush are each sampled on a writer
        # frame boundary; allow one frame for each, plus the console reply.
        self.assertLess(self.clock.now, .25 + .28 + 2/120 + .004)
        self.assert_clean_finish()

    def test_balanced_reduces_pan_speed_changes_caused_by_integer_tick_updates(self):
        changes = {}
        for mode in ("off", "balanced"):
            self.setUp()
            self.run_shot(mode=mode)
            # Exclude start/stop easing; compare the constant authored pan in
            # the middle of the shot as tick acknowledgements arrive at 6.4 Hz.
            frames = [frame for frame in self.console.frames if .8 < frame["sent_at"] < 2.2]
            speeds = [(b["pose"][4] - a["pose"][4]) / (b["sent_at"] - a["sent_at"])
                      for a, b in zip(frames, frames[1:])]
            self.assertTrue(all(speed > 0 for speed in speeds))
            changes[mode] = max(abs(b-a) for a, b in zip(speeds, speeds[1:]))
        self.assertGreater(changes["off"], .01)
        self.assertLess(changes["balanced"], changes["off"] * .25)

    def test_cancellation_during_filtered_tail_keeps_current_pose_and_restores_hud(self):
        self.run_shot(mode="strong", waits=318)
        self.assertGreater(self.clock.now, 2.5)
        self.assertNotEqual(self.console.frames[-1]["pose"], [20, 40, 320, 15, 30])
        self.assertNotIn("Shot finished", self.controller.status().get("message", ""))
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)

    def test_changed_replay_during_filtered_tail_does_not_receive_followup_camera_or_pause(self):
        self.console.switch_at = 2.55
        with self.assertLogs("dolly", level="ERROR"):
            self.run_shot(mode="strong")
        self.assertNotEqual(self.console.frames[-1]["pose"], [20, 40, 320, 15, 30])
        self.assertLessEqual(self.console.frames[-1]["sent_at"], self.console.switch_at)
        self.assertNotIn("demo_pause", self.console.operations)
        self.assertIn("different", self.controller.status()["message"])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_external_large_seek_stops_filter_before_it_follows_new_tick(self):
        self.console.external_seek_at = .5
        with self.assertLogs("dolly", level="ERROR"):
            self.run_shot()
        self.assertLess(self.console.frames[-1]["pose"][0], 12)
        self.assertLessEqual(self.console.frames[-1]["sent_at"], .5)
        self.assertIn("jumped forward", self.controller.status()["message"])
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)

    def test_explicit_off_preserves_default_command_stream_and_duration(self):
        self.run_shot(mode=_DEFAULT_MODE)
        original_commands = list(self.console.camera_writes)
        original_time = self.clock.now
        self.setUp()
        self.run_shot(mode="off")
        self.assertEqual(self.console.camera_writes, original_commands)
        self.assertEqual(self.clock.now, original_time)
        self.assert_clean_finish()

    def test_invalid_smoothing_is_rejected_before_stopping_or_changing_existing_session(self):
        self.controller._state["paused_camera"] = True
        original_state = deepcopy(self.controller._state)
        original_values = dict(self.console.values)
        with patch.object(self.controller, "stop") as stop:
            with self.assertRaisesRegex(ValueError, "smoothing|Smoothing"):
                self.controller.play(self.project, smoothing="maximum")
        stop.assert_not_called()
        self.assertEqual(self.console.requests, [])
        self.assertEqual(self.controller._state, original_state)
        self.assertEqual(self.console.values, original_values)

    def test_public_play_passes_selected_mode_to_worker_after_normal_preparation(self):
        self.console = FakeConsole()
        self.console.goto_output = self.console.tick = 1000
        self.controller._console = self.console
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch("dolly.controller.CAMERA_SETTLE_INTERVAL", 0), \
                patch("dolly.controller.SEEK_SETTLE_INTERVAL", 0), \
                patch("dolly.controller.threading.Thread", return_value=worker) as thread:
            self.controller.play(self.project, speed=.1, smoothing="strong")
        invocation = thread.call_args.kwargs
        # Accept either positional or keyword delivery while checking the
        # worker's actual public signature and the selected mode it receives.
        bound = inspect.signature(invocation["target"]).bind(
            *invocation.get("args", ()), **invocation.get("kwargs", {}))
        bound.apply_defaults()
        self.assertEqual(bound.arguments["smoothing"], "strong")
        worker.start.assert_called_once()
        self.assertEqual(len(self.console.resume_snapshots), 1)
        self.assertEqual(self.console.resume_snapshots[0]["pose"], [10, 20, 300, 0, 0])


if __name__ == "__main__":
    unittest.main()
