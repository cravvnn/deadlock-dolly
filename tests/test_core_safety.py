"""Headless safety regressions using isolated files; no game or updater process runs."""
import hashlib
import json
import os
import stat
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from dolly import gameinfo_transaction as journal, updater
from dolly.launch_errors import LaunchError
from dolly.settings import AppSettings, load_settings, save_settings

try:
    import tkinter  # noqa: F401
except ImportError:
    # Only the update worker's file operations are exercised here.
    tk = types.ModuleType('tkinter')
    tk.ttk = types.ModuleType('tkinter.ttk')
    with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.ttk': tk.ttk}):
        from dolly import update_worker as worker
else:
    from dolly import update_worker as worker


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dolly-safety-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def game_fixture(self, name):
        root = self.root / name
        game = root / 'game'
        exe = game / 'bin/win64/deadlock.exe'
        server = game / 'citadel/bin/win64/server.dll'
        for path in (exe, server):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        target = game / 'citadel/gameinfo.gi'
        target.write_bytes(b'mounted\r\n')
        session = root / 'sessions/one'
        session.mkdir(parents=True)
        backup = session / 'original.gameinfo.gi'
        backup.write_bytes(b'original\r\n')
        record = {'owner': 'Deadlock Dolly', 'session_dir': str(session),
                  'original_gameinfo': str(target),
                  'overlay_dir': str(game / 'citadel_dolly_fixture'),
                  'backup_name': backup.name, 'original_sha256': sha(b'original\r\n'),
                  'patched_sha256': sha(b'mounted\r\n'), 'config_state': 'mounted'}
        journal.save_record(session, record)
        return target, session, backup, record

    def test_settings_roundtrip_and_invalid_backups(self):
        path = self.root / 'settings.json'
        self.assertEqual(load_settings(path), AppSettings())
        self.assertFalse(path.exists())
        settings = AppSettings(full_editor=True, auto_updates=False, export_folder='D:/shots', movement_speed=500)
        save_settings(settings, path)
        self.assertEqual(load_settings(path), settings)
        valid = path.read_bytes()
        version = json.loads(valid)['version']
        duplicate = b'{"version": ' + str(version).encode() + b',' + valid.lstrip()[1:]
        invalid = [b'{broken\xff', duplicate,
                   valid.replace(b'"movement_speed": 500.0', b'"movement_speed": NaN'),
                   json.dumps({**json.loads(valid), 'version': 999}).encode()]
        self.assertNotEqual(invalid[2], valid)
        for value in invalid:
            with self.subTest(value=value[:50]):
                path.write_bytes(value)
                with self.assertRaises(ValueError):
                    load_settings(path)
                self.assertEqual(path.read_bytes(), value)
                save_settings(settings, path)
                self.assertEqual(load_settings(path), settings)
        backups = list(self.root.glob('settings.json.*.invalid'))
        self.assertCountEqual([p.read_bytes() for p in backups], invalid)

    def test_settings_failed_backup_and_replace_preserve_original(self):
        path = self.root / 'settings.json'
        path.write_bytes(b'{invalid')
        with patch('dolly.settings.shutil.copyfileobj', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                save_settings(AppSettings(), path)
        self.assertEqual(path.read_bytes(), b'{invalid')
        self.assertEqual(list(self.root.iterdir()), [path])
        # Publication collision/failure cannot destroy invalid settings.
        publish = 'rename' if os.name == 'nt' else 'link'
        with patch('dolly.settings.os.' + publish, side_effect=FileExistsError('collision')):
            with self.assertRaises(FileExistsError):
                save_settings(AppSettings(), path)
        self.assertEqual(path.read_bytes(), b'{invalid')
        self.assertEqual(list(self.root.iterdir()), [path])
        save_settings(AppSettings(), path)
        original = path.read_bytes()
        with patch('dolly.settings.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                save_settings(AppSettings(full_editor=True), path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list(self.root.glob('*.tmp'))), 0)
        self.assertEqual(len(list(self.root.glob('*.invalid'))), 1)

    def test_journal_restores_original_and_write_failures_remain_recoverable(self):
        target, session, backup, record = self.game_fixture('normal')
        self.assertEqual(journal.load_record(session)['owner'], 'Deadlock Dolly')
        self.assertTrue(journal.restore_record(session))
        self.assertEqual(target.read_bytes(), b'original\r\n')
        self.assertEqual(backup.read_bytes(), b'original\r\n')
        self.assertEqual(journal.load_record(session)['config_state'], 'restored')
        before = (session / 'session.json').read_bytes()
        self.assertTrue(journal.restore_record(session))
        self.assertEqual((session / 'session.json').read_bytes(), before)
        for fail_target in (True, False):
            with self.subTest(fail_target=fail_target):
                target, session, backup, record = self.game_fixture('failure' + str(fail_target))
                if fail_target:
                    with self.assertRaises(LaunchError):
                        journal.restore_record(session, atomic_write=lambda *a: (_ for _ in ()).throw(OSError('locked')))
                    self.assertEqual(target.read_bytes(), b'mounted\r\n')
                else:
                    with self.assertRaises(LaunchError):
                        journal.restore_record(session, save_record=lambda *a: (_ for _ in ()).throw(OSError('locked')))
                    self.assertEqual(target.read_bytes(), b'original\r\n')
                self.assertEqual(journal.load_record(session)['config_state'], 'mounted')
                self.assertEqual(backup.read_bytes(), b'original\r\n')
                self.assertTrue(journal.restore_record(session))
                self.assertEqual(target.read_bytes(), b'original\r\n')
                self.assertEqual(journal.load_record(session)['config_state'], 'restored')

    def test_journal_rejects_foreign_tampered_linked_and_modified_files(self):
        probe = self.root / 'link-probe'
        probe_target = self.root / 'link-target'
        probe_target.write_bytes(b'probe')
        try:
            probe.symlink_to(probe_target)
        except (OSError, NotImplementedError):
            links_supported = False
        else:
            links_supported = True
            probe.unlink()
        for case in ('owner', 'session', 'overlay', 'backup_name', 'hash', 'truncated',
                     'backup_tampered', 'backup_link', 'target_link', 'journal_link', 'external_edit'):
            with self.subTest(case=case):
                if case.endswith('_link') and not links_supported:
                    raise unittest.SkipTest('file symlinks unavailable in this environment')
                target, session, backup, record = self.game_fixture(case)
                marker = self.root / (case + '-outside')
                marker.write_bytes(b'outside untouched')
                if case == 'owner': record['owner'] = 'Another app'
                elif case == 'session': record['session_dir'] = str(self.root)
                elif case == 'overlay': record['overlay_dir'] = str(self.root / 'citadel_dolly_foreign')
                elif case == 'backup_name': record['backup_name'] = '../other'
                elif case == 'hash': record['original_sha256'] = 'bad'
                journal.save_record(session, record)
                if case == 'truncated': (session / 'session.json').write_bytes(b'{broken')
                elif case == 'backup_tampered': backup.write_bytes(b'tampered')
                elif case == 'backup_link':
                    backup.unlink(); backup.symlink_to(marker)
                elif case == 'target_link':
                    target.unlink(); target.symlink_to(marker)
                elif case == 'journal_link':
                    (session / 'session.json').unlink(); (session / 'session.json').symlink_to(marker)
                elif case == 'external_edit': target.write_bytes(b'external edit')
                expected = target.read_bytes()
                with self.assertRaises(LaunchError):
                    journal.restore_record(session)
                self.assertEqual(target.read_bytes(), expected)
                self.assertEqual(marker.read_bytes(), b'outside untouched')
                if case == 'external_edit':
                    self.assertEqual(journal.load_record(session)['config_state'], 'conflict')
                    self.assertEqual(backup.read_bytes(), b'original\r\n')

    def test_verified_update_install_and_recovery(self):
        required = ('Dolly.exe', 'DollyUpdater.exe', '_internal/native/bin/win64/DollyNative.dll',
                    '_internal/third_party/ffmpeg/bin/ffmpeg.exe')
        target = self.root / 'installed'
        source = self.root / 'new'
        for folder, data in ((target, b'old'), (source, b'new')):
            for name in required:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data + b':' + name.encode())
        obsolete = target / '_internal/obsolete.dll'
        obsolete.write_bytes(b'old obsolete')
        newfile = source / '_internal/new.dll'
        newfile.write_bytes(b'new file')
        updater.write_manifest(target, '1.0.0')
        updater.write_manifest(source, '1.0.1')
        user = target / 'my-shot.json'
        user.write_bytes(b'user work')
        archive = self.root / 'release.zip'
        def make_zip(extra=None):
            with zipfile.ZipFile(archive, 'w') as z:
                for path in source.rglob('*'):
                    if path.is_file(): z.write(path, 'DeadlockDolly/' + path.relative_to(source).as_posix())
                if extra: z.writestr(extra, 'bad')
        make_zip()
        release = {'version': '1.0.1', 'sha256': updater.digest_file(archive)}
        bad_checksum = dict(release, sha256='0' * 64)
        with self.assertRaises(ValueError):
            updater.extract_verified(archive, self.root / 'bad-checksum', bad_checksum)
        self.assertFalse((self.root / 'bad-checksum').exists())
        with self.assertRaises(ValueError):
            updater.extract_verified(archive, self.root / 'bad-version',
                                     dict(release, version='9.9.9'))
        self.assertEqual((target / 'Dolly.exe').read_bytes(), b'old:Dolly.exe')
        work = target / '.dolly-update-test'
        work.mkdir()
        payload = work / 'payload'
        self.assertEqual(updater.extract_verified(archive, payload, release)['version'], '1.0.1')
        plan = {'target': str(target), 'work': str(work)}
        old_manifest = (target / updater.MANIFEST).read_bytes()
        def old_is_intact():
            for name in required:
                self.assertEqual((target / name).read_bytes(), b'old:' + name.encode())
            self.assertEqual(obsolete.read_bytes(), b'old obsolete')
            self.assertFalse((target / '_internal/new.dll').exists())
            self.assertEqual((target / updater.MANIFEST).read_bytes(), old_manifest)
            self.assertEqual(user.read_bytes(), b'user work')
            self.assertFalse((target / worker.PENDING).exists())
        # A bad backup copy fails before publishing a pending transaction.
        real_copy = worker.shutil.copyfile
        def corrupt_copy(src, dst, *args, **kwargs):
            result = real_copy(src, dst, *args, **kwargs)
            if Path(dst).name == 'Dolly.exe':
                Path(dst).write_bytes(b'damaged during copy')
            return result
        with patch.object(worker.shutil, 'copyfile', side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'backup verification failed'):
                worker.install(plan)
        self.assertFalse((work / 'journal.json').exists())
        old_is_intact()

        # A locally modified owned binary is backed up as it exists, not as
        # its old manifest describes it, and restored after a health failure.
        custom = b'locally modified executable'
        (target / 'Dolly.exe').write_bytes(custom)
        def failed_custom_health(_): raise RuntimeError('custom health failed')
        with self.assertRaisesRegex(RuntimeError, 'custom health failed'):
            worker.install(plan, failed_custom_health)
        self.assertEqual((target / 'Dolly.exe').read_bytes(), custom)
        self.assertEqual(user.read_bytes(), b'user work')
        self.assertFalse((target / worker.PENDING).exists())
        self.assertIn('Dolly.exe', json.loads((work / 'journal.json').read_text())['modified'])
        (work / 'journal.json').unlink()
        (target / 'Dolly.exe').write_bytes(b'old:Dolly.exe')
        old_is_intact()
        # Refuse a pending recovery and same-version/downgrade before writes.
        pending = target / worker.PENDING
        pending.write_text('pending recovery')
        with self.assertRaises(ValueError): worker.install(plan)
        self.assertEqual(pending.read_text(), 'pending recovery')
        pending.unlink()
        old_is_intact()
        payload_manifest = payload / updater.MANIFEST
        original_manifest = payload_manifest.read_bytes()
        manifest = json.loads(original_manifest)
        for version in ('1.0.0', '0.9.0'):
            manifest['version'] = version
            payload_manifest.write_text(json.dumps(manifest))
            with self.subTest(version=version):
                with self.assertRaises(ValueError): worker.install(plan)
                old_is_intact()
        payload_manifest.write_bytes(original_manifest)
        (payload / 'Dolly.exe').write_bytes(b'tampered')
        with self.assertRaises(ValueError): worker.install(plan)
        old_is_intact()
        (payload / 'Dolly.exe').write_bytes(b'new:Dolly.exe')
        # An unowned file at a newly introduced managed path must survive.
        unowned = target / '_internal/new.dll'
        unowned.write_bytes(b'personal file')
        with self.assertRaises(ValueError): worker.install(plan)
        self.assertEqual(unowned.read_bytes(), b'personal file')
        unowned.unlink()
        old_is_intact()
        calls = []
        def fail_midway(src, dst):
            calls.append(dst)
            if len(calls) == 2: raise OSError('replace failed')
            worker.replace_file(src, dst)
        with self.assertRaises(OSError): worker.install(plan, replace=fail_midway)
        self.assertEqual(len(calls), 2)
        old_is_intact()
        worker.rollback(plan)
        old_is_intact()
        # A damaged recovery copy must not overwrite a changed installed file.
        backup_exe = work / 'backup/Dolly.exe'
        original_backup = backup_exe.read_bytes()
        backup_exe.write_bytes(b'corrupt backup')
        (target / 'Dolly.exe').write_bytes(b'interrupted replacement')
        with self.assertRaisesRegex(ValueError, 'backup is damaged'):
            worker.rollback(plan)
        self.assertEqual((target / 'Dolly.exe').read_bytes(), b'interrupted replacement')
        self.assertEqual(user.read_bytes(), b'user work')
        backup_exe.write_bytes(original_backup)
        worker.rollback(plan)
        old_is_intact()
        (work / 'journal.json').unlink()
        def bad_health(_): raise RuntimeError('health failed')
        with self.assertRaisesRegex(RuntimeError, 'health failed'): worker.install(plan, bad_health)
        old_is_intact()
        (work / 'journal.json').unlink()
        worker.install(plan)
        for name in required:
            self.assertEqual((target / name).read_bytes(), b'new:' + name.encode())
        self.assertFalse(obsolete.exists())
        self.assertEqual((target / '_internal/new.dll').read_bytes(), b'new file')
        self.assertEqual(user.read_bytes(), b'user work')
        self.assertFalse((target / worker.PENDING).exists())
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        # Each archive includes a complete good payload, followed by an unsafe
        # entry. The target and user file must stay unchanged after rejection.
        absolute = self.root / 'absolute-escape'
        cases = [
            ('traversal', 'DeadlockDolly/../../outside'),
            ('absolute', str(absolute)),
            ('backslash', r'DeadlockDolly\..\outside'),
            ('case_alias', 'DeadlockDolly/dolly.EXE'),
            ('symlink', 'DeadlockDolly/_internal/link'),
        ]
        for label, name in cases:
            with self.subTest(archive=label):
                make_zip()
                with zipfile.ZipFile(archive, 'a') as package:
                    if label == 'symlink':
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        package.writestr(info, '../../outside')
                    else:
                        package.writestr(name, b'unsafe')
                staging = self.root / ('unsafe-' + label) / 'staged'
                with self.assertRaises(ValueError):
                    updater.extract_verified(archive, staging,
                        {'version': '1.0.1', 'sha256': updater.digest_file(archive)})
                # ../../ from the extraction directory would reach root/outside.
                self.assertFalse((self.root / 'outside').exists())
                self.assertFalse((staging.parent / 'outside').exists())
                self.assertFalse(absolute.exists())
                self.assertEqual((target / 'Dolly.exe').read_bytes(), b'new:Dolly.exe')
                self.assertEqual(user.read_bytes(), b'user work')
