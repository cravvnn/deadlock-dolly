"""Atomic-file failure boundaries and real directory-link path guards."""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import launcher, session_cleanup


def atomic_trace(writer, fault=None, mode=0o666):
    """Exercise the real filesystem with faults at OS/stream boundaries."""
    with tempfile.TemporaryDirectory(prefix='dolly-file-contract-') as directory:
        folder = Path(directory)
        target = folder / 'target.bin'
        target.write_bytes(b'original\x00\r\n')
        events = []
        fdopen, fsync, replace, chmod, unlink = os.fdopen, os.fsync, os.replace, os.chmod, Path.unlink
        primary = OSError('injected ' + str(fault))
        attempts = 0
        replace_failed = False
        requested_failed = False

        class Stream:
            def __init__(self, descriptor, mode):
                self.stream = fdopen(descriptor, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                events.append('close')
                self.stream.close()
            def write(self, data):
                events.append('write')
                if fault == 'write':
                    raise primary
                return self.stream.write(data)
            def flush(self):
                events.append('flush')
                if fault == 'flush':
                    raise primary
                self.stream.flush()
            def fileno(self):
                return self.stream.fileno()

        def sync(descriptor):
            events.append('fsync')
            if fault == 'fsync':
                raise primary
            fsync(descriptor)

        def swap(source, destination):
            nonlocal attempts, replace_failed
            attempts += 1
            events.append('replace')
            if fault in ('retry_success', 'retry_failure', 'make_writable') and attempts == 1:
                raise PermissionError('injected read-only destination')
            if fault in ('replace', 'cleanup_chmod', 'cleanup_unlink', 'retry_failure'):
                replace_failed = True
                raise primary
            replace(source, destination)

        def set_mode(path, value):
            nonlocal requested_failed
            kind = 'target' if path == target else 'temp'
            events.append('chmod_' + kind)
            if (fault == 'requested_chmod' and kind == 'temp' and not requested_failed) or (fault == 'make_writable' and kind == 'target'):
                requested_failed = True
                raise primary
            if fault == 'cleanup_chmod' and kind == 'temp' and replace_failed:
                raise OSError('secondary cleanup chmod')
            chmod(path, value)

        def remove(path, *args, **kwargs):
            events.append('unlink_temp')
            if fault == 'cleanup_unlink':
                raise OSError('secondary cleanup unlink')
            return unlink(path, *args, **kwargs)

        error = None
        with ExitStack() as stack:
            for obj, name, replacement in ((os, 'fdopen', Stream), (os, 'fsync', sync),
                    (os, 'replace', swap), (os, 'chmod', set_mode), (Path, 'unlink', remove)):
                stack.enter_context(patch.object(obj, name, replacement))
            try:
                writer(target, b'new\x00\r\n', mode)
            except OSError as exc:
                error = {'type': type(exc).__name__, 'message': str(exc), 'primary': exc is primary}
        return {'events': events, 'bytes': target.read_bytes().hex(), 'error': error,
                'mode': target.stat().st_mode & 0o777,
                'leftovers': sorted(path.read_bytes().hex() for path in folder.glob('.dolly-write-*.tmp'))}


class AtomicWriteTests(unittest.TestCase):
    def test_cleanup_stat_preserves_success_or_primary_error(self):
        exists = Path.exists
        for fail_body in (False, True):
            with self.subTest(fail_body=fail_body), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'target'
                target.write_bytes(b'old')
                primary = OSError('replacement failed')
                def check(path):
                    if path.name.startswith('.dolly-write-'):
                        raise PermissionError('cleanup stat denied')
                    return exists(path)
                with patch.object(Path, 'exists', check):
                    if fail_body:
                        with patch.object(os, 'replace', side_effect=primary):
                            with self.assertRaises(OSError) as caught:
                                launcher._atomic_write(target, b'new')
                        self.assertIs(caught.exception, primary)
                    else:
                        launcher._atomic_write(target, b'new')
                self.assertEqual(target.read_bytes(), b'old' if fail_body else b'new')

    def test_cleanup_programming_error_remains_visible(self):
        exists = Path.exists
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            def check(path):
                if path.name.startswith('.dolly-write-'):
                    raise RuntimeError('unexpected cleanup bug')
                return exists(path)
            with patch.object(Path, 'exists', check):
                with self.assertRaisesRegex(RuntimeError, 'unexpected cleanup bug'):
                    launcher._atomic_write(target, b'new')
            self.assertEqual(target.read_bytes(), b'new')

    def test_write_flush_and_fsync_failures_leave_original_and_clean_temp(self):
        for fault in ('write', 'flush', 'fsync'):
            with self.subTest(fault=fault):
                trace = atomic_trace(launcher._atomic_write, fault)
                self.assertEqual(trace['bytes'], b'original\x00\r\n'.hex())
                self.assertNotIn('replace', trace['events'])
                self.assertTrue(trace['error']['primary'])
                self.assertEqual(trace['leftovers'], [])

    def test_success_prepares_mode_after_close_before_replace(self):
        trace = atomic_trace(launcher._atomic_write)
        self.assertEqual(trace['events'], ['write', 'flush', 'fsync', 'close', 'chmod_temp', 'replace'])
        self.assertEqual(trace['bytes'], b'new\x00\r\n'.hex())
        self.assertIsNone(trace['error'])
        self.assertEqual(trace['leftovers'], [])

    def test_mode_preparation_failure_leaves_original_untouched(self):
        trace = atomic_trace(launcher._atomic_write, 'requested_chmod')
        self.assertEqual(trace['bytes'], b'original\x00\r\n'.hex())
        self.assertNotIn('replace', trace['events'])
        self.assertTrue(trace['error']['primary'])
        self.assertEqual(trace['leftovers'], [])

    def test_no_requested_mode_keeps_original_operation_sequence(self):
        trace = atomic_trace(launcher._atomic_write, mode=None)
        self.assertEqual(trace['events'], ['write', 'flush', 'fsync', 'close', 'replace'])
        self.assertEqual(trace['bytes'], b'new\x00\r\n'.hex())
        self.assertIsNone(trace['error'])
        self.assertEqual(trace['leftovers'], [])

    def test_readonly_mode_is_present_at_the_replacement_boundary(self):
        for readonly_target in (False, True):
            with self.subTest(readonly_target=readonly_target), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'target'
                target.write_bytes(b'original')
                if readonly_target:
                    target.chmod(0o444)
                replace = os.replace
                observed = []
                def swap(source, destination):
                    replace(source, destination)
                    observed.append((target.read_bytes(), bool(target.stat().st_mode & stat.S_IWRITE)))
                try:
                    with patch.object(os, 'replace', side_effect=swap):
                        launcher._atomic_write(target, b'new', 0o444)
                    self.assertEqual(observed, [(b'new', False)])
                    self.assertEqual(list(target.parent.glob('.dolly-write-*.tmp')), [])
                finally:
                    target.chmod(0o666)

    def test_cleanup_failures_do_not_hide_original_replace_failure(self):
        for fault in ('cleanup_chmod', 'cleanup_unlink'):
            with self.subTest(fault=fault):
                trace = atomic_trace(launcher._atomic_write, fault)
                self.assertTrue(trace['error']['primary'])
                self.assertEqual(trace['bytes'], b'original\x00\r\n'.hex())
                self.assertEqual(trace['leftovers'], [b'new\x00\r\n'.hex()])

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only replacement retry')
    def test_permission_retry_order_and_failures(self):
        for fault in ('retry_success', 'retry_failure', 'make_writable'):
            with self.subTest(fault=fault):
                trace = atomic_trace(launcher._atomic_write, fault)
                prefix = ['write', 'flush', 'fsync', 'close', 'chmod_temp', 'replace', 'chmod_target']
                self.assertEqual(trace['events'][:7], prefix)
                self.assertEqual(trace['events'].count('replace'), 1 if fault == 'make_writable' else 2)
                self.assertEqual(trace['leftovers'], [])
                if fault == 'retry_success':
                    self.assertIsNone(trace['error'])
                    self.assertEqual(trace['bytes'], b'new\x00\r\n'.hex())
                else:
                    self.assertTrue(trace['error']['primary'])
                    self.assertEqual(trace['bytes'], b'original\x00\r\n'.hex())


class PathGuardTests(unittest.TestCase):
    def test_missing_path_raises_and_plain_file_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plain'
            with self.assertRaises(FileNotFoundError):
                session_cleanup._plain_ancestors(path)
            path.write_bytes(b'original')
            self.assertTrue(session_cleanup._plain_path(path))
            self.assertTrue(session_cleanup._plain_ancestors(path))

    def test_replaced_ancestor_is_rechecked_and_target_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, link = root / 'outside', root / 'parent'
            target.mkdir()
            (target / 'keep').write_bytes(b'untouched')
            link.mkdir()
            self.assertTrue(session_cleanup._plain_ancestors(link))
            link.rmdir()
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            else:
                link.symlink_to(target, target_is_directory=True)
            try:
                self.assertFalse(session_cleanup._plain_path(link))
                # The child itself is plain; rejecting only the leaf is insufficient.
                self.assertTrue(session_cleanup._plain_path(link / 'keep'))
                self.assertFalse(session_cleanup._plain_ancestors(link / 'keep'))
                self.assertEqual((target / 'keep').read_bytes(), b'untouched')
            finally:
                if os.name == 'nt':
                    link.rmdir()
                else:
                    link.unlink()

    def test_reparse_point_that_resolves_to_itself_is_plain(self):
        # Wine reports each Unix mount point, including the root behind Z:,
        # as a directory reparse point that does not redirect anywhere.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()
            mount = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x410)
            with patch.object(Path, 'lstat', return_value=mount):
                self.assertTrue(session_cleanup._plain_path(path))
                with patch.object(os.path, 'realpath', return_value=str(path.parent)):
                    self.assertFalse(session_cleanup._plain_path(path))


class FileBoundaryTests(unittest.TestCase):
    def test_leaf_import_does_not_load_lifecycle_modules(self):
        result = subprocess.run([sys.executable, '-c',
            'import sys; import dolly.file_ops; '
            'assert not ({"dolly.launcher", "dolly.controller", '
            '"dolly.graphics_profiles", "dolly.session_cleanup"} & sys.modules.keys())'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_imports_expose_shared_leaf_functions(self):
        from dolly import file_ops, graphics_profiles
        self.assertIs(launcher._atomic_write, file_ops._atomic_write)
        self.assertIs(graphics_profiles._atomic_write, file_ops._atomic_write)
        self.assertIs(graphics_profiles._plain_ancestors, file_ops._plain_ancestors)
        self.assertIs(session_cleanup._plain_path, file_ops._plain_path)
        self.assertIs(session_cleanup._plain_ancestors, file_ops._plain_ancestors)


if __name__ == '__main__':
    unittest.main()
