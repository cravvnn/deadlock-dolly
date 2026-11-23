"""Release and build tooling checks; no compiler, game or network."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import build_native
import build_ui_override
import format_cpp
import release_files
import release_status
import update_game


class NativeBuildResults(unittest.TestCase):
    def build(self, registered, env=None):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'native').mkdir()
            (root / 'native/CMakeLists.txt').write_text(
                (ROOT / 'native/CMakeLists.txt').read_text(encoding='utf-8'), encoding='utf-8')

            def run(command, **kwargs):
                calls.append(command)
                if command[:2] == ['cmake', '--build']:
                    for relative in (build_native.DLL_RELATIVE, build_native.AUDIO_RELATIVE):
                        path = root / 'native' / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b'binary')
                if '--show-only=json-v1' in command:
                    tests = [{'name': name} for name in sorted(registered)]
                    return subprocess.CompletedProcess(command, 0, json.dumps({'tests': tests}), '')
                return subprocess.CompletedProcess(command, 0)

            with patch.object(build_native, 'check_generated'), \
                    patch.object(build_native, 'verify_native_dll', return_value={}), \
                    patch.object(build_native.subprocess, 'run', side_effect=run), \
                    patch.dict(os.environ, env or {}, clear=False):
                for name in ('DOLLY_SKIP_VIDEO_SMOKE', 'DOLLY_SKIP_DEPTH_SCENE_SMOKE',
                             'DOLLY_SKIP_PLAYER_MATTE_SMOKE', 'DOLLY_SKIP_PLAYER_VERTEX_GUARD_SMOKE'):
                    if name not in (env or {}):
                        os.environ.pop(name, None)
                info = build_native.build_native(root)
        return info, calls

    def declared(self):
        return build_native.declared_tests(
            (ROOT / 'native/CMakeLists.txt').read_text(encoding='utf-8'))

    def test_declared_tests_expand_layout_loop(self):
        declared = self.declared()
        self.assertIn('native_dx11_layout_paged', declared)
        self.assertIn('native_video_late_fault_smoke', declared)
        self.assertFalse(any('$' in name for name in declared))

    def test_release_build_keeps_its_recorded_results(self):
        info, calls = self.build(self.declared(), {'DOLLY_SKIP_VIDEO_SMOKE': '1'})
        ctest = next(c for c in calls if c[0] == 'ctest' and '--output-on-failure' in c)
        self.assertIn('--no-tests=error', ctest)
        self.assertEqual(info['skipped_tests'], ['native_video_encoder_smoke'])
        self.assertFalse(info['native_video_encoder_tests_passed'])
        self.assertTrue(info['native_depth_scene_tests_passed'])
        self.assertFalse(info['full_suite_complete'])

    def test_unregistered_ci_video_tests_are_not_reported_as_passing(self):
        registered = {name for name in self.declared() if 'video' not in name
                      and name != 'native_camera_only_smoke'} | {'native_video_math_tests'}
        info, _ = self.build(registered)
        self.assertFalse(info['native_video_encoder_tests_passed'])
        self.assertTrue(info['native_video_math_tests_passed'])
        self.assertIn('native_video_encoder_smoke', info['skipped_tests'])
        self.assertIn('native_video_late_fault_smoke', info['skipped_tests'])
        self.assertFalse(info['full_suite_complete'])

    def test_complete_local_build_is_full_suite(self):
        info, _ = self.build(self.declared())
        self.assertEqual(info['skipped_tests'], [])
        self.assertTrue(info['full_suite_complete'])
        self.assertTrue(all(info[flag] for flag in build_native.RESULT_TESTS))

    def test_unknown_registered_test_refuses_the_build(self):
        with self.assertRaisesRegex(RuntimeError, 'Unrecognised'):
            build_native.test_results(self.declared(), self.declared() | {'native_new'}, [])


class ReleaseSet(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root, self.bundle, self.dist = base / 'src', base / 'bundle', base / 'dist'
        for folder in (self.root, self.bundle, self.dist):
            folder.mkdir()
        (self.root / 'a.py').write_text('print(1)\n')
        (self.root / 'SOURCE_FILES.txt').write_text('a.py\n')
        (self.bundle / 'Dolly.exe').write_bytes(b'exe')

    def test_writes_both_zips_then_matching_sums(self):
        windows_zip, sources = release_files.release_set(self.bundle, self.root, self.dist, '1.0')
        lines = (self.dist / 'SHA256SUMS.txt').read_text().splitlines()
        self.assertEqual(lines, [f'{release_files.sha256(windows_zip)}  {windows_zip.name}',
                                 f'{release_files.sha256(sources)}  {sources.name}'])
        self.assertEqual(sorted(p.name for p in self.dist.iterdir()),
                         ['Deadlock_Dolly_1.0_Source.zip', 'Deadlock_Dolly_1.0_Windows_x64.zip',
                          'SHA256SUMS.txt'])

    def test_failed_source_zip_leaves_no_stale_sums_or_partial_files(self):
        (self.dist / 'SHA256SUMS.txt').write_text('old  Deadlock_Dolly_1.0_Source.zip\n')
        (self.dist / 'Deadlock_Dolly_1.0_Source.zip').write_bytes(b'old')
        with patch.object(release_files, 'source_files',
                          side_effect=[[self.root / 'a.py'], OSError('disk full')]):
            with self.assertRaises(OSError):
                release_files.release_set(self.bundle, self.root, self.dist, '1.0')
        names = sorted(p.name for p in self.dist.iterdir())
        self.assertNotIn('SHA256SUMS.txt', names)
        self.assertFalse(any(name.endswith('.partial') for name in names))

    def test_bad_source_list_is_refused_before_writing(self):
        (self.root / 'SOURCE_FILES.txt').write_text('missing.py\n')
        with self.assertRaises(ValueError):
            release_files.release_set(self.bundle, self.root, self.dist, '1.0')
        self.assertEqual(list(self.dist.iterdir()), [])

    def test_corrupt_zip_never_takes_the_final_name(self):
        output = self.dist / 'Deadlock_Dolly_1.0_Windows_x64.zip'
        with patch.object(release_files.zipfile.ZipFile, 'testzip', return_value='Dolly.exe'):
            with self.assertRaises(OSError):
                release_files.bundle_zip(self.bundle, output)
        self.assertEqual(list(self.dist.iterdir()), [])


class ReleaseStatus(unittest.TestCase):
    def status(self, published, local):
        calls = []

        def gh(args):
            calls.append(args)
            if args[0] == 'release':
                return [{'tagName': 'v' + published, 'name': published, 'createdAt': 'now'}]
            return {'assets': []}

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp,                 patch.object(release_status, '_slug', return_value='owner/repo'),                 patch.object(release_status, '_gh_json', side_effect=gh),                 patch.object(release_status, 'ROOT', Path(temp)),                 contextlib.redirect_stdout(output):
            self.assertEqual(release_status.main(['--version', local]), 0)
        return output.getvalue(), calls

    def test_latest_release_excludes_drafts(self):
        _, calls = self.status('0.7.51-beta', '0.7.51-beta')
        self.assertIn('--exclude-drafts', calls[0])

    def test_versions_are_ordered_not_only_compared(self):
        self.assertIn('STATUS: LOCAL AHEAD', self.status('0.7.50-beta', '0.7.51-beta')[0])
        self.assertIn('STATUS: LOCAL BEHIND', self.status('0.7.51', '0.7.51-beta')[0])
        self.assertIn('STATUS: VERSION MATCH', self.status('0.7.51-beta', '0.7.51-beta')[0])


class FormatCpp(unittest.TestCase):
    def test_generated_headers_are_not_reformatted(self):
        names = {path.name for path in format_cpp.owned_files(ROOT)}
        self.assertIn('dolly_path.hpp', names)
        self.assertFalse([name for name in names if name.endswith('_generated.hpp')])


class UpdateGamePaths(unittest.TestCase):
    def test_game_dir_from_root_game_win64_or_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'Deadlock'
            game = root / 'game'
            for relative in ('bin/win64/deadlock.exe', 'citadel/gameinfo.gi',
                             'citadel/bin/win64/server.dll', 'citadel/steam.inf'):
                (game / relative).parent.mkdir(parents=True, exist_ok=True)
                (game / relative).write_text('ClientVersion=1\n')
            for selected in (root, game, game / 'bin/win64', game / 'bin/win64/deadlock.exe'):
                with self.subTest(selected=selected.name):
                    self.assertEqual(update_game.installed_game_dir(selected).resolve(), game)
            self.assertEqual(update_game.read_steam_inf(game), {'ClientVersion': '1'})


class UpdateGamePipeline(unittest.TestCase):
    def test_latest_run_is_newest_date_and_build_not_mtime(self):
        with tempfile.TemporaryDirectory() as temp:
            analysis = Path(temp)
            for index, name in enumerate(('update-20261118-6870', 'update-20261118-6864',
                                          'update-20261101-7000', 'update-notes')):
                (analysis / name).mkdir()
                os.utime(analysis / name, (1000 + index, 1000 + index))
            with patch.object(update_game, 'ANALYSIS', analysis):
                self.assertEqual(update_game.latest_run().name, 'update-20261118-6870')

    def test_confetti_refresh_reports_failure(self):
        import confetti_offsets
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            self.assertIs(update_game.refresh_confetti_table(game), False)
            client = game / 'citadel/bin/win64/client.dll'
            client.parent.mkdir(parents=True)
            client.write_bytes(b'client')
            with patch.object(confetti_offsets, 'scan', side_effect=ValueError('no pattern')):
                self.assertIs(update_game.refresh_confetti_table(game), False)
            with patch.object(confetti_offsets, 'scan', return_value={}), \
                    patch.object(confetti_offsets, 'newest_row_matches', return_value=True):
                self.assertIs(update_game.refresh_confetti_table(game), True)


class UiOverrideCompile(unittest.TestCase):
    def test_stale_output_is_not_accepted_when_the_compiler_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            sdk = Path(temp)
            (sdk / 'game/citadel').mkdir(parents=True)
            (sdk / 'game/citadel/gameinfo.gi').write_text('')
            output = sdk / 'game/citadel_addons' / build_ui_override.ADDON
            for name, suffix in build_ui_override.EXPECTED_SOURCES.items():
                stale = (output / name).with_suffix(suffix)
                stale.parent.mkdir(parents=True, exist_ok=True)
                stale.write_bytes(b'stale')
            with patch.object(build_ui_override.subprocess, 'run',
                              return_value=subprocess.CompletedProcess([], 0)):
                with self.assertRaisesRegex(SystemExit, 'produced no output'):
                    build_ui_override.compile_sources(sdk, sdk / 'resourcecompiler.exe')


if __name__ == '__main__':
    unittest.main()
