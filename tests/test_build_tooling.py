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
import check_game_update
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
                    patch.object(build_native, 'check_ui_generated'), \
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
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(release_status, '_slug', return_value='owner/repo'), \
                patch.object(release_status, '_gh_json', side_effect=gh), \
                patch.object(release_status, 'ROOT', Path(temp)), \
                contextlib.redirect_stdout(output):
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


class GameUpdateCheck(unittest.TestCase):
    def run_check(self, published):
        report = type('Report', (), {'modules': []})()
        with tempfile.TemporaryDirectory() as temp,                 patch.object(check_game_update.compatibility, 'scan_game_modules', return_value=report),                 patch.object(check_game_update, 'published_definitions', return_value=published),                 contextlib.redirect_stdout(io.StringIO()) as out:
            game = Path(temp) / 'game'
            (game / 'citadel').mkdir(parents=True)
            return check_game_update.main(['--game-dir', str(game)]), out.getvalue()

    def test_invalid_definitions_feed_fails_the_check(self):
        code, text = self.run_check((None, 'bad feed'))
        self.assertEqual(code, 2)
        self.assertIn('SUMMARY: the published definitions feed is invalid: bad feed', text)

    def test_valid_feed_with_reviewed_modules_passes(self):
        code, text = self.run_check(check_game_update.published_definitions())
        self.assertEqual(code, 0, text)


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


class UiGenerated(unittest.TestCase):
    def test_checked_in_ui_headers_match_their_generator(self):
        import generate_ui_tokens
        src = ROOT / 'native/src'
        self.assertEqual((src / 'dolly_ui_tokens_generated.hpp').read_text(encoding='utf-8'),
                         generate_ui_tokens.render())
        self.assertEqual((src / 'dolly_rail_tokens_generated.hpp').read_text(encoding='utf-8'),
                         generate_ui_tokens.render_rail())
        build_native.check_ui_generated(ROOT)

    def test_native_build_refuses_a_stale_token_header(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / 'native/src'
            src.mkdir(parents=True)
            (src / 'dolly_ui_tokens_generated.hpp').write_text('// stale\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'out of date'):
                build_native.check_ui_generated(Path(temp))


@unittest.skipUnless(sys.platform == 'win32', 'Windows job objects')
class BuildDesktopJob(unittest.TestCase):
    def test_closing_the_job_ends_the_worker(self):
        import ctypes
        import build_desktop
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        job = build_desktop.kill_on_close_job(kernel)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            self.assertTrue(kernel.AssignProcessToJobObject(job, int(child._handle)))
        finally:
            kernel.CloseHandle(job)
        try:
            child.wait(timeout=10)  # The 60 s sleeper ends only if the job killed it.
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


class UpdaterSelfTest(unittest.TestCase):
    def run_self_test(self, stub_install):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'updater-smoke.json'
            code = ('import sys; import dolly.update_worker as worker\n'
                    + ('worker.install = lambda *args, **kwargs: None\n' if stub_install else '')
                    + 'sys.exit(worker.self_test(sys.argv[1]))')
            # -O strips assert statements; the self-test must still check.
            result = subprocess.run([sys.executable, '-O', '-c', code, str(report)], cwd=ROOT)
            return result.returncode, json.loads(report.read_text(encoding='utf-8'))

    def test_self_test_passes_with_the_real_installer(self):
        code, report = self.run_self_test(False)
        self.assertEqual(code, 0)
        self.assertTrue(report['passed'])

    def test_self_test_fails_under_optimisation_when_install_does_nothing(self):
        code, report = self.run_self_test(True)
        self.assertEqual(code, 1)
        self.assertFalse(report['passed'])
        self.assertIn('did not stop the install', report['error'])


class TlaDiscovery(unittest.TestCase):
    def test_unlisted_configs_and_skipped_mutants_are_reported(self):
        import check_tla
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in ('dolly/A.tla', 'dolly/A.cfg', 'native/src/B.tla', 'native/src/B.cfg',
                             'native/src/B-fast.cfg', 'native/vendor/C.tla', 'native/vendor/C.cfg',
                             'tools/tla/node_modules/D.tla', 'tools/tla/node_modules/D.cfg',
                             'native/assets/game.cfg', 'tools/BApi.tla'):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text('')
            (root / 'native/src/B.mutants.json').write_text('[{"name": "x"}, {"name": "y"}]')
            (root / 'tools').mkdir(exist_ok=True)
            rows = [{'kind': 'model', 'name': r'dolly\A.cfg'},
                    {'kind': 'model', 'name': r'native\src\B.cfg'},
                    {'kind': 'mutant', 'name': r'native\src\B.tla mutant "x"'}]
            self.assertEqual(check_tla.unchecked(rows, root),
                             ['native/src/B-fast.cfg', 'native/src/B.tla (2 mutants, 1 ran)'])
            rows += [{'kind': 'model', 'name': r'native\src\B-fast.cfg'},
                     {'kind': 'mutant', 'name': r'native\src\B.tla mutant "y"'}]
            self.assertEqual(check_tla.unchecked(rows, root), [])


class SoundProfileReview(unittest.TestCase):
    def test_recent_sound_profile_must_be_reviewed_in_the_manifest(self):
        import generate_compatibility
        import generate_sound_profile
        recent = json.loads(generate_sound_profile.RECENT_PROFILE.read_text(encoding='utf-8'))['sha256']
        real = generate_compatibility.read_json

        def unreviewed(path):
            data = real(path)
            if Path(path).name == 'manifest.json':
                module = data['modules']['bin/win64/soundsystem.dll']
                module['accepted'] = [h for h in module['accepted'] if h != recent]
            return data

        with patch.object(generate_compatibility, 'read_json', side_effect=unreviewed):
            with self.assertRaisesRegex(Exception, 'Sound profile hash is not reviewed'):
                generate_compatibility.generated_outputs(ROOT)


class SharedDigest(unittest.TestCase):
    def test_tools_share_one_streamed_sha256(self):
        import hashlib
        import fetch_ffmpeg
        self.assertIs(fetch_ffmpeg.sha256, release_files.sha256)
        self.assertIs(update_game.sha256, release_files.sha256)
        self.assertIs(release_status.sha256, release_files.sha256)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'blob'
            path.write_bytes(b'x' * (3 * 1024 * 1024 + 7))
            self.assertEqual(release_files.sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())


class BundleManifest(unittest.TestCase):
    def bundle(self, temp):
        from dolly.updater import write_manifest
        bundle = Path(temp) / 'bundle'
        (bundle / '_internal/logs').mkdir(parents=True)
        (bundle / 'Dolly.exe').write_bytes(b'exe')
        for name in ('_internal/DollyApp.exe', '_internal/native/bin/win64/DollyNative.dll',
                     '_internal/third_party/ffmpeg/bin/ffmpeg.exe'):
            (bundle / name).parent.mkdir(parents=True, exist_ok=True)
            (bundle / name).write_bytes(name.encode())
        return bundle, write_manifest

    def test_manifest_files_must_ship_in_the_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, write_manifest = self.bundle(temp)
            # The manifest owns _internal/, but the ZIP drops runtime logs.
            (bundle / '_internal/logs/run.txt').write_text('private')
            write_manifest(bundle, '1.0.0')
            output = Path(temp) / 'dist/Deadlock_Dolly_1.0.0_Windows_x64.zip'
            with self.assertRaisesRegex(OSError, 'missing from the Windows archive'):
                release_files.bundle_zip(bundle, output)
            self.assertFalse(output.exists())

    def test_matching_manifest_zip_is_published(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, write_manifest = self.bundle(temp)
            write_manifest(bundle, '1.0.0')
            output = Path(temp) / 'dist/Deadlock_Dolly_1.0.0_Windows_x64.zip'
            self.assertEqual(release_files.bundle_zip(bundle, output), output)


if __name__ == '__main__':
    unittest.main()
