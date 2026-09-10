"""Source2 packet framing, without shipping a player's recording or payloads."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from dolly import demo_packets


def varint(number):
    result = bytearray()
    while number >= 128:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def record(command, tick, payload=b""):
    return varint(command) + varint(tick) + varint(len(payload)) + payload


def synthetic_demo(ticks=(1, 22631, 22634, 22637, 48391, 48394, 48397), *, compressed=True):
    body = record(1, 0xffffffff, b"\x0a\x08PBDEMS2\0\x10\x30")
    body += record(8, 0xffffffff, b"signon")
    body += record(13 | (64 if compressed else 0), ticks[0], b"full snapshot")
    for tick in ticks:
        body += record(7 | (64 if compressed else 0), tick, b"packet")
    body += record(0, ticks[-1])
    spawn = len(body) + 16
    body += record(15, ticks[-1])
    info = len(body) + 16
    body += record(2, ticks[-1], b"\x10" + varint(ticks[-1]) + b"\x18" + varint(len(ticks)))
    return b"PBDEMS2\0" + struct.pack("<II", info, spawn) + body


class DemoPacketTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "custom.recording.dem"
        self.file.write_bytes(synthetic_demo())
        with demo_packets._CACHE_LOCK:
            demo_packets._CACHE.clear()

    def test_sparse_packets_resolve_to_actual_record_not_arbitrary_tick_tolerance(self):
        for compressed in (False, True):
            self.file.write_bytes(synthetic_demo(compressed=compressed))
            index = demo_packets.packet_index(self.file)
            self.assertEqual(index.total_ticks, 48397)
            self.assertEqual(index.following(48393), 48394)
            self.assertEqual(index.following(22636), 22637)
            self.assertEqual(index.following(22632), 22634)
            self.assertEqual(index.following(22631), 22631)
            self.assertIsNone(index.following(0))
            self.assertIsNone(index.following(48398))

    def test_negative_signon_is_excluded_and_snapshot_does_not_duplicate_packet(self):
        index = demo_packets.packet_index(self.file)
        self.assertEqual(list(index.ticks), [1, 22631, 22634, 22637, 48391, 48394, 48397])

    def test_cache_invalidates_after_file_replacement(self):
        first = demo_packets.packet_index(self.file)
        self.assertIs(first, demo_packets.packet_index(self.file))
        replacement = self.file.with_suffix(".tmp")
        replacement.write_bytes(synthetic_demo((1, 4, 7)))
        replacement.replace(self.file)
        second = demo_packets.packet_index(self.file)
        self.assertIsNot(first, second)
        self.assertEqual(list(second.ticks), [1, 4, 7])

    def test_cache_invalidates_after_append_or_truncation(self):
        self.assertIsNotNone(demo_packets.packet_index(self.file))
        with self.file.open("ab") as stream:
            stream.write(record(7, 48400, b"unexpected after stop"))
        self.assertIsNone(demo_packets.packet_index(self.file))
        self.file.write_bytes(synthetic_demo()[:-1])
        self.assertIsNone(demo_packets.packet_index(self.file))

    def test_file_changing_while_scanning_does_not_supply_boundaries(self):
        scan = demo_packets._scan
        def changing(stream, size, cancelled):
            result = scan(stream, size, cancelled)
            with self.file.open("ab") as output:
                output.write(b"\0")
            return result
        with patch.object(demo_packets, "_scan", side_effect=changing):
            self.assertIsNone(demo_packets.packet_index(self.file))
        self.assertFalse(demo_packets._CACHE)

    def test_unknown_truncated_and_unfinished_formats_leave_exact_seek_available(self):
        complete = synthetic_demo()
        cases = [b"HL2DEMO\0" + complete[8:], b"PBDEMS2\0" + b"\0" * 8,
                 complete[:24], complete[:-4], complete[:8] + struct.pack("<II", 17, 18) + complete[16:],
                 complete[:16] + record(1, 0xffffffff, b"\x0a\x08notdemo!"),
                 complete + record(19, 48397), synthetic_demo((1, 10, 5))]
        for data in cases:
            with self.subTest(prefix=data[:24], length=len(data)):
                self.file.write_bytes(data)
                self.assertIsNone(demo_packets.packet_index(self.file))

    def test_bad_varint_and_payload_bounds_are_rejected(self):
        header = synthetic_demo()[:16]
        for suffix in (b"\x80" * 5, b"\x80\x80\x80\x80\x10",
                       varint(1) + varint(0xffffffff) + varint(0xffffffff),
                       varint(1) + varint(0xffffffff) + b"\x7fshort"):
            self.file.write_bytes(header + suffix)
            self.assertIsNone(demo_packets.packet_index(self.file))

    def test_resource_limits_fall_back_without_retaining_partial_index(self):
        with patch.object(demo_packets, "_MAX_RECORDS", 3):
            self.assertIsNone(demo_packets.packet_index(self.file))
        self.file.write_bytes(synthetic_demo((1, 4)))
        with patch.object(demo_packets, "_MAX_BYTES", 10):
            self.assertIsNone(demo_packets.packet_index(self.file))

    def test_missing_file_and_directories_do_not_block_normal_exact_seeking(self):
        self.assertIsNone(demo_packets.packet_index(self.file.parent / "missing.dem"))
        self.assertIsNone(demo_packets.packet_index(self.file.parent))

    def test_cancellation_propagates_without_caching_partial_work(self):
        def cancelled():
            raise RuntimeError("Seek cancelled.")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            demo_packets.packet_index(self.file, check_cancelled=cancelled)
        self.assertFalse(demo_packets._CACHE)

    def test_cache_is_bounded(self):
        for i in range(4):
            current = self.file.parent / f"{i}.dem"
            current.write_bytes(synthetic_demo())
            self.assertIsNotNone(demo_packets.packet_index(current))
        self.assertEqual(len(demo_packets._CACHE), 2)


if __name__ == "__main__":
    unittest.main()
