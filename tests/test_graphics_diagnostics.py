"""Optional renderer observations survive failure without affecting controls."""
import struct
import unittest

from dolly import graphics_diagnostics as gfx
from dolly import native_bridge as nb


def packet(sample=1, *, sequence=2, state=1, flags=127, abi=1):
    return gfx.WIRE.pack(gfx.MAGIC, sequence, abi, state, flags,
                         sample, 1000 * sample, 100, 101, 12, 1, 1, 0, 0,
                         42, 128, 50, 0, 49, 98, 100, 99, 101, 0x4d6000,
                         b"a" * 64, b"Read-only renderer sample")


class ClosedMemory(bytearray):
    closed = False

    def __getitem__(self, key):
        if self.closed:
            raise ValueError("mapping is closed")
        return super().__getitem__(key)

    def close(self):
        self.closed = True


class GraphicsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.memory = ClosedMemory(nb.MAPPING_BYTES)
        self.now = 0.0
        self.bridge = nb.NativeBridge.create(
            mapping_factory=lambda name, size: self.memory,
            clock=lambda: self.now, sleep=self.advance, start_heartbeat=False,
            editor_pid=1001, token="a" * 32)
        self.addCleanup(self.bridge.close)

    def advance(self, value):
        self.now += value

    def publish(self, **values):
        self.memory[gfx.OFFSET:gfx.OFFSET + gfx.WIRE.size] = packet(**values)

    def test_optional_wire_has_no_overlap_with_camera_or_editor_status(self):
        from dolly import editor_wire
        self.assertEqual(gfx.WIRE.size, 512)
        self.assertGreaterEqual(gfx.OFFSET, nb.CONTROL_BYTES + nb.STATUS.size)
        self.assertLessEqual(gfx.OFFSET + gfx.WIRE.size, editor_wire.STATUS_OFFSET)
        sample = gfx.unpack(packet())
        self.assertEqual(sample["pending_count"], 42)
        self.assertEqual(sample["retirement_frame"], 98)
        self.assertEqual(sample["execution_frame"], 100)
        self.assertEqual(sample["head_buffer_frame"], 99)
        self.assertEqual(sample["tail_buffer_frame"], 101)

    def test_absent_or_unsupported_probe_does_not_prevent_camera_status(self):
        self.assertEqual(self.bridge.status()["state"], "starting")
        self.assertIsNone(self.bridge.diagnostics()["graphics"]["latest"])
        self.publish(state=2, flags=0)
        sample = self.bridge.graphics_diagnostics()["latest"]
        self.assertEqual(sample["state"], "unsupported")
        self.assertIsNone(sample["pending_count"])
        self.assertIsNone(sample["retirement_frame"])
        self.assertEqual(self.bridge.status()["state"], "starting")

    def test_odd_or_invalid_probe_preserves_last_sample_and_controls(self):
        self.publish()
        self.bridge.graphics_diagnostics()
        for values in ({"sequence": 3}, {"abi": 2}, {"state": 99}, {"flags": 128}):
            self.publish(sample=2, **values)
            self.advance(1)
            self.assertEqual(self.bridge.status()["state"], "starting")
            result = self.bridge.graphics_diagnostics()
            self.assertEqual(result["latest"]["sample"], 1)
            self.assertTrue(result["read_error"])

    def test_normal_polling_samples_at_most_once_per_second_and_never_writes(self):
        self.publish(sample=1)
        before = bytes(self.memory)
        self.bridge.status()
        self.publish(sample=2)
        for _ in range(10):
            self.bridge.status()
        self.assertEqual(len(self.bridge._graphics_samples), 1)
        self.advance(1)
        self.bridge.status()
        self.assertEqual(len(self.bridge._graphics_samples), 2)
        # Only the simulated native publication changed memory.
        expected = bytearray(before)
        expected[gfx.OFFSET:gfx.OFFSET + gfx.WIRE.size] = packet(sample=2)
        self.assertEqual(self.memory, expected)

    def test_history_is_bounded_deduplicated_and_copied(self):
        for sample in range(130):
            self.publish(sample=sample + 1)
            self.bridge.graphics_diagnostics()
        result = self.bridge.graphics_diagnostics()
        self.assertEqual(len(result["samples"]), 120)
        self.assertEqual(result["samples"][0]["sample"], 11)
        result["latest"]["pending_count"] = 99999
        self.assertEqual(self.bridge.graphics_diagnostics()["latest"]["pending_count"], 42)

    def test_final_graphics_sample_survives_mapping_close_after_game_crash(self):
        self.publish(sample=7)
        self.bridge.close()
        self.assertTrue(self.memory.closed)
        result = self.bridge.diagnostics()
        self.assertEqual(result["state"], "unavailable")
        self.assertTrue(result["graphics"]["cached_after_close"])
        self.assertEqual(result["graphics"]["latest"]["sample"], 7)


if __name__ == "__main__":
    unittest.main()
