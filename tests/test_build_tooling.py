"""Release and build tooling checks; no compiler, game or network."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import build_native
import release_files


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


if __name__ == '__main__':
    unittest.main()
