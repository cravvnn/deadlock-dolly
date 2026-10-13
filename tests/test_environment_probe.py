import json
import struct
import unittest
from unittest.mock import patch, Mock

from dolly import environment_probe as wire
from dolly.native_bridge import NativeBridge, MAPPING_BYTES
from dolly.native_errors import NativeBridgeError
from tools.generate_environment_shader_contracts import generate


class EnvironmentProbeTests(unittest.TestCase):
    def setUp(self):
        self.memory = bytearray(MAPPING_BYTES)
        self.bridge = NativeBridge.create(mapping_factory=lambda name, size: self.memory,
            editor_pid=123, token="e" * 32, start_heartbeat=False)
        self.bridge.bind_game(456)
        self.probe = wire.EnvironmentProbe(self.bridge)
        self.addCleanup(self.bridge.close)

    def publish(self, *, sequence=2, magic=b"DLYENVS1", abi=1, payload=b'{"draws":100}'):
        data = wire.STATUS.pack(magic, sequence, abi, self.probe.command, 2, payload)
        self.memory[wire.STATUS_OFFSET:wire.STATUS_OFFSET + len(data)] = data

    def test_start_stop_counters_and_seqlock_preserve_other_blocks(self):
        before = bytes(self.memory)
        self.assertEqual(self.probe.start(), 1)
        request = wire.REQUEST.unpack_from(self.memory, wire.REQUEST_OFFSET)
        self.assertEqual(request[0], b"DLYENVR1")
        self.assertEqual(request[1] & 1, 0)
        self.assertEqual(request[2:], (1, 1, 1, 123, 456, 8, 100000, 5000, 0))
        self.assertEqual(before[:wire.REQUEST_OFFSET], self.memory[:wire.REQUEST_OFFSET])
        self.assertEqual(before[wire.REQUEST_OFFSET + wire.REQUEST.size:],
                         self.memory[wire.REQUEST_OFFSET + wire.REQUEST.size:])
        replacement = wire.EnvironmentProbe(self.bridge)
        self.assertEqual(replacement.stop(), 2)
        request = wire.REQUEST.unpack_from(self.memory, wire.REQUEST_OFFSET)
        self.assertEqual(request[4:], (0, 123, 456, 0, 0, 0, 0))

    def test_invalid_bounds_do_not_write(self):
        before = bytes(self.memory)
        for name, value in (("frames", 0), ("frames", 65), ("frames", True), ("draws", 100001),
                             ("milliseconds", 10001), ("milliseconds", 1.5)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.probe.start(**{name: value})
        self.assertEqual(bytes(self.memory), before)

    def test_closed_or_unowned_do_not_write(self):
        self.bridge.game_pid = 0
        with self.assertRaises(NativeBridgeError): self.probe.start()
        self.bridge.game_pid = 456
        self.bridge.close()
        before = bytes(self.memory)
        with self.assertRaises(NativeBridgeError): self.probe.start()
        with self.assertRaises(NativeBridgeError): self.probe.stop()
        with self.assertRaises(NativeBridgeError): self.probe.status()
        self.assertEqual(bytes(self.memory), before)

    def test_unpublished_malformed_torn_and_valid_status(self):
        self.assertIsNone(self.probe.status())
        self.publish(abi=2)
        self.assertIsNone(self.probe.status())
        self.publish(sequence=3)
        with self.assertRaisesRegex(NativeBridgeError, "busy"): self.probe.status()
        self.publish(payload=b"bad json")
        with self.assertRaisesRegex(NativeBridgeError, "Invalid"): self.probe.status()
        self.publish()
        with patch.object(self.bridge, "_load_sequence", side_effect=[2, 4] * 8):
            with self.assertRaisesRegex(NativeBridgeError, "busy"): self.probe.status()
        self.assertEqual(self.probe.status()["details"], {"draws": 100})

    def test_counter_overflow_is_rejected(self):
        self.probe.start()
        struct.pack_into("<I", self.memory, wire.REQUEST_OFFSET + 16, 0xffffffff)
        with self.assertRaisesRegex(NativeBridgeError, "exhausted"): self.probe.start()

    def test_extension_is_after_all_existing_editor_blocks(self):
        from dolly import editor_wire, native_capabilities
        legacy_end = 2 * 1024 * 1024 + 24576
        self.assertGreaterEqual(wire.REQUEST_OFFSET, legacy_end)
        self.assertGreaterEqual(wire.STATUS_OFFSET, wire.REQUEST_OFFSET + wire.REQUEST.size)
        self.assertLessEqual(wire.STATUS_OFFSET + wire.STATUS.size, MAPPING_BYTES)
        self.assertLess(editor_wire.CAMERA_LIST_OFFSET, legacy_end)
        self.assertLess(native_capabilities.OFFSET + native_capabilities.WIRE.size, legacy_end)
        self.memory[:legacy_end] = b'\x5a' * legacy_end
        self.probe.start()
        self.probe.stop()
        self.publish()
        self.assertEqual(self.probe.status()['details'], {'draws': 100})
        self.assertEqual(self.memory[:legacy_end], b'\x5a' * legacy_end)


class SourceMeasurementTests(unittest.TestCase):
    def test_waits_for_current_command_and_verifies_stop(self):
        probe=Mock();probe.start.return_value=3;probe.stop.return_value=4
        good=dict(command=3,state=2,details=dict(source_state=9,candidates=8,ambiguous_frames=0,
                                                lock_skips=20,width=1024,height=1024))
        probe.status.side_effect=[dict(command=2,state=3),good,good,dict(command=4,state=3)]
        with patch.object(wire,'EnvironmentProbe',return_value=probe):
            result=wire.measure_source(object(),Mock(),sleep=lambda _:None)
        self.assertEqual(result['width'],1024)
        probe.stop.assert_called_once()

    def test_ambiguous_source_rejected_and_stopped(self):
        probe=Mock();probe.start.return_value=3;probe.stop.return_value=4
        bad=dict(command=3,state=2,details=dict(source_state=9,candidates=2,ambiguous_frames=1,lock_skips=0))
        probe.status.side_effect=[bad,bad,dict(command=4,state=3)]
        with patch.object(wire,'EnvironmentProbe',return_value=probe):
            with self.assertRaisesRegex(RuntimeError,'dimensions'):
                wire.measure_source(object(),Mock())
        probe.stop.assert_called_once()

    def test_replacement_probe_never_stopped(self):
        probe=Mock();probe.start.return_value=3
        probe.status.return_value=dict(command=4,state=1)
        with patch.object(wire,'EnvironmentProbe',return_value=probe):
            with self.assertRaisesRegex(RuntimeError,'replaced'):
                wire.measure_source(object(),Mock())
        probe.stop.assert_not_called()


class ContractGeneratorTests(unittest.TestCase):
    def proof(self):
        return dict(source_shader_sha256="0" * 64, variants=[dict(static_id=s, dynamic_id=d,
            bytecode_sha256="a"*64, bytecode_size=1000, hdr_srv_slot=0,
            per_view_cb_slot=1, postprocess_cb_slot=2) for s in range(2) for d in range(16)])

    def test_metadata_only_and_all_variants(self):
        result = generate(self.proof())
        self.assertEqual(result.count("    {{"), 32)
        self.assertIn("Source VCS SHA256", result)

    def test_incomplete_duplicate_bad_binding_and_hash_rejected(self):
        for field, value in (("hdr_srv_slot", 1), ("postprocess_cb_slot", 9),
                             ("bytecode_sha256", "bad"), ("bytecode_size", 65537)):
            proof = self.proof()
            proof["variants"][0][field] = value
            with self.assertRaises(ValueError): generate(proof)
        proof = self.proof()
        proof["variants"][1] = proof["variants"][0]
        with self.assertRaises(ValueError): generate(proof)
        proof = self.proof()
        proof["variants"].pop()
        with self.assertRaises(ValueError): generate(proof)


if __name__ == "__main__":
    unittest.main()
