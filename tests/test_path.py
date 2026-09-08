import json
import math
from pathlib import Path
import tempfile
import unittest

from dolly.path import CvarTrack, Keyframe, Project, TrackKey, validate_cvar_name


def key(time, x=0, y=0, z=0, pitch=0, yaw=0, roll=0, fov=90):
    return Keyframe(time, x, y, z, pitch, yaw, roll, fov)


class PathTests(unittest.TestCase):
    def test_empty_new_project_can_save_but_cannot_play(self):
        project = Project()
        project.validate()
        self.assertEqual(project.duration, 0)
        self.assertEqual(Project.from_dict(project.to_dict()), project)
        with self.assertRaisesRegex(ValueError, "camera keyframe"):
            project.evaluate(0)

    def test_single_key_holds_and_negative_evaluation_is_clamped(self):
        project = Project(keyframes=[key(2, x=5, yaw=720, fov=35)])
        for time in (-10, 0, 2, 900):
            with self.subTest(time=time):
                result = project.evaluate(time)
                self.assertEqual(result["x"], 5)
                self.assertEqual(result["yaw"], 0)
                self.assertEqual(result["fov"], 35)

    def test_linear_movement_and_independent_lens(self):
        project = Project(interpolation="linear", keyframes=[
            key(1, x=10, y=-20, fov=100), key(3, x=30, y=20, fov=40)])
        result = project.evaluate(1.5)
        self.assertAlmostEqual(result["x"], 15)
        self.assertAlmostEqual(result["y"], -10)
        self.assertAlmostEqual(result["fov"], 85)

    def test_uneven_smooth_path_reproduces_constant_velocity(self):
        project = Project(keyframes=[key(t, x=3*t+1, y=-2*t, z=0.5*t)
                                     for t in (0, 0.1, 2.3, 10)])
        for time in (0, 0.04, 0.1, 0.8, 2.3, 7.1, 10):
            with self.subTest(time=time):
                result = project.evaluate(time)
                self.assertAlmostEqual(result["x"], 3*time+1)
                self.assertAlmostEqual(result["y"], -2*time)
                self.assertAlmostEqual(result["z"], 0.5*time)

    def test_smooth_positions_are_continuously_differentiable_at_keys(self):
        project = Project(keyframes=[key(0, x=0), key(0.4, x=5), key(4, x=-10)])
        eps = 1e-6
        center = project.evaluate(0.4)["x"]
        left = (center-project.evaluate(0.4-eps)["x"])/eps
        right = (project.evaluate(0.4+eps)["x"]-center)/eps
        self.assertAlmostEqual(left, right, places=3)

    def test_shortest_yaw_and_roll_cross_wrap_without_long_spin(self):
        project = Project(keyframes=[key(0, yaw=170, roll=-170), key(4, yaw=-170, roll=170)])
        self.assertEqual(project.evaluate(1)["yaw"], 175)
        self.assertEqual(project.evaluate(1)["roll"], -175)
        self.assertEqual(project.evaluate(3)["yaw"], 185)
        self.assertEqual(project.evaluate(3)["roll"], -185)

    def test_shortest_angles_stay_continuous_across_seams_and_rewinds(self):
        # The first crossing travels above +180 for yaw and below -180 for
        # roll. Later keys reverse across those seams on uneven time spacing.
        project = Project(keyframes=[
            key(0, yaw=170, roll=-170),
            key(0.7, yaw=-175, roll=175),
            key(1.8, yaw=175, roll=-175),
            key(3, yaw=-170, roll=170),
        ])
        authored = project.to_dict()
        samples = [project.evaluate(i / 1000) for i in range(3001)]
        for name in ("yaw", "roll"):
            differences = [abs(right[name] - left[name])
                           for left, right in zip(samples, samples[1:])]
            self.assertLess(max(differences), 0.1)
        self.assertGreater(max(frame["yaw"] for frame in samples), 180)
        self.assertLess(min(frame["roll"] for frame in samples), -180)
        for authored_key in project.keyframes:
            evaluated = project.evaluate(authored_key.time)
            for name in ("yaw", "roll"):
                # Equivalent orientations still pass exactly through each
                # authored view, regardless of the numeric representation.
                delta = (evaluated[name] - getattr(authored_key, name) + 180) % 360 - 180
                self.assertAlmostEqual(delta, 0)
        for index in (3000, 1700, 699, 1800, 0, 2500, 700):
            self.assertEqual(project.evaluate(index / 1000), samples[index])
        self.assertEqual(project.to_dict(), authored)

    def test_unwrapped_mode_preserves_intentional_multiple_spins(self):
        project = Project(rotation_mode="unwrapped", keyframes=[key(0), key(4, yaw=720, roll=-360)])
        self.assertEqual(project.evaluate(2)["yaw"], 360)
        self.assertEqual(project.evaluate(2)["roll"], -180)
        self.assertEqual(project.evaluate(4)["yaw"], 720)

    def test_exact_half_turn_uses_authored_direction(self):
        for target, halfway in ((180, 90), (-180, -90)):
            project = Project(keyframes=[key(0), key(2, yaw=target)])
            self.assertEqual(project.evaluate(1)["yaw"], halfway)

    def test_extreme_finite_numbers_never_produce_nonfinite_output(self):
        project = Project(keyframes=[key(0, x=-1e308, yaw=1e308),
                                     key(1e-310, x=1e308, yaw=-1e308),
                                     key(1, x=0, yaw=0)])
        for time in (0, 0.5e-310, 1e-310, 0.5, 1):
            result = project.evaluate(time)
            self.assertTrue(all(math.isfinite(result[name]) for name in ("x", "yaw", "fov")))

    def test_fov_and_smooth_track_never_overshoot_on_uneven_keys(self):
        times = [0, 0.03, 1, 20]
        values = [1, 179, 20, 150]
        project = Project(keyframes=[key(t, fov=v) for t, v in zip(times, values)], tracks=[
            CvarTrack("r_dof_override_focus_distance", [TrackKey(t, v) for t, v in zip(times, values)], "smooth")])
        for i in range(3):
            low, high = sorted(values[i:i+2])
            for step in range(101):
                t = times[i] + (times[i+1]-times[i])*step/100
                result = project.evaluate(t)
                self.assertGreaterEqual(result["fov"], low)
                self.assertLessEqual(result["fov"], high)
                self.assertGreaterEqual(result["cvars"]["r_dof_override_focus_distance"], low)
                self.assertLessEqual(result["cvars"]["r_dof_override_focus_distance"], high)

    def test_step_track_has_exact_key_boundary_and_rewinds(self):
        project = Project(keyframes=[key(0)], tracks=[
            CvarTrack("r_dof_override", [TrackKey(1, 0), TrackKey(2, 1), TrackKey(5, 0)], "step", 0)])
        self.assertEqual(project.duration, 5)
        expected = [(-1, 0), (1.99, 0), (2, 1), (4.99, 1), (5, 0), (7, 0), (3, 1), (1, 0)]
        for time, value in expected:
            self.assertEqual(project.evaluate(time)["cvars"]["r_dof_override"], value)

    def test_setup_values_form_baseline_and_tracks_override(self):
        project = Project(keyframes=[key(0)], setup_values={"r_dof_override": 1, "spec_fov": 90}, tracks=[
            CvarTrack("spec_fov", [TrackKey(2, 50)], restore_value=90),
            CvarTrack("r_dof_override", [])])
        self.assertEqual(project.evaluate(0)["cvars"], {"r_dof_override": 1, "spec_fov": 50})

    def test_json_roundtrip_and_independent_container_defaults(self):
        project = Project(name="Camera \u2606", keyframes=[key(0), key(2, x=20)],
                          tracks=[CvarTrack("r_dof_override", [TrackKey(0, 1)], "step", 0)],
                          start_tick=1234, tick_rate=60, setup_values={"cl_drawhud": 0})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"shot.dolly.json"
            project.save(path)
            self.assertEqual(Project.load(path), project)
            project.name = "Updated"
            project.save(path)
            self.assertEqual(Project.load(path).name, "Updated")
            self.assertEqual(list(Path(directory).iterdir()), [path])
        first, second = Project(), Project()
        first.setup_values["cl_drawhud"] = 0
        self.assertEqual(second.setup_values, {})

    def test_cvar_names_reject_commands_and_injection(self):
        for name in ("exec", "quit", "bind", "alias", "sv_cheats", "connect", "host_writeconfig",
                     "r_dof_override;quit", "r_dof_override\nquit", "r_dof_override 1", "r_dof_override\x00",
                     "R_DOF_OVERRIDE", "r_dof_override\n", "r_dof_\u0430", "", None, 12):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_cvar_name(name)
        for name in ("r_dof_override", "cam_idealdist", "cl_drawhud", "fov_cs_debug",
                     "r_citadel_depthoffield_enable", "r_citadel_depthoffield_focus_distance", "r_depth_of_field"):
            self.assertEqual(validate_cvar_name(name), name)

    def test_bad_numbers_and_timestamps_are_rejected(self):
        cases = [Project(keyframes=[key(-1)]), Project(keyframes=[key(0), key(0)]),
                 Project(keyframes=[key(2), key(1)]), Project(keyframes=[key(0, x=math.nan)]),
                 Project(keyframes=[key(0, fov=180)]), Project(keyframes=[key(0, fov=0)]),
                 Project(keyframes=[key(0, yaw=math.inf)]), Project(keyframes=[key(True)]),
                 Project(keyframes=[key(0, x="1;quit")]), Project(tick_rate=0),
                 Project(tick_rate=math.inf), Project(start_tick=True), Project(start_tick=-1),
                 Project(tracks=[CvarTrack("r_dof_override", [TrackKey(0, True)])]),
                 Project(setup_values={"r_dof_override": "1;quit"}),
                 Project(tracks=[CvarTrack("r_dof_override", [TrackKey(0, 1), TrackKey(0, 2)])]),
                 Project(tracks=[CvarTrack("r_dof_override"), CvarTrack("r_dof_override")]),
                 Project(interpolation="exec"), Project(rotation_mode="auto")]
        for project in cases:
            with self.subTest(project=project), self.assertRaises(ValueError):
                project.validate()
        for timestamp in (math.inf, math.nan, True, "0"):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                Project(keyframes=[key(0)]).evaluate(timestamp)

    def test_json_version_schema_and_nonfinite_numbers_are_rejected(self):
        bad_objects = [{"version": 2}, {"version": True}, {}, {"version": 1, "keyframes": {}},
                       {"version": 1, "command": "quit"}, {"version": 1, "keyframes": [{"time": 0}]},
                       {"version": 1, "tracks": [{"keys": []}]}, {"version": 1, "setup_values": []}]
        for obj in bad_objects:
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                Project.from_dict(obj)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bad.json"
            for text in ('{"version":1,"tick_rate":NaN}', '{"version":1,"version":1}',
                         '{"version":1,"tick_rate":1e999}', '[1,2]', 'not json'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    Project.load(path)


if __name__ == "__main__":
    unittest.main()
