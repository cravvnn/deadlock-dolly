"""Player layer protocol tests: schema parsing, marker format, bundle reader."""
import struct
import tempfile
import unittest
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
        for offset, frames in ((0, 120), (0x8000, 120), (408, 1), (408, 999)):
            with self.subTest(offset=offset, frames=frames):
                with self.assertRaises(ValueError):
                    player_layer.marker_text(offset, frames)

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
            self.assertEqual(marker.read_text(encoding="ascii"),
                             "color-sequence-v3 0 8 players 408 816\n")
            self.assertEqual(player_layer.capture_status(deployment), "")
            (deployment / player_layer.STATUS_NAME).write_text("complete\n", encoding="ascii")
            self.assertEqual(player_layer.wait_for_capture(deployment, timeout=1), "complete")


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


if __name__ == "__main__":
    unittest.main()
