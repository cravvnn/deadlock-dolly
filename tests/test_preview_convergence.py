"""Framing and paused-preview convergence regressions.

Position response shapes from earlier FOV diagnostics now exercise the aspect
transport. Lens-dependent origins are a simulated robustness case, not a claim
about how aspect ratio changes the rendered Deadlock camera. The console maps
native coordinates to view coordinates with a continuous affine response.
"""

import math
from unittest.mock import patch
import unittest

from dolly.path import Keyframe, Project, STANDARD_ASPECT
import test_camera_position as camera_fixture
from test_controller import FakeConsole


TARGET = (-183.0, 2056.0, 432.6)
GAIN = (0.3, 0.27, 0.735)
PREVIOUS_OFFSET = (-0.2, 0.0, 94.1)


class AffineViewConsole(FakeConsole):
    """Stable native origins with partial position response and aspect-ratio dependence."""

    def __init__(self):
        super().__init__()
        self.goto_output = 100
        self.values["r_aspectratio"] = STANDARD_ASPECT
        self.pose = [*TARGET, -10.5, 317.4]
        self.gain = GAIN
        # At aspect ratio 1.0, the previously measured correction leaves the reported
        # residual (0, -5.8, .2). Subsequent corrections follow the same affine
        # equation, yielding Y errors near -4.2 and -3.1 on the next two tries.
        residual = (0.0, -5.8, 0.2)
        self.origins = {
            1.0: tuple(target - correction - error / gain
                      for target, correction, error, gain
                      in zip(TARGET, PREVIOUS_OFFSET, residual, GAIN)),
            STANDARD_ASPECT: tuple(target - bias / gain for target, bias, gain
                      in zip(TARGET, (-0.2, 0.0, 57.4), GAIN)),
        }
        self.view_writes = []
        self.ignore_height = False
        self.ignore_aspect_writes = False
        self.fixed_axis = None

    def _execute(self, command):
        for item in command.split(";"):
            item = item.strip()
            if self.ignore_aspect_writes and item.startswith("r_aspectratio "):
                continue
            previous = list(self.pose)
            super()._execute(item)
            if not item.startswith("spec_goto "):
                continue
            native = list(self.pose)
            aspect_ratio = self.values["r_aspectratio"]
            origin = self.origins[1.0 if aspect_ratio < 1.4 else STANDARD_ASPECT]
            self.pose[:3] = [target + gain * (value - center)
                             for target, gain, value, center
                             in zip(TARGET, self.gain, native[:3], origin)]
            if self.ignore_height:
                self.pose[2] = previous[2]
            if self.fixed_axis is not None:
                self.pose[self.fixed_axis] = TARGET[self.fixed_axis] + 2.0
            self.view_writes.append({"command": native, "view": list(self.pose), "aspect_ratio": aspect_ratio})

    def _request_item(self, command):
        if command == "spec_pos":
            return "[Console] spec_goto " + " ".join(f"{value:.1f}" for value in self.pose)
        return super()._request_item(command)


def lens_project():
    return Project(name="Edited lens shot", start_tick=100, tick_rate=10,
                   keyframes=[Keyframe(0, *TARGET, -10.5, 317.4, 0, 90, STANDARD_ASPECT),
                              Keyframe(3, *TARGET, -10.5, 317.4, 0, 90, 1.0)])


class PreviewConvergenceTests(unittest.TestCase):
    def setUp(self):
        # Reuse the owned-process, replay, probe and zero-wait fixture while
        # exercising production calibration, parsing and command generation.
        camera_fixture.CameraPositionTests.setUp(self)
        self.console = AffineViewConsole()
        self.controller._console = self.console

    def assert_target_view(self):
        for actual, wanted in zip(self.console.pose[:3], TARGET):
            self.assertLessEqual(abs(actual - wanted), 0.55)

    def report(self):
        return camera_fixture.CameraPositionTests.diagnostic_report(self)

    def test_preview_converges_past_three_measured_corrections(self):
        self.controller._camera_offset = dict(zip(("x", "y", "z"), PREVIOUS_OFFSET))
        project = lens_project()
        saved = project.to_dict()

        # Private convergence remains useful evidence, but is insufficient
        # to permit public movement/native Resume in a weak-response state.
        self.controller._position_frame(project.evaluate(3))

        self.assert_target_view()
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["r_aspectratio"], 1.0)
        self.assertEqual(project.to_dict(), saved)
        self.assertGreater(len(self.console.view_writes), 3)
        self.assertTrue(all(math.isfinite(value) for write in self.console.view_writes
                            for value in write["command"]))
        calibration = self.report()["camera_calibration"]
        self.assertTrue(calibration["verified"])
        self.assertEqual(calibration["frame"]["aspect_ratio"], 1.0)

    def test_edited_standard_and_1_0_aspect_keys_preview_repeatedly_without_recapture(self):
        # A serialization round trip represents the edited UI project, keeping
        # stored view coordinates independent of native command corrections.
        project = Project.from_dict(lens_project().to_dict())
        saved = project.to_dict()
        for shot_time, wanted_aspect in ((0, STANDARD_ASPECT), (3, 1.0), (0, STANDARD_ASPECT), (3, 1.0)):
            with self.subTest(aspect_ratio=wanted_aspect):
                self.console.view_writes.clear()
                self.controller._position_frame(project.evaluate(shot_time))
                self.assert_target_view()
                self.assertAlmostEqual(self.console.values["r_aspectratio"], wanted_aspect)
                self.assertTrue(self.console.view_writes)
                self.assertTrue(all(abs(write["aspect_ratio"] - wanted_aspect) < 1e-7
                                    for write in self.console.view_writes))
                self.assertEqual(self.controller._camera_calibration["frame"]["aspect_ratio"], wanted_aspect)
                self.assertTrue(self.controller._camera_calibration["verified"])
                self.assertEqual(project.to_dict(), saved)

    def test_partial_height_response_is_accepted_only_after_verified_return(self):
        self.controller._position_frame(lens_project().evaluate(3))

        calibration = self.controller._camera_calibration
        attempts = calibration["attempts"]
        probe_index = next(index for index, attempt in enumerate(attempts)
                           if attempt["stage"] == "height_probe")
        before = attempts[probe_index - 1]["observed"]["z"]
        after = attempts[probe_index]["observed"]["z"]
        # A commanded 16-unit rise moves this camera about 11.8 units. The
        # response proves height control despite not being a unit-gain mapping.
        self.assertGreater(after - before, 10)
        self.assertLess(after - before, 13)
        self.assertTrue(any(attempt["stage"] == "final" for attempt in attempts[probe_index + 1:]))
        self.assert_target_view()
        self.assertTrue(calibration["height_response_verified"])
        self.assertTrue(calibration["verified"])

    def test_public_preview_and_play_reject_nonrecovering_partial_response(self):
        for action in ("apply", "play"):
            with self.subTest(action=action), patch("dolly.controller.threading.Thread") as worker:
                self.console.goto_output = 130
                with self.assertRaisesRegex(RuntimeError, "only part"):
                    if action == "play":
                        self.controller.play(lens_project(), time=3)
                    else:
                        self.controller.apply(lens_project(), 3)
                worker.assert_not_called()
                self.assertEqual(self.console.resume_snapshots, [])
                self.assertFalse(self.controller._camera_calibration["verified"])
                gains = self.controller._camera_calibration["translation_response"]["gain"]
                self.assertLess(gains["x"], .4)
                self.assertLess(gains["z"], .8)
                self.assertNotIn("Checking", self.controller.status()["message"])

    def test_ignored_height_still_rejects_without_resuming(self):
        self.console.ignore_height = True
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaises(RuntimeError):
                self.controller.play(lens_project())

        worker.assert_not_called()
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.controller._camera_calibration["verified"])
        self.assertNotIn("Checking", self.controller.status()["message"])

    def test_nonprogressing_position_remains_bounded_and_preserves_failure(self):
        self.console.fixed_axis = 0
        with self.assertRaises(RuntimeError):
            self.controller.apply(lens_project(), 3)

        self.assertLess(len(self.console.view_writes), 30)
        self.assertNotIn("demo_resume", self.console.operations)
        self.assertTrue(self.console.paused)
        report = self.report()
        self.assertFalse(report["camera_calibration"]["verified"])
        self.assertTrue(report["camera_calibration"]["error"])
        self.assertEqual(report["camera_calibration"]["frame"]["aspect_ratio"], 1.0)
        self.assertNotIn("Checking", self.controller.status()["message"])

    def test_failed_aspect_readback_prevents_any_camera_write_or_resume(self):
        for action in ("preview", "play"):
            with self.subTest(action=action):
                self.console.ignore_aspect_writes = True
                self.console.values["r_aspectratio"] = STANDARD_ASPECT
                self.console.view_writes.clear()
                self.console.operations.clear()
                project = lens_project()
                with patch("dolly.controller.threading.Thread") as worker:
                    with self.assertRaisesRegex(RuntimeError, "(?i)aspect|lens"):
                        if action == "preview":
                            self.controller.apply(project, 3)
                        else:
                            self.console.goto_output = 130
                            self.controller.play(project, time=3)
                worker.assert_not_called()
                self.assertEqual(self.console.view_writes, [])
                self.assertNotIn("demo_resume", self.console.operations)
                self.assertTrue(self.console.paused)
                self.assertEqual(self.console.values["citadel_hud_visible"], 1)
                calibration = self.report()["camera_calibration"]
                self.assertFalse(calibration["verified"])
                self.assertEqual(calibration["frame"]["aspect_ratio"], 1.0)
                self.assertTrue(calibration["error"])
                self.assertNotIn("Checking", self.controller.status()["message"])


if __name__ == "__main__":
    unittest.main()
