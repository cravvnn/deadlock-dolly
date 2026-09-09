import math
import os
import struct
import unittest
from unittest.mock import patch

from dolly import native_bridge as nb
from dolly.native_path import compile_project
from dolly.path import Keyframe, Project


class Memory(bytearray):
    def __init__(self, size):
        super().__init__(size)
        self.closed = 0
        self.on_write = None
        self.on_read = None

    def __setitem__(self, key, value):
        if self.on_write:
            self.on_write(key, value)
        super().__setitem__(key, value)

    def __getitem__(self, key):
        result = super().__getitem__(key)
        if self.on_read:
            self.on_read(key)
        return result

    def close(self):
        self.closed += 1


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.memory = Memory(nb.MAPPING_BYTES)
        self.now = 0.0
        self.after_sleep = None
        self.bridge = nb.NativeBridge.create(
            mapping_factory=lambda name, size: self.memory,
            clock=lambda: self.now, sleep=self.sleep, start_heartbeat=False,
            editor_pid=1001, token="a" * 32)
        self.bridge.bind_game(2002)
        self.project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                          Keyframe(2, 100, 30, 20, 45, 90, 5)])
        self.addCleanup(self.bridge.close)

    def sleep(self, duration):
        self.now += duration
        if self.after_sleep:
            self.after_sleep()

    def header(self):
        return nb.CONTROL.unpack(self.memory[:nb.CONTROL.size])

    def publish_status(self, *, state=2, pid=2002, ack=None, abi=1,
                       phase=0.5, message=b"Ready", sequence=2, paused=1):
        data = nb.STATUS.pack(
            nb.STATUS_MAGIC, sequence, abi, state, pid,
            self.header()[3] if ack is None else ack, 0,
            42, 10.0, 28.0, phase, 112181, paused,
            *range(7), *range(10, 17), 75.0, 70.0, 45,
            message, b"replays/example.dem", 16.8, 8.3)
        self.memory[nb.CONTROL_BYTES:nb.CONTROL_BYTES + len(data)] = data

    def respond(self):
        mode = self.header()[4]
        self.publish_status(state={0: 5, 1: 2, 2: 3, 3: 2}[mode])

    def prepare(self):
        self.after_sleep = self.respond
        return self.bridge.prepare(self.project, 0.1, 0.1, False, "replays/example.dem")

    def test_layout_matches_native_header_and_private_mapping_name(self):
        self.assertEqual(nb.CONTROL.size, 576)
        self.assertEqual(nb.STATUS.size, 992)
        self.assertEqual(self.header()[:2], (nb.CONTROL_MAGIC, 1))
        self.assertEqual(self.header()[5:7], (1001, 2002))
        factory = unittest.mock.Mock(return_value=Memory(nb.MAPPING_BYTES))
        bridge = nb.NativeBridge.create(mapping_factory=factory, start_heartbeat=False,
                                        token="b" * 32, editor_pid=1001)
        self.addCleanup(bridge.close)
        factory.assert_called_once_with("Local\\DeadlockDollyNative_" + "b" * 32, nb.MAPPING_BYTES)

    def test_prepare_uploads_complete_path_once_and_preserves_project(self):
        before = self.project.to_dict()
        result = self.prepare()
        header = self.header()
        payload = compile_project(self.project)
        self.assertEqual(result["state"], "armed")
        self.assertEqual(header[4], 1)
        self.assertEqual(header[8:12], (2, len(payload), 0.1, 0.1))
        self.assertEqual(bytes(self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET + len(payload)]), payload)
        self.bridge.play()
        self.assertEqual(self.header()[4], 2)
        self.assertEqual(self.header()[9], 0)
        self.assertEqual(bytes(self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET + len(payload)]), payload)
        self.assertEqual(self.project.to_dict(), before)

    def test_hold_preserves_native_phase_without_uploading_python_pose(self):
        self.prepare()
        self.bridge.play()
        status = self.bridge.hold()
        self.assertEqual(status["state"], "armed")
        self.assertEqual(self.header()[4], 3)
        self.assertEqual(self.header()[9], 0)

    def test_header_publication_keeps_independent_heartbeat_and_odd_sequence(self):
        observed = []
        self.bridge._beat()
        expected_heartbeat = struct.unpack_from("<Q", self.memory, 32)[0]
        def observe(key, value):
            if isinstance(key, slice) and key.start == nb.PAYLOAD_OFFSET:
                observed.append(struct.unpack_from("<I", self.memory, 12)[0])
                self.assertEqual(struct.unpack_from("<Q", self.memory, 32)[0], expected_heartbeat)
        self.memory.on_write = observe
        self.prepare()
        self.bridge.play()
        self.bridge.release()
        self.assertTrue(observed)
        self.assertTrue(all(sequence & 1 for sequence in observed))
        self.assertFalse(self.header()[2] & 1)
        self.assertEqual(struct.unpack_from("<Q", self.memory, 32)[0], expected_heartbeat)
        self.assertEqual(self.header()[3], 3)

    def test_status_reports_native_view_clock_without_unit_substitution(self):
        self.publish_status(state=4)
        status = self.bridge.status()
        self.assertTrue(status["complete"])
        self.assertEqual(status["frame_count"], 42)
        self.assertEqual(status["phase"], 0.5)
        self.assertEqual(status["engine_time"], 28)
        self.assertEqual(status["original_pose"], list(range(7)))
        self.assertEqual(status["applied_pose"], list(range(10, 17)))
        self.assertEqual(status["demo_name"], "replays/example.dem")
        self.assertEqual(status["frame_interval_ms"], 8.3)
        self.assertTrue(status["paused"])

    def test_status_retries_a_torn_snapshot_and_refuses_permanently_odd_writer(self):
        self.publish_status(phase=0.5)
        def change(key):
            if isinstance(key, slice) and key.start == nb.CONTROL_BYTES:
                self.memory.on_read = None
                self.publish_status(phase=1.5, sequence=4)
        self.memory.on_read = change
        self.assertEqual(self.bridge.status()["phase"], 1.5)
        self.publish_status(sequence=5)
        with self.assertRaisesRegex(nb.NativeBridgeError, "busy"):
            self.bridge.status()

    def test_wrong_pid_abi_state_ack_and_nonfinite_status_are_refused(self):
        for kwargs in ({"pid": 999}, {"abi": 2}, {"state": 8}, {"ack": 99},
                       {"phase": math.nan}, {"paused": 2}):
            with self.subTest(kwargs=kwargs):
                self.publish_status(**kwargs)
                with self.assertRaises(nb.NativeBridgeError):
                    self.bridge.status()
                self.assertEqual(self.bridge.diagnostics()["state"], "unavailable")

    def test_wait_rejects_stale_ack_and_is_bounded_and_late_hold_is_cancelled(self):
        self.publish_status(ack=0)
        with self.assertRaisesRegex(nb.NativeBridgeError, "acknowledge"):
            self.bridge.prepare(self.project, 0, 0.1, False, "example.dem", timeout=0.025)
        self.assertAlmostEqual(self.now, 0.025)
        self.assertEqual(self.header()[4], 0)
        self.assertEqual(self.header()[3], 2)
        with self.assertRaisesRegex(nb.NativeBridgeError, "Prepare"):
            self.bridge.play()

    def test_fault_is_actionable_and_does_not_leave_a_late_play_command(self):
        self.prepare()
        self.after_sleep = lambda: self.publish_status(state=6, message=b"View signature changed")
        with self.assertRaisesRegex(nb.NativeBridgeError, "View signature changed"):
            self.bridge.play()
        self.assertEqual(self.header()[4], 0)

    def test_release_waits_past_previous_command_fault_for_its_own_ack(self):
        self.prepare()
        previous_command = self.header()[3]
        self.publish_status(state=6, ack=previous_command, message=b"Previous shot lost its view")
        before_wait = self.now
        result = self.bridge.release()
        self.assertGreater(self.now, before_wait)
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["ack_command"], previous_command + 1)
        self.assertEqual(self.header()[4], 0)

    def test_prepare_can_recover_after_a_previous_command_fault(self):
        self.prepare()
        previous_command = self.header()[3]
        self.publish_status(state=6, ack=previous_command, message=b"Previous shot interrupted")
        before_wait = self.now
        result = self.bridge.prepare(self.project, 0.25, 0.1, False, "replays/example.dem")
        self.assertGreater(self.now, before_wait)
        self.assertEqual(result["state"], "armed")
        self.assertEqual(result["ack_command"], previous_command + 1)
        self.assertEqual(self.header()[4], 1)
        self.assertTrue(self.bridge._prepared)

    def test_startup_unsupported_remains_terminal_without_a_command_ack(self):
        self.publish_status(state=7, ack=0, message=b"Unsupported game build")
        with self.assertRaisesRegex(nb.NativeBridgeError, "Unsupported game build"):
            self.bridge.prepare(self.project, 0, 0.1, False, "replays/example.dem")
        self.assertEqual(self.now, 0)
        self.assertEqual(self.header()[4], 0)

    def test_invalid_arguments_do_not_publish_or_disturb_running_camera(self):
        self.prepare()
        self.bridge.play()
        before = bytes(self.memory[:nb.CONTROL.size])
        invalid = [(-1, 0.1, False, "a"), (0, 0, False, "a"),
                   (0, math.nan, False, "a"), (0, True, False, "a"),
                   (0, 0.1, 1, "a"), (0, 0.1, False, ""),
                   (0, 0.1, False, "a\0b"), (0, 0.1, False, "a" * 512),
                   (3, 0.1, False, "a")]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.bridge.prepare(self.project, *args)
            self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)

    def test_game_pid_cannot_be_rebound_and_same_pid_is_idempotent(self):
        self.prepare()
        before = bytes(self.memory[:nb.CONTROL.size])
        self.bridge.bind_game(2002)
        self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)
        for pid in (0, -1, True, 1 << 32, "2002", 9999):
            with self.subTest(pid=pid), self.assertRaises((ValueError, nb.NativeBridgeError)):
                self.bridge.bind_game(pid)

    def test_heartbeat_thread_starts_before_game_is_bound(self):
        with patch.object(nb.threading, "Thread") as thread:
            bridge = nb.NativeBridge.create(mapping_factory=lambda *args: Memory(nb.MAPPING_BYTES))
        self.addCleanup(bridge.close)
        thread.return_value.start.assert_called_once_with()
        self.assertEqual(bridge.game_pid, 0)
        self.assertGreater(struct.unpack_from("<Q", bridge._mapping, 32)[0], 0)

    def test_close_releases_then_stops_heartbeat_and_unmaps_once_even_after_timeout(self):
        self.prepare()
        self.after_sleep = None
        self.bridge.close()
        self.assertEqual(self.header()[4], 0)
        self.assertEqual(self.memory.closed, 1)
        self.assertTrue(self.bridge._heartbeat_stop.is_set())
        self.bridge.close()
        self.assertEqual(self.memory.closed, 1)
        with self.assertRaisesRegex(nb.NativeBridgeError, "closed"):
            self.bridge.status()

    def test_payload_limit_and_command_overflow_refused_without_partial_publication(self):
        before = bytes(self.memory[:nb.CONTROL.size])
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.bridge._publish(1, b"x" * (nb.MAX_PAYLOAD_BYTES + 1))
        self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)
        self.bridge._command = 0xFFFFFFFF
        with self.assertRaisesRegex(nb.NativeBridgeError, "exhausted"):
            self.bridge._publish(0)
        self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)

    @unittest.skipUnless(os.name == "nt", "Windows named-mapping and interlocked API smoke test")
    def test_windows_mapping_is_readable_by_a_second_handle_and_heartbeat_advances(self):
        bridge = nb.NativeBridge.create()
        self.addCleanup(bridge.close)
        self.assertIsNotNone(bridge._atomic_library)
        reader = nb.mmap.mmap(-1, nb.MAPPING_BYTES,
            tagname="Local\\DeadlockDollyNative_" + bridge.token, access=nb.mmap.ACCESS_READ)
        try:
            header = nb.CONTROL.unpack(reader[:nb.CONTROL.size])
            self.assertEqual(header[:2], (nb.CONTROL_MAGIC, nb.ABI))
            self.assertEqual(header[5], os.getpid())
            initial = struct.unpack_from("<Q", reader, 32)[0]
            deadline = nb.time.monotonic() + 2
            while struct.unpack_from("<Q", reader, 32)[0] <= initial and nb.time.monotonic() < deadline:
                nb.threading.Event().wait(0.01)
            self.assertGreater(struct.unpack_from("<Q", reader, 32)[0], initial)
            # Exercise the actual loaded CAS export as well as both exchanges.
            bridge._store(nb.CONTROL_BYTES + 8, 0xFFFFFFFE)
            self.assertEqual(bridge._load_sequence(), 0xFFFFFFFE)
            with bridge._lock:
                bridge._heartbeat = 0x100000000
                bridge._beat()
                self.assertEqual(struct.unpack_from("<Q", reader, 32)[0], 0x100000001)
            bridge._store(nb.CONTROL_BYTES + 8, 0)
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
