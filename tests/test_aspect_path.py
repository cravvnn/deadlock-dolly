import copy
import math
from pathlib import Path
import tempfile
import unittest

from dolly.path import (
    ASPECT_MAX, ASPECT_MIN, CAMERA_FIELDS, FORMAT_VERSION, STANDARD_ASPECT,
    CvarTrack, Keyframe, Project, TrackKey, validate_cvar_name,
)


def camera(time, aspect=STANDARD_ASPECT, **values):
    return Keyframe(time=time, x=values.get("x", 0), y=0, z=values.get("z", 400),
                    pitch=0, yaw=0, roll=0, aspect_ratio=aspect)


def legacy_shot():
    return {
        "format": "deadlock-dolly", "version": 1, "name": "Low camera",
        "start_tick": 112141, "tick_rate": 64, "interpolation": "linear",
        "rotation_mode": "unwrapped", "setup_values": {"r_citadel_depthoffield_enable": 1},
        "keyframes": [
            {"time": 0, "x": 240.1, "y": 3816.2, "z": 421.3,
             "pitch": -10.5, "yaw": 317.4, "roll": 0, "fov": 75},
            {"time": 3.75, "x": 330, "y": 3900, "z": 710,
             "pitch": 4, "yaw": 410, "roll": 20, "fov": 40},
        ],
        "tracks": [{"name": "r_citadel_depthoffield_focus_distance", "interpolation": "smooth",
                    "restore_value": 100, "keys": [{"time": 0, "value": 120},
                                                    {"time": 5, "value": 400}]}],
    }


class AspectPathTests(unittest.TestCase):
    def test_new_shot_defaults_to_standard_and_fov_can_be_omitted(self):
        key = camera(0)
        project = Project(keyframes=[key])
        self.assertEqual(key.fov, 90)
        self.assertEqual(key.aspect_ratio, 16 / 9)
        self.assertEqual(project.standard_aspect, 16 / 9)
        self.assertEqual(project.lens_interpolation, "smooth")
        self.assertIn("aspect_ratio", CAMERA_FIELDS)
        self.assertEqual(project.evaluate(0)["aspect_ratio"], STANDARD_ASPECT)

    def test_legacy_migration_preserves_the_shot_without_converting_fov(self):
        source = legacy_shot()
        original = copy.deepcopy(source)
        project = Project.from_dict(source)
        self.assertEqual(source, original)
        self.assertEqual(project.standard_aspect, STANDARD_ASPECT)
        self.assertEqual(project.lens_interpolation, "smooth")
        for old, new in zip(source["keyframes"], project.keyframes):
            for name, value in old.items():
                self.assertEqual(getattr(new, name), value)
            self.assertEqual(new.aspect_ratio, STANDARD_ASPECT)
        migrated = project.to_dict()
        self.assertEqual(migrated["version"], 2)
        for field in ("name", "start_tick", "tick_rate", "interpolation", "rotation_mode",
                      "setup_values", "tracks"):
            self.assertEqual(migrated[field], source[field])
        for time in (0, 1, 3.75, 5):
            self.assertAlmostEqual(project.evaluate(time)["aspect_ratio"], STANDARD_ASPECT)
        self.assertEqual(project.duration, 5)

    def test_v2_roundtrip_preserves_custom_standard_curve_and_other_channels(self):
        project = Project(standard_aspect=4 / 3, lens_interpolation="step",
                          keyframes=[camera(0, 4 / 3, z=421.3), camera(3, 0.9, z=600)],
                          tracks=[CvarTrack("r_dof", [TrackKey(0, 1)], "step", 0)])
        self.assertEqual(Project.from_dict(project.to_dict()), project)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shot.dolly.json"
            project.save(path)
            self.assertEqual(Project.load(path), project)
        self.assertEqual(project.to_dict()["version"], FORMAT_VERSION)
        self.assertEqual(project.evaluate(2.999)["aspect_ratio"], 4 / 3)
        self.assertEqual(project.evaluate(3)["aspect_ratio"], 0.9)

    def test_v2_rejects_missing_zoom_fields_and_unknown_fields(self):
        valid = Project(keyframes=[camera(0)]).to_dict()
        cases = []
        for field in ("standard_aspect", "lens_interpolation"):
            bad = copy.deepcopy(valid)
            del bad[field]
            cases.append(bad)
        bad = copy.deepcopy(valid)
        del bad["keyframes"][0]["aspect_ratio"]
        cases.append(bad)
        bad = copy.deepcopy(valid)
        bad["zoom_command"] = "quit"
        cases.append(bad)
        bad = copy.deepcopy(valid)
        bad["keyframes"][0]["aspect"] = 2
        cases.append(bad)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                Project.from_dict(data)

    def test_v1_does_not_accept_v2_fields_or_missing_legacy_fov(self):
        source = legacy_shot()
        for field in ("standard_aspect", "lens_interpolation"):
            bad = copy.deepcopy(source)
            bad[field] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                Project.from_dict(bad)
        bad = copy.deepcopy(source)
        bad["keyframes"][0]["aspect_ratio"] = 1.7
        with self.assertRaises(ValueError):
            Project.from_dict(bad)
        del source["keyframes"][0]["fov"]
        with self.assertRaises(ValueError):
            Project.from_dict(source)

    def test_aspect_values_reject_nonfinite_numbers_and_auto_sentinel(self):
        for value in (0, -1, ASPECT_MIN - 0.001, ASPECT_MAX + 0.001,
                      math.nan, math.inf, -math.inf, True, "1.77778", None):
            for field in ("keyframe", "standard"):
                project = (Project(keyframes=[camera(0, value)]) if field == "keyframe"
                           else Project(standard_aspect=value))
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    project.validate()
        for value in (ASPECT_MIN, ASPECT_MAX, 4 / 3, 3 / 2, 21 / 9):
            project = Project(standard_aspect=value, keyframes=[camera(0, value)])
            self.assertEqual(Project.from_dict(project.to_dict()), project)
        with self.assertRaisesRegex(ValueError, "automatic 0"):
            Project(keyframes=[camera(0, 0)]).validate()

    def test_smooth_zoom_is_bounded_on_uneven_timestamps_and_honors_keys(self):
        times = (0, 0.03, 1, 20)
        ratios = (STANDARD_ASPECT, ASPECT_MIN, ASPECT_MAX, 1.1)
        project = Project(keyframes=[camera(t, ratio) for t, ratio in zip(times, ratios)])
        for time, ratio in zip(times, ratios):
            self.assertEqual(project.evaluate(time)["aspect_ratio"], ratio)
        for index in range(len(times) - 1):
            low, high = sorted(ratios[index:index + 2])
            for step in range(101):
                time = times[index] + (times[index + 1] - times[index]) * step / 100
                value = project.evaluate(time)["aspect_ratio"]
                self.assertGreaterEqual(value, low)
                self.assertLessEqual(value, high)

    def test_smooth_zoom_derivative_is_continuous_at_interior_keys(self):
        project = Project(keyframes=[camera(0, 1), camera(0.8, 2), camera(3, 3)])
        eps = 1e-6
        center = project.evaluate(0.8)["aspect_ratio"]
        left = (center - project.evaluate(0.8 - eps)["aspect_ratio"]) / eps
        right = (project.evaluate(0.8 + eps)["aspect_ratio"] - center) / eps
        self.assertAlmostEqual(left, right, places=4)

    def test_lens_interpolation_is_independent_from_spatial_interpolation(self):
        keys = [camera(0, 1, x=0), camera(1, 2, x=10), camera(4, 1, x=-10)]
        project = Project(keyframes=keys, interpolation="linear", lens_interpolation="smooth")
        smooth_frame = project.evaluate(0.5)
        self.assertEqual(smooth_frame["x"], 5)
        self.assertNotAlmostEqual(smooth_frame["aspect_ratio"], 1.5)
        project.lens_interpolation = "linear"
        linear_frame = project.evaluate(0.5)
        self.assertEqual(linear_frame["x"], 5)
        self.assertEqual(linear_frame["aspect_ratio"], 1.5)
        project.interpolation = "smooth"
        other_frame = project.evaluate(0.5)
        self.assertNotEqual(other_frame["x"], 5)
        self.assertEqual(other_frame["aspect_ratio"], 1.5)
        project.lens_interpolation = "step"
        self.assertEqual(project.evaluate(0.999)["aspect_ratio"], 1)
        self.assertEqual(project.evaluate(1)["aspect_ratio"], 2)

    def test_zoom_endpoints_hold_and_rewinds_do_not_mutate_keys(self):
        project = Project(keyframes=[camera(1, 1.5), camera(3, 0.8), camera(6, 2)])
        before = project.to_dict()
        samples = {time: project.evaluate(time)["aspect_ratio"] for time in (-1, 1, 2, 3, 5, 6, 9)}
        self.assertEqual(samples[-1], 1.5)
        self.assertEqual(samples[9], 2)
        for time in (9, 2, 6, -1, 5, 1, 3):
            self.assertEqual(project.evaluate(time)["aspect_ratio"], samples[time])
        self.assertEqual(project.to_dict(), before)

    def test_legacy_fov_does_not_change_zoom(self):
        first = camera(0, 1.3)
        second = camera(2, 2.1)
        first.fov, second.fov = 75, 40
        project = Project(keyframes=[first, second])
        initial = [project.evaluate(time)["aspect_ratio"] for time in (0, 1, 2)]
        first.fov, second.fov = 100, 170
        self.assertEqual([project.evaluate(time)["aspect_ratio"] for time in (0, 1, 2)], initial)

    def test_unknown_lens_mode_is_rejected(self):
        for value in ("ease", "", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Project(lens_interpolation=value).validate()

    def test_aspect_identifier_is_allowed_but_not_command_text(self):
        self.assertEqual(validate_cvar_name("r_aspectratio"), "r_aspectratio")
        for name in ("r_aspectratio 0", "r_aspectratio;quit", "r_aspectratio\nquit"):
            with self.assertRaises(ValueError):
                validate_cvar_name(name)


if __name__ == "__main__":
    unittest.main()
