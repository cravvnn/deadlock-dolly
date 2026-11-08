"""Preservation and verified-transition gaps from the eight-owner review."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from dolly import controller, gameinfo_transaction, graphics_profiles, keyvalues
from dolly import launcher, preload, session_recovery
from dolly.export_timing import ExportTimingMixin
from dolly.launch_errors import LaunchError
from dolly.path import Keyframe, Project


class TimingOwner(ExportTimingMixin):
    """Console boundary: independently track actual values and ignored writes."""
    def __init__(self):
        self._console = SimpleNamespace(is_connected=True)
        self._demo_speed_changed = False
        self.values = {'host_framerate': 0., 'r_wait_on_present': 0.}
        self.ignore = set()
        self._export_timing = {'fps': 60, 'speed': .5, 'restore': dict(self.values), 'suspended': True}

    def _alive(self):
        return True

    def _request(self, command):
        if len(command.split()) == 1:
            return command + ' = ' + str(self.values[command])
        for part in command.split(';'):
            name, value = part.split()
            if name in self.values and name not in self.ignore:
                self.values[name] = float(value)
        return ''


class ReviewedOwnerTests(unittest.TestCase):
    def test_diagnostics_preserves_peer_created_after_selection_and_writes_numbered_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root/'report.zip'; peer = b'previous support evidence'
            def collision(_):
                target.write_bytes(peer)
                return target
            with patch.object(controller, 'ROOT', root), patch.object(controller, '_diagnostics_destination', side_effect=collision):
                with self.assertRaises(FileExistsError):
                    controller._write_diagnostics_archive(target, {}, None, [], None, [])
            self.assertEqual(target.read_bytes(), peer)
            with patch.object(controller, 'ROOT', root):
                result = controller._write_diagnostics_archive(target, {'ok': True}, 'console', [], None, [])
            self.assertNotEqual(result, target)
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(json.loads(archive.read('diagnostics.json')), {'ok': True})
                self.assertEqual(archive.read('logs/recent_console.txt'), b'console')

    def test_project_save_cleanup_keeps_primary_error_and_completed_bytes(self):
        project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6)])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'shot.json'; target.write_bytes(b'previous shot')
            primary = PermissionError('replace refused'); cleanup = PermissionError('stage locked')
            with patch('os.replace', side_effect=primary), patch.object(Path, 'unlink', side_effect=cleanup):
                with self.assertRaises(PermissionError) as caught:
                    project.save(target)
            self.assertIs(caught.exception, primary)
            self.assertEqual(target.read_bytes(), b'previous shot')
            with patch.object(Path, 'unlink', side_effect=cleanup):
                project.save(target)
            self.assertEqual(Project.load(target), project)
            with patch.object(Path, 'unlink', side_effect=RuntimeError('programmer error')):
                with self.assertRaisesRegex(RuntimeError, 'programmer error'):
                    project.save(target)

    def test_export_timing_requires_both_readbacks_then_allows_retry_and_restore(self):
        for action in ('resume', 'suspend'):
            for ignored in ('host_framerate', 'r_wait_on_present'):
                with self.subTest(action=action, ignored=ignored):
                    owner = TimingOwner()
                    if action == 'suspend':
                        owner.values.update(host_framerate=120., r_wait_on_present=1.)
                        owner._export_timing['suspended'] = False
                    owner.ignore = {ignored}
                    with self.assertRaisesRegex(RuntimeError, 'did not '+action):
                        getattr(owner, action+'_export_timing')()
                    self.assertEqual(owner._export_timing['suspended'], action == 'resume')
                    owner.ignore.clear()
                    getattr(owner, action+'_export_timing')()
                    self.assertEqual(owner._export_timing['suspended'], action == 'suspend')
                    owner.clear_export_timing()
                    self.assertIsNone(owner._export_timing)
                    self.assertEqual(owner.values, {'host_framerate': 0., 'r_wait_on_present': 0.})

    def test_bad_recovery_json_does_not_skip_independent_graphics_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'session.json').write_text('{"unused":'+'['*12000+'0'+']'*12000+'}', encoding='utf-8')
            with patch.object(graphics_profiles, 'pending', return_value=False), patch.object(graphics_profiles, 'restore') as restore:
                with self.assertRaisesRegex(LaunchError, 'recovery journal'):
                    session_recovery.restore_session_configs(root, restore_gameinfo=gameinfo_transaction.load_record)
            restore.assert_called_once_with(root)

    def test_game_file_cache_rechecks_identity_bounds_retention_and_refuses_changing_read(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(preload, '_FILE_CACHE', {}), patch.object(preload, '_FILE_CACHE_BUDGET', 32):
            root = Path(folder); target = root/'client.dll'; target.write_bytes(b'original')
            self.assertEqual(preload.game_file(target), (b'original', hashlib.sha256(b'original').hexdigest()))
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('warm cache reread')):
                self.assertEqual(preload.game_file(target)[0], b'original')
            old = target.stat(); replacement = root/'replacement'; replacement.write_bytes(b'replaced')
            os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns)); os.replace(replacement, target)
            self.assertEqual(preload.game_file(target)[0], b'replaced')
            # Equal-size replacements use the old entry's allocation, not both.
            other = root/'other.dll'; other.write_bytes(b'a'*24); preload.game_file(other)
            replacement.write_bytes(b'newvalue'); os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns)); os.replace(replacement, target)
            self.assertEqual(preload.game_file(target)[0], b'newvalue')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('unnecessary cache eviction')):
                self.assertEqual(preload.game_file(other)[0], b'a'*24)
            huge = root/'large.dll'; huge.write_bytes(b'x'*33)
            self.assertEqual(preload.game_file(huge)[0], b'x'*33)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('oversize retained')):
                with self.assertRaisesRegex(AssertionError, 'oversize retained'):
                    preload.game_file(huge)
            fresh = root/'fresh.dll'; fresh.write_bytes(b'first')
            read = Path.read_bytes
            def changing(path):
                data = read(path)
                if path == fresh:
                    info = path.stat(); path.write_bytes(b'other')
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1_000_000_000))
                return data
            with patch.object(Path, 'read_bytes', changing):
                with self.assertRaisesRegex(preload.PreloadError, 'changed while reading'):
                    preload.game_file(fresh)
            self.assertEqual(preload.game_file(fresh)[0], b'other')

    def test_keyvalues_nesting_error_keeps_game_discovery_fallback(self):
        nested = 'a { '*1400+'x y '+'} '*1400
        with self.assertRaises(LaunchError):
            keyvalues._parse(nested)
        valid = '"Root" { Path "x\\\\y" // note\n Value 1 [$WIN32] Nested { A B } }'
        parsed = keyvalues._parse(valid)
        self.assertEqual(parsed[0].children[0].value.value, 'x\\y')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'steamapps').mkdir()
            (root/'steamapps/libraryfolders.vdf').write_text(nested, encoding='utf-8')
            with patch.object(launcher, '_steam_roots', return_value=[root]), patch.object(launcher, 'validate_game', side_effect=LaunchError('not installed')):
                self.assertIsNone(launcher.discover_game())
