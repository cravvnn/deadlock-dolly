"""High-resolution still tests: still shot, launch sizes, bundle stills, assembly."""
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from dolly import player_layer, screenshot
from dolly.editor_wire import EXTRA_ACTIONS
from dolly.path import Keyframe, Project
from dolly.replays import parse_launch_options


def read_png(path):
    """Minimal PNG reader for the files this module writes (filter 0 only)."""
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset, idat, header = 8, b"", None
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">2I5B", body)
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height, depth, color = header[:4]
    channels = {0: 1, 6: 4}[color]
    stride = width * channels * depth // 8
    raw = zlib.decompress(idat)
    rows = []
    for y in range(height):
        row = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert row[0] == 0
        rows.append(row[1:])
    return width, height, depth, color, rows


def bundle_frame(width, height, pixels, draws=1):
    header = struct.pack("<8Q", player_layer.BUNDLE_MAGIC, width, height, draws, 0, 0, 0, 8)
    return header + struct.pack("<%de" % (width * height * 4), *pixels)


class StillProjectTests(unittest.TestCase):
    def test_holds_the_captured_pose_from_the_captured_tick(self):
        base = Project(name="Shot", tick_rate=64.0, confetti_enabled=True)
        key = Keyframe(3.5, 1, 2, 3, 10, 20, 5, fov=70, source="attach", lens_scale=.5,
                       source_blend=1.0)
        key.attach = None
        project = screenshot.still_project(base, key, 4321)
        self.assertEqual(project.start_tick, 4321)
        self.assertEqual([k.time for k in project.keyframes], [0.0, screenshot.STILL_DURATION])
        for frame in project.keyframes:
            self.assertEqual((frame.x, frame.y, frame.z, frame.pitch, frame.yaw, frame.roll),
                             (1, 2, 3, 10, 20, 5))
            self.assertEqual((frame.fov, frame.lens_scale, frame.source), (70, .5, "free"))
        self.assertTrue(project.confetti_enabled)
        self.assertEqual(project.tracks, [])

    def test_still_records_the_configured_frame_count(self):
        frames = math.ceil(screenshot.STILL_DURATION * screenshot.STILL_FPS / screenshot.STILL_SPEED - 1e-6)
        self.assertEqual(frames, screenshot.STILL_FRAMES)
        self.assertLess(screenshot.SETTLED_INDEX, frames)
        self.assertGreaterEqual(frames, player_layer.MIN_FRAMES)

    def test_rejects_missing_tick(self):
        with self.assertRaises(ValueError):
            screenshot.still_project(Project(), Keyframe(0, 0, 0, 0, 0, 0, 0), None)


class RenderSizeTests(unittest.TestCase):
    def test_sets_windowed_size_and_keeps_other_options(self):
        text = screenshot.apply_render_size("-fullscreen -refresh 144 -w 1920 -h 1080", (7680, 4320))
        self.assertEqual(text, "-refresh 144 -windowed -noborder -w 7680 -h 4320")
        parse_launch_options(text)

    def test_game_window_option_only_drops_the_size(self):
        self.assertEqual(screenshot.apply_render_size("-windowed -w 7680 -h 4320", None), "-windowed")

    def test_label_round_trip(self):
        for label, size in screenshot.RENDER_SIZES:
            self.assertEqual(screenshot.render_size_label(screenshot.apply_render_size("", size)), label)
        self.assertEqual(screenshot.render_size_label("-w 1234 -h 999"), screenshot.RENDER_SIZE_LABELS[0])

    def test_rejects_oversized(self):
        with self.assertRaises(ValueError):
            screenshot.apply_render_size("", (16384, 8192))


class BundleStillTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_settled_frame_is_selected_and_written(self):
        width, height = 3, 2
        empty = [0.0] * (width * height * 4)
        # Frame 1: one opaque red pixel, one half-covered premultiplied white.
        settled = list(empty)
        settled[0:4] = [1.0, 0.0, 0.0, 1.0]
        settled[(1 * width + 2) * 4:(1 * width + 2) * 4 + 4] = [.5, .5, .5, .5]
        bundle = self.folder / "bundle.bin"
        bundle.write_bytes(bundle_frame(width, height, empty) + bundle_frame(width, height, settled))
        w, h, pixels = screenshot.read_bundle_frame(bundle, 1)
        alpha, isolated = screenshot.write_hero_stills(w, h, pixels, self.folder)

        aw, ah, adepth, acolor, rows = read_png(alpha)
        self.assertEqual((aw, ah, adepth, acolor), (3, 2, 16, 0))
        values = [struct.unpack(">3H", row) for row in rows]
        self.assertEqual(values, [(65535, 0, 0), (0, 0, 32768)])

        iw, ih, idepth, icolor, rows = read_png(isolated)
        self.assertEqual((iw, ih, idepth, icolor), (3, 2, 8, 6))
        red = rows[0][0:4]
        self.assertEqual((red[1], red[2], red[3]), (0, 0, 255))
        self.assertEqual(red[0], player_layer._preview_channel(1.0))
        white = rows[1][8:12]
        self.assertEqual(white[3], 128)
        self.assertEqual(white[0], player_layer._preview_channel(1.0))
        self.assertEqual(bytes(rows[0][4:12]), bytes(8))

    def test_missing_settled_frame_is_reported(self):
        bundle = self.folder / "bundle.bin"
        bundle.write_bytes(bundle_frame(2, 2, [0.0] * 16))
        with self.assertRaises(RuntimeError):
            screenshot.read_bundle_frame(bundle, 3)

    def test_settled_index_is_clamped_to_the_take(self):
        (self.folder / "shot.json").write_text(json.dumps({"video_file": "a.mkv", "frames_written": 4}))
        self.assertEqual(screenshot.settled_index(self.folder, "a.mkv"), 3)
        (self.folder / "shot.json").write_text(json.dumps({"video_file": "a.mkv", "frames_written": 40}))
        self.assertEqual(screenshot.settled_index(self.folder, "a.mkv"), screenshot.SETTLED_INDEX)
        with self.assertRaises(RuntimeError):
            screenshot.settled_index(self.folder, "b.mkv")


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg is not on PATH")
class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)

    def probe(self, path):
        result = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height,pix_fmt", "-of", "json", str(path)],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)["streams"][0]

    def test_builds_stills_and_removes_intermediates(self):
        path = self.parent / "Dolly_Still_x.mkv"
        folder = path.with_suffix("")
        folder.mkdir()
        video = folder / path.name
        # Same container/codec/pixel format as the native lossless take.
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=60",
                        "-frames:v", "12", "-pix_fmt", "bgra", "-c:v", "ffv1", str(video)], check=True)
        (folder / "shot.json").write_text(json.dumps({"video_file": path.name, "frames_written": 12}))
        (folder / "depth" / "exr").mkdir(parents=True)
        (folder / "depth" / "exr" / ("%08d.exr" % screenshot.SETTLED_INDEX)).write_bytes(b"exr")
        (folder / "players").mkdir()
        pixels = [0.0] * (64 * 36 * 4)
        pixels[3::4] = [1.0] * (64 * 36)
        screenshot.write_hero_stills(64, 36, struct.pack("<%de" % len(pixels), *pixels), folder)
        run = screenshot.StillRun(None, SimpleNamespace(path=path), True)

        outputs = screenshot.finish(Path(FFMPEG), run)

        self.assertEqual(sorted(p.name for p in outputs), sorted(screenshot.OUTPUT_NAMES))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(screenshot.OUTPUT_NAMES))
        self.assertEqual(self.probe(folder / "plate.png"), {"width": 64, "height": 36, "pix_fmt": "rgb24"})
        self.assertEqual(self.probe(folder / "hero_rgba.png"),
                         {"width": 64, "height": 36, "pix_fmt": "rgba64be"})
        self.assertEqual((folder / "depth.exr").read_bytes(), b"exr")

    def test_merged_alpha_keeps_16_bit_coverage(self):
        plate, alpha, output = (self.parent / n for n in ("p.png", "hero_alpha.png", "o.png"))
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=gray:s=4x2",
                        "-frames:v", "1", "-pix_fmt", "rgb24", str(plate)], check=True)
        coverage = [0.5, 1.0, 0.25, 0.0, 0.75, 0.125, 1.0, 0.0]
        pixels = []
        for value in coverage:
            pixels += [value * .5, value * .5, value * .5, value]
        screenshot.write_hero_stills(4, 2, struct.pack("<32e", *pixels), self.parent)
        screenshot.merge_hero_rgba(Path(FFMPEG), plate, alpha, output)
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(output), "-f", "rawvideo",
                              "-pix_fmt", "rgba64le", "-"], capture_output=True, check=True).stdout
        merged = [struct.unpack_from("<4H", raw, i * 8)[3] for i in range(8)]
        self.assertEqual(merged, [round(v * 65535) for v in coverage])

    def test_failure_keeps_the_take(self):
        path = self.parent / "Dolly_Still_y.mkv"
        folder = path.with_suffix("")
        folder.mkdir()
        (folder / "shot.json").write_text(json.dumps({"video_file": path.name, "frames_written": 12}))
        run = screenshot.StillRun(None, SimpleNamespace(path=path), False)
        with self.assertRaises(RuntimeError):
            screenshot.finish(Path(FFMPEG), run)
        self.assertTrue((folder / "shot.json").is_file())


class EditorActionTests(unittest.TestCase):
    def test_screenshot_action_has_the_native_id(self):
        # ACTION_ORDER holds 26 entries; TakeScreenshot is native action 98.
        self.assertEqual(EXTRA_ACTIONS.index("take_screenshot") + 26, 98)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(FFMPEG, "FFmpeg is not on PATH")
class DesktopFlowTests(unittest.TestCase):
    """Players finish and final assembly, with a simulated native capture."""

    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)

    def test_players_finish_then_restore_writes_the_still(self):
        from dolly import screenshot_ui
        deployment = self.parent / "game"
        deployment.mkdir()
        path = self.parent / "Dolly_Still_z.mkv"
        folder = path.with_suffix("")
        (folder / "players").mkdir(parents=True)
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=8x4:rate=60",
                        "-frames:v", "12", "-pix_fmt", "bgra", "-c:v", "ffv1", str(folder / path.name)],
                       check=True)
        (folder / "shot.json").write_text(json.dumps({"video_file": path.name, "frames_written": 12}))
        frames = b""
        for index in range(12):
            value = 1.0 if index == screenshot.SETTLED_INDEX else 0.25
            frames += bundle_frame(8, 4, [0.5, 0.5, 0.5, value] * 32)
        (deployment / player_layer.BUNDLE_NAME).write_bytes(frames)
        (deployment / player_layer.STATUS_NAME).write_text("complete\n")
        options = SimpleNamespace(path=path, ffmpeg_path=Path(FFMPEG))
        restored = []
        app = SimpleNamespace(
            _still_run=screenshot.StillRun(None, options, False), _base_capture=options,
            controller=SimpleNamespace(deployment_directory=lambda: deployment),
            _restore_scene_state=lambda: restored.append(True), _enqueue_log=lambda text: None)

        result = screenshot_ui.finish_player_capture(app, "players")
        self.assertEqual(result["state"], "completed")
        self.assertFalse((deployment / player_layer.BUNDLE_NAME).exists())
        _w, _h, _d, _c, rows = read_png(folder / "hero_alpha.png")
        self.assertEqual(set(rows[0]), {255})  # the settled frame, not its neighbours

        self.assertEqual(screenshot_ui.restore(app), str(folder))
        self.assertEqual(restored, [True])
        self.assertIsNone(app._still_run)
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["hero_alpha.png", "hero_isolated.png", "hero_rgba.png", "plate.png"])


@unittest.skipUnless(FFMPEG, "FFmpeg is not on PATH")
class PlayersTakeContainerTests(unittest.TestCase):
    """A lossless (.mkv) still must not build a players take named .mp4."""

    def test_players_take_matches_the_lossless_color_container(self):
        from unittest.mock import MagicMock, patch
        from dolly.gui import DollyApp
        from dolly.video_export import VideoOptions
        parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        base = VideoOptions(parent / "Dolly_Still_x.mkv", screenshot.STILL_FPS, 20_000_000,
                            codec=screenshot.STILL_CODEC, ffmpeg_path=Path(FFMPEG), fixed_step=True,
                            speed=screenshot.STILL_SPEED, layers=("players",)).validated()
        app = SimpleNamespace(
            _base_capture=base, _pov_project=None, _snapshot=lambda: None,
            controller=MagicMock(deployment_directory=lambda: parent, _request=lambda *_a, **_k: ""),
            video_export=MagicMock(status=lambda: {"width": 64, "height": 36}))
        with patch.object(player_layer, "parse_owner_offset", return_value=408), \
             patch.object(player_layer, "parse_scene_node_offset", return_value=816), \
             patch.object(player_layer, "reference_frame_count", return_value=12), \
             patch.object(player_layer, "check_capture_space", return_value=1):
            DollyApp._start_player_capture(app, "players")
        options = app.video_export.start.call_args.args[0]
        self.assertEqual(options.path, (parent / "Dolly_Still_x" / "players" / "players.mkv").absolute())
        self.assertEqual(options.codec, screenshot.STILL_CODEC)


def frame_meta(times):
    """Players capture metadata: a header record, then (index, time bits) per frame."""
    data = struct.pack("<8Q", 0x314154454d4c4f44, 1, 64, len(times), 0, 0, 0, 0)
    for index, seconds in enumerate(times):
        bits = struct.unpack("<Q", struct.pack("<d", seconds))[0]
        data += struct.pack("<8Q", index, bits, 0, 32, 0, 0, 0, 0)
    return data


@unittest.skipUnless(FFMPEG, "FFmpeg is not on PATH")
class ShortPlayersCaptureTests(unittest.TestCase):
    """The players pass may end a frame short; only the settled frame matters."""

    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)
        self.deployment = self.parent / "game"
        self.deployment.mkdir()
        self.path = self.parent / "Dolly_Still_s.mkv"
        self.folder = self.path.with_suffix("")
        (self.folder / "players").mkdir(parents=True)
        self.color_times = [i * 0.000831604 for i in range(14)]
        (self.folder / "shot.json").write_text(json.dumps({
            "video_file": self.path.name, "frames_written": 14,
            "clock_samples": [{"frame": i, "phase": t, "source": "native"}
                              for i, t in enumerate(self.color_times)]}))
        frames = b"".join(bundle_frame(8, 4, [0.5, 0.5, 0.5, 1.0] * 32) for _ in range(13))
        (self.deployment / player_layer.BUNDLE_NAME).write_bytes(frames)
        (self.deployment / player_layer.STATUS_NAME).write_text(
            screenshot.SHORT_CAPTURE_STATUS + "; keep capture data\nrequest x\n")

    def app(self):
        from dolly import screenshot_ui
        options = SimpleNamespace(path=self.path, ffmpeg_path=Path(FFMPEG))
        return screenshot_ui, SimpleNamespace(
            _still_run=screenshot.StillRun(None, options, False), _base_capture=options,
            controller=SimpleNamespace(deployment_directory=lambda: self.deployment))

    def test_aligned_settled_frame_is_used(self):
        (self.deployment / player_layer.META_NAME).write_bytes(frame_meta(self.color_times[:13]))
        module, app = self.app()
        self.assertEqual(module.finish_player_capture(app, "players")["state"], "completed")
        self.assertTrue((self.folder / "hero_alpha.png").is_file())

    def test_shifted_capture_is_refused(self):
        shifted = self.color_times[1:14]  # frame i holds the color take's frame i+1
        (self.deployment / player_layer.META_NAME).write_bytes(frame_meta(shifted))
        module, app = self.app()
        with self.assertRaises(RuntimeError):
            module.finish_player_capture(app, "players")
        self.assertFalse((self.folder / "hero_alpha.png").exists())
