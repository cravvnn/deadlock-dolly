"""Seven owner contracts for capture helpers and native bitmap admission."""
import contextlib
import ctypes
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from dolly import camera_bundle, clip_audio, hotkey, movie_audio, runtime, source2viewer
from dolly import panorama_assets
from dolly.ui import fade


class CaptureBoundaryTests(unittest.TestCase):
    def test_refused_shortcut_worker_can_stop_and_restart(self):
        helper = hotkey.CaptureHotkey(lambda: None, lambda: None)
        refused = RuntimeError('thread refused before starting')
        with patch.object(hotkey.threading.Thread, 'start', side_effect=refused):
            with self.assertRaises(RuntimeError) as caught:
                helper.start()
        self.assertIs(caught.exception, refused)
        helper.stop()
        api = NS(key_down=lambda vk: False, foreground_pid=lambda: None)
        with patch.object(hotkey, '_WindowsHotkeyAPI', return_value=api):
            try:
                helper.start()
                self.assertTrue(helper.running)
            finally:
                helper.stop()
        # A readiness timeout is a different boundary: the accepted worker
        # still needs to be owned and joined.
        helper._run = lambda: helper._stop.wait(2)
        with patch.object(helper._ready, 'wait', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                helper.start()
        self.assertIsNotNone(helper._thread)
        helper.stop()
        self.assertFalse(helper._thread.is_alive())

    def test_movie_conversions_use_external_environment_and_unwind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for mapped, fail in ((False, False), (True, False), (True, True)):
                with self.subTest(mapped=mapped, fail=fail):
                    take = root / f'{mapped}-{fail}'
                    take.mkdir()
                    work = take / 'work'
                    work.mkdir()
                    movie_root = take / 'movie'
                    movie_root.mkdir()
                    capture = movie_root / 'take'
                    capture.mkdir()
                    owner = movie_audio.MovieWave(take / 'video.mp4', take / 'game/bin/win64/deadlock.exe',
                        output_context=movie_audio.MovieOutputContext(movie_root, lambda controller: None))
                    owner.folder = owner._capture_folder = capture
                    owner._capture_name = owner.name
                    owner.started = True
                    source = capture / (owner.name + '.wav')
                    sf.write(source, np.zeros((441, 2)), 44100)
                    state = dict(active=False, enters=0, exits=0, calls=0)
                    environment = {'OWNED_CONVERSION': '1'}
                    @contextlib.contextmanager
                    def context():
                        state['active'] = True
                        state['enters'] += 1
                        try:
                            yield environment
                        finally:
                            state['active'] = False
                            state['exits'] += 1
                    def convert(command, **options):
                        self.assertTrue(state['active'])
                        self.assertIs(options.get('env'), environment)
                        state['calls'] += 1
                        if fail and state['calls'] == 2:
                            raise OSError('final spawn refused')
                        sf.write(command[-1], np.zeros((480, 2)), 48000)
                        return NS(returncode=0, stderr='')
                    controller = NS(_request=lambda *args, **kwargs: 'Finishing movie owned fixture')
                    with patch.object(runtime, 'external_program_environment', context), \
                            patch.object(clip_audio, '_ffmpeg', return_value=take / 'ffmpeg.exe'), \
                            patch.object(movie_audio, '_movie_sample_windows', return_value=(48000, [(0, 480)])), \
                            patch.object(movie_audio.subprocess, 'run', side_effect=convert):
                        if fail:
                            with self.assertRaisesRegex(OSError, 'final spawn refused'):
                                owner.finish(controller, .01, work, video_status={})
                            self.assertTrue(source.is_file())
                        else:
                            result = owner.finish(controller, .01, work, video_status={} if mapped else None)
                            self.assertEqual(sf.info(result).frames, 480)
                            self.assertFalse(source.exists())
                    self.assertFalse(state['active'])
                    self.assertEqual(state['enters'], state['exits'])
                    self.assertEqual(state['calls'], 2 if mapped else 1)

    def test_projection_rejects_nonfinite_pixels_but_keeps_offscreen(self):
        view = camera_bundle.View((1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1),
                                  (0, 0, 0), (0, 1, 0), (0, 0, -1))
        for scale in (1, 1e308):
            record = camera_bundle.CameraRecord(0, 1, 0, 0, 'complete', view,
                (scale, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0), (0, -1, 1, 0), 0, 1, 1, None)
            bundle = camera_bundle.CameraBundle(True, 1, 8192, 4096, 8192, 4096, (record,))
            self.assertEqual(camera_bundle.validate_bundle(bundle), [])
            center = bundle.project_point(record, (0, 0, -1))
            self.assertEqual((center.x, center.y, center.distance), (4096, 2048, 1))
            self.assertIsNone(bundle.project_point(record, (0, 0, 1)))
            if scale == 1:
                self.assertEqual(bundle.project_point(record, (4, 0, -1)).x, 20480)
            else:
                self.assertIsNone(bundle.project_point(record, (1, 0, -1)))

    def test_vpk_listing_rejects_duplicate_matching_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            index = Path(folder) / 'pak01_dir.vpk'
            def write(names):
                tree = b'txt\0materials\0'
                for offset, name in enumerate(names):
                    tree += name.encode() + b'\0' + struct.pack('<IHHIIH', 0, 0, 0, offset, 1, 0xffff)
                tree += b'\0\0\0'
                index.write_bytes(struct.pack('<III', 0x55aa1234, 1, len(tree)) + tree)
            write(['a', 'a'])
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                panorama_assets.list_vpk_entries(index, prefixes=('materials/',))
            write(['a', 'b'])
            self.assertEqual(panorama_assets.list_vpk_entries(index, prefixes=('materials/',)),
                             {'materials/a.txt': (0, 0, 1), 'materials/b.txt': (0, 1, 1)})

    def test_viewer_discovery_continues_after_inaccessible_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            denied, valid = root / 'blocked.exe', root / 'valid.exe'
            valid.write_bytes(b'owned fixture, never executed')
            original = Path.is_file
            def available(path):
                if path in (denied, root / 'cache/Source2Viewer-CLI.exe'):
                    raise PermissionError('inaccessible optional candidate')
                return original(path)
            with patch.object(Path, 'is_file', available), \
                    patch.object(source2viewer, '_candidate_paths', return_value=[denied, valid]), \
                    patch.object(source2viewer, 'cache_root', return_value=root / 'cache'):
                self.assertEqual(source2viewer.find_viewer(), valid)
                self.assertEqual(source2viewer.ensure_viewer(), valid)
            with patch.object(Path, 'is_file', available), \
                    patch.object(source2viewer, '_candidate_paths', return_value=[]), \
                    patch.object(source2viewer, 'cache_root', return_value=root / 'cache'), \
                    patch.object(source2viewer.shutil, 'which', return_value=str(valid)):
                self.assertEqual(source2viewer.find_viewer(), valid)

    def test_fade_clips_offscreen_spans_and_rejects_wrong_buffer_size(self):
        spans = [(-10, -2, 0, 2), (20, 30, 0, 2), (2, 4, 0, 2)]
        bits = fade.render_rows(10, 2, spans, (60, 90, 120), 'top')
        expected = bytes(8) + bytes((120, 90, 60, 255)) * 2 + bytes(24) + bytes(40)
        self.assertEqual(bits, expected)
        backend = object.__new__(fade.Win32Backend)
        backend.ctypes, backend.wintypes = object(), object()
        backend._surface = Mock()
        with self.assertRaises(ValueError):
            backend.update({}, 0, 0, 10, 2, bytes(84), 255)
        backend._surface.assert_not_called()

    def test_surface_admission_cleans_each_provisional_resource(self):
        # API refusals are simulated here; real Windows GDI acknowledgments
        # and process resource counts are a separate platform qualification.
        class Header(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in
                        ('biSize', 'biWidth', 'biHeight', 'biPlanes', 'biBitCount', 'biCompression')]
        for fault in ('dc', 'bitmap', 'bits', 'select', 'none'):
            with self.subTest(fault=fault):
                backend = object.__new__(fade.Win32Backend)
                backend.ctypes, backend.BITMAPINFOHEADER = ctypes, Header
                def create(dc, info, mode, bits, section, offset):
                    if fault != 'bits':
                        ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
                    return 0 if fault == 'bitmap' else 2
                gdi = backend.gdi32 = NS(CreateCompatibleDC=Mock(return_value=0 if fault == 'dc' else 1),
                    CreateDIBSection=Mock(side_effect=create), SelectObject=Mock(return_value=0 if fault == 'select' else 3),
                    DeleteObject=Mock(), DeleteDC=Mock())
                handle = dict(dc=None, bitmap=None, old=None, bits=None, size=None)
                if fault == 'none':
                    backend._surface(handle, 2, 2)
                    self.assertEqual(handle['size'], (2, 2))
                    backend._free(handle)
                else:
                    with self.assertRaises(OSError):
                        backend._surface(handle, 2, 2)
                    self.assertIsNone(handle['dc'])
                self.assertEqual(gdi.DeleteDC.call_count, 0 if fault == 'dc' else 1)
                self.assertEqual(gdi.DeleteObject.call_count, 0 if fault in ('dc', 'bitmap') else 1)
