"""Player layer protocol tests: schema parsing, marker format, bundle reader."""
import struct
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from dolly import player_layer


LAYOUT = """
Class 'CGameSceneNode

          OuterOffset Offset   Class                                    Field                                    Type

          0          8        CGameSceneNode                           Unaccounted                              VTable?

          0          408      CGameSceneNode                           m_pOwner                                 CGameSceneNode*

          0          160      CGameSceneNode                           m_nodeToWorld                            CTransform

          +          0        CGameSceneNode                           m_nFlags                                 int32
"""


class OwnerOffsetTests(unittest.TestCase):
    def test_reads_owner_offset_from_schema_text(self):
        self.assertEqual(player_layer.parse_owner_offset(LAYOUT), 408)

    def test_missing_field_is_unavailable(self):
        with self.assertRaises(RuntimeError):
            player_layer.parse_owner_offset("Class CGameSceneNode\n")


class MarkerTests(unittest.TestCase):
    def test_players_only_marker_has_no_fixed_handles(self):
        self.assertEqual(player_layer.marker_text(408, 120), "color-sequence-v3 0 120 players 408 816\n")

    def test_scene_node_back_link_offset_is_parsed(self):
        layout = ("          0          816      C_BaseEntity                             "
                  "m_pGameSceneNode                         CGameSceneNode*\n")
        self.assertEqual(player_layer.parse_scene_node_offset(layout), 816)

    def test_marker_ranges_are_enforced(self):
        for offset, frames in ((0, 120), (0x8000, 120), (408, 1), (408, player_layer.MAX_FRAMES + 1)):
            with self.subTest(offset=offset, frames=frames):
                with self.assertRaises(ValueError):
                    player_layer.marker_text(offset, frames)

    def test_capture_clock_includes_slow_motion_and_rejects_invalid_speed(self):
        request = "a" * 32
        text = player_layer.marker_text(48, 1200, fps=600, request_id=request, speed=.5)
        self.assertIn("fps 600 request " + request + " speed 0.5", text)
        for speed in (0, float("nan"), float("inf"), 5):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                player_layer.marker_text(48, 1200, request_id=request, speed=speed)
        with self.assertRaises(ValueError):
            player_layer.marker_text(48, 1200, fps=600)

    def test_small_owner_offsets_are_legitimate(self):
        # The September 17 update moved CGameSceneNode.m_pOwner to offset 48.
        self.assertEqual(player_layer.marker_text(48, 120), "color-sequence-v3 0 120 players 48 816\n")
        layout = ("          0          48       CGameSceneNode                           "
                  "m_pOwner                                 CEntityInstance*\n")
        self.assertEqual(player_layer.parse_owner_offset(layout), 48)

    def test_capture_protocol_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            deployment = Path(folder)
            marker = player_layer.begin_capture(deployment, 408, 8)
            self.assertRegex(marker.read_text(encoding="ascii"),
                             r"^color-sequence-v3 0 8 players 408 816 fps 60 request [0-9a-f]{32}\n$")
            self.assertEqual(player_layer.capture_status(deployment), "")
            request_id = marker.read_text().split()[-1]
            result = "complete\nrequest " + request_id
            (deployment / player_layer.STATUS_NAME).write_text(result, encoding="ascii")
            self.assertEqual(player_layer.wait_for_capture(deployment, timeout=1), result)

    def test_stop_request_is_written_and_a_new_capture_clears_it(self):
        with tempfile.TemporaryDirectory() as folder:
            deployment = Path(folder)
            player_layer.begin_capture(deployment, 48, 8)
            stop = player_layer.request_stop(deployment)
            self.assertEqual(stop.name, player_layer.STOP_NAME)
            self.assertTrue(stop.is_file())
            player_layer.begin_capture(deployment, 48, 8)
            self.assertFalse(stop.exists())

    def test_new_take_rejects_old_completion_and_supports_600_fps(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            first = player_layer.begin_capture(d, 48, 200, fps=600).read_text()
            old_id = first.split()[-1]
            (d/player_layer.STATUS_NAME).write_text("complete\nrequest " + old_id)
            self.assertTrue(player_layer.capture_status(d).startswith("complete"))
            second = player_layer.begin_capture(d, 48, 200, fps=600).read_text()
            self.assertNotEqual(first, second)
            self.assertIn("fps 600", second)
            self.assertEqual(player_layer.capture_status(d), "")
            (d/player_layer.STATUS_NAME).write_text("armed\nrequest " + second.split()[-1])
            player_layer.wait_until_armed(d, timeout=.1)

    def test_cleanup_keeps_movie_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)/"deploy"; d.mkdir()
            layer = Path(folder)/"players"; layer.mkdir()
            previews = layer/"capture"; previews.mkdir()
            for p in (d/player_layer.BUNDLE_NAME, previews/"native-player-layer-000.png",
                      layer/"players.mp4", layer/"players.mov", previews/"personal.png"):
                p.write_bytes(b"data")
            self.assertEqual(player_layer.cleanup_capture(d, layer), [])
            self.assertTrue((layer/"players.mov").exists())
            self.assertTrue((previews/"personal.png").exists())
            self.assertFalse((layer/"players.mp4").exists())
            self.assertFalse((d/player_layer.BUNDLE_NAME).exists())


class ReferenceFramesTests(unittest.TestCase):
    def test_uses_measured_color_length_and_rejects_unrelated_or_incomplete_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            color = Path(root) / "color.mp4"
            color.write_bytes(b"test output")
            metadata = color.parent / "shot.json"
            # Observed three-second / 30FPS / half-speed take has 182, not 180.
            data = {"format": "deadlock-dolly-shot", "version": 1, "video_file": color.name,
                    "fps": 30, "fixed_step": True, "frames_written": 182,
                    "first_frame": 0, "last_frame": 181}
            metadata.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(player_layer.reference_frame_count(color, 30), 182)
            for field, value in [("video_file", "other.mp4"), ("fps", 60),
                                 ("frames_written", True), ("frames_written", 182.0),
                                 ("last_frame", 180), ("first_frame", 1),
                                 ("fixed_step", False)]:
                with self.subTest(field=field, value=value):
                    metadata.write_text(json.dumps({**data, field: value}), encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "completed color take"):
                        player_layer.reference_frame_count(color, 30)
            for content in ["{}", "[]", "broken", " " * (128 * 1024 + 1)]:
                metadata.write_text(content, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    player_layer.reference_frame_count(color, 30)
            metadata.unlink()
            with self.assertRaises(RuntimeError):
                player_layer.reference_frame_count(color, 30)
            self.assertEqual(color.read_bytes(), b"test output")


class StorageTests(unittest.TestCase):
    def test_long_high_fps_capture_is_refused_before_writing_if_space_is_short(self):
        frames = 6000
        raw = frames * (64 + 2560 * 1440 * 8)
        with patch("dolly.player_layer.shutil.disk_usage", return_value=SimpleNamespace(free=raw)):
            with self.assertRaisesRegex(RuntimeError, "temporary space"):
                player_layer.check_capture_space(Path("unused"), 2560, 1440, frames)
        with patch("dolly.player_layer.shutil.disk_usage", return_value=SimpleNamespace(free=raw+1024**3)):
            self.assertEqual(player_layer.check_capture_space(Path("unused"), 2560, 1440, frames), raw)

    def test_unknown_capture_dimensions_cannot_authorize_a_long_capture(self):
        with self.assertRaisesRegex(RuntimeError, "dimensions"):
            player_layer.check_capture_space(Path("unused"), None, 1440, 6000)


class BundleTests(unittest.TestCase):
    def bundle(self, width, height, pixels, magic=None):
        folder = tempfile.mkdtemp()
        path = Path(folder) / player_layer.BUNDLE_NAME
        header = struct.pack("<4Q", magic if magic is not None else player_layer.BUNDLE_MAGIC,
                             width, height, 1) + struct.pack("<4Q", 0, 0, 0, 8)
        path.write_bytes(header + pixels)
        return path

    def test_iter_frames_reads_reviewed_bundle(self):
        width, height = 4, 2
        pixels = struct.pack("<%de" % (width * height * 4), *([0.0] * (width * height * 4)))
        frames = list(player_layer.iter_frames(self.bundle(width, height, pixels)))
        self.assertEqual(frames, [(width, height, pixels)])

    def test_iter_frames_rejects_foreign_bundle(self):
        path = self.bundle(4, 2, b"\0" * 32, magic=0x1234)
        with self.assertRaises(RuntimeError):
            list(player_layer.iter_frames(path))


class CaptureDiagnosticsTests(unittest.TestCase):
    def test_preserves_bounded_reports_without_images_or_raw_events(self):
        with tempfile.TemporaryDirectory() as folder:
            deployment = Path(folder) / "deployment"
            session = Path(folder) / "session"
            deployment.mkdir()
            session.mkdir()
            (deployment / player_layer.STATUS_NAME).write_text("complete")
            (deployment / "dolly_owner_draws.txt").write_bytes(b"x" * 100_000)
            for name in (player_layer.BUNDLE_NAME, "dolly_owner_events.bin", "private.txt"):
                (deployment / name).write_bytes(b"not included")
            player_layer.preserve_diagnostics(deployment, session)
            self.assertEqual({p.name for p in session.iterdir()},
                             {player_layer.STATUS_NAME, "dolly_owner_draws.txt"})
            self.assertEqual((session / "dolly_owner_draws.txt").stat().st_size,
                             player_layer.DIAGNOSTIC_LIMIT)
            # A subsequent cleanup/report after removal keeps the saved evidence.
            (deployment / player_layer.STATUS_NAME).unlink()
            player_layer.preserve_diagnostics(deployment, session)
            self.assertEqual((session / player_layer.STATUS_NAME).read_text(), "complete")


if __name__ == "__main__":
    unittest.main()
