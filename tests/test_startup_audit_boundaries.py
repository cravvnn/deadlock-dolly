"""Refused-worker recovery, failed-write preservation and honest take audits."""
import ctypes
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from dolly import console, export_audit, file_ops
import test_core_native as native_cases

_Thread = threading.Thread
_Event = threading.Event


class StartupAuditBoundaries(unittest.TestCase):
    def test_console_refused_reader_retires_socket_and_allows_real_reader_retry(self):
        for constructor in (False, True):
            with self.subTest(constructor=constructor):
                client = console.ConsoleClient(protocol='netcon')
                sock, peer = socket.socketpair()
                reader = _Thread(target=lambda: None)
                error = RuntimeError('reader refused')
                try:
                    with patch.object(console.socket, 'create_connection', return_value=sock), \
                            patch.object(socket.socket, 'setsockopt', return_value=None), \
                            patch.object(console.threading, 'Thread', side_effect=error if constructor else None,
                                         return_value=reader), \
                            patch.object(reader, 'start', side_effect=error):
                        with self.assertRaises(RuntimeError) as caught:
                            client.connect()
                    self.assertIs(caught.exception, error)
                    self.assertFalse(client.is_connected)
                    self.assertIsNone(client._reader)
                    self.assertEqual(sock.fileno(), -1)
                    client.close()
                    retry, other = socket.socketpair()
                    try:
                        with patch.object(console.socket, 'create_connection', return_value=retry), \
                                patch.object(socket.socket, 'setsockopt', return_value=None):
                            client.connect()
                        accepted = client._reader
                        self.assertTrue(client.is_connected)
                        client.close()
                        self.assertFalse(accepted.is_alive())
                        self.assertEqual(retry.fileno(), -1)
                    finally:
                        other.close()
                finally:
                    # Also permit a failing baseline/mutation to release owned fixtures.
                    if client._reader is reader:
                        client._reader = None
                    client.close()
                    sock.close()
                    peer.close()

    def test_playback_start_refusal_preserves_error_cleanup_and_retry(self):
        for mode in ('native', 'console', 'pov'):
            with self.subTest(mode=mode):
                case = native_cases.NativeWorkflows()
                case.setUp()
                try:
                    if mode == 'console':
                        case.session.native = None
                    if mode == 'pov':
                        case.controller._pov_active = True
                        case.controller._prepare_native_shot_replay = lambda: None
                        case.controller._wait_for_recorder_ready = lambda: None
                        prepare = case.bridge.prepare
                        case.bridge.prepare = lambda *args, **kwargs: prepare(*args)
                    with patch.object(threading, 'Event', _Event):
                        never_started = _Thread(target=lambda: None)
                    error = RuntimeError('playback refused')
                    case.worker.start.side_effect = error
                    case.worker.join.side_effect = never_started.join
                    play = (lambda: case.controller.play_pov(case.project)) if mode == 'pov' else (
                        lambda: case.controller.play(case.project, frozen=True))
                    with self.assertRaises(RuntimeError) as caught:
                        play()
                    self.assertIs(caught.exception, error)
                    self.assertIsNone(case.controller._thread)
                    if mode == 'pov':
                        self.assertIn('native.release', case.bridge.events)
                        self.assertFalse(case.controller._pov_active)
                    case.controller._halt(native_action='release')
                    case.worker.start.side_effect = None
                    case.worker.join.side_effect = None
                    if mode == 'pov':
                        case.controller._pov_active = True
                    play()
                    self.assertIs(case.controller._thread, case.worker)
                    case.controller._halt(native_action='release')
                    self.assertIsNone(case.controller._thread)
                finally:
                    case.controller._thread = None
                    case.doCleanups()

    def test_failed_config_replace_keeps_bytes_mode_and_primary_error(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'owned.cfg'
            target.write_bytes(b'previous settings')
            if os.name == 'nt':
                from ctypes import wintypes as w
                api = ctypes.WinDLL('kernel32', use_last_error=True)
                api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                           w.DWORD, w.DWORD, w.HANDLE]
                api.CreateFileW.restype = w.HANDLE
                api.CloseHandle.argtypes = [w.HANDLE]
                api.CloseHandle.restype = w.BOOL
                os.chmod(target, stat.S_IREAD)
                handle = api.CreateFileW(str(target), 0x80000000, 3, None, 3, 0, None)
                self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
                try:
                    with self.assertRaises(PermissionError):
                        file_ops._atomic_write(target, b'replacement', stat.S_IREAD)
                    self.assertEqual(target.read_bytes(), b'previous settings')
                    self.assertFalse(target.stat().st_mode & stat.S_IWRITE)
                finally:
                    api.CloseHandle(handle)
                    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
                os.chmod(target, stat.S_IREAD)
                try:
                    file_ops._atomic_write(target, b'replacement', stat.S_IREAD)
                    self.assertEqual(target.read_bytes(), b'replacement')
                    self.assertFalse(target.stat().st_mode & stat.S_IWRITE)
                    readonly = stat.S_IMODE(target.stat().st_mode)
                    chmod = os.chmod
                    primary = PermissionError('replacement refused')
                    def denied_restore(path, mode):
                        if Path(path) == target and mode == readonly:
                            raise OSError('mode restoration refused')
                        return chmod(path, mode)
                    with patch.object(os, 'replace', side_effect=primary), \
                            patch.object(os, 'chmod', side_effect=denied_restore):
                        with self.assertRaises(PermissionError) as caught:
                            file_ops._atomic_write(target, b'another', stat.S_IREAD)
                    self.assertIs(caught.exception, primary)
                    self.assertEqual(target.read_bytes(), b'replacement')
                finally:
                    os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
            else:
                with patch.object(os, 'replace', side_effect=PermissionError('refused')):
                    with self.assertRaises(PermissionError):
                        file_ops._atomic_write(target, b'replacement')
                self.assertEqual(target.read_bytes(), b'previous settings')

    def test_decoded_audit_reports_invalid_color_counts_and_keeps_valid_take(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'color.mov').write_bytes(b'inert fixture; no decoder invoked')
            (folder/'shot.json').write_text(json.dumps(dict(
                format=export_audit.SIDECAR_FORMAT, version=1, fixed_step=True,
                frames_written=1, first_frame=0, last_frame=0, video_file='color.mov')),
                encoding='utf-8')
            for count in (0, -1, True, None, 'invalid', 1):
                with self.subTest(count=count):
                    (folder/'decoded-audit.json').write_text(json.dumps(dict(streams=dict(
                        color=dict(decoded_frames=count), depth=dict(decoded_frames=1)))), encoding='utf-8')
                    result = export_audit.audit_take(folder)
                    self.assertEqual(result['ok'], type(count) is int and count > 0)
                    if not result['ok']:
                        self.assertTrue(any('color' in finding for finding in result['findings']))

    def test_audit_json_contains_recursion_and_enforces_actual_read_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder/'shot.json'
            raw = b'[' * 12000 + b'0' + b']' * 12000
            target.write_bytes(raw)
            result = export_audit.audit_take(folder)
            self.assertFalse(result['ok'])
            self.assertTrue(any('unreadable' in finding for finding in result['findings']))
            self.assertEqual(target.read_bytes(), raw)
            target.write_bytes(b'{}')
            opened = Path.open
            oversized = b'{}' + b' ' * (1024 * 1024 + 32)
            reads = []
            class GrowingStream(io.BytesIO):
                def read(self, count=-1):
                    reads.append(count)
                    return super().read(count)
            def growing(path, *args, **kwargs):
                if path == target:
                    if args and args[0] == 'rb':
                        return GrowingStream(oversized)
                    return io.StringIO(oversized.decode())
                return opened(path, *args, **kwargs)
            with patch.object(Path, 'open', growing):
                self.assertIsNone(export_audit._load(target))
            self.assertEqual(reads, [1024 * 1024 + 1])
