"""Single-call pixel, numeric, optional-tool and binary-reader contracts."""
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from dolly import camera_bundle, depth_exr, depth_normals, native_effects, runtime, screenshot, video_export
from dolly.path import AttachKey, CvarTrack, Keyframe, Project, TrackKey
from test_output_ownership import png_rows


def depth_frame(alias_header=False):
    def attribute(name, kind, payload):
        return name + b'\0' + kind + b'\0' + struct.pack('<i', len(payload)) + payload
    row = struct.pack('<2i2f', 0, 8, 9, 10)
    prefix = struct.pack('<2i', 20000630, 2)
    ignored = attribute(b'ignored', b'opaque', row)
    embedded = len(prefix) + len(ignored) - len(row)
    header = (prefix + ignored
              + attribute(b'channels', b'chlist', b'Z\0' + struct.pack('<4i', 2, 0, 1, 1) + b'\0')
              + attribute(b'compression', b'compression', b'\0')
              + attribute(b'dataWindow', b'box2i', struct.pack('<4i', 0, 0, 1, 0))
              + attribute(b'lineOrder', b'lineOrder', b'\0') + b'\0')
    return header + struct.pack('<Q', embedded if alias_header else len(header) + 8) + row


class NumericBoundaryTests(unittest.TestCase):
    def test_still_rejects_invalid_visible_pixels_but_ignores_hidden_rgb(self):
        with tempfile.TemporaryDirectory() as folder:
            for alpha in (-1, 2, float('inf'), float('-inf'), float('nan')):
                with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                    screenshot.write_hero_stills(1, 1, struct.pack('<4e', 0, 0, 0, alpha), folder)
            for value in (float('inf'), float('-inf'), float('nan')):
                with self.subTest(color=value), self.assertRaises(ValueError):
                    screenshot.write_hero_stills(1, 1, struct.pack('<4e', value, 0, 0, 1), folder)
            pixels = struct.pack('<8e', 0, 0, 0, 1, float('nan'), float('inf'), 0, 0)
            matte, color = screenshot.write_hero_stills(2, 1, pixels, folder)
            self.assertEqual(png_rows(matte)[1], b'\0\xff\xff\0\0')
            self.assertEqual(png_rows(color)[1], b'\0\0\0\0\xff\0\0\0\0')

    def test_camera_comparison_rejects_overflow_and_preserves_tolerance(self):
        huge = 1e308
        matrix = [huge] * 16
        for i in (3, 7, 11):
            matrix[i] = 0
        matrix[15] = 1
        view = camera_bundle.View(tuple(matrix), (0, 0, 0), (huge,) * 3, (-huge,) * 3)
        issues = []
        camera_bundle._check_view(SimpleNamespace(view=view), 'fixture', issues)
        self.assertTrue(any('orthonormal' in issue for issue in issues))
        self.assertTrue(camera_bundle._close(1.0001, 1, .001))
        for a, b in ((float('inf'), 1), (float('inf'), float('inf')), (float('nan'), 0), (2, 1)):
            self.assertFalse(camera_bundle._close(a, b, .001))

    def test_optional_ffmpeg_lookup_contains_io_errors_and_keeps_fallback(self):
        with patch.object(Path, 'is_file', side_effect=PermissionError('fixture blocked')):
            self.assertIsNone(video_export.resolve_ffmpeg('C:/fixture/ffmpeg.exe'))
            with patch.object(runtime, 'resource_root', return_value=Path('C:/fixture')):
                self.assertIsNone(video_export.bundled_ffmpeg_path())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / '_internal/third_party/ffmpeg/bin/ffmpeg.exe'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'fixture; never executed')
            self.assertEqual(video_export.resolve_ffmpeg(executable), executable)
            original = Path.is_file
            def inspect(path):
                if path == root / 'third_party/ffmpeg/bin/ffmpeg.exe':
                    raise PermissionError('first candidate blocked')
                return original(path)
            with patch.object(runtime, 'resource_root', return_value=root), patch.object(Path, 'is_file', inspect):
                self.assertEqual(video_export.bundled_ffmpeg_path(), executable)

    def test_attach_walk_has_linear_work_and_known_interval_boundaries(self):
        keys = [Keyframe(i * .02, 0, 0, 0, 0, 0, 0, source='attach',
                         attach=AttachKey(handle=1, model='hero')) for i in range(64)]
        conversions = []
        def number(value):
            conversions.append(value)
            return float(value)
        with patch.object(native_effects, 'float', number, create=True):
            self.assertTrue(native_effects.compile_attach(Project(keyframes=keys)))
        self.assertLessEqual(len(conversions), 10 * len(keys))
        # Known interval oracle includes a leading free gap and a track longer than the cameras.
        for version in (1, 2, 3, 4):
            attach = AttachKey(handle=1, model='hero', hide_body=False,
                               point='bone' if version == 2 else 'eyes',
                               bone='head' if version == 2 else '',
                               orientation_mode='rigid' if version == 4 else 'legacy')
            keys = [Keyframe(.25, 0, 0, 0, 0, 0, 0, source='attach', attach=attach,
                             source_blend=.01 if version >= 3 else 0),
                    Keyframe(.5, 0, 0, 0, 0, 0, 0)]
            project = Project(keyframes=keys, tracks=[CvarTrack('r_depth_of_field', [TrackKey(1, 1)])])
            blob = native_effects.compile_attach(project)
            self.assertEqual(struct.unpack_from('<8s3I', blob), (b'DLYATT01', version, 0, 3))
            stride = {1: 96, 2: 104, 3: 112, 4: 112}[version]
            intervals = [struct.unpack_from('<2dI', blob, 20 + i * stride) for i in range(3)]
            self.assertEqual(intervals, [(0, .25, 0), (.25, .5, 9 if version == 4 else 1), (.5, 1, 0)])

    def test_normals_options_are_finite_and_smoothing_is_integer(self):
        record = SimpleNamespace(projection=(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -1, 0))
        depth = np.ones((3, 3))
        for name in ('step_relative', 'step_absolute'):
            for value in (-1, float('nan'), float('inf'), True):
                with self.subTest(option=name, value=value), self.assertRaises(ValueError):
                    depth_normals.reconstruct_normals(record, depth, **{name: value})
        for value in (-1, .5, True):
            with self.subTest(smoothing=value), self.assertRaises(ValueError):
                depth_normals.reconstruct_normals(record, depth, smoothing=value)
        result = depth_normals.reconstruct_normals(record, depth, smoothing=1)
        self.assertTrue(result.valid.all())
        np.testing.assert_allclose(result.normals[:, :, 2], 1)

    def test_exr_scanline_cannot_alias_header_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture.exr'
            path.write_bytes(depth_frame())
            self.assertEqual(depth_exr.read_distances(path).tolist(), [[9, 10]])
            path.write_bytes(depth_frame(alias_header=True))
            with self.assertRaises(depth_exr.DepthExrError):
                depth_exr.read_distances(path)
