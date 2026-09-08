"""Aspect-ratio transport regressions for the freecam framing replacement.

These exercise production command generation, readback, capture and playback
against a console simulation; a numeric readback is not a rendered-game check.
"""

import math
import unittest
from unittest.mock import patch

from dolly.controller import ASPECT_CVAR, frame_commands, parse_camera
from dolly.path import CvarTrack, Keyframe, Project, STANDARD_ASPECT, TrackKey
import test_camera_position as camera_fixture
from test_controller import CountedEvent, FakeClock, make_project


LEGACY_FOV = ("citadel_camera_fov", "citadel_camera_spectator_fov")


class AspectControllerTests(unittest.TestCase):
    def setUp(self):
        camera_fixture.CameraPositionTests.setUp(self)
        self.console.offset = (0, 0, 0)

    def assert_no_legacy_fov_access(self):
        for command in self.console.operations:
            self.assertNotIn(command.split()[0], LEGACY_FOV, command)
        for name in LEGACY_FOV:
            self.assertNotIn(name, self.controller._probe_result.get("capabilities", {}))
        self.assertEqual(self.console.values["citadel_camera_fov"], 90)
        self.assertEqual(self.console.values["citadel_camera_spectator_fov"], 80)

    def test_probe_capture_preview_play_and_restore_never_access_old_fov_controls(self):
        self.controller.probe()
        with patch("dolly.controller.client_aspect_ratio", return_value=STANDARD_ASPECT):
            key = self.controller.capture(0)
        self.assertEqual(key.aspect_ratio, STANDARD_ASPECT)
        project = make_project()
        self.controller.apply(project, 5)
        self.controller.stop()
        with patch("dolly.controller.threading.Thread") as worker:
            worker.return_value.is_alive.return_value = False
            self.controller.play(project)
        self.controller.stop()
        self.assert_no_legacy_fov_access()

    def test_new_default_key_starts_at_standard_and_stop_restores_automatic_zero(self):
        self.console.values[ASPECT_CVAR] = 0
        project = Project(keyframes=[Keyframe(0, 1, 2, 200, 0, 0, 0)])
        self.controller.apply(project, 0)
        self.assertAlmostEqual(self.console.values[ASPECT_CVAR], STANDARD_ASPECT)
        self.assertEqual(self.controller._restore[ASPECT_CVAR], 0)
        self.controller.stop()
        self.assertEqual(self.console.values[ASPECT_CVAR], 0)
        self.assertIn("r_aspectratio 0", self.console.operations)

    def test_capture_explicit_ratio_preserves_view_without_consulting_window(self):
        self.console.values[ASPECT_CVAR] = 1.25
        with patch("dolly.controller.client_aspect_ratio") as native:
            captured = self.controller.capture(0)
        native.assert_not_called()
        self.assertEqual(captured.aspect_ratio, 1.25)
        self.assertEqual((captured.x, captured.y, captured.z), tuple(self.console.pose[:3]))
        self.assertEqual(captured.fov, 90, "Retired FOV values remain inert compatibility data")
        self.assertEqual(self.console.camera_writes, [])

    def test_capture_auto_zero_resolves_actual_game_window_ratio(self):
        self.console.values[ASPECT_CVAR] = 0
        with patch("dolly.controller.client_aspect_ratio", return_value=21 / 9) as native:
            captured = self.controller.capture(0)
        native.assert_called_once_with(1234)
        self.assertEqual(captured.aspect_ratio, 21 / 9)
        self.assertNotEqual(captured.aspect_ratio, 0)
        self.assertEqual(self.controller._aspect_capture["source"], "game_client_area")

    def test_capture_auto_without_window_uses_configured_standard(self):
        self.controller.standard_aspect = 4 / 3
        with patch("dolly.controller.client_aspect_ratio", return_value=None):
            captured = self.controller.capture(0)
        self.assertEqual(captured.aspect_ratio, 4 / 3)
        self.assertEqual(self.controller._aspect_capture["source"], "shot_standard")

    def test_invalid_native_aspects_reject_capture_without_any_camera_write(self):
        for aspect in (-1, 0.49, 4.01, math.inf, math.nan):
            with self.subTest(aspect=aspect):
                self.console.values[ASPECT_CVAR] = aspect
                with self.assertRaises(ValueError):
                    self.controller.capture(0)
        self.assertEqual(self.console.camera_writes, [])

    def test_repeated_edited_previews_keep_original_nonzero_restore_value(self):
        self.console.values[ASPECT_CVAR] = 2.35
        project = make_project()
        saved_coordinates = [(k.x, k.y, k.z) for k in project.keyframes]
        for aspect in (1.5, 0.9, 2.0):
            project.keyframes[1].aspect_ratio = aspect
            self.controller.apply(project, 10)
            self.assertEqual(self.console.values[ASPECT_CVAR], aspect)
            self.assertEqual(self.controller._restore[ASPECT_CVAR], 2.35)
        self.assertEqual([(k.x, k.y, k.z) for k in project.keyframes], saved_coordinates)
        self.controller.stop()
        self.assertEqual(self.console.values[ASPECT_CVAR], 2.35)

    def test_playback_streams_smooth_intermediate_ratios_with_camera_and_tick(self):
        project = make_project()
        project.lens_interpolation = "smooth"
        with patch("dolly.controller.threading.Thread"):
            self.controller.play(project)
        self.console.requests.clear()
        # Ten replay ticks/second at 60 console updates/second. The old
        # fixture jumped a whole game second on every 1/60-second response;
        # a continuous clock correctly refuses to snap across those gaps.
        self.console.goto_outputs.extend(100 + min(100, index // 6)
                                          for index in range(602))
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 650)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(project, 0, 1, 60, False)
        frames = [request for request in self.console.requests if request.startswith("spec_goto ")]
        ratios = []
        for command in frames:
            items = [part.strip().split() for part in command.split(";")]
            lens = [parts for parts in items if parts[0] == ASPECT_CVAR]
            self.assertEqual(len(lens), 1)
            ratios.append(float(lens[0][1]))
            self.assertIn(["demo_goto"], items)
        self.assertGreaterEqual(len(ratios), 10)
        self.assertAlmostEqual(ratios[0], STANDARD_ASPECT)
        self.assertEqual(ratios[-1], 1.0)
        self.assertTrue(any(1.1 < ratio < 1.6 for ratio in ratios))
        self.assertTrue(all(a >= b for a, b in zip(ratios, ratios[1:])))
        self.assertTrue(all(1 <= ratio <= STANDARD_ASPECT + 1e-8 for ratio in ratios))
        self.assertAlmostEqual(clock.now, project.duration, delta=1 / 30)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assert_no_legacy_fov_access()

    def test_lens_readback_is_recorded_before_resume(self):
        project = make_project()
        with patch("dolly.controller.threading.Thread"):
            self.controller.play(project)
        lens = self.controller._camera_calibration["lens"]
        self.assertEqual(lens["control"], ASPECT_CVAR)
        self.assertEqual(lens["requested"], STANDARD_ASPECT)
        self.assertAlmostEqual(lens["observed"], STANDARD_ASPECT)
        self.assertTrue(lens["verified"])
        readback = self.console.operations.index(ASPECT_CVAR,
                    self.console.operations.index("demo_gototick 100 0 1"))
        self.assertLess(readback, self.console.operations.index("demo_resume"))
        report = camera_fixture.CameraPositionTests.diagnostic_report(self)
        self.assertEqual(report["lens_control"], ASPECT_CVAR)
        self.assertEqual(report["standard_aspect"], STANDARD_ASPECT)

    def test_conflicting_or_retired_camera_variables_reject_before_moving(self):
        for name in (ASPECT_CVAR, *LEGACY_FOV):
            for mode in ("fixed", "track"):
                with self.subTest(name=name, mode=mode):
                    project = make_project()
                    if mode == "fixed":
                        project.setup_values[name] = 1.2
                    else:
                        project.tracks.append(CvarTrack(name, [TrackKey(0, 1.2)]))
                    self.console.requests.clear()
                    with self.assertRaises(ValueError):
                        self.controller.apply(project, 0)
                    self.assertEqual(self.console.camera_writes, [])

    def test_command_defaults_use_ratio_and_retired_fov_argument_is_rejected(self):
        frame = make_project().evaluate(5)
        # Retained project FOV is never used to produce a native command.
        frame["fov"] = 40
        first = frame_commands(frame)
        frame["fov"] = 170
        self.assertEqual(frame_commands(frame), first)
        self.assertIn("r_aspectratio ", first)
        self.assertNotIn("fov", first)
        for legacy in LEGACY_FOV:
            with self.subTest(legacy=legacy), self.assertRaises(ValueError):
                frame_commands(frame, legacy)

    def test_parser_keeps_view_axes_and_explicit_aspect(self):
        key = parse_camera("spec_goto 240.1 3816.2 421.3 -10.5 317.4",
                           aspect_ratio=1.25)
        self.assertEqual((key.x, key.y, key.z), (240.1, 3816.2, 421.3))
        self.assertEqual((key.pitch, key.yaw), (-10.5, 317.4))
        self.assertEqual(key.aspect_ratio, 1.25)


if __name__ == "__main__":
    unittest.main()
