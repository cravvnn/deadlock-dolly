"""Track interpolation: rail shape independent of key timing, plus a monotone push."""

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from dolly import native_path
from dolly.path import Keyframe, Project, TRACK_FORMAT_VERSION


def _shot(mode, keys):
    return Project(name="track", interpolation=mode, keyframes=[
        Keyframe(time=t, x=x, y=y, z=z, pitch=p, yaw=yaw, roll=0.0) for t, x, y, z, p, yaw in keys])


# Fast long move, two keys close in space but far apart in time, then fast again:
# the layout that makes the time-based spline swing wide.
UNEVEN = [(0, 0, 0, 100, 10, 0), (1, 800, 0, 100, 10, 20),
          (5, 840, 30, 100, 12, 25), (6, 1600, 400, 100, 15, 60)]


def _positions(project, keys, steps=2000):
    first, last = keys[0][0], keys[-1][0]
    return [tuple(project.evaluate(first + (last - first) * n / steps)[a] for a in "xyz")
            for n in range(steps + 1)]


def _excursion(points, keys):
    worst = 0.0
    for axis in range(3):
        low = min(k[1 + axis] for k in keys)
        high = max(k[1 + axis] for k in keys)
        worst = max(worst, *(max(low - p[axis], p[axis] - high) for p in points))
    return worst


def _native(blob, time):
    """Python mirror of NativePath::evaluate in native/src/dolly_path.cpp."""
    header = native_path.HEADER.unpack_from(blob, 0)
    count, channels = header[2], header[3]
    if time <= header[6]:
        return list(header[8:8 + channels])
    if time >= header[7]:
        return list(header[8 + channels:8 + 2 * channels])
    size = native_path.SEGMENT_BYTES
    for index in range(count):
        offset = native_path.HEADER_BYTES + index * size
        begin, end = native_path.SEGMENT_TIMES.unpack_from(blob, offset)
        if begin <= time < end:
            break
    span = end - begin
    u = (time - begin) / span
    result = []
    for channel in range(channels):
        kind, flags, left, right, d0, d1 = native_path.CHANNEL_RECORD.unpack_from(
            blob, offset + 16 + channel * native_path.CHANNEL_RECORD.size)
        if kind == 0:
            value = left
        elif kind == 1:
            value = (1 - u) * left + u * right
        else:
            value = ((2 * u ** 3 - 3 * u ** 2 + 1) * left + (u ** 3 - 2 * u ** 2 + u) * span * d0
                     + (-2 * u ** 3 + 3 * u ** 2) * right + (u ** 3 - u ** 2) * span * d1)
            if flags & 1:
                value = max(min(left, right), min(max(left, right), value))
        result.append(value)
    return result


class TrackShape(unittest.TestCase):
    def test_track_stays_close_where_spline_swings_wide(self):
        spline = _excursion(_positions(_shot("spline", UNEVEN), UNEVEN), UNEVEN)
        track = _excursion(_positions(_shot("track", UNEVEN), UNEVEN), UNEVEN)
        self.assertGreater(spline, 100)
        self.assertLess(track, spline / 5)

    def test_every_key_is_hit_at_its_time(self):
        project = _shot("track", UNEVEN)
        for t, x, y, z, *_ in UNEVEN:
            value = project.evaluate(t)
            self.assertEqual((value["x"], value["y"], value["z"]), (x, y, z))

    def test_motion_never_backtracks_along_a_line(self):
        keys = [(0, 0, 0, 0, 0, 0), (1, 100, 0, 0, 0, 0), (4, 200, 0, 0, 0, 0), (5, 600, 0, 0, 0, 0)]
        xs = [p[0] for p in _positions(_shot("track", keys), keys)]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(xs, xs[1:])))

    def test_repeated_position_is_a_clean_hold(self):
        keys = [(0, 0, 0, 0, 0, 0), (1, 300, 100, 0, 0, 10), (3, 300, 100, 0, 0, 10), (4, 600, 0, 0, 0, 30)]
        project = _shot("track", keys)
        for step in range(21):
            value = project.evaluate(1 + 2 * step / 20)
            for axis, expected in zip("xyz", (300, 100, 0)):
                self.assertAlmostEqual(value[axis], expected, places=9)

    def test_rotation_uses_bounded_smooth_curves(self):
        track = _shot("track", UNEVEN)
        smooth = _shot("smooth", UNEVEN)
        for step in range(61):
            t = step / 10
            self.assertEqual(track.evaluate(t)["yaw"], smooth.evaluate(t)["yaw"])


class TrackNative(unittest.TestCase):
    def test_native_blob_matches_editor_evaluation(self):
        project = _shot("track", UNEVEN)
        blob = native_path.compile_project(project)
        for step in range(601):
            t = step / 100
            editor = project.evaluate(t)
            native = _native(blob, t)
            for index, name in enumerate(native_path.CHANNELS):
                self.assertAlmostEqual(editor[name], native[index], places=9, msg=(name, t))

    def test_native_segments_are_contiguous_bit_for_bit(self):
        blob = native_path.compile_project(_shot("track", UNEVEN))
        header = native_path.HEADER.unpack_from(blob, 0)
        count, channels = header[2], header[3]
        previous = list(header[8:8 + channels])
        previous_time = header[6]
        for index in range(count):
            offset = native_path.HEADER_BYTES + index * native_path.SEGMENT_BYTES
            begin, end = native_path.SEGMENT_TIMES.unpack_from(blob, offset)
            self.assertEqual(begin, previous_time)
            self.assertGreater(end, begin)
            for channel in range(channels):
                record = native_path.CHANNEL_RECORD.unpack_from(
                    blob, offset + 16 + channel * native_path.CHANNEL_RECORD.size)
                self.assertEqual(record[2], previous[channel])
                self.assertTrue(all(math.isfinite(v) for v in record[2:]))
                previous[channel] = record[3]
            previous_time = end
        self.assertEqual(previous_time, header[7])
        self.assertEqual(previous, list(header[8 + channels:8 + 2 * channels]))

    def test_other_modes_compile_one_segment_per_key_interval(self):
        for mode in ("linear", "smooth", "spline"):
            blob = native_path.compile_project(_shot(mode, UNEVEN))
            self.assertEqual(struct.unpack_from("<I", blob, 12)[0], len(UNEVEN) - 1)


class TrackViewer(unittest.TestCase):
    def test_guide_packet_reports_key_slopes_from_the_track(self):
        from dolly import visualization_wire
        project = _shot("track", UNEVEN)
        packet = visualization_wire.build_visualization(project)
        self.assertTrue(packet)
        for index in range(len(UNEVEN)):
            slopes = project.camera_tangent_slopes(index, "x")
            self.assertTrue(all(math.isfinite(v) for v in slopes))


    def test_guide_packet_builds_while_cameras_are_being_placed(self):
        # The first placed camera is a one-key Track shot; it must still draw.
        from dolly import visualization_wire
        for count in range(1, len(UNEVEN) + 1):
            project = _shot("track", UNEVEN[:count])
            for selected in range(count):
                self.assertTrue(visualization_wire.build_visualization(
                    project, selected_camera=selected, revision=1, session_id=1))
            for name in ("x", "y", "z"):
                slopes = project.camera_tangent_slopes(count - 1, name)
                self.assertTrue(all(math.isfinite(v) for v in slopes))


class TrackFile(unittest.TestCase):
    def test_track_saves_with_its_own_format_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shot.json"
            _shot("track", UNEVEN).save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], TRACK_FORMAT_VERSION)
            loaded, version = Project.load_versioned(path)
        self.assertEqual((loaded.interpolation, version), ("track", TRACK_FORMAT_VERSION))

    def test_track_is_refused_in_older_versions(self):
        raw = _shot("track", UNEVEN).to_dict()
        raw["version"] = TRACK_FORMAT_VERSION - 1
        with self.assertRaises(ValueError):
            Project.from_dict(raw)

    def test_saved_shots_keep_their_mode_and_new_shots_use_the_new_shot_default(self):
        from dolly.path import INTERPOLATIONS, NEW_SHOT_INTERPOLATION
        from dolly.ui.shot_commands import finish_capture
        self.assertIn(NEW_SHOT_INTERPOLATION, INTERPOLATIONS)
        raw = _shot("spline", UNEVEN).to_dict()
        self.assertLess(raw["version"], TRACK_FORMAT_VERSION)
        del raw["interpolation"]
        self.assertEqual(Project.from_dict(raw).interpolation, "smooth")
        candidate = _shot("spline", UNEVEN)
        finish_capture(candidate, "start", candidate.keyframes[0], 10)
        self.assertEqual(candidate.interpolation, NEW_SHOT_INTERPOLATION)


if __name__ == "__main__":
    unittest.main()
