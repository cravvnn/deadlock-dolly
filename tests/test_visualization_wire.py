"""Bounded transport and real portable-C++ parity for the optional path guide."""

import copy
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from dolly.path import Keyframe, Project
from dolly.visualization_wire import (
    ABI, HEADER, HEADER_BYTES, MAGIC, MAPPING_BYTES, MAX_MARKERS, MAX_SAMPLES,
    build_visualization,
)


def shot(count=3):
    return Project(keyframes=[Keyframe(i, i * 100, i * i * 10, i * 30,
                                       i * 10, i * 45, 0, aspect_ratio=16 / 9)
                              for i in range(count)])


class VisualizationWireTests(unittest.TestCase):
    def test_layout_keeps_original_path_and_timestamps(self):
        from dolly.native_path import compile_project
        project = shot()
        original = copy.deepcopy(project.to_dict())
        blob = build_visualization(project, sequence=24, selected_camera=1)
        header = HEADER.unpack_from(blob)
        self.assertEqual(HEADER_BYTES, 64)
        self.assertEqual(header[:6], (MAGIC, 24, ABI, 1, 1, 3))
        self.assertEqual(header[7:], (2048, 64))
        self.assertEqual(struct.unpack_from("<3d", blob, 64), (0, 1, 2))
        self.assertEqual(blob[88:], compile_project(project))
        self.assertEqual(project.to_dict(), original)

    def test_clearing_and_size_limits(self):
        for project, enabled in ((None, True), (Project(), True), (shot(), False)):
            blob = build_visualization(project, enabled=enabled)
            self.assertEqual(len(blob), 64)
            self.assertEqual(HEADER.unpack_from(blob)[3:7], (0, 0, 0, 0))
        blob = build_visualization(shot(4096), selected_camera=4095,
                                   sample_budget=2, marker_budget=MAX_MARKERS)
        self.assertLess(len(blob), MAPPING_BYTES)
        self.assertEqual(HEADER.unpack_from(blob)[7], MAX_SAMPLES)
        with self.assertRaises(ValueError):
            build_visualization(shot(4097))

    def test_rejects_bad_config_and_project(self):
        for kwargs in ({"sequence": 0}, {"sequence": 3}, {"sequence": True},
                       {"sequence": 0x100000000}, {"enabled": 1},
                       {"selected_camera": 3}, {"selected_camera": -1},
                       {"sample_budget": 4097}, {"sample_budget": 1},
                       {"marker_budget": 129}, {"marker_budget": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_visualization(shot(), **kwargs)
        with self.assertRaises(ValueError):
            build_visualization({})
        bad = shot()
        bad.keyframes[0].x = math.nan
        with self.assertRaises(ValueError):
            build_visualization(bad)


@unittest.skipUnless(shutil.which("g++"), "Portable viewer parity requires g++; Windows CI runs the native CMake test")
class VisualizationNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dolly-viewer-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.runner = Path(cls.temp.name) / "visualization_tests"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([shutil.which("g++"), "-std=c++17", "-O2", "-ffp-contract=off",
                        "-Wall", "-Wextra", "-pedantic", "-I", str(root / "native/include"),
                        str(root / "native/src/dolly_path.cpp"),
                        str(root / "native/src/dolly_visualization.cpp"),
                        str(root / "native/tests/visualization_tests.cpp"), "-o", str(cls.runner)],
                       check=True, capture_output=True, text=True)

    def test_projection_clipping_and_parser(self):
        result = subprocess.run([str(self.runner)], check=True, capture_output=True, text=True)
        self.assertIn("Native visualization tests passed", result.stdout)

    def test_authored_spline_samples_match_playback(self):
        project = shot()
        project.keyframes[1].time = .25
        project.keyframes[2].time = 4
        packet = Path(self.temp.name) / "shot.dlyvis"
        packet.write_bytes(build_visualization(project, selected_camera=2))
        result = subprocess.run([str(self.runner), str(packet)], check=True, capture_output=True, text=True)
        rows = result.stdout.splitlines()
        self.assertEqual(rows[0], "65 3 2")
        times = [0] + [.25 * i / 32 for i in range(1, 33)] + [
            .25 + 3.75 * i / 32 for i in range(1, 33)]
        self.assertEqual(len(rows) - 1, len(times))
        for row, time in zip(rows[1:], times):
            pose = project.evaluate(time)
            for actual, field in zip(map(float, row.split()),
                                     ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")):
                self.assertAlmostEqual(actual, pose[field], places=9)
