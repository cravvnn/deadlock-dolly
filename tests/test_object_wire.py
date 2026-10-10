"""The object wire block is bounded, offset-preserving, and rejects torn data."""
import struct
import unittest

from dolly import object_wire as wire
from dolly import editor_wire, native_bridge
from dolly.object_library import MAX_OBJECTS, OBJECT_IDS
from dolly.object_model import PlacedObject


class ObjectWireLayoutTests(unittest.TestCase):
    def test_block_lives_after_legacy_tail_without_overlap(self):
        legacy_end = 2 * 1024 * 1024 + 24576
        self.assertEqual(wire.OBJECT_BASE, legacy_end)
        self.assertEqual(wire.OBJECT_CONFIG_OFFSET, legacy_end)
        self.assertGreaterEqual(wire.OBJECT_STATUS_OFFSET, wire.OBJECT_CONFIG_OFFSET + wire.OBJECT_CONFIG.size)
        self.assertLessEqual(wire.OBJECT_STATUS_OFFSET + wire.OBJECT_STATUS.size, wire.OBJECT_MAPPING_BYTES)
        # Every existing editor block ends before the legacy tail.
        self.assertLess(editor_wire.FRAMING_GRID_OFFSET + editor_wire.FRAMING_GRID.size, legacy_end)

    def test_mapping_must_grow_for_the_object_page(self):
        # The planned mapping size includes the appended page; the current
        # constant is documented to grow in lockstep when the bridge is wired.
        self.assertEqual(wire.OBJECT_MAPPING_BYTES, native_bridge.CONTROL_BYTES + 24576 + 4096)
        self.assertLessEqual(wire.OBJECT_STATUS_OFFSET + wire.OBJECT_STATUS.size, wire.OBJECT_MAPPING_BYTES)

    def test_row_and_config_sizes_are_stable(self):
        rows_off = wire.OBJECT_CONFIG_HEADER.size + wire.OBJECT_CONFIG_DISTANCE.size
        self.assertEqual(wire.OBJECT_CONFIG.size, rows_off + MAX_OBJECTS * wire.OBJECT_ROW.size)


class ObjectWirePackTests(unittest.TestCase):
    def test_empty_and_full_lists_round_trip_bounds(self):
        packed = wire.pack_config(2, [])
        header = wire.OBJECT_CONFIG_HEADER.unpack_from(packed)
        self.assertEqual(header[0], wire.OBJECT_CONFIG_MAGIC)
        self.assertEqual(header[4], 0)  # count
        self.assertEqual(len(packed), wire.OBJECT_CONFIG.size)

    def test_active_preview_selected_and_distance_encoded(self):
        packed = wire.pack_config(5, [], active=True, preview=True, distance=1234.5)
        magic, sequence, abi, flags, count, selected, reserved = wire.OBJECT_CONFIG_HEADER.unpack_from(packed)
        self.assertEqual(flags, 0b11)
        self.assertEqual(selected, -1)
        distance = wire.OBJECT_CONFIG_DISTANCE.unpack_from(
            packed, wire.OBJECT_CONFIG_HEADER.size)[0]
        self.assertEqual(distance, 1234.5)

    def test_row_encoded_with_library_index_and_model_token(self):
        from dolly.native_effects import model_token
        obj = PlacedObject(library_id="barrel", model="models/props/barrel.vmdl",
                           position=[1, 2, 3], angles=[4, 5, 6], scale=1.5)
        packed = wire.pack_config(3, [obj])
        offset = wire.OBJECT_CONFIG_HEADER.size + wire.OBJECT_CONFIG_DISTANCE.size
        index, token, px, py, pz, ax, ay, az, scale = wire.OBJECT_ROW.unpack_from(packed, offset)
        self.assertEqual(index, OBJECT_IDS.index("barrel"))
        self.assertEqual(token, model_token(obj.model))
        self.assertEqual((px, py, pz, ax, ay, az, scale), (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 1.5))

    def test_distance_and_selection_bounds(self):
        obj = PlacedObject()
        for distance in (0, 5, 20001, float("nan"), True):
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                wire.pack_config(2, [obj], distance=distance)
        with self.assertRaises(ValueError):
            wire.pack_config(2, [obj], selected=5)

    def test_unknown_library_id_and_overflow_rejected(self):
        with self.assertRaises(ValueError):
            wire.pack_config(2, [dict(library_id="nope", position=[0, 0, 0], angles=[0, 0, 0], scale=1)])
        with self.assertRaises(ValueError):
            wire.pack_config(2, [dict(library_id="marker", position=[0, 0, 0], angles=[0, 0, 0], scale=1)] * (MAX_OBJECTS + 1))
        with self.assertRaises(ValueError):
            wire.pack_config(2, [dict(library_id="marker", position=[0, 0, 0], angles=[0, 0, 0], scale=100)])

    def test_bad_sequence_rejected(self):
        for value in (True, -1, 2**32):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wire.pack_config(value, [])


class ObjectWireStatusTests(unittest.TestCase):
    def good(self, sequence=2, flags=0b011, active=2, selected=1, error=0):
        return wire.OBJECT_STATUS.pack(wire.OBJECT_STATUS_MAGIC, sequence, 1, flags, active, selected, error)

    def test_zeroed_is_none(self):
        self.assertIsNone(wire.unpack_status(bytes(wire.OBJECT_STATUS.size)))

    def test_valid_round_trip(self):
        result = wire.unpack_status(self.good())
        self.assertTrue(result["available"] and result["active"] and not result["preview"])
        self.assertEqual((result["active_count"], result["selected"]), (2, 1))

    def test_bad_magic_abi_parity_flags_and_bounds_rejected(self):
        for raw in (self.good(sequence=3), self.good(flags=0b1000),
                    wire.OBJECT_STATUS.pack(b"WRONG!!!", 2, 1, 0, 0, -1, 0),
                    wire.OBJECT_STATUS.pack(wire.OBJECT_STATUS_MAGIC, 2, 2, 0, 0, -1, 0),
                    self.good(active=MAX_OBJECTS + 1), self.good(selected=MAX_OBJECTS)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                wire.unpack_status(raw)

    def test_wrong_size_rejected(self):
        with self.assertRaises(ValueError):
            wire.unpack_status(b"\0" * 8)


if __name__ == "__main__":
    unittest.main()
