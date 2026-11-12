"""Bridge teardown/readback using owned memory and real Python owner methods."""
import mmap
import struct
import sys
import threading
import unittest
from unittest.mock import patch

from dolly import native_bridge as native


class BridgeLifetime(unittest.TestCase):
    def test_close_waits_for_a_heartbeat_already_past_its_open_check(self):
        memory = mmap.mmap(-1, native.MAPPING_BYTES)
        bridge = native.NativeBridge.create(mapping_factory=lambda n, s: memory, start_heartbeat=False)
        paused, resume, closed = threading.Event(), threading.Event(), threading.Event()
        errors = []
        store = bridge._store.__code__

        def trace(frame, event, arg):
            if frame.f_code is store and event == 'call':
                paused.set()
                if not resume.wait(5):
                    raise RuntimeError('test failed to resume heartbeat')
            return trace

        def beat():
            try:
                sys.settrace(trace)
                bridge._heartbeat_loop()
            except BaseException as error:
                errors.append(error)
            finally:
                sys.settrace(None)

        def close():
            try:
                bridge.close()
            except BaseException as error:
                errors.append(error)
            finally:
                closed.set()

        worker = threading.Thread(target=beat)
        bridge._thread = worker
        closer = threading.Thread(target=close)
        try:
            worker.start()
            self.assertTrue(paused.wait(3))
            closer.start()
            self.assertTrue(bridge._heartbeat_stop.wait(3))
            # Exceeds the real0.5sjoin; no mock of either mmap.close or _store.
            self.assertFalse(closed.wait(0.8), 'mapping retired while heartbeat still owns its write')
        finally:
            resume.set()
            worker.join(3)
            if closer.ident is not None:
                closer.join(3)
            elif not bridge._closed:
                bridge.close()
        self.assertFalse(worker.is_alive() or closer.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(memory.closed and bridge._closed)

    def test_heartbeat_progresses_independently_of_command_publication(self):
        bridge = native.NativeBridge.create(mapping_factory=lambda n, s: bytearray(s), start_heartbeat=False)
        done, errors = threading.Event(), []

        def beat():
            try:
                bridge._beat()
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=beat)
        initial = bridge._heartbeat
        try:
            with bridge._lock:
                worker.start()
                progressed = done.wait(0.5)
            worker.join(2)
            self.assertTrue(progressed, 'command lock starved heartbeat')
            self.assertFalse(worker.is_alive() or errors)
            value = struct.unpack_from('<Q', bridge._mapping, 32)[0]
            self.assertEqual(value, initial + 1)
            bridge._publish(4)
            self.assertEqual(struct.unpack_from('<Q', bridge._mapping, 32)[0], value)
        finally:
            bridge.close()

    def test_torn_status_cannot_turn_an_old_fault_into_a_current_command_refusal(self):
        def packet(sequence, ack, state):
            values = list(native.STATUS.unpack(bytes(native.STATUS.size)))
            values[:7] = [native.STATUS_MAGIC, sequence, native.ABI,
                          native.STATES.index(state), 123, ack, 0]
            return native.STATUS.pack(*values)

        class Memory(bytearray):
            first = True

            def __getitem__(self, key):
                if (self.first and isinstance(key, slice) and key.start == native.CONTROL_BYTES
                        and key.stop == native.CONTROL_BYTES + native.STATUS.size):
                    self.first = False
                    torn = packet(2, 2, 'fault')
                    self[native.CONTROL_BYTES:native.CONTROL_BYTES + native.STATUS.size] = packet(4, 2, 'stopped')
                    return torn
                return super().__getitem__(key)

        memory = Memory(native.MAPPING_BYTES)
        bridge = native.NativeBridge.create(mapping_factory=lambda n, s: memory,
                                            start_heartbeat=False, sleep=lambda _: None)
        bridge.game_pid, bridge._command = 123, 2
        memory[native.CONTROL_BYTES:native.CONTROL_BYTES + native.STATUS.size] = packet(2, 1, 'fault')
        try:
            result = bridge._wait(2, {'stopped'}, 1)
            self.assertEqual((result['ack_command'], result['state']), (2, 'stopped'))
        finally:
            with patch.object(bridge, 'release', return_value=None):
                bridge.close()
