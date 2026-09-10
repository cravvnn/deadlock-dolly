"""Native playback ownership at the controller / bridge boundary.

The fake advances rendered samples independently of console camera commands.
It checks routing and handoff, not compatibility with a running Deadlock build.
"""
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.path import CvarTrack, Keyframe, Project, TrackKey
import test_controller as controller_fixture


class Clock:
    now = 0.0

    def advance(self, seconds):
        self.now += seconds
        return False


class Bridge:
    def __init__(self, console, clock):
        self.console, self.clock = console, clock
        self.project = None
        self.state = "probe"
        self.phase = 0.0
        self.frame_count = 0
        self.events = []
        self.requests = []
        self.fail_prepare = False
        self.fail_handoff = False
        self.freeze_frames = False
        self.change_demo = False
        self.rewind = False
        self.frozen = False
        self.effect_originals = {}
        self.effect_samples = []
        self.roll = 0
        request = console.request

        def record(command, **kwargs):
            self.requests.append((self.state, command))
            self.events.append(command)
            for item in command.split(";"):
                if item.strip().startswith("cl_citadel_forceangles "):
                    self.roll = float(item.split()[-1])
            return request(command, **kwargs)

        console.request = record

    def prepare(self, project, start, speed, frozen, demo_name, timeout=5):
        self.events.append("native.prepare")
        if self.fail_prepare:
            raise RuntimeError("Native prepare timed out")
        self.project = deepcopy(project)
        self.effect_originals = {name:self.console.values[name] for name in project.evaluate(start)["cvars"]}
        self.phase, self.speed, self.frozen = start, speed, frozen
        self.state = "armed"

    def play(self, timeout=3):
        self.events.append("native.play")
        self.state = "playing"

    def hold(self, timeout=3):
        self.events.append("native.hold")
        self.state = "armed"

    def release(self, timeout=3):
        self.events.append("native.release")
        self.state = "stopped"
        self.console.values.update(self.effect_originals)
        self.effect_originals = {}

    def status(self):
        self.clock.advance(.008)
        if not self.freeze_frames:
            self.frame_count += 1
            if self.state == "playing" and (not self.console.paused or self.frozen):
                self.phase = min(self.project.duration, self.phase + .125)
                if not self.frozen:
                    self.console.tick = self.project.start_tick + round(self.phase * self.project.tick_rate)
                if self.phase == self.project.duration:
                    self.state = "completed"
        if self.change_demo and self.state == "playing":
            self.console.demo_name = "another.dem"
        if self.rewind and self.phase > .25:
            self.phase = 0
        frame = self.project.evaluate(self.phase) if self.project else dict(x=0, y=0, z=0, pitch=0, yaw=0, roll=0, aspect_ratio=16/9)
        effects = self.project.evaluate(self.phase)["cvars"] if self.project and self.state in ("armed", "playing", "completed") else {}
        self.console.values.update(effects)
        if effects:self.effect_samples.append((self.phase, dict(effects)))
        original = self.console.pose + [self.roll, self.console.values["r_aspectratio"]]
        if self.fail_handoff:
            original[0] += 100
        return {"effect_count": len(effects), "effect_error": 0, "effect_frames": len(self.effect_samples), "effect_phase": self.phase, "state": self.state, "phase": self.phase, "frame_count": self.frame_count,
            "tick": self.console.tick, "paused": self.console.paused,
            "demo_name": self.console.demo_name, "game_pid": 1234,
            "original_pose": original,
            "applied_pose": [frame[k] for k in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")],
            "frame_interval_ms": 8, "max_frame_interval_ms": 8}


class NativeControllerTests(unittest.TestCase):
    def setUp(self):
        controller_fixture.ControllerTests.setUp(self)
        self.clock = Clock()
        self.bridge = Bridge(self.console, self.clock)
        self.controller._session.native = self.bridge
        self.project = Project(start_tick=100, tick_rate=64,
            interpolation="linear", keyframes=[Keyframe(0, 0, 0, 100, 0, 350, 0),
                Keyframe(1, 80, 40, 120, 25, 380, 10, aspect_ratio=1)], tracks=[
                CvarTrack("r_citadel_depthoffield_focus_distance", [TrackKey(0, 600), TrackKey(1, 1200)]),
                CvarTrack("r_citadel_depthoffield_enable", [TrackKey(0, 0), TrackKey(.5, 1)], interpolation="step")])
        self.effect_tracks = self.project.tracks
        self.project.tracks = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("dolly.controller.time.perf_counter", lambda: self.clock.now))
        self.stack.enter_context(patch("dolly.controller.threading.Event", lambda: SimpleNamespace(wait=self.clock.advance)))
        self.stack.enter_context(patch("dolly.controller.FrameWait", lambda: SimpleNamespace(
            wait=lambda delay, event: self.clock.advance(delay), close=lambda: None)))
        self.thread = Mock()
        self.stack.enter_context(patch("dolly.controller.threading.Thread", return_value=self.thread))

    def prepare(self, frozen=False):
        # Position calibration and exact-seek behavior have separate integration
        # suites; retain all native publication ordering and settings handling.
        def position(frame, tick):
            self.controller._request(self.controller._position_commands(frame))
        def seek(*args):
            self.bridge.events.append("seek")
            self.console.tick = 100
            self.console.paused = True
            return {"tick": 100}
        with patch.object(self.controller, "_seek", side_effect=seek), \
             patch.object(self.controller, "_seek_tick", side_effect=seek), \
             patch.object(self.controller, "_position_direct_frame", side_effect=position):
            self.controller.play(self.project, speed=.1, rate=120, frozen=frozen, smoothing="balanced")
        self.controller._thread = None

    def run_native(self, frozen=False):
        self.controller._run_native(self.project, 0, .1, 120, frozen)

    def test_full_shot_is_armed_before_resume_and_gui_smoothing_is_not_applied(self):
        saved = self.project.to_dict()
        self.prepare()
        events = self.bridge.events
        prepared, playing = events.index("native.prepare"), events.index("native.play")
        resume = next(i for i, e in enumerate(events) if "demo_resume" in e)
        self.assertLess(prepared, playing)
        self.assertLess(playing, resume)
        self.assertEqual(self.bridge.project.to_dict(), saved)
        self.assertEqual(self.project.to_dict(), saved)
        self.assertEqual(self.controller._playback_details["smoothing"]["kind"], "native_view_time")
        self.assertEqual(self.controller._playback_details["smoothing"]["nominal_delay_seconds"], 0)

    def test_active_native_path_never_streams_pose_or_aspect_console_commands(self):
        self.prepare()
        self.run_native()
        active = [command for state, command in self.bridge.requests if state == "playing"]
        self.assertTrue(active)
        for command in active:
            self.assertNotIn("spec_goto ", command)
            self.assertNotIn("cl_citadel_forceangles ", command)
            self.assertNotIn("r_aspectratio ", command)
        self.assertTrue(self.controller._native_handoff_details["verified"])
        self.assertFalse(self.controller._native_active)
        self.assertEqual(self.bridge.state, "stopped")
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["demo_timescale"], 1)
        self.assertIn("Native shot finished", self.controller.status()["message"])
        self.assertEqual(self.controller._playback_samples[-1]["time"], 1)

    def test_effects_are_published_with_shot_and_no_animated_console_writes(self):
        self.project.tracks = self.effect_tracks
        originals = dict(self.console.values)
        self.prepare()
        self.run_native()
        self.assertTrue(self.bridge.effect_samples)
        for phase, effects in self.bridge.effect_samples:
            self.assertEqual(effects, self.project.evaluate(phase)["cvars"])
        self.assertEqual(self.console.values["r_citadel_depthoffield_focus_distance"], 1200)
        self.assertTrue(self.controller._native_active)
        self.assertTrue(self.controller._native_handoff_details["pending"])
        for _, command in self.bridge.requests:
            self.assertNotIn("r_citadel_depthoffield_focus_distance ", command)
            self.assertNotIn("r_citadel_depthoffield_enable ", command)
        self.controller.stop()
        self.assertFalse(self.controller._native_active)
        for track in self.effect_tracks:self.assertEqual(self.console.values[track.name], originals[track.name])

    def test_repeated_native_dof_shots_release_before_restarting(self):
        self.project.tracks = self.effect_tracks
        for _ in range(3):
            self.prepare();self.run_native()
            self.assertTrue(self.controller._native_active)
        self.assertEqual(self.bridge.events.count("native.release"), 2)
        self.controller.stop()
        self.assertEqual(self.bridge.events.count("native.release"), 3)

    def test_wrong_effect_phase_stops_instead_of_reporting_synchronization(self):
        self.project.tracks = self.effect_tracks
        self.prepare()
        get_status = self.bridge.status
        def wrong_phase():
            result = get_status()
            result["effect_phase"] += .1
            return result
        self.bridge.status = wrong_phase
        self.run_native()
        self.assertFalse(self.controller.status()["playing"])
        self.assertIn("did not acknowledge", self.controller.status()["message"])
        self.controller.stop()

    def test_unsupported_native_effect_rejected_before_stopping_current_shot(self):
        self.prepare()
        self.project.setup_values["cam_idealdist"] = 100
        before = list(self.bridge.events)
        with self.assertRaisesRegex(ValueError, "Not supported"):
            self.prepare()
        self.assertEqual(before, self.bridge.events)

    def test_handoff_uses_original_view_and_accepts_wrapped_angles(self):
        self.prepare()
        original_status = self.bridge.status
        def wrapped():
            status = original_status()
            status["original_pose"][4] %= 360
            return status
        self.bridge.status = wrapped
        self.run_native()
        self.assertTrue(self.controller._native_handoff_details["verified"])
        handoff = self.bridge.events.index("native.hold") if "native.hold" in self.bridge.events else self.bridge.events.index("demo_pause", self.bridge.events.index("native.play"))
        self.assertFalse(any("spec_pos" in event for event in self.bridge.events[handoff:]))

    def test_failed_handoff_holds_visible_view_and_blocks_paused_entry_until_retry(self):
        self.prepare()
        self.bridge.fail_handoff = True
        self.run_native()
        self.assertTrue(self.controller._native_active)
        self.assertNotEqual(self.bridge.state, "stopped")
        self.assertIn("final camera held", self.controller.status()["message"])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        with self.assertRaisesRegex(RuntimeError, "did not settle"):
            self.controller.begin_paused_camera()
        self.controller.stop()
        self.assertFalse(self.controller._native_active)
        self.assertEqual(self.console.values["r_aspectratio"], 0)

    def test_handoff_waits_for_rendered_pause_before_positioning(self):
        self.prepare()
        self.bridge.hold()
        original_status = self.bridge.status
        samples = iter([(100, False), (101, False), (102, True), (103, True),
                        (103, True), (103, True), (103, True), (103, True)])
        delivered = []
        def delayed_pause():
            status = original_status()
            tick, paused = next(samples, (103, True))
            status.update(tick=tick, paused=paused)
            delivered.append((tick, paused))
            return status
        original_request = self.controller._request
        placements = []
        def request(command, *args, **kwargs):
            if "spec_goto " in command:
                placements.append(list(delivered))
            return original_request(command, *args, **kwargs)
        self.bridge.status = delayed_pause
        with patch.object(self.controller, "_request", side_effect=request):
            self.controller._handoff_native_camera()
        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0][-2:], [(103, True), (103, True)])
        self.assertTrue(self.controller._native_handoff_details["verified"])
        self.assertEqual(self.controller._native_handoff_details["paused_tick"], 103)

    def test_unacknowledged_pause_keeps_native_view_without_repositioning(self):
        self.prepare()
        self.bridge.hold()
        original_status = self.bridge.status
        def still_running():
            status = original_status()
            status["paused"] = False
            return status
        self.bridge.status = still_running
        self.bridge.requests.clear()
        self.controller._handoff_native_camera(allow_hold=True)
        self.assertTrue(self.controller._native_active)
        self.assertTrue(self.controller._native_handoff_details["pending"])
        self.assertFalse(any("spec_goto " in command for _, command in self.bridge.requests))
        self.assertNotEqual(self.bridge.state, "stopped")

    def test_tick_change_after_confirmed_pause_still_blocks_handoff(self):
        self.prepare()
        self.bridge.hold()
        original_status = self.bridge.status
        samples = iter([100, 100, 100, 101])
        def moved_after_pause():
            status = original_status()
            status.update(tick=next(samples, 101), paused=True)
            return status
        self.bridge.status = moved_after_pause
        with self.assertRaisesRegex(RuntimeError, "replay moved during native camera handoff"):
            self.controller._handoff_native_camera()
        self.assertTrue(self.controller._native_active)
        self.assertNotEqual(self.bridge.state, "stopped")

    def test_repeated_play_releases_stalled_endpoint_before_new_preparation(self):
        self.bridge.fail_handoff = True
        for attempt in range(3):
            before = len(self.bridge.events)
            self.prepare()
            events = self.bridge.events[before:]
            if attempt:
                self.assertLess(events.index("native.release"), events.index("seek"))
                self.assertLess(events.index("native.release"), events.index("native.prepare"))
                self.assertLess(events.index("native.release"), next(i for i, e in enumerate(events) if "spec_goto " in e))
            self.run_native()
            self.assertTrue(self.controller._native_active)
            self.assertTrue(self.controller._native_handoff_details["pending"])
            self.assertIn("Native shot finished", self.controller.status()["message"])
        self.controller.stop()
        self.assertFalse(self.controller._native_active)
        self.assertFalse(self.controller._native_handoff_details["verified"])
        self.assertTrue(self.controller._native_handoff_details["released"])

    def test_pause_keeps_stalled_final_view_without_blocking_stop(self):
        self.prepare()
        self.bridge.fail_handoff = True
        self.run_native()
        self.controller.pause()
        self.assertTrue(self.controller._native_active)
        self.assertTrue(self.console.paused)
        self.controller.stop()
        self.assertFalse(self.controller._native_active)

    def test_release_failure_blocks_restart_before_camera_commands(self):
        self.prepare()
        self.bridge.fail_handoff = True
        self.run_native()
        before = len(self.bridge.events)
        with patch.object(self.bridge, "release", side_effect=RuntimeError("release timed out")):
            with self.assertRaisesRegex(RuntimeError, "release timed out"):
                self.prepare()
        self.assertTrue(self.controller._native_active)
        self.assertEqual(self.bridge.events[before:], [])

    def test_cancel_holds_before_pausing_and_restores_hud_then_hands_off(self):
        self.prepare()
        self.bridge.status()  # One rendered camera sample before cancellation.
        self.controller._stop_event.set()
        self.run_native()
        held = self.bridge.events.index("native.hold")
        pause = next(i for i, e in enumerate(self.bridge.events) if i > held and e.startswith("demo_pause"))
        released = self.bridge.events.index("native.release")
        self.assertLess(held, pause)
        self.assertLess(pause, released)
        self.assertFalse(self.controller._native_active)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertNotIn("Native shot finished", self.controller.status()["message"])

    def test_changed_replay_releases_without_repositioning_or_pausing_other_demo(self):
        self.prepare()
        self.bridge.requests.clear()
        self.bridge.change_demo = True
        self.run_native()
        self.assertEqual(self.bridge.state, "stopped")
        commands = [command for state, command in self.bridge.requests]
        self.assertFalse(any("spec_goto " in command or "demo_pause" in command for command in commands))
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertIn("identity changed", self.controller.status()["message"])

    def test_native_time_rewind_stops_instead_of_following_a_jump(self):
        self.prepare()
        self.bridge.rewind = True
        self.run_native()
        self.assertIn("time changed unexpectedly", self.controller.status()["message"])
        self.assertFalse(self.controller.status()["playing"])

    def test_frozen_native_path_moves_without_resume_and_keeps_demo_tick(self):
        self.prepare(frozen=True)
        self.run_native(frozen=True)
        self.assertFalse(any("demo_resume" in e for e in self.bridge.events))
        self.assertEqual(self.console.tick, 100)
        self.assertEqual(self.controller._playback_samples[-1]["time"], 1)
        self.assertTrue(self.controller._native_handoff_details["verified"])

    def test_prepare_failure_does_not_resume_and_restores_playback_settings(self):
        self.bridge.fail_prepare = True
        with self.assertRaisesRegex(RuntimeError, "prepare timed out"):
            self.prepare()
        self.assertFalse(any("demo_resume" in e for e in self.bridge.events))
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertFalse(self.controller._native_active)
        self.assertEqual(self.bridge.state, "stopped")

    def test_stale_view_callbacks_stop_camera_instead_of_streaming_replacements(self):
        self.prepare()
        self.bridge.freeze_frames = True
        self.run_native()
        self.assertIn("stopped receiving rendered views", self.controller.status()["message"])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertTrue(self.controller._native_active)

    def test_console_session_still_reports_legacy_backend(self):
        self.controller._session.native = None
        self.assertEqual(self.controller.status()["camera_backend"], "console")
        self.controller._handoff_native_camera()
        self.assertEqual(self.console.requests, [])


if __name__ == "__main__":
    unittest.main()
