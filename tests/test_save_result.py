"""Outer settings-save result survives best-effort payload cleanup."""
import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dolly import settings as s


class SaveResultTests(unittest.TestCase):
    def test_cleanup_preserves_success_and_primary_save_error(self):
        unlink = Path.unlink
        primary = OSError('primary save failure')
        def cleanup(path, *args, **kwargs):
            if path.name.endswith('.tmp') and not path.name.endswith('.invalid.tmp'):
                raise PermissionError('payload cleanup denied')
            return unlink(path, *args, **kwargs)
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'settings.json'
                target.write_bytes(b'invalid original')
                with patch.object(Path, 'unlink', cleanup):
                    if fail:
                        with patch.object(s, '_backup_invalid_settings', side_effect=primary):
                            with self.assertRaises(OSError) as caught:
                                s.save_settings(s.AppSettings(full_editor=True), target)
                        self.assertIs(caught.exception, primary)
                    else:
                        s.save_settings(s.AppSettings(full_editor=True), target)
                if fail:
                    self.assertEqual(target.read_bytes(), b'invalid original')
                else:
                    self.assertTrue(s.load_settings(target).full_editor)

    def test_cleanup_programming_error_remains_visible(self):
        unlink = Path.unlink
        def cleanup(path, *args, **kwargs):
            if path.name.endswith('.tmp') and not path.name.endswith('.invalid.tmp'):
                raise RuntimeError('unexpected cleanup bug')
            return unlink(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'settings.json'
            with patch.object(Path, 'unlink', cleanup):
                with self.assertRaisesRegex(RuntimeError, 'unexpected cleanup bug'):
                    s.save_settings(s.AppSettings(full_editor=True), target)
            self.assertTrue(s.load_settings(target).full_editor)

    @unittest.skipUnless(os.name == 'nt', 'Windows delete-sharing behavior')
    def test_locked_payload_cleanup_keeps_primary_backup_failure(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'settings.json'
            target.write_bytes(b'invalid original')
            primary = OSError('backup refused')
            handle = None
            stage = None
            def backup(_target):
                nonlocal handle, stage
                stage = next(target.parent.glob('settings.json.*.tmp'))
                handle = kernel.CreateFileW(str(stage), 0x80000000, 3, None, 3, 0x80, None)
                self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value))
                raise primary
            try:
                with patch.object(s, '_backup_invalid_settings', backup):
                    with self.assertRaises(OSError) as caught:
                        s.save_settings(s.AppSettings(), target)
                self.assertIs(caught.exception, primary)
                self.assertEqual(target.read_bytes(), b'invalid original')
                self.assertTrue(stage.exists())
            finally:
                if handle not in (None, ctypes.c_void_p(-1).value):
                    self.assertTrue(kernel.CloseHandle(handle))
