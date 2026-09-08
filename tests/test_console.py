"""Protocol and synchronization regressions using a real loopback TCP peer."""
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from dolly.console import (ConsoleClient, ConsoleError, ConsoleTimeout,
                           VConsoleDecoder, encode_command, error_text,
                           parse_demo_info, parse_demo_tick)


def print_packet(text):
    payload = bytes(28) + text.encode("utf-8") + b"\x00"
    return struct.pack("!4sIHH", b"PRNT", 0xD40000, 12 + len(payload), 0) + payload


class Peer:
    def __init__(self, protocol="vconsole", silent=False, disconnect=False, responses=None):
        self.protocol, self.silent, self.disconnect = protocol, silent, disconnect
        self.responses = responses or {}
        self.end_written = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.commands = []
        self.socket = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            self.socket, _ = self.listener.accept()
            pending = b""
            while True:
                data = self.socket.recv(4096)
                if not data:
                    return
                pending += data
                while True:
                    if self.protocol == "vconsole":
                        if len(pending) < 12:
                            break
                        length = struct.unpack("!H", pending[8:10])[0]
                        if len(pending) < length:
                            break
                        cmd = pending[12:length-1].decode()
                        pending = pending[length:]
                    else:
                        if b"\n" not in pending:
                            break
                        line, pending = pending.split(b"\n", 1)
                        cmd = line.decode()
                    self.commands.append(cmd)
                    if self.disconnect:
                        self.socket.close()
                        return
                    if self.silent:
                        continue
                    if cmd.startswith("echo "):
                        # Include an echoed command first: it must not delimit.
                        response = "> " + cmd + "\n" + cmd[5:] + "\n"
                    elif cmd in self.responses:
                        response = self.responses[cmd]
                    elif cmd.startswith("help "):
                        name = cmd[5:]
                        response = ("Unknown command 'absent'!\n" if name == "absent"
                                    else f'"{name}" = "90"\n - Test help.\n')
                    else:
                        response = "Playing back demo: 'test.dem' at tick 123\n"
                    packet = print_packet(response) if self.protocol == "vconsole" else response.encode()
                    # Several sends force fragmentation without timing sleeps.
                    for start in range(0, len(packet), 7):
                        self.socket.sendall(packet[start:start+7])
                    if cmd.startswith("echo DOLLY_END_"):
                        self.end_written.set()
        except OSError:
            pass

    def close(self):
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.socket.close()
        self.listener.close()
        self.thread.join(1)


class ConsoleTests(unittest.TestCase):
    COMPLETION = (r"Removed hidden flags from \d+ concommands\b",
                  r"Removed hidden flags from \d+ cvars\b")

    def test_command_length_counts_utf8_bytes(self):
        packet = encode_command("echo café")
        self.assertEqual(packet[:8], b"CMND\x00\xd4\x00\x00")
        self.assertEqual(struct.unpack("!H", packet[8:10])[0], len(packet))
        self.assertTrue(packet.endswith("café\0".encode()))

    def test_reject_command_line_breaks_and_oversize(self):
        for value in ("", "echo a\nquit", "echo a\x00", "a" * 65524):
            with self.assertRaises(ValueError):
                encode_command(value)

    def test_fragmented_and_coalesced_print_packets(self):
        data = print_packet("hello ") + print_packet("world\n")
        decoder = VConsoleDecoder()
        decoded = []
        for byte in data:
            decoded.extend(decoder.feed(bytes([byte])))
        self.assertEqual(decoded, ["hello ", "world\n"])
        self.assertEqual(VConsoleDecoder().feed(data), decoded)

    def test_ignore_other_packets_and_reject_invalid_lengths(self):
        decoder = VConsoleDecoder()
        self.assertEqual(decoder.feed(struct.pack("!4sIHH", b"AINF", 0, 14, 0) + b"ab"), [])
        with self.assertRaises(ConsoleError):
            decoder.feed(struct.pack("!4sIHH", b"PRNT", 0, 8, 0))
        with self.assertRaises(ConsoleError):
            VConsoleDecoder().feed(b"plain netcon response\n")

    def test_request_both_protocols_handles_echo_and_fragments(self):
        for protocol in ("vconsole", "netcon"):
            with self.subTest(protocol=protocol):
                peer = Peer(protocol)
                client = ConsoleClient(protocol)
                try:
                    client.connect(port=peer.port)
                    output = client.request("demo_info")
                    self.assertIn("at tick 123", output)
                    self.assertNotIn("DOLLY_BEGIN_", output)
                    self.assertNotIn("DOLLY_END_", output)
                    self.assertEqual(parse_demo_info(output)["tick"], 123)
                    self.assertTrue(client.supports("citadel_camera_fov"))
                    self.assertFalse(client.supports("absent"))
                    client.send("citadel_camera_fov 90")
                    self.assertTrue(client.is_connected)
                finally:
                    client.close()
                    peer.close()

    def test_request_writes_all_three_protocol_frames_together(self):
        # TCP can still fragment a single write. Verify the application makes
        # one write while retaining three correctly framed commands; the peer
        # then fragments its response to exercise the normal reader as well.
        for protocol in ("netcon", "vconsole"):
            with self.subTest(protocol=protocol):
                peer = Peer(protocol)
                client = ConsoleClient(protocol)
                writes = []
                create_connection = socket.create_connection

                class RecordingSocket:
                    def __init__(self, *args, **kwargs):
                        self.socket = create_connection(*args, **kwargs)

                    def sendall(self, data):
                        writes.append(bytes(data))
                        return self.socket.sendall(data)

                    def __getattr__(self, name):
                        return getattr(self.socket, name)

                try:
                    with patch("dolly.console.socket.create_connection", RecordingSocket):
                        client.connect(port=peer.port)
                    output = client.request("demo_info; echo café")
                    self.assertEqual(parse_demo_info(output)["tick"], 123)
                    self.assertEqual(len(writes), 1)
                    self.assertEqual(len(peer.commands), 3)
                    self.assertTrue(peer.commands[0].startswith("echo DOLLY_BEGIN_"))
                    self.assertEqual(peer.commands[1], "demo_info; echo café")
                    self.assertTrue(peer.commands[2].startswith("echo DOLLY_END_"))
                    expected = (b"".join(encode_command(command) for command in peer.commands)
                                if protocol == "vconsole" else
                                b"".join((command + "\n").encode("utf-8") for command in peer.commands))
                    self.assertEqual(writes[0], expected)
                finally:
                    client.close()
                    peer.close()

    def test_invalid_request_never_sends_a_partial_delimiter_batch(self):
        for protocol in ("netcon", "vconsole"):
            with self.subTest(protocol=protocol):
                client = ConsoleClient(protocol)
                # A rejected command must fail before checking connection or
                # writing even the otherwise valid begin marker.
                with patch.object(client, "_send_commands_locked") as send:
                    for command in ("", "demo_info\nquit", "echo a\x00", "é" * 32762):
                        with self.subTest(command=command[:30]), self.assertRaises(ValueError):
                            client.request(command)
                    send.assert_not_called()

    def test_reject_non_loopback_and_invalid_port(self):
        client = ConsoleClient()
        for host in ("8.8.8.8", "example.com"):
            with self.assertRaises(ValueError):
                client.connect(host)
        for port in (0, 65536, True):
            with self.assertRaises(ValueError):
                client.connect(port=port)

    def test_timeout_and_eof_are_distinct(self):
        for kwargs, exception in (({"silent": True}, ConsoleTimeout), ({"disconnect": True}, ConsoleError)):
            with self.subTest(kwargs=kwargs):
                peer = Peer(**kwargs)
                client = ConsoleClient()
                try:
                    client.connect(port=peer.port)
                    with self.assertRaises(exception):
                        client.request("demo_info", timeout=0.1)
                finally:
                    client.close()
                    peer.close()

    def test_serial_requests_cannot_mix_responses(self):
        peer = Peer()
        client = ConsoleClient()
        try:
            client.connect(port=peer.port)
            outputs = []
            threads = [threading.Thread(target=lambda: outputs.append(client.request("demo_info"))) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
            self.assertEqual(len(outputs), 3)
            self.assertTrue(all(parse_demo_info(output)["tick"] == 123 for output in outputs))
        finally:
            client.close()
            peer.close()

    def test_diagnostics_preserve_output_between_requests_without_reusing_it(self):
        peer = Peer("netcon")
        client = ConsoleClient("netcon")
        try:
            client.connect(port=peer.port)
            client.request("demo_info")
            # A game worker may print after a completed request's end marker.
            peer.socket.sendall(b"LATE_UNLOCKER_OUTPUT_FOR_DIAGNOSTICS\n")
            output = client.request("demo_info")
            self.assertNotIn("LATE_UNLOCKER", output)
            history = client.recent_output()
            self.assertIn("LATE_UNLOCKER_OUTPUT_FOR_DIAGNOSTICS", history)
            self.assertIn("DOLLY_END_", history)
            self.assertIn("DOLLY_BEGIN_", history)
            client.close()
            self.assertEqual(client.recent_output(), history)
        finally:
            client.close()
            peer.close()

    def test_diagnostic_tail_is_byte_bounded_and_rejects_invalid_limit(self):
        client = ConsoleClient("netcon")
        # Exercise a byte boundary inside a UTF-8 character without a network
        # scheduling dependency. History reads must not consume queued events.
        with client._condition:
            client._events.append((1, "café" * 100))
        tail = client.recent_output(8)
        self.assertEqual(tail, "fécafé")
        self.assertLessEqual(len(tail.encode("utf-8")), 8)
        self.assertEqual(client.recent_output(8), tail)
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                client.recent_output(limit)

    def test_new_connection_clears_old_diagnostic_history(self):
        peer = Peer("netcon")
        client = ConsoleClient("netcon")
        with client._condition:
            client._events.append((1, "old session response"))
        try:
            client.connect(port=peer.port)
            self.assertEqual(client.recent_output(), "")
            client.request("demo_info")
            self.assertNotIn("old session", client.recent_output())
        finally:
            client.close()
            peer.close()

    def test_completion_wait_collects_late_summaries_after_end_marker(self):
        for protocol in ("netcon", "vconsole"):
            with self.subTest(protocol=protocol):
                peer = Peer(protocol, responses={"cvar_unhide": ""})
                client = ConsoleClient(protocol)
                results, errors = [], []

                def request():
                    try:
                        results.append(client.request("cvar_unhide", timeout=2,
                                                      completion_patterns=self.COMPLETION))
                    except Exception as exc:
                        errors.append(exc)

                worker = threading.Thread(target=request)
                try:
                    client.connect(port=peer.port)
                    worker.start()
                    self.assertTrue(peer.end_written.wait(1))
                    self.assertTrue(worker.is_alive())
                    late = "Removed hidden flags from 42 concommands\nRemoved hidden flags from 17 cvars\n"
                    wire = print_packet(late) if protocol == "vconsole" else late.encode()
                    peer.socket.sendall(wire)
                    worker.join(1)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(len(results), 1)
                    self.assertIn("42 concommands", results[0])
                    self.assertIn("17 cvars", results[0])
                    self.assertNotIn("DOLLY_END_", results[0])
                    self.assertNotIn("DOLLY_BEGIN_", results[0])
                finally:
                    client.close()
                    worker.join(1)
                    peer.close()

    def test_partial_completion_waits_until_deadline_and_keeps_raw_evidence(self):
        peer = Peer("netcon", responses={"cvar_unhide": "Removed hidden flags from 0 concommands\n"})
        client = ConsoleClient("netcon")
        try:
            client.connect(port=peer.port)
            start = time.monotonic()
            with self.assertRaisesRegex(ConsoleTimeout, "acknowledged.*completion messages"):
                client.request("cvar_unhide", timeout=0.15, completion_patterns=self.COMPLETION)
            self.assertGreaterEqual(time.monotonic() - start, 0.12)
            self.assertIn("0 concommands", client.recent_output())
        finally:
            client.close()
            peer.close()

    def test_completion_rejection_returns_without_waiting_for_success_patterns(self):
        peer = Peer("netcon", responses={"cvar_unhide": "Unknown command 'cvar_unhide'!\n"})
        client = ConsoleClient("netcon")
        try:
            client.connect(port=peer.port)
            start = time.monotonic()
            output = client.request("cvar_unhide", timeout=2, completion_patterns=self.COMPLETION)
            self.assertIsNotNone(error_text(output))
            self.assertLess(time.monotonic() - start, 1)
        finally:
            client.close()
            peer.close()

    def test_default_empty_response_still_finishes_at_end_echo(self):
        peer = Peer("netcon", responses={"version": ""})
        client = ConsoleClient("netcon")
        try:
            client.connect(port=peer.port)
            self.assertEqual(client.request("version", timeout=0.25), "")
        finally:
            client.close()
            peer.close()

    def test_completion_does_not_use_previous_request_output(self):
        summary = "Removed hidden flags from 42 concommands\nRemoved hidden flags from 17 cvars\n"
        peer = Peer("netcon", responses={"first": summary, "cvar_unhide": ""})
        client = ConsoleClient("netcon")
        try:
            client.connect(port=peer.port)
            self.assertIn("42 concommands", client.request("first"))
            with self.assertRaises(ConsoleTimeout):
                client.request("cvar_unhide", timeout=0.15, completion_patterns=self.COMPLETION)
        finally:
            client.close()
            peer.close()

    def test_invalid_completion_patterns_are_rejected_before_send(self):
        client = ConsoleClient()
        for patterns in ((), ["x"], ("",), (3,), ("[",), ("x",) * 9, ("x" * 513,)):
            with self.subTest(patterns=patterns), self.assertRaises(ValueError):
                client.request("cvar_unhide", completion_patterns=patterns)


class ParsingTests(unittest.TestCase):
    # Actual response shape from the supplied 2026-09-07 Deadlock diagnostics;
    # paths and identifying names are replaced with a generic fixture.
    METADATA = (
        'Demo contents for B:/SteamLibrary/Deadlock/game/citadel/replays/example.dem:\r\n'
        'DemoFileHeader: demo_file_stamp: "PBDEMS2\\000"\r\n'
        'patch_version: 48\r\nserver_name: "Example replay server"\r\n'
        'client_name: "SourceTV Demo"\r\nmap_name: "start"\r\n'
        'game_directory: "/opt/srcds/deadlock/citadel"\r\n'
        'fullpackets_version: 2\r\nallow_clientside_entities: true\r\n'
        'allow_clientside_particles: true\r\naddons: ""\r\n'
        'demo_version_name: "valve_demo_2"\r\nbuild_num: 10854\r\n'
        'server_start_tick: 1658\r\n\r\n'
        'DemoFileInfo: playback_time: 2522.15625\r\n'
        'playback_ticks: 161418\r\nplayback_frames: 161410\r\n'
        'game_info {\r\n}\r\n'
    )

    def test_deadlock_metadata_is_identity_and_total_duration_not_current_tick(self):
        data = parse_demo_info(self.METADATA)
        self.assertTrue(data["playing"])
        self.assertIsNone(data["tick"])
        self.assertIsNone(data["paused"])
        self.assertEqual(data["name"], "B:/SteamLibrary/Deadlock/game/citadel/replays/example.dem")
        self.assertEqual(data["total_ticks"], 161418)
        self.assertEqual(data["playback_time"], 2522.15625)
        self.assertEqual(data["tick_rate"], 64)
        with self.assertRaises(ValueError):
            parse_demo_tick(self.METADATA)

    def test_metadata_without_duration_fields_has_no_invented_clock(self):
        output = 'Demo contents for replays/example.dem:\nDemoFileHeader: map_name: "start"\nDemoFileInfo: game_info {}\n'
        data = parse_demo_info(output)
        for key in ("tick", "total_ticks", "playback_time", "tick_rate", "paused"):
            self.assertIsNone(data[key])

    def test_incomplete_or_invalid_metadata_rejected(self):
        for output in (
            'Demo contents for a.dem:\n',
            'Demo contents for a.dem:\nDemoFileHeader: map_name: "start"\n',
            'Demo contents for a.dem:\nDemoFileInfo: playback_ticks: 12\n',
            self.METADATA.replace('playback_ticks: 161418', 'playback_ticks: -1'),
            self.METADATA.replace('playback_ticks: 161418', 'playback_ticks: 1.5'),
            self.METADATA.replace('playback_time: 2522.15625', 'playback_time: nan'),
            self.METADATA.replace('playback_time: 2522.15625', 'playback_time: -5'),
        ):
            with self.subTest(output=output[-100:]), self.assertRaises(ValueError):
                parse_demo_info(output)

    def test_no_demo_response_overrides_other_status_and_metadata(self):
        no_demo = 'Error - Not currently playing back a demo.\n'
        for parser, output in (
            (parse_demo_info, self.METADATA),
            (parse_demo_info, "Playing back demo: 'example.dem' at tick 55\n"),
            (parse_demo_tick, 'Currently playing 50 of 200 ticks. Minutes:0.05 File:example.dem\n'),
        ):
            for combined in (no_demo + output, output + no_demo):
                with self.subTest(parser=parser.__name__):
                    data = parser(combined)
                    self.assertFalse(data["playing"])
                    self.assertIsNone(data["tick"])

    def test_current_deadlock_bare_goto_reports_live_tick_and_file(self):
        output = (
            "Syntax: demo_goto <tick> [relative] [pause]\r\n"
            "  eg: 'demo_goto 6666' or 'demo_gototick 25%' or 'demo_gototick 42min'\r\n"
            "  Currently playing 67543 of 161418 ticks. Minutes:42.04 File:B:/Steam Library/replays/example.dem\r\n"
        )
        self.assertEqual(parse_demo_tick(output), {
            "playing": True, "tick": 67543, "total_ticks": 161418,
            "name": "B:/Steam Library/replays/example.dem", "paused": None,
        })
        self.assertIsNone(error_text(output))

    def test_legacy_bare_goto_requires_separate_identity(self):
        data = parse_demo_tick("Currently playing 67295 of 142355 ticks. Minutes:37.07\n")
        self.assertEqual(data["tick"], 67295)
        self.assertEqual(data["total_ticks"], 142355)
        self.assertIsNone(data["name"])

    def test_live_tick_parser_accepts_console_prefix_and_quoted_file(self):
        output = '\x1b[32m[Engine] Currently playing 0 of 120 ticks. Minutes:0.03 File:"replays/test demo.dem"\x1b[0m\n'
        data = parse_demo_tick(output)
        self.assertEqual(data["tick"], 0)
        self.assertEqual(data["name"], 'replays/test demo.dem')

    def test_live_tick_rejects_invalid_ranges_and_unrelated_numbers(self):
        for output in (
            'Currently playing 201 of 200 ticks. Minutes:1.00 File:example.dem',
            'Currently playing -1 of 200 ticks. Minutes:1.00 File:example.dem',
            'Currently playing 1.5 of 200 ticks. Minutes:1.00 File:example.dem',
            'Currently playing 20 of 200 ticks. Minutes:-1.00 File:example.dem',
            'Currently playing 20 of 200 ticks. Minutes:1e999 File:example.dem',
            'Currently playing 20 of 200 ticks. Minutes:nan File:example.dem',
            'Currently playing 20 of 200 ticks. Minutes:1.00 File:\nUnrelated output\n',
            'Currently playing 20 of 200 ticks. Minutes:1.00 File:   \n',
            'Syntax: demo_goto <tick> [relative] [pause]\n',
            'tick=1234\nplayback_ticks: 161418\n',
            'Demo paused at engine time 10, demo tick 555\n',
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_demo_tick(output)

    def test_last_live_status_wins_without_inferred_pause(self):
        output = 'Currently playing 50 of 200 ticks. Minutes:0.05 File:example.dem\n'
        data = parse_demo_tick(output + output.replace('playing 50', 'playing 51'))
        self.assertEqual(data["tick"], 51)
        self.assertIsNone(data["paused"])

    def test_source_backed_demo_formats(self):
        data = parse_demo_info("[Engine] Playing back demo: 'replays/match.dem' at tick 555\n")
        self.assertEqual(data, {"playing": True, "tick": 555, "name": "replays/match.dem", "paused": None})
        data = parse_demo_info("Error - Not currently playing back a demo.\n")
        self.assertFalse(data["playing"])
        with self.assertRaises(ValueError):
            parse_demo_info("tick=1200\n")
        with self.assertRaises(ValueError):
            parse_demo_info("Demo paused at engine time 10, demo tick 600\n")

    def test_pause_notifications_not_inferred_from_ticks(self):
        out = "Playing back demo: 'a.dem' at tick 555\n"
        self.assertIsNone(parse_demo_info(out)["paused"])
        self.assertTrue(parse_demo_info(out + "Demo paused at engine time 123.45, demo tick 555\n")["paused"])
        self.assertIsNone(parse_demo_info("Demo paused at engine time 123.45, demo tick 555\n" + out)["paused"])

    def test_detect_command_rejections(self):
        for output in ("Unknown command 'oops'!", "Unknown convar 'oops'!",
                       "SV: Convar 'x' is cheat protected, change ignored.",
                       "SV: Cheat command 'x' ignored. Set sv_cheats to 1 enable cheats."):
            self.assertIsNotNone(error_text(output))
        self.assertIsNone(error_text('"x" = "1"\n'))


if __name__ == "__main__":
    unittest.main()
