"""The optional manifest repair preserves the original and all other records."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from dolly import demo_packets, replay_repair
from test_demo_packets import record, varint


def network_message(messages):
    # Independent Source 2 bit writer: message id (6 bits here), varint size,
    # then the protobuf payload; each next message begins at the current bit.
    bits = []
    def append(value, size):
        bits.extend((value >> i) & 1 for i in range(size))
    for kind, payload in messages:
        append(kind, 6)
        for byte in varint(len(payload)) + payload:
            append(byte, 8)
    output = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        output[i // 8] |= bit << (i % 8)
    return bytes(output)


def snappy_literal(payload):
    # A literal-only Snappy block, independent of the production writer.
    length = len(payload)
    if not length:
        return b"\0"
    if length <= 60:
        prefix = bytes([(length - 1) * 4])
    elif length <= 256:
        prefix = b"\xf0" + bytes([length - 1])
    else:
        prefix = b"\xf4" + struct.pack("<H", length - 1)
    return varint(length) + prefix + payload


def spawn_load(handle, *, incomplete=True, initial=False):
    # group handle=4; manifest=8; flags=9; manifestincomplete=11.
    return (b"\x20" + varint(handle) + b"\x42\x01x\x48" +
            varint(152 if initial else 144) + b"\x58" + bytes([int(incomplete)]))


def spawn_update(handle):
    # group handle=1; manifest=2; manifestincomplete=3 (false).
    return b"\x08" + varint(handle) + b"\x12\x01y\x18\x00"


def make_demo(*, duplicate=True, compressed=True, updates=True, initial=False,
              full_packet=False, split=False):
    # Two dynamic groups with repeated incomplete loads and later updates.
    # Another message and the footer must remain byte-for-byte untouched.
    a, b = spawn_load(5, initial=initial), spawn_load(7, initial=initial)
    repeated = (a, b) if duplicate else (spawn_load(5, incomplete=False),
                                         spawn_load(7, incomplete=False))
    if split:
        network = network_message([(8, a), (8, repeated[0]), (4, b"tick data")])
    else:
        network = network_message([(8, a), (8, b), (8, repeated[0]), (8, repeated[1]),
                                   (4, b"tick data"), (7, b"keep this too")])
    envelope = b"\x1a" + varint(len(network)) + network
    if full_packet:
        # Full snapshots carry the packet in field 2, beside string tables.
        envelope = b"\x0a\x04keep\x12" + varint(len(envelope)) + envelope
    payload = snappy_literal(envelope) if compressed else envelope
    header = record(1, 0xffffffff, b"\x0a\x08PBDEMS2\0\x10\x30")
    first = record((13 if full_packet else 7) | (64 if compressed else 0), 1, payload)
    if full_packet:
        simple = network_message([(4, b"first packet")])
        first += record(7, 1, b"\x1a" + varint(len(simple)) + simple)
    if split:
        second_network = network_message([(8, b), (8, repeated[1]), (4, b"second packet")])
        second_packet = b"\x1a" + varint(len(second_network)) + second_network
        second = record(7 | (64 if compressed else 0), 4,
                        snappy_literal(second_packet) if compressed else second_packet)
    else:
        second = b""
    later_messages = [(4, b"later packet")]
    if updates:
        later_messages += [(9, spawn_update(5)), (9, spawn_update(7))]
    later_network = network_message(later_messages)
    later = record(7, 12, b"\x1a" + varint(len(later_network)) + later_network)
    stop = record(0, 12)
    body = header + first + second + later + stop
    spawn = 16 + len(body)
    body += record(15, 12)
    info = 16 + len(body)
    body += record(2, 12, b"\x10\x0c\x18\x02")
    return b"PBDEMS2\0" + struct.pack("<II", info, spawn) + body


class ReplayRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = self.root / "mydemo.dem"
        self.output = self.root / "mydemo-repaired.dem"

    def test_known_snappy_literals_and_copy_forms(self):
        for encoded, expected in ((b"\x05\x10hello", b"hello"),
                                  (b"\x0a\x00a\x15\x01", b"a" * 10),
                                  (b"\x05\x00b\x0e\x01\x00", b"b" * 5),
                                  (b"\x05\x00c\x0f\x01\x00\x00\x00", b"c" * 5)):
            with self.subTest(encoded=encoded):
                self.assertEqual(replay_repair._uncompress_snappy(encoded), expected)
        for size in (0, 1, 60, 61, 256, 257, 1024):
            data = bytes(i % 256 for i in range(size))
            self.assertEqual(replay_repair._uncompress_snappy(replay_repair._compress_literal(data)), data)
        for invalid in (b"\x01\x01\x00", b"\x05\x10hel", b"\x00\x00", b"\x80"):
            with self.subTest(invalid=invalid), self.assertRaises(replay_repair.ReplayRepairUnavailable):
                replay_repair._uncompress_snappy(invalid)

    def test_repaired_copy_clears_only_matched_incomplete_manifest_flags(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                original = make_demo(compressed=compressed)
                self.original.write_bytes(original)
                if self.output.exists():
                    self.output.unlink()
                self.assertEqual(replay_repair.repair_replay(self.original, self.output), self.output.resolve())
                self.assertEqual(self.original.read_bytes(), original)
                before = demo_packets.packet_index(self.original)
                after = demo_packets.packet_index(self.output)
                self.assertEqual(list(after.ticks), [1, 12])
                self.assertEqual(before.total_ticks, after.total_ticks)
                # Independent byte-for-byte expectation for every retained
                # network message, not just the number of removed entries.
                source = self.output.read_bytes()
                pos = 16 + len(record(1, 0xffffffff, b"\x0a\x08PBDEMS2\0\x10\x30"))
                command, pos = replay_repair._read_varint(source, pos)
                _, pos = replay_repair._read_varint(source, pos)
                length, pos = replay_repair._read_varint(source, pos)
                payload = source[pos:pos + length]
                if command & 64:
                    payload = replay_repair._uncompress_snappy(payload)
                _, data_start, data_end = replay_repair._packet_data_range(payload)
                expected = network_message([(8, spawn_load(5, incomplete=False)),
                                            (8, spawn_load(7, incomplete=False)),
                                            (8, spawn_load(5, incomplete=False)),
                                            (8, spawn_load(7, incomplete=False)),
                                            (4, b"tick data"), (7, b"keep this too")])
                self.assertEqual(payload[data_start:data_end], expected)
                later = network_message([(4, b"later packet"), (9, spawn_update(5)),
                                         (9, spawn_update(7))])
                self.assertIn(record(7, 12, b"\x1a" + varint(len(later)) + later), source)

    def test_full_packet_snapshot_keeps_string_tables_and_updates_flags(self):
        original = make_demo(full_packet=True)
        self.original.write_bytes(original)
        replay_repair.repair_replay(self.original, self.output)
        self.assertEqual(self.original.read_bytes(), original)
        source = self.output.read_bytes()
        pos = 16 + len(record(1, 0xffffffff, b"\x0a\x08PBDEMS2\0\x10\x30"))
        command, pos = replay_repair._read_varint(source, pos)
        _, pos = replay_repair._read_varint(source, pos)
        length, pos = replay_repair._read_varint(source, pos)
        self.assertEqual(command & ~64, 13)
        snapshot = replay_repair._uncompress_snappy(source[pos:pos + length])
        self.assertTrue(snapshot.startswith(b"\x0a\x04keep"))
        _, packet_start, packet_end = replay_repair._byte_field_range(snapshot, 2)
        packet = snapshot[packet_start:packet_end]
        _, start, end = replay_repair._packet_data_range(packet)
        expected = network_message([(8, spawn_load(5, incomplete=False)),
                                    (8, spawn_load(7, incomplete=False)),
                                    (8, spawn_load(5, incomplete=False)),
                                    (8, spawn_load(7, incomplete=False)),
                                    (4, b"tick data"), (7, b"keep this too")])
        self.assertEqual(packet[start:end], expected)

    def test_multiple_changed_packets_keep_footer_offsets_and_later_records(self):
        original = make_demo(split=True)
        self.original.write_bytes(original)
        replay_repair.repair_replay(self.original, self.output)
        self.assertEqual(self.original.read_bytes(), original)
        self.assertEqual(list(demo_packets.packet_index(self.output).ticks), [1, 4, 12])

        def records(data):
            result = []
            position = 16
            while position < len(data):
                command, position = replay_repair._read_varint(data, position)
                tick, position = replay_repair._read_varint(data, position)
                size, position = replay_repair._read_varint(data, position)
                result.append((command, tick, data[position:position + size]))
                position += size
            return result

        before, after = records(original), records(self.output.read_bytes())
        self.assertEqual([(x[0], x[1]) for x in before], [(x[0], x[1]) for x in after])
        for record_index, group, suffix in ((1, 5, b"tick data"), (2, 7, b"second packet")):
            command, tick, payload = after[record_index]
            self.assertEqual(command, 71)
            self.assertIn(tick, (1, 4))
            packet = replay_repair._uncompress_snappy(payload)
            _, start, end = replay_repair._packet_data_range(packet)
            expected = network_message([(8, spawn_load(group, incomplete=False)),
                                        (8, spawn_load(group, incomplete=False)), (4, suffix)])
            self.assertEqual(packet[start:end], expected)
        self.assertEqual(before[3:], after[3:])

    def test_unrecognized_pattern_and_invalid_source_create_no_output(self):
        for original in (make_demo(duplicate=False), make_demo(updates=False),
                         make_demo(initial=True), b"bad demo"):
            with self.subTest(length=len(original)):
                self.original.write_bytes(original)
                with self.assertRaises(replay_repair.ReplayRepairUnavailable):
                    replay_repair.repair_replay(self.original, self.output)
                self.assertEqual(self.original.read_bytes(), original)
                self.assertFalse(self.output.exists())

    def test_existing_destination_and_original_are_never_overwritten(self):
        original = make_demo()
        self.original.write_bytes(original)
        self.output.write_bytes(b"existing file")
        with self.assertRaises(FileExistsError):
            replay_repair.repair_replay(self.original, self.output)
        with self.assertRaises(ValueError):
            replay_repair.repair_replay(self.original, self.original)
        self.assertEqual(self.original.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), b"existing file")

    def test_failed_output_verification_removes_only_the_created_file(self):
        self.original.write_bytes(make_demo())
        original = self.original.read_bytes()
        valid = demo_packets.packet_index(self.original)
        with patch.object(replay_repair, "packet_index", side_effect=[valid, None]):
            with self.assertRaisesRegex(replay_repair.ReplayRepairUnavailable, "did not pass"):
                replay_repair.repair_replay(self.original, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.original.read_bytes(), original)


class ReplayRepairGuiTests(unittest.TestCase):
    def test_chosen_destination_is_used_by_worker_and_success_describes_copy(self):
        from dolly.gui import DollyApp
        app = DollyApp.__new__(DollyApp)
        app.busy = False
        app.demo_path = Mock(get=Mock(return_value="C:/replays/mydemo.dem"))
        app.status_text = Mock()
        app.root = Mock()
        app._submit = Mock(return_value=True)
        with patch("dolly.gui.filedialog.asksaveasfilename", return_value="C:/replays/fixed.dem"), \
                patch("dolly.replay_repair.repair_replay", return_value=Path("C:/replays/fixed.dem")) as repair:
            app._repair_selected_replay()
            label, work, finished = app._submit.call_args.args
            self.assertEqual(label, "Repairing replay")
            result = work()
        repair.assert_called_once_with(Path("C:/replays/mydemo.dem"), Path("C:/replays/fixed.dem"))
        with patch("dolly.gui.messagebox.showinfo") as notice:
            finished(result)
        self.assertIn("original replay was not changed", notice.call_args.args[1])
        self.assertIn("Select it", app.status_text.set.call_args.args[0])

    def test_cancelled_save_or_busy_state_does_not_submit_work(self):
        from dolly.gui import DollyApp
        app = DollyApp.__new__(DollyApp)
        app.busy = False
        app.demo_path = Mock(get=Mock(return_value="C:/replays/mydemo.dem"))
        app.status_text = Mock()
        app.root = Mock()
        app._submit = Mock()
        with patch("dolly.gui.filedialog.asksaveasfilename", return_value="") as save:
            app._repair_selected_replay()
            app.busy = True
            app._repair_selected_replay()
        save.assert_called_once()
        app._submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
