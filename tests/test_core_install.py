"""Headless installation admission, launch, recovery and cleanup contracts."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dolly import compatibility, definitions, gameinfo_transaction, game_processes, launcher, session_cleanup, session_recovery
from dolly.launch_errors import LaunchError
from tools import generate_compatibility

ROOT = Path(__file__).resolve().parents[1]
GAMEINFO = ('''"GameInfo"\n{\n game "citadel"\n // user comment\n FileSystem\n {\n  SteamAppId 1422450\n  SearchPaths\n  {\n   Game "citadel/addons"\n   Game "citadel/hyperline"\n   Game "citadel"\n   Game "core"\n  }\n }\n}\n''').replace('\n', '\r\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tree(root):
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def doc(**changes):
    value = {'format': 1, 'native_abi': 4, 'updated': '2026-10-25T00:00:00Z', 'notes': 'test',
             'modules': {}, 'engine': {'layouts': {}, 'profiles': []},
             'tier0': {'layouts': {}, 'profiles': []}, 'client': {'profiles': []}, 'editing': {}}
    value.update(changes)
    return value


class InstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dolly-install-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.game = self.root / 'steamapps/common/Deadlock/game'
        for name, data in [('bin/win64/deadlock.exe', b'exe'), ('citadel/bin/win64/server.dll', b'server'),
                           ('citadel/gameinfo.gi', GAMEINFO)]:
            path = self.game / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        self.paths = launcher.validate_game(self.game.parent)
        self.package = self.root / 'Dolly'

    def journal_fixture(self, name):
        session = self.package / 'logs' / name
        session.mkdir(parents=True)
        backup = session / 'original.gameinfo.gi'; backup.write_bytes(GAMEINFO)
        patched = GAMEINFO.replace(b'Game "core"', b'Game "citadel_dolly_' + name.encode() + b'/cvar_unlocker"\r\n   Game "core"')
        self.paths.gameinfo.write_bytes(patched)
        overlay = self.game / ('citadel_dolly_' + name)
        overlay.mkdir()
        record = {'owner': 'Deadlock Dolly', 'session_dir': str(session), 'original_gameinfo': str(self.paths.gameinfo),
                  'overlay_dir': str(overlay), 'backup_name': backup.name, 'original_sha256': digest(GAMEINFO),
                  'patched_sha256': digest(patched), 'config_state': 'mounted'}
        gameinfo_transaction.save_record(session, record)
        (overlay / '.dolly-session.json').write_text(json.dumps(record))
        return session, overlay, backup, record, patched

    def test_p01_reviewed_game_admission_and_published_definitions(self):
        pins = {}
        for relative in compatibility.MODULE_RELATIVES:
            path = self.game / relative; path.parent.mkdir(parents=True, exist_ok=True)
            data = ('fixture-' + relative).encode(); path.write_bytes(data); pins[relative] = (digest(data),)
        before = tree(self.game)
        self.assertEqual(compatibility.scan_game_modules(self.game, manifest={}, pins=pins).state, compatibility.SUPPORTED)
        native = self.root / 'native'; dll = native / 'bin/win64/DollyNative.dll'; dll.parent.mkdir(parents=True)
        binary = bytearray(128); binary[:2] = b'MZ'; struct.pack_into('<I', binary, 60, 64); binary[64:68] = b'PE\0\0'; struct.pack_into('<H', binary, 68, 0x8664)
        dll.write_bytes(binary); (native / 'build_info.json').write_text(json.dumps({'abi': launcher.NATIVE_ABI, 'sha256': digest(binary)}))
        relative = compatibility.CAMERA_MODULE_RELATIVES[0]
        changed = self.game / relative; original = changed.read_bytes(); changed.write_bytes(b'unreviewed')
        unsupported = tree(self.game)
        self.assertEqual(compatibility.scan_game_modules(self.game, manifest={}, pins=pins).camera_report().state, compatibility.UNSUPPORTED)
        # Use the same valid DLL as the positive case: an absent artifact
        # must not mask loss of the changed-game compatibility refusal.
        with patch.object(launcher, 'game_pins', return_value=pins), patch.object(launcher, 'NATIVE_ROOT', native), patch.object(compatibility, 'load_manifest', return_value={}):
            with self.assertRaisesRegex(LaunchError, 'Game files were not changed'):
                launcher._verified_native(self.paths)
        self.assertEqual(tree(self.game), unsupported)
        changed.unlink()
        self.assertEqual(compatibility.scan_game_modules(self.game, manifest={}, pins=pins).state, compatibility.INCOMPLETE)
        changed.write_bytes(original)
        self.assertEqual(tree(self.game), before)
        with patch.object(launcher, 'game_pins', return_value=pins), patch.object(launcher, 'NATIVE_ROOT', native), patch.object(compatibility, 'load_manifest', return_value={}):
            self.assertEqual(launcher._verified_native(self.paths), dll)
        cache = self.root / 'definitions.json'; module = relative
        published = json.dumps(doc(modules={module: {'accepted': ['a' * 64]}})).encode()
        with patch.object(definitions, 'cache_path', return_value=cache):
            definitions.reset(); self.addCleanup(definitions.reset)
            with patch.object(definitions, 'fetch', return_value=published): definitions.refresh()
            self.assertEqual(compatibility.merged_pins(pins, definitions.current().modules)[module], pins[module] + ('a' * 64,))
            for invalid in (b'{', b' ' * (definitions.MAX_BYTES + 1),
                            json.dumps(doc(native_abi=999)).encode(),
                            json.dumps(doc(modules={'unknown.dll': {'accepted': ['b' * 64]}})).encode()):
                with self.subTest(invalid=invalid[:40]), patch.object(definitions, 'fetch', return_value=invalid):
                    with self.assertRaises(definitions.DefinitionsError): definitions.refresh()
                    self.assertEqual(cache.read_bytes(), published)
            with patch.object(definitions, 'fetch', return_value=json.dumps(doc()).encode()): definitions.refresh()
            self.assertTrue(definitions.current().empty)
            self.assertEqual(compatibility.merged_pins(pins, definitions.current().modules)[module], pins[module])
        definitions.load(ROOT / 'definitions.json')
        self.assertIsNone(generate_compatibility.check_generated())
        self.assertEqual(tree(self.game), before)

    def test_p02_launch_transaction_restores_install_on_exit_and_failure(self):
        from dolly import editing_assets, editing_session
        editing = self.root / 'editing'; editing.mkdir()
        source = ROOT / 'assets/editing'
        shutil.copytree(source / 'cfg', editing / 'cfg'); shutil.copyfile(source / 'cfg-manifest.json', editing / 'cfg-manifest.json')
        shutil.copyfile(source / 'video.txt', editing / 'video.txt')
        (editing / 'gameinfo.gi').write_bytes(GAMEINFO)
        from dolly.stock_provenance import CONFIGURATION_MODULES
        from dolly.compatibility import CAMERA_MODULE_RELATIVES
        modules = {}
        for name in set(CONFIGURATION_MODULES) | set(CAMERA_MODULE_RELATIVES):
            p = self.game / name; p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists(): p.write_bytes(('fixture-' + name).encode())
            modules[name] = digest(p.read_bytes())
        profile = {'format': 1, 'gameinfo_sha256': digest(GAMEINFO),
                   'cfg_manifest_sha256': digest((editing / 'cfg-manifest.json').read_bytes()),
                   'video_sha256': digest((editing / 'video.txt').read_bytes()),
                   'modules': {n: modules[n] for n in CAMERA_MODULE_RELATIVES},
                   'configuration_modules': {n: modules[n] for n in CONFIGURATION_MODULES},
                   'unlocker_server_sha256': digest((self.game / 'citadel/bin/win64/server.dll').read_bytes())}
        (editing / 'profile.json').write_text(json.dumps(profile))
        replay = self.root / 'chosen.dem'; replay.write_bytes(b'replay')
        player = self.game / 'citadel/cfg/config.cfg'; player.parent.mkdir(parents=True, exist_ok=True); player.write_bytes(b'user cfg\r\n')
        (self.game / 'citadel/addons/models').mkdir(parents=True); (self.game / 'citadel/hyperline/panorama').mkdir(parents=True)
        before = tree(self.game)
        process = MagicMock(); process.pid = 4321; process.poll.return_value = 0
        real_atomic = launcher._atomic_write
        def check_before_game_write(path, data, mode=None):
            if path == self.paths.gameinfo and data != GAMEINFO:
                session_dir = next((self.package / 'logs').iterdir())
                self.assertEqual((session_dir / 'original.gameinfo.gi').read_bytes(), GAMEINFO)
                self.assertEqual(json.loads((session_dir / 'session.json').read_text())['config_state'], 'prepared')
            return real_atomic(path, data, mode)
        def spawn(command, **kwargs):
            session = next((self.package / 'logs').iterdir())
            record = json.loads((session / 'session.json').read_text())
            self.assertEqual((session / 'original.gameinfo.gi').read_bytes(), GAMEINFO)
            self.assertEqual(record['config_state'], 'mounted')
            self.assertNotEqual(self.paths.gameinfo.read_bytes(), GAMEINFO)
            self.assertIn('-dev', command); self.assertIn('-insecure', command); self.assertIn('-netconport', command)
            self.assertEqual(record['selected_demo'], str(replay.resolve()))
            self.assertNotIn('+playdemo', command)
            self.assertEqual(player.read_bytes(), b'user cfg\r\n')
            return process
        with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), \
             patch.object(launcher, 'EDITING_ROOT', editing), patch.object(game_processes, 'running_processes', return_value={'steam.exe'}), \
             patch.object(launcher, 'running_processes', return_value={'steam.exe'}), \
             patch('dolly.stock_provenance.verify', return_value={}), patch.object(launcher, '_atomic_write', side_effect=check_before_game_write), \
             patch.object(launcher.threading, 'Thread'), patch.object(launcher.subprocess, 'Popen', side_effect=spawn):
            session = launcher.launch(self.paths.root, replay)
            record = json.loads((session.session_dir / 'session.json').read_text())
            self.assertTrue(any('addons' in x['path'] for x in record['carried_addon_mounts']))
            self.assertTrue(any('hyperline' in x['path'] for x in record['excluded_addon_mounts']))
            self.assertTrue(session.close())
        self.assertEqual(tree(self.game), before)
        # The default native mode deploys its wrapper and both capture packs.
        shutil.rmtree(self.package)
        native_dll = self.root / 'native.dll'; native_dll.write_bytes(b'verified native fixture')
        confetti = self.root / 'confetti.vpk'; confetti.write_bytes(b'confetti fixture')
        ui = self.root / 'ui.vpk'; ui.write_bytes(b'ui fixture')
        bridge = MagicMock(); bridge.token = 'fixture-token'; bridge.editor_pid = 99
        def native_spawn(command, **kwargs):
            result = spawn(command, **kwargs)
            overlay = next(self.game.glob('citadel_dolly_*'))
            self.assertEqual((overlay / 'cvar_unlocker/bin/win64/server.dll').read_bytes(), b'verified native fixture')
            self.assertEqual((overlay / 'cvar_unlocker/pak01_dir.vpk').read_bytes(), b'confetti fixture')
            self.assertEqual((overlay / 'cvar_unlocker/pak02_dir.vpk').read_bytes(), b'ui fixture')
            self.assertTrue((overlay / 'cvar_unlocker/bin/win64/dolly_cvar_unlocker.dll').is_file())
            return result
        with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), \
             patch.object(launcher, 'EDITING_ROOT', editing), patch.object(game_processes, 'running_processes', return_value={'steam.exe'}), \
             patch.object(launcher, 'running_processes', return_value={'steam.exe'}), patch('dolly.stock_provenance.verify', return_value={}), \
             patch.object(launcher, '_atomic_write', side_effect=check_before_game_write), patch.object(launcher, '_verified_native', return_value=native_dll), \
             patch.object(launcher, '_verified_confetti_pack', return_value=confetti), patch.object(launcher, '_verified_ui_override_pack', return_value=ui), \
             patch.object(launcher, 'verify_stock_ui_assets'), patch.object(launcher.NativeBridge, 'create', return_value=bridge), \
             patch.object(launcher.threading, 'Thread'), patch.object(launcher.subprocess, 'Popen', side_effect=native_spawn):
            session = launcher.launch(self.paths.root, replay, native=True)
            bridge.bind_game.assert_called_once_with(process.pid)
            self.assertTrue(session.close())
        self.assertEqual(tree(self.game), before)
        for name, processes in [('running', {'steam.exe', 'deadlock.exe'}), ('steam', set())]:
            with self.subTest(name=name), patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), \
                 patch.object(launcher, 'running_processes', return_value=processes), patch.object(game_processes, 'running_game_processes', return_value=[]):
                with self.assertRaises(LaunchError): launcher.launch(self.paths.root)
                self.assertEqual(tree(self.game), before)

        # Failures after preparation must restore exact installed bytes.
        for scenario in ('malformed', 'popen', 'changed', 'race'):
            with self.subTest(scenario=scenario):
                original = self.paths.gameinfo.read_bytes()
                if scenario == 'malformed': self.paths.gameinfo.write_bytes(b'bad {')
                expected = self.paths.gameinfo.read_bytes()
                real_prepare = editing_session.prepare
                processes = {'steam.exe'}
                def prepare(*args, **kwargs):
                    result = real_prepare(*args, **kwargs)
                    if scenario == 'changed': self.paths.gameinfo.write_bytes(b'newer external gameinfo')
                    if scenario == 'race': processes.add('deadlock.exe')
                    return result
                with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), \
                     patch.object(launcher, 'EDITING_ROOT', editing), patch.object(game_processes, 'running_processes', return_value={'steam.exe'}), \
                     patch.object(launcher, 'running_processes', side_effect=lambda: set(processes)), \
                     patch('dolly.stock_provenance.verify', return_value={}), patch.object(editing_session, 'prepare', side_effect=prepare), \
                     patch.object(launcher.subprocess, 'Popen', side_effect=OSError('spawn failure')) as popen:
                    with self.assertRaises(LaunchError): launcher.launch(self.paths.root)
                    if scenario == 'popen': popen.assert_called_once()
                    else: popen.assert_not_called()
                self.assertEqual(self.paths.gameinfo.read_bytes(), b'newer external gameinfo' if scenario == 'changed' else expected)
                self.paths.gameinfo.write_bytes(original)
                if scenario != 'changed':
                    self.assertEqual(list(self.game.glob('citadel_dolly_*')), [])
                else:
                    self.assertTrue(list(self.game.glob('citadel_dolly_*')))
                # External edit intentionally leaves a conflict journal; isolate it.
                shutil.rmtree(self.package, ignore_errors=True)
                for overlay in self.game.glob('citadel_dolly_*'): shutil.rmtree(overlay)
                self.assertEqual(tree(self.game), before)

    def test_p03_interrupted_recovery_preserves_foreign_and_modified_files(self):
        session, overlay, backup, record, patched = self.journal_fixture('normal')
        with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), patch.object(launcher, 'running_processes', return_value={'steam.exe'}):
            self.assertEqual(launcher.recover_pending(self.paths.root), [str(session)])
            self.assertEqual(self.paths.gameinfo.read_bytes(), GAMEINFO)
            self.assertEqual(gameinfo_transaction.load_record(session)['config_state'], 'restored')
            self.assertFalse(overlay.exists())
            self.assertEqual(launcher.recover_pending(self.paths.root), [])
        for case in ('owner', 'hash', 'backup', 'external'):
            with self.subTest(case=case):
                session, overlay, backup, record, patched = self.journal_fixture(case)
                if case == 'owner': record['owner'] = 'Foreign'; gameinfo_transaction.save_record(session, record)
                elif case == 'hash': record['original_sha256'] = 'bad'; gameinfo_transaction.save_record(session, record)
                elif case == 'backup': backup.write_bytes(b'tampered')
                elif case == 'external': self.paths.gameinfo.write_bytes(b'external edit')
                expected = self.paths.gameinfo.read_bytes()
                with patch.object(launcher, '_check_runtime'), patch.object(launcher, 'PACKAGE_ROOT', self.package), patch.object(launcher, 'running_processes', return_value={'steam.exe'}):
                    with self.assertRaises(LaunchError): launcher.recover_pending(self.paths.root)
                self.assertEqual(self.paths.gameinfo.read_bytes(), expected)
                self.assertTrue(overlay.exists())
                shutil.rmtree(overlay); shutil.rmtree(session)
        session, overlay, backup, record, patched = self.journal_fixture('linked')
        outside = self.root / 'protected'; outside.write_bytes(b'outside')
        backup.unlink()
        try:
            backup.symlink_to(outside)
        except (OSError, NotImplementedError):
            pass  # Windows without symlink privileges.
        else:
            with self.assertRaises(LaunchError): session_recovery.restore_session_configs(session, game_is_running=lambda: False)
            self.assertEqual(outside.read_bytes(), b'outside')
            self.assertEqual(self.paths.gameinfo.read_bytes(), patched)
        shutil.rmtree(overlay); shutil.rmtree(session)
        session, overlay, backup, record, patched = self.journal_fixture('retry')
        with self.assertRaises(LaunchError):
            session_recovery.restore_session_configs(session, restore_gameinfo=lambda p: gameinfo_transaction.restore_record(p, atomic_write=lambda *args: (_ for _ in ()).throw(OSError('locked'))), game_is_running=lambda: False)
        self.assertEqual(self.paths.gameinfo.read_bytes(), patched)
        self.assertEqual(gameinfo_transaction.load_record(session)['config_state'], 'mounted')
        session_recovery.restore_session_configs(session, game_is_running=lambda: False)
        self.assertEqual(self.paths.gameinfo.read_bytes(), GAMEINFO)
        with patch('dolly.graphics_profiles.pending', return_value=True), patch('dolly.graphics_profiles.restore') as restore:
            with self.assertRaises(LaunchError): session_recovery.restore_session_configs(session, game_is_running=lambda: True)
            restore.assert_not_called()

    def test_p04_cleanup_preserves_unknown_and_removes_only_owned_session_files(self):
        session, overlay, backup, record, patched = self.journal_fixture('cleanup')
        dll = overlay / 'cvar_unlocker/bin/win64/server.dll'; dll.parent.mkdir(parents=True); dll.write_bytes(b'generated')
        report = overlay / 'cvar_unlocker/rpt/client/report.txt'; report.parent.mkdir(parents=True); report.write_bytes(b'report')
        note = overlay / 'notes.txt'; note.write_bytes(b'user notes')
        with self.assertRaises(LaunchError): session_cleanup.remove_overlay(overlay, self.paths, session)
        self.assertEqual(dll.read_bytes(), b'generated')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        self.assertFalse(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertEqual(note.read_bytes(), b'user notes'); self.assertFalse(dll.exists()); self.assertFalse(report.exists())
        self.assertTrue((overlay / '.dolly-session.json').exists())
        self.assertIn('notes.txt', (session / 'launch.log').read_text())
        note.unlink()
        self.assertTrue(session_cleanup.remove_overlay(overlay, self.paths, session))
        session, overlay, backup, record, patched = self.journal_fixture('locked')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        dll = overlay / 'cvar_unlocker/bin/win64/server.dll'; dll.parent.mkdir(parents=True); dll.write_bytes(b'generated')
        unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == dll: raise PermissionError('locked')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', locked):
            with self.assertRaises(PermissionError): session_cleanup.remove_overlay(overlay, self.paths, session)
        self.assertTrue((overlay / '.dolly-session.json').exists())
        self.assertTrue(session_cleanup.remove_overlay(overlay, self.paths, session))
        # A file arriving after inspection defeats final rmdir. Ownership
        # evidence must be repaired so the next cleanup preserves that file.
        session, overlay, backup, record, patched = self.journal_fixture('late')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        real_rmdir = Path.rmdir
        arrived = []
        def late_file(path):
            if path == overlay and not arrived:
                arrived.append(True)
                (overlay / 'late.txt').write_bytes(b'late user work')
                raise OSError('directory changed during cleanup')
            return real_rmdir(path)
        with patch.object(Path, 'rmdir', late_file):
            with self.assertRaisesRegex(OSError, 'directory changed'):
                session_cleanup.remove_overlay(overlay, self.paths, session)
        self.assertTrue((overlay / '.dolly-session.json').is_file())
        self.assertEqual((overlay / 'late.txt').read_bytes(), b'late user work')
        self.assertFalse(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertEqual((overlay / 'late.txt').read_bytes(), b'late user work')
        session, overlay, backup, record, patched = self.journal_fixture('replay')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        replay = overlay / 'cvar_unlocker/replays/my.dem'; replay.parent.mkdir(parents=True); replay.write_bytes(b'user replay')
        self.assertFalse(session_cleanup.remove_overlay(overlay, self.paths, session))
        self.assertEqual(replay.read_bytes(), b'user replay')
        shutil.rmtree(overlay); shutil.rmtree(session)
        session, overlay, backup, record, patched = self.journal_fixture('link')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'data').write_bytes(b'protected')
        link = overlay / 'cvar_unlocker'
        try: link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError): pass
        else:
            with self.assertRaises(LaunchError): session_cleanup.remove_overlay(overlay, self.paths, session)
            self.assertEqual((outside / 'data').read_bytes(), b'protected')
            link.unlink()
        shutil.rmtree(overlay); shutil.rmtree(session)
        session, overlay, backup, record, patched = self.journal_fixture('foreign')
        self.paths.gameinfo.write_bytes(GAMEINFO)
        marker = overlay / '.dolly-session.json'; marker.write_text(json.dumps({**record, 'original_gameinfo': str(self.root / 'other')}))
        self.assertEqual(session_cleanup.recover_orphans(self.paths), [])
        self.assertTrue(marker.exists())
