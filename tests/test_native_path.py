"""Compiler validation and real C++ callback parity for authored camera paths."""

import copy
import math
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

from dolly.native_path import CHANNELS, HEADER, HEADER_BYTES, MAX_CAMERA_KEYS, SEGMENT_BYTES, compile_project
from dolly.path import CvarTrack, Keyframe, Project, TrackKey


def camera(time, x=0, y=0, z=0, pitch=0, yaw=0, roll=0, aspect=16 / 9):
    return Keyframe(time, x, y, z, pitch, yaw, roll, aspect_ratio=aspect)


class NativeCompilerTests(unittest.TestCase):
    def test_format_and_track_duration(self):
        shot = Project(keyframes=[camera(1, x=10), camera(3, x=30)],
                       tracks=[CvarTrack("r_dof", [TrackKey(8, 1)])])
        before = copy.deepcopy(shot.to_dict())
        data = compile_project(shot)
        header = HEADER.unpack_from(data)
        self.assertEqual(header[:5], (b"DLYPATH\0", 1, 1, 7, 0))
        self.assertEqual(header[5:8], (8, 1, 3))
        self.assertEqual(len(data), HEADER_BYTES + SEGMENT_BYTES)
        self.assertEqual(shot.to_dict(), before)
        self.assertEqual(data, compile_project(shot))

    def test_one_key_and_shortest_endpoints(self):
        shot = Project(keyframes=[camera(0, yaw=370, roll=-370)])
        data = compile_project(shot)
        self.assertEqual(len(data), 160)
        header = HEADER.unpack_from(data)
        self.assertEqual(header[2], 0)
        self.assertEqual(header[12:14], (10, -10))
        self.assertEqual(header[8:15], header[15:22])

    def test_rejects_invalid_input_and_camera_limit(self):
        for bad in (None, {}, Project(), Project(keyframes=[camera(0), camera(0)]),
                    Project(keyframes=[camera(0, x=math.inf)])):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError):
                compile_project(bad)
        with self.assertRaisesRegex(ValueError, "4,096"):
            compile_project(Project(keyframes=[camera(i) for i in range(MAX_CAMERA_KEYS + 1)]))
        allowed = compile_project(Project(keyframes=[camera(i) for i in range(MAX_CAMERA_KEYS)]))
        self.assertEqual(len(allowed), HEADER_BYTES + (MAX_CAMERA_KEYS - 1) * SEGMENT_BYTES)

    def test_nonfinite_derivatives_compile_linear_fallback(self):
        shot = Project(keyframes=[camera(0, x=-1e308), camera(1, x=1e308), camera(2, x=0)])
        data = compile_project(shot)
        kind, _flags, _left, _right, derivative_a, derivative_b = struct.unpack_from("<II4d", data, 176)
        self.assertEqual((kind, derivative_a, derivative_b), (1, 0, 0))


@unittest.skipUnless(shutil.which("g++"), "Native C++ parity runner requires g++; Windows CI runs the native CMake gate")
class NativeEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dolly-native-path-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.runner = Path(cls.temp.name) / "path_tests"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([
            shutil.which("g++"), "-std=c++17", "-O2", "-ffp-contract=off", "-Wall", "-Wextra", "-pedantic",
            "-I", str(root / "native/include"), str(root / "native/src/dolly_path.cpp"),
            str(root / "native/tests/path_tests.cpp"), "-o", str(cls.runner),
        ], check=True, capture_output=True, text=True)

    def assert_matches(self, shot, times):
        blob = Path(self.temp.name) / "test.dlypath"
        blob.write_bytes(compile_project(shot))
        completed = subprocess.run([str(self.runner), str(blob), *map(repr, times)],
                                   check=True, text=True, capture_output=True)
        rows = completed.stdout.splitlines()
        self.assertEqual(len(rows), len(times))
        for time, row in zip(times, rows):
            expected = shot.evaluate(time)
            values = [float(value) for value in row.split()]
            self.assertEqual(len(values), len(CHANNELS))
            for name, actual in zip(CHANNELS, values):
                with self.subTest(time=time, channel=name):
                    self.assertTrue(math.isclose(actual, expected[name], rel_tol=1e-12, abs_tol=1e-10),
                                    f"{actual!r} != {expected[name]!r}")

    def test_native_parser_and_polynomial_self_tests(self):
        result = subprocess.run([str(self.runner)], check=True, capture_output=True, text=True)
        self.assertIn("Native path self-tests passed", result.stdout)

    def test_nonuniform_position_diagonal_turn_zoom_and_endpoint_holds(self):
        shot = Project(keyframes=[
            camera(.2, 10, -80, 400, -50, 170, 350, 1.4),
            camera(.27, 20, -90, 405, -10, -175, 5, 2.5),
            camera(2.9, -100, 30, 720, 40, -140, 25, .8),
            camera(7.1, 500, 20, 450, -30, 179, -20, 1.8),
        ], tracks=[CvarTrack("r_dof", [TrackKey(10, 1)])])
        times = [-1, 0, .2, .27, 2.9, 7.1, 10, 20]
        times += [i * .021 for i in range(340)]
        self.assert_matches(shot, times)

    def test_two_keys_linear_even_in_smooth_mode_and_step_zoom(self):
        shot = Project(keyframes=[camera(1, -10, 20, 400, -70, 170, -180, .6),
                                  camera(3, 100, -30, 500, 70, -170, 180, 3.5)])
        for lens_mode in ("linear", "smooth", "step"):
            shot.lens_interpolation = lens_mode
            self.assert_matches(shot, [0, 1, 1.01, 1.5, 2, 2.999, 3, 4])

    def test_single_key_and_cvar_extended_hold(self):
        shot = Project(keyframes=[camera(4, 12, 34, 56, 7, 720, -390, 2.1)],
                       tracks=[CvarTrack("r_dof", [TrackKey(10, 1)])])
        self.assert_matches(shot, [-100, 0, 4, 5, 10, 100])

    def test_unwrapped_full_turns_and_half_turn_sign(self):
        shot = Project(keyframes=[camera(0, yaw=0, roll=360), camera(.1, yaw=180, roll=180),
                                  camera(.7, yaw=540, roll=-180), camera(2, yaw=1080, roll=-720)])
        for rotation in ("shortest", "unwrapped"):
            shot.rotation_mode = rotation
            for motion in ("linear", "smooth"):
                shot.interpolation = motion
                self.assert_matches(shot, [0, .03, .1, .31, .7, 1.1, 1.9, 2, 3])

    def test_finite_overflow_fallback_and_tiny_intervals(self):
        shot = Project(keyframes=[camera(0, x=-1e308), camera(1, x=1e308), camera(2, x=0)])
        self.assert_matches(shot, [0, .1, .5, .9, 1, 1.5, 2])
        tiny = Project(keyframes=[camera(0, x=0), camera(1e-310, x=1), camera(2, x=10)])
        self.assert_matches(tiny, [0, 5e-311, 1e-310, .01, 1, 2])

    def test_repeatable_random_nonuniform_paths(self):
        rng = random.Random(831)
        for _ in range(6):
            timestamp = rng.uniform(0, 2)
            keys = []
            for _ in range(9):
                timestamp += rng.uniform(.01, 3)
                keys.append(camera(timestamp, *[rng.uniform(-1000, 1000) for _ in range(3)],
                                   *[rng.uniform(-540, 540) for _ in range(3)], rng.uniform(.5, 4)))
            shot = Project(keyframes=keys)
            self.assert_matches(shot, [rng.uniform(-1, timestamp + 1) for _ in range(30)] +
                                [key.time for key in keys])


if __name__ == "__main__":
    unittest.main()
