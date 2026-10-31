import ctypes
from ctypes import wintypes as wt
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from dolly.discord_ipc import DiscordRpc, FrameReader, MAX_FRAME, WindowsPipe, frame
from dolly.discord_presence import Activity, RichPresence, activity_for


class FakePipe:
    def __init__(self, *, error=False, silent=False):
        self.sent = []
        self.incoming = bytearray()
        self.closed = False
        self.error, self.silent = error, silent

    def send(self, data):
        opcode, length = struct.unpack_from('<II', data)
        self.assert_frame_length = length == len(data)-8
        value = json.loads(data[8:])
        self.sent.append((opcode, value))
        if self.silent: return
        if opcode == 0:
            self.incoming.extend(frame(3, {'ping': 'unchanged'}))
            self.incoming.extend(frame(1, {'cmd': 'DISPATCH', 'evt': 'READY', 'data': {'user': 'not logged'}}))
        elif opcode == 1:
            self.incoming.extend(frame(1, {'cmd': value['cmd'], 'nonce': value['nonce'],
                'evt': 'ERROR' if self.error else None}))

    def receive(self):
        # Exercise partial headers/bodies through the actual RPC reader.
        data = bytes(self.incoming[:7])
        del self.incoming[:7]
        return data

    def close(self):
        self.closed = True


class DiscordProtocolTests(unittest.TestCase):
    def test_handshake_ack_ping_and_clear_use_actual_framing(self):
        pipe = FakePipe()
        with patch('dolly.discord_ipc.RESPONSE_TIMEOUT', 3):
            rpc = DiscordRpc('1564091346917138512', threading.Event(), connector=lambda: pipe)
            rpc.set_activity({'details': 'Editing a campath'}, threading.Event())
        self.assertEqual(pipe.sent[0], (0, {'v': 1, 'client_id': '1564091346917138512'}))
        self.assertIn((4, {'ping': 'unchanged'}), pipe.sent)
        request = next(value for opcode, value in pipe.sent if opcode == 1)
        self.assertEqual(request['args'], {'pid': os.getpid(), 'activity': {'details': 'Editing a campath'}})
        rpc.close()
        self.assertIsNone(pipe.sent[-1][1]['args']['activity'])
        self.assertTrue(pipe.closed)
        self.assertTrue(pipe.assert_frame_length)

    def test_frame_limits_unknown_opcodes_and_fragmented_messages(self):
        reader = FrameReader()
        payload = frame(1, {'details': 'Camera 🎥'})
        result = []
        for byte in payload:
            result.extend(reader.feed(bytes((byte,))))
        self.assertEqual(json.loads(result[0][1]), {'details': 'Camera 🎥'})
        for header in (struct.pack('<II', 1, MAX_FRAME+1), struct.pack('<II', 9, 0)):
            with self.subTest(header=header), self.assertRaises(ValueError):
                FrameReader().feed(header)

    def test_rejected_activity_times_out_and_cancelled_handshakes_release_handles(self):
        pipe = FakePipe(error=True)
        rpc = DiscordRpc('1564091346917138512', threading.Event(), connector=lambda: pipe)
        with self.assertRaisesRegex(ValueError, 'rejected'):
            rpc.set_activity({'details': 'Editing'}, threading.Event())
        rpc.close()
        self.assertTrue(pipe.closed)
        silent = FakePipe(silent=True)
        with patch('dolly.discord_ipc.RESPONSE_TIMEOUT', 0), self.assertRaises(TimeoutError):
            DiscordRpc('1564091346917138512', threading.Event(), connector=lambda: silent)
        self.assertTrue(silent.closed)
        cancelled = threading.Event(); cancelled.set()
        pipe = FakePipe()
        with self.assertRaises(InterruptedError):
            DiscordRpc('1564091346917138512', cancelled, connector=lambda: pipe)
        self.assertTrue(pipe.closed)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows named-pipe boundary')
    def test_actual_windows_pipe_roundtrip_disconnect_and_full_buffer_never_wait(self):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
                                         wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_void_p]
        api.CreateNamedPipeW.restype = wt.HANDLE
        api.CloseHandle.argtypes = [wt.HANDLE]
        api.DisconnectNamedPipe.argtypes = [wt.HANDLE]
        for name in ('ReadFile', 'WriteFile'):
            getattr(api, name).argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                           ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        path = r'\\.\pipe\DollyPresenceTest-' + uuid.uuid4().hex
        server = api.CreateNamedPipeW(path, 3, 1, 1, 4096, 4096, 0, None)
        self.assertNotEqual(server, ctypes.c_void_p(-1).value)
        self.addCleanup(api.CloseHandle, server)
        client = WindowsPipe(path)
        self.addCleanup(client.close)
        self.assertEqual(client.receive(), b'')
        client.send(b'hello')
        buffer = ctypes.create_string_buffer(32); count = wt.DWORD()
        self.assertTrue(api.ReadFile(server, buffer, 32, ctypes.byref(count), None))
        self.assertEqual(buffer.raw[:count.value], b'hello')
        answer = ctypes.create_string_buffer(b'ready')
        self.assertTrue(api.WriteFile(server, answer, 5, ctypes.byref(count), None))
        self.assertEqual(client.receive(), b'ready')
        start = time.monotonic()
        with self.assertRaises(OSError):
            client.send(b'x' * (4*MAX_FRAME))
        self.assertLess(time.monotonic()-start, 1)
        self.assertTrue(api.DisconnectNamedPipe(server))
        with self.assertRaises(OSError):
            client.receive()


class PresenceBehaviorTests(unittest.TestCase):
    def test_gui_off_switch_saves_during_export_and_failed_save_preserves_choice(self):
        from dolly.gui import DollyApp
        from dolly.settings import AppSettings, load_settings, save_settings
        app = DollyApp.__new__(DollyApp)
        app.app_settings = AppSettings(game_path='B:/Deadlock', export_folder='E:/Personal exports')
        app.busy = app.playing = True
        app.discord_presence = Mock()
        app.discord_presence.get.return_value = False
        app._refresh_discord_presence = Mock()
        app._error = Mock()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'settings.json'
            with patch('dolly.gui.save_settings', side_effect=lambda value: save_settings(value, target)):
                app._save_discord_preference()
            self.assertFalse(load_settings(target).discord_presence)
            self.assertEqual(load_settings(target).export_folder, 'E:/Personal exports')
            app._refresh_discord_presence.assert_called_once()
            app.discord_presence.get.return_value = True
            with patch('dolly.gui.save_settings', side_effect=OSError('read only')):
                app._save_discord_preference()
            self.assertFalse(app.app_settings.discord_presence)
            app.discord_presence.set.assert_called_once_with(False)
            app._error.assert_called_once()

    def test_activity_priority_and_private_fields_do_not_reach_the_payload(self):
        private = {'demo': 'private-match.dem', 'project': 'secret client', 'message': 'C:/private/file',
                   'startup_stage': 'editing_ready', 'game_running': True}
        self.assertEqual(activity_for(private, {}, cameras=3), Activity('Editing a campath', '3 cameras in the shot'))
        self.assertEqual(activity_for({**private, 'playing': True}, {}), Activity('Previewing a campath'))
        for state in ('starting', 'recording', 'finalizing'):
            expected = 'Saving a video export' if state == 'finalizing' else 'Exporting video'
            self.assertEqual(activity_for({**private, 'playing': True}, {'state': state, 'path': 'secret.mov'}), Activity(expected))
        self.assertEqual(activity_for(private, {}, processing_players=True), Activity('Processing the Players layer'))
        self.assertEqual(activity_for({'game_running': True, 'startup_stage': 'preloading'}, {}), Activity('Preparing a replay'))
        self.assertEqual(activity_for({'game_ui_visible': True}, {}), Activity('Browsing a replay'))
        self.assertEqual(activity_for({}, {}), Activity('Ready to create'))

    def test_publisher_coalesces_changes_keeps_session_time_and_clears_when_disabled(self):
        clock = [100.0]
        rpc = Mock()
        service = RichPresence(rpc_factory=Mock(return_value=rpc), clock=lambda: clock[0])
        service.update(Activity('Editing a campath'), enabled=True)
        service._step()
        first = rpc.set_activity.call_args.args[0]
        self.assertEqual(first['details'], 'Editing a campath')
        service.update(Activity('Previewing a campath'), enabled=True)
        service._step()
        service.update(Activity('Exporting video'), enabled=True)
        service._step()
        self.assertEqual(rpc.set_activity.call_count, 1)
        clock[0] += 15
        service._step()
        final = rpc.set_activity.call_args.args[0]
        self.assertEqual(final['details'], 'Exporting video')
        self.assertEqual(first['timestamps'], final['timestamps'])
        self.assertEqual([b['label'] for b in final['buttons']], ['Get Dolly', 'Editing community'])
        self.assertNotIn('secrets', final)
        service.update(Activity('Ready to create'), enabled=False)
        service._step()
        rpc.close.assert_called_once()
        self.assertEqual(service.status, 'Off')

    def wait_for(self, predicate):
        deadline = time.monotonic()+3
        while not predicate() and time.monotonic()<deadline:
            threading.Event().wait(0.01)
        self.assertTrue(predicate())

    def test_missing_discord_retries_with_backoff_and_worker_stops_after_close(self):
        clock = [100.0]
        rpc = Mock()
        factory = Mock(side_effect=[OSError('Discord closed'), rpc])
        service = RichPresence(rpc_factory=factory, clock=lambda: clock[0])
        self.addCleanup(service.close)
        service.update(Activity('Ready to create'), enabled=True)
        service.start()
        self.wait_for(lambda: service.status == 'Waiting for Discord')
        service.update(Activity('Editing a campath'), enabled=True)
        self.assertEqual(factory.call_count, 1)
        clock[0] += 31
        service.update(Activity('Previewing a campath'), enabled=True)
        self.wait_for(lambda: rpc.set_activity.call_count == 1)
        service.close()
        self.assertFalse(service._thread.is_alive())
        rpc.close.assert_called_once()
        service.update(Activity('Exporting video'), enabled=True)
        self.assertEqual(rpc.set_activity.call_count, 1)

    def test_disabling_interrupts_a_stalled_discord_handshake_without_blocking_ui(self):
        pipe = FakePipe(silent=True)
        service = RichPresence(rpc_factory=lambda app, stop: DiscordRpc(app, stop, connector=lambda: pipe))
        self.addCleanup(service.close)
        service.update(Activity('Ready to create'), enabled=True)
        service.start()
        self.wait_for(lambda: bool(pipe.sent))
        service.update(Activity('Ready to create'), enabled=False)
        self.wait_for(lambda: pipe.closed and service.status == 'Off')
        self.assertFalse(any(opcode == 1 for opcode, value in pipe.sent))


if __name__ == '__main__':
    unittest.main()
