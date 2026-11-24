"""Take-size estimate and recording progress text (desktop side).

SHARED_CASES are mirrored in native/tests/capture_estimate_tests.cpp so the
in-game Export drawer and the desktop show the same words for the same take.
"""
from types import SimpleNamespace
import unittest

from dolly import capture_estimate as ce
from dolly import gui
from dolly.path import Keyframe, Project

SHARED_FRAMES = (
    ((6.55, 600, .25, True), 15720),
    ((6.55, 600, .25, False), 3930),
    ((6.55, 600, 1, True), 3930),
    ((10, 60, 1, True), 600),
    ((1 / 3, 30, 1, True), 10),
    ((0, 60, 1, True), None),
    ((5, 0, 1, True), None),
    ((5, 60, 0, True), None),
)
SHARED_DURATIONS = ((0, "5 s"), (3, "5 s"), (42, "40 s"), (42.5, "45 s"), (90, "2 min"), (54, "55 s"), (55, "1 min"),
                    (1441, "24 min"), (3570, "1 h"), (3600, "1 h"), (3900, "1 h 5 min"))
SHARED_LINES = (
    (ce.estimate_line(15720, 26.2, None), "15,720 frames · 26.2 s of video"),
    (ce.estimate_line(15720, 26.2, 11.0), "15,720 frames · 26.2 s of video · about 24 min to record"),
    (ce.estimate_line(600, 10, 120.0), "600 frames · 10.0 s of video · about 5 s to record"),
    (ce.progress_line(1810, 15720, 1264.5), "1,810 / 15,720 frames · about 21 min left"),
    (ce.progress_line(1810, 15720, None), "1,810 / 15,720 frames · estimating time left"),
    (ce.progress_line(15725, 15720, 0), "15,725 / 15,725 frames · almost done"),
    (ce.progress_line(15000, 15720, 4.9), "15,000 / 15,720 frames · almost done"),
    (ce.progress_line(15000, 15720, 5), "15,000 / 15,720 frames · about 5 s left"),
    (ce.estimate_line(15720, 26.2, 11.0, per_pass=True), "15,720 frames per pass · 26.2 s of video"),
    (ce.progress_line(1810, None, None), "1,810 frames"),
)


class CaptureEstimate(unittest.TestCase):
    def test_shared_cases(self):
        for args, frames in SHARED_FRAMES:
            with self.subTest(args=args):
                self.assertEqual(ce.take_frames(*args), frames)
        for seconds, text in SHARED_DURATIONS:
            with self.subTest(seconds=seconds):
                self.assertEqual(ce.format_duration(seconds), text)
        for actual, expected in SHARED_LINES:
            self.assertEqual(actual, expected)
        self.assertIsNone(ce.take_frames(float("nan"), 60, 1, True))
        self.assertIsNone(ce.take_frames("x", 60, 1, True))

    def test_rate_needs_enough_of_the_take(self):
        self.assertIsNone(ce.capture_rate(29, 100))
        self.assertIsNone(ce.capture_rate(1000, 4.9))
        self.assertAlmostEqual(ce.capture_rate(55, 5), 11)
        self.assertIsNone(ce.seconds_left(10, 100, None))
        self.assertEqual(ce.seconds_left(10, 100, 9), 10)
        self.assertEqual(ce.seconds_left(200, 100, 9), 0)

    def test_elapsed_uses_the_recorder_clock(self):
        self.assertIsNone(ce.elapsed_since(0, 10_000_000, 5))
        self.assertIsNone(ce.elapsed_since(10, 0, 5))
        self.assertAlmostEqual(ce.elapsed_since(20_000_000, 10_000_000, 12.5), 10.5)
        self.assertIsNone(ce.elapsed_since(200_000_000, 10_000_000, 12.5))

    def test_take_progress_learns_only_from_takes_that_finish(self):
        memory = ce.RateMemory()
        take = ce.TakeProgress(memory)
        take.begin(15720, depth=True, depth_exr=False, fixed_step=True)
        recording = dict(state="recording", video_first_qpc=10_000_000, qpc_frequency=10_000_000)
        self.assertEqual(take.update(dict(recording, frames_written=10), now=2),
                         "10 / 15,720 frames · estimating time left")
        self.assertEqual(take.update(dict(recording, frames_written=1100), now=101),
                         "1,100 / 15,720 frames · about 22 min left")
        self.assertIsNone(take.update({"state": "finalizing"}))
        self.assertAlmostEqual(memory.rate(True, False), 11)
        self.assertIsNone(memory.rate(False, False))
        self.assertAlmostEqual(memory.rate(True, False), memory.rate(True, False))
        # EXR without depth is not a separate kind; depth+EXR is.
        self.assertIsNone(memory.rate(True, True))
        self.assertEqual(ce.RateMemory.key(False, True), (False, False))
        # A layer pass keeps the take's length but never teaches the color rate.
        take.begin(15720, depth=False, depth_exr=False, fixed_step=True)
        take.layer_pass()
        self.assertEqual(take.total, 15720)
        take.update(dict(recording, frames_written=550), now=51)
        take.update({"state": "completed"})
        self.assertIsNone(memory.rate(False, False))
        # Failed, discarded and real-time takes do not teach a rate.
        for end, fixed in (("failed", True), ("cancelled", True), ("completed", False)):
            with self.subTest(end=end, fixed=fixed):
                fresh = ce.RateMemory()
                take = ce.TakeProgress(fresh)
                take.begin(600, depth=False, depth_exr=False, fixed_step=fixed)
                take.update(dict(recording, frames_written=550), now=51)
                take.update({"state": end})
                self.assertIsNone(fresh.rate(False, False))


class DesktopWiring(unittest.TestCase):
    @staticmethod
    def var(value):
        return SimpleNamespace(get=lambda: value)

    def app(self, keyframes, **controls):
        values = dict(video_source="Camera path", video_pov_duration="10", video_fixed_step=True,
                      video_export_speed=".25", video_fps="600", video_depth=True, video_depth_exr=False)
        values.update(controls)
        app = SimpleNamespace(project=Project(keyframes=keyframes), _take_rates=ce.RateMemory(),
                              video_export=SimpleNamespace(_capture_only=False),
                              **{name: self.var(value) for name, value in values.items()})
        app._take_progress = ce.TakeProgress(app._take_rates)
        return app

    def test_estimate_follows_the_export_settings(self):
        path = [Keyframe(0, 0, 0, 0, 0, 0, 0), Keyframe(6.55, 0, 0, 0, 0, 0, 0)]
        app = self.app(path)
        self.assertEqual(gui.take_estimate_text(app), "15,720 frames · 26.2 s of video")
        app._take_rates.remember(True, False, 11)
        self.assertEqual(gui.take_estimate_text(app),
                         "15,720 frames · 26.2 s of video · about 24 min to record")
        # Real time ignores Export speed and never claims a capture time.
        app.video_fixed_step = self.var(False)
        self.assertEqual(gui.take_estimate_text(app), "3,930 frames · 6.5 s of video")
        # Player POV and a single attached camera use the replay duration.
        pov = self.app(path, video_source="Player POV", video_fps="60", video_export_speed="1")
        self.assertEqual(gui.take_estimate_text(pov), "600 frames · 10.0 s of video")
        attached = self.app([Keyframe(0, 0, 0, 0, 0, 0, 0, source="attach")], video_fps="60",
                            video_export_speed="1")
        self.assertEqual(gui.take_estimate_text(attached), "600 frames · 10.0 s of video")
        # A layered export: frames per pass, no time (passes differ in cost).
        layered = self.app(path, video_layer_players=True)
        layered._take_rates.remember(True, False, 11)
        self.assertEqual(gui.take_estimate_text(layered), "15,720 frames per pass · 26.2 s of video")
        self.assertIsNone(gui.take_estimate_text(self.app([Keyframe(0, 0, 0, 0, 0, 0, 0)])))
        self.assertIsNone(gui.take_estimate_text(self.app(path, video_fps="bad")))

    def test_recording_text_needs_a_known_take_length(self):
        app = self.app([])
        status = dict(state="recording", frames_written=1810, frames_dropped=0,
                      video_first_qpc=0, qpc_frequency=0)
        app._take_progress.begin(None, depth=False, depth_exr=False, fixed_step=True)
        self.assertIsNone(gui.recording_progress_text(app, status))
        app._take_progress.begin(15720, depth=False, depth_exr=False, fixed_step=True)
        self.assertEqual(gui.recording_progress_text(app, status),
                         "Recording · 1,810 / 15,720 frames · estimating time left")
        self.assertEqual(gui.recording_progress_text(app, dict(status, frames_dropped=3)),
                         "Recording · 1,810 / 15,720 frames · estimating time left · 3 missed frames")
        self.assertIsNone(gui.recording_progress_text(app, dict(status, capture_waiting="storage")))
        app.video_export._capture_only = True
        self.assertIsNone(gui.recording_progress_text(app, status))
        self.assertIsNone(gui.recording_progress_text(self.app([]), {"state": "idle"}))


class FooterFollowsTheTake(unittest.TestCase):
    """The action bar shows progress over routine text, never over a real message."""

    def app(self, footer, controller="Playing native camera path at render time."):
        class Var:
            def __init__(self, value):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
        return SimpleNamespace(status_text=Var(footer), video_status_text=Var("Saved · 9.0 s"),
                               _last_controller_message=controller)

    def test_progress_replaces_routine_text_and_hands_back(self):
        app = self.app("Playing native camera path at render time.")
        gui.follow_progress_in_footer(app, "Recording · 10 / 600 frames")
        self.assertEqual(app.status_text.get(), "Recording · 10 / 600 frames")
        gui.follow_progress_in_footer(app, "Recording · 20 / 600 frames")
        self.assertEqual(app.status_text.get(), "Recording · 20 / 600 frames")
        gui.follow_progress_in_footer(app, None)
        self.assertEqual(app.status_text.get(), "Saved · 9.0 s")
        # Nothing to hand back afterwards; later messages are left alone.
        app.status_text.set("Weather saved.")
        gui.follow_progress_in_footer(app, None)
        self.assertEqual(app.status_text.get(), "Weather saved.")

    def test_a_message_written_during_the_take_stays(self):
        app = self.app("")
        gui.follow_progress_in_footer(app, "Recording · 10 / 600 frames")
        app.status_text.set("Finish the current operation before stopping the recording.")
        gui.follow_progress_in_footer(app, "Recording · 20 / 600 frames")
        self.assertEqual(app.status_text.get(), "Finish the current operation before stopping the recording.")
        gui.follow_progress_in_footer(app, None)
        self.assertEqual(app.status_text.get(), "Finish the current operation before stopping the recording.")


if __name__ == "__main__":
    unittest.main()
