"""Optional input observations remain coherent and cannot stop camera controls."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dolly import input_diagnostics as inp
from dolly import native_bridge as nb


def packet(*, sequence=2, abi=1, flags=511, packets=101):
    return inp.WIRE.pack(inp.MAGIC, sequence, abi, flags, 0x130,
                         packets, 99, 97, 7, 83, 4, 2, 0x123456789,
                         5)


class Memory(bytearray):
    closed = False
    on_read = None

    def __getitem__(self, key):
        if self.closed:
            raise ValueError("mapping is closed")
        result = super().__getitem__(key)
        if self.on_read:
            self.on_read(key)
        return result

    def close(self):
        self.closed = True


class InputDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(nb.MAPPING_BYTES)
        self.now = 0.0
        self.bridge = nb.NativeBridge.create(
            mapping_factory=lambda name, size: self.memory,
            clock=lambda: self.now, sleep=self.advance, start_heartbeat=False,
            editor_pid=1001, token="a" * 32)
        self.addCleanup(self.bridge.close)

    def advance(self, value):
        self.now += value

    def publish(self, **values):
        self.memory[inp.OFFSET:inp.OFFSET + inp.WIRE.size] = packet(**values)

    def test_native_offsets_and_unsigned_counters_do_not_overlap_other_blocks(self):
        from dolly import editor_wire, graphics_diagnostics
        self.assertEqual(inp.OFFSET, nb.CONTROL_BYTES + 3584)
        self.assertEqual(inp.WIRE.size, 128)
        self.assertGreaterEqual(inp.OFFSET, editor_wire.STATUS_OFFSET + editor_wire.STATUS_BYTES)
        self.assertGreaterEqual(inp.OFFSET, graphics_diagnostics.OFFSET + graphics_diagnostics.WIRE.size)
        self.assertLessEqual(inp.OFFSET + inp.WIRE.size, nb.MAPPING_BYTES)
        data = bytearray(128)
        data[:8] = b"DLYINP01"
        for offset, value in ((8, 2), (12, 1), (16, 511), (20, 0x130), (88, 5)):
            struct.pack_into("<I", data, offset, value)
        fields = ("raw_mouse_packets", "relative_mouse_packets", "accepted_motion_packets",
                  "legacy_mouse_moves", "consumed_motion_frames", "cursor_syncs",
                  "cursor_failures", "registration_target")
        for index in range(8):
            struct.pack_into("<Q", data, 24 + 8 * index, (1 << 48) + index)
        sample = inp.unpack(bytes(data))
        for index, field in enumerate(fields):
            self.assertEqual(sample[field], (1 << 48) + index)
        self.assertEqual(sample["registration_flags"], 0x130)
        self.assertEqual(sample["last_cursor_error"], 5)
        self.assertEqual(sample["sequence"], 2)

    def test_each_native_flag_has_its_own_boolean(self):
        flags = ("raw_registration_known", "raw_mouse_registered", "target_is_game_window",
                 "ready", "manual_active", "focused", "flight_owner", "cursor_clipped", "no_legacy")
        for index, expected in enumerate(flags):
            with self.subTest(flag=expected):
                sample = inp.unpack(packet(flags=1 << index))
                for name in flags:
                    self.assertIs(sample[name], name == expected)

    def test_old_helper_has_no_input_block_and_camera_keeps_starting(self):
        self.assertIsNone(inp.unpack(bytes(inp.WIRE.size)))
        result = self.bridge.diagnostics()
        self.assertEqual(result["state"], "starting")
        self.assertIsNone(result["input"]["latest"])
        self.assertEqual(result["input"]["samples"], [])
        self.assertEqual(result["input"]["read_error"], "")

    def test_invalid_or_busy_optional_block_preserves_last_sample_and_camera(self):
        self.publish()
        self.bridge.input_diagnostics()
        for values in ({"sequence": 3}, {"abi": 2}, {"flags": 512}):
            with self.subTest(values=values):
                self.publish(packets=202, **values)
                self.advance(1)
                self.assertEqual(self.bridge.status()["state"], "starting")
                result = self.bridge.input_diagnostics()
                self.assertEqual(result["latest"]["raw_mouse_packets"], 101)
                self.assertTrue(result["read_error"])
        self.publish(sequence=4, packets=202)
        result = self.bridge.input_diagnostics()
        self.assertEqual(result["latest"]["raw_mouse_packets"], 202)
        self.assertEqual(result["read_error"], "")

    def test_decoder_refuses_wrong_size_magic_and_odd_sequence(self):
        for data in (packet()[:-1], packet() + b"\0", b"BADMAGIC" + packet()[8:], packet(sequence=3)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inp.unpack(data)

    def test_torn_snapshot_retries_and_continuous_racing_is_bounded(self):
        self.publish()
        reads = []

        def change_once(key):
            if isinstance(key, slice) and key.start == inp.OFFSET:
                reads.append(key)
                self.memory.on_read = None
                self.publish(sequence=4, packets=202)

        self.memory.on_read = change_once
        self.assertEqual(self.bridge.input_diagnostics()["latest"]["raw_mouse_packets"], 202)
        self.assertEqual(len(reads), 1)
        reads.clear()

        def change_always(key):
            if isinstance(key, slice) and key.start == inp.OFFSET:
                reads.append(key)
                self.publish(sequence=4 + 2 * len(reads), packets=303)

        self.memory.on_read = change_always
        result = self.bridge.input_diagnostics()
        self.memory.on_read = None
        self.assertEqual(len(reads), 2)
        self.assertEqual(result["latest"]["raw_mouse_packets"], 202)
        self.assertTrue(result["read_error"])
        self.assertEqual(self.now, 0.0)

    def test_normal_polling_is_throttled_and_read_only(self):
        self.publish()
        self.bridge.status()
        self.publish(sequence=4, packets=202)
        expected = bytes(self.memory)
        for _ in range(10):
            self.bridge.status()
        self.assertEqual(len(self.bridge._input_samples), 1)
        self.advance(1)
        self.bridge.status()
        self.assertEqual(len(self.bridge._input_samples), 2)
        self.assertEqual(bytes(self.memory), expected)

    def test_export_history_is_bounded_deduplicated_and_copied(self):
        for sequence in range(2, 262, 2):
            self.publish(sequence=sequence)
            self.bridge.input_diagnostics()
        result = self.bridge.diagnostics()
        self.assertEqual(len(result["input"]["samples"]), 120)
        self.assertEqual(result["input"]["samples"][0]["sequence"], 22)
        self.assertEqual(json.loads(json.dumps(result))["input"]["latest"]["sequence"], 260)
        result["input"]["latest"]["raw_mouse_packets"] = 0
        result["input"]["samples"][0]["raw_mouse_packets"] = 0
        cached = self.bridge.input_diagnostics()
        self.assertEqual(cached["latest"]["raw_mouse_packets"], 101)
        self.assertEqual(cached["samples"][0]["raw_mouse_packets"], 101)

    def test_observation_time_uses_bridge_clock_and_only_changes_for_new_snapshot(self):
        self.advance(123.25)
        self.publish()
        first = self.bridge.input_diagnostics()["latest"]
        self.assertEqual(first["sampled_at"], 123.25)
        self.advance(2)
        self.assertEqual(self.bridge.input_diagnostics()["latest"], first)
        self.publish(sequence=4, packets=202)
        result = self.bridge.input_diagnostics()
        self.assertEqual(result["latest"]["sampled_at"], 125.25)
        self.assertEqual(result["samples"][0]["sampled_at"], 123.25)
        self.assertEqual(result["latest"]["raw_mouse_packets"], 202)

    def test_close_captures_final_input_and_preserves_it_after_mapping_closes(self):
        self.advance(123.25)
        self.publish(sequence=8, packets=707)
        self.bridge.close()
        self.advance(10)
        self.assertTrue(self.memory.closed)
        result = self.bridge.diagnostics()
        self.assertEqual(result["state"], "unavailable")
        self.assertTrue(result["input"]["cached_after_close"])
        self.assertEqual(result["input"]["latest"]["raw_mouse_packets"], 707)
        self.assertEqual(result["input"]["latest"]["sampled_at"], 123.25)

    def test_controller_archive_includes_cached_native_input_after_close(self):
        from dolly.controller import Controller
        self.publish(sequence=8, packets=707)
        self.bridge.close()
        controller = Controller()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(controller, "_native_bridge", return_value=self.bridge), \
                patch("dolly.controller.ROOT", Path(directory)):
            destination = controller.export_diagnostics(Path(directory) / "diagnostics.zip")
            with zipfile.ZipFile(destination) as archive:
                report = json.loads(archive.read("diagnostics.json"))
        diagnostic = report["native_editor_runtime"]["input"]
        self.assertTrue(diagnostic["cached_after_close"])
        self.assertEqual(diagnostic["latest"]["raw_mouse_packets"], 707)
        self.assertEqual(diagnostic["latest"]["last_cursor_error"], 5)


if __name__ == "__main__":
    unittest.main()
