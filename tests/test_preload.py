"""Dashboard counts alone must never authorize automatic replay loading."""
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import preload


class PreloadTests(unittest.TestCase):
    def monitor(self, *, resource=0x300000, job=0, completed=14, total=14, done=1):
        monitor = preload.PreloadMonitor.__new__(preload.PreloadMonitor)
        monitor.session = SimpleNamespace(running=True)
        monitor.base, monitor.resource_base = 0x180000000, 0x190000000
        raw = bytearray(64)
        struct.pack_into('<Q', raw, 0, monitor.base+preload.MANAGER_VTABLE)
        struct.pack_into('<ii', raw, 0x24, completed, total)
        struct.pack_into('<Q', raw, 0x30, resource)
        struct.pack_into('<i', raw, 0x38, job)
        blocks = {monitor.base+preload.MANAGER: bytes(raw), resource+0x44: bytes([done])}
        pointers = {monitor.base+preload.RESOURCE_GLOBAL: 0x400000,
                    monitor.base+preload.INTRO_GLOBAL: 0,
                    0x400000: monitor.resource_base+preload.RESOURCE_VTABLE,
                    monitor.resource_base+preload.RESOURCE_VTABLE+0xc0: monitor.resource_base+preload.RESOURCE_QUERY}
        monitor.memory = Mock()
        monitor.intro_sent = False
        monitor.memory.read.side_effect = lambda address, size: blocks[address][:size]
        monitor.memory.pointer.side_effect = pointers.__getitem__
        return monitor, blocks, pointers

    def test_equal_counts_before_preload_started_are_not_ready(self):
        m, _, _ = self.monitor(resource=0)
        self.assertFalse(m.sample()['ready'])
        self.assertFalse(m.sample()['started'])

    def test_active_job_and_incomplete_resource_each_block_ready(self):
        for kwargs in ({'job': -2147483608}, {'done': 0}, {'completed': 4}):
            with self.subTest(kwargs=kwargs):
                m, _, _ = self.monitor(**kwargs)
                self.assertFalse(m.sample()['ready'])

    def test_completed_started_preload_is_ready(self):
        m, _, _ = self.monitor()
        self.assertTrue(m.sample()['ready'])

    def test_status_transition_is_not_coherent(self):
        m, blocks, _ = self.monitor()
        raw = blocks[m.base+preload.MANAGER]
        m.memory.read.side_effect = [raw, raw[:-1]+b'\x01']
        self.assertEqual(m.sample(), {'coherent': False})

    def test_changed_resource_query_fails_closed(self):
        m, _, pointers = self.monitor()
        pointers[m.resource_base+preload.RESOURCE_VTABLE+0xc0] += 1
        with self.assertRaisesRegex(preload.PreloadError, 'query differs'):
            m.sample()

    def test_invalid_counter_or_type_fails_closed(self):
        m, _, _ = self.monitor(completed=-1)
        with self.assertRaisesRegex(preload.PreloadError, 'counters'):
            m.sample()
        m, blocks, _ = self.monitor()
        raw = blocks[m.base+preload.MANAGER]
        blocks[m.base+preload.MANAGER] = b'\0'*8+raw[8:]
        with self.assertRaisesRegex(preload.PreloadError, 'object type'):
            m.sample()

    def test_dead_process_is_never_read(self):
        m, _, _ = self.monitor()
        m.session.running = False
        with self.assertRaisesRegex(preload.PreloadError, 'closed'):
            m.sample()
        m.memory.read.assert_not_called()

    def test_missing_development_flags_rejected_before_process_access(self):
        session = SimpleNamespace(running=True, command=('deadlock.exe',), process=Mock())
        with patch('dolly.preload._Memory') as memory:
            with self.assertRaisesRegex(preload.PreloadError, 'Dolly-owned'):
                preload.PreloadMonitor(session)
            memory.assert_not_called()

    def test_unknown_module_hash_rejected_before_process_access(self):
        command = (str(Path('/game/bin/win64/deadlock.exe').resolve()), '-dev', '-insecure')
        session = SimpleNamespace(running=True, command=command,
                                  process=SimpleNamespace(args=command), owns_console_port=lambda: True)
        with patch.object(Path, 'read_bytes', return_value=b'unreviewed build'), \
                patch('dolly.preload._Memory') as memory:
            with self.assertRaisesRegex(preload.PreloadError, 'not supported'):
                preload.PreloadMonitor(session)
            memory.assert_not_called()

    def test_close_is_idempotent(self):
        m, _, _ = self.monitor()
        memory = m.memory
        m.close()
        m.close()
        memory.close.assert_called_once()

    def test_automatic_intro_input_requires_interactive_phase_and_is_once_only(self):
        m, _, _ = self.monitor()
        with patch('dolly.preload._post_intro_escape') as send:
            for sample in ({'coherent': False}, {'coherent': True, 'started': True, 'intro_phase': 2},
                           {'coherent': True, 'started': False, 'intro_phase': 1},
                           {'coherent': True, 'started': False, 'intro_phase': 3}):
                m.sample = Mock(return_value=sample)
                self.assertFalse(m.advance_intro())
            send.assert_not_called()
            m.sample = Mock(return_value={'coherent': True, 'started': False, 'intro_phase': 2})
            self.assertTrue(m.advance_intro())
            self.assertFalse(m.advance_intro())
            send.assert_called_once_with(m.session)

    def test_failed_intro_send_is_not_repeated_blindly(self):
        m, _, _ = self.monitor()
        m.sample = Mock(return_value={'coherent': True, 'started': False, 'intro_phase': 2})
        with patch('dolly.preload._post_intro_escape', side_effect=preload.PreloadError('failed')) as send:
            with self.assertRaises(preload.PreloadError):
                m.advance_intro()
            self.assertFalse(m.advance_intro())
            send.assert_called_once()

    def test_intro_phase_comes_from_verified_live_object(self):
        m, blocks, pointers = self.monitor(resource=0)
        intro = 0x500000
        pointers[m.base+preload.INTRO_GLOBAL] = intro
        state = bytearray(0x84)
        struct.pack_into('<Q', state, 0, m.base+preload.INTRO_VTABLE)
        struct.pack_into('<i', state, 0x80, 2)
        blocks[intro], blocks[intro+0x80] = bytes(state), bytes(state[0x80:])
        self.assertEqual(m.sample()['intro_phase'], 2)
        blocks[intro] = b'\0'*8+bytes(state[8:])
        with self.assertRaisesRegex(preload.PreloadError, 'Intro object type'):
            m.sample()

    def test_intro_key_pair_targets_only_the_owned_window(self):
        api = Mock()
        session = SimpleNamespace(pid=42, running=True)
        api.GetWindowTextW.side_effect = lambda hwnd, text, size: setattr(text, 'value', 'Deadlock') or 8
        api.GetWindowThreadProcessId.side_effect = lambda hwnd, owner: setattr(owner._obj, 'value',
                                                                              42 if hwnd == 100 else 77) or 1
        api.IsWindowVisible.return_value = True
        api.EnumWindows.side_effect = lambda visit, _: visit(200, 0) and visit(100, 0)
        api.PostMessageW.return_value = True
        decorator = lambda *args: lambda function: function
        with patch('dolly.preload.ctypes.WinDLL', return_value=api, create=True), \
                patch('dolly.preload.ctypes.WINFUNCTYPE', side_effect=decorator, create=True):
            preload._post_intro_escape(session)
        self.assertEqual([call.args for call in api.PostMessageW.call_args_list],
                         [(100, 0x100, 0x1b, 0x00010001), (100, 0x101, 0x1b, 0xc0010001)])

    def test_missing_owned_window_never_sends_input(self):
        api = Mock()
        api.EnumWindows.return_value = True
        decorator = lambda *args: lambda function: function
        with patch('dolly.preload.ctypes.WinDLL', return_value=api, create=True), \
                patch('dolly.preload.ctypes.WINFUNCTYPE', side_effect=decorator, create=True):
            with self.assertRaisesRegex(preload.PreloadError, 'owned Deadlock intro window'):
                preload._post_intro_escape(SimpleNamespace(pid=42, running=True))
        api.PostMessageW.assert_not_called()


if __name__ == '__main__':
    unittest.main()
