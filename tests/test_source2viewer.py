"""Verified, one-time reconstructed-audio helper setup."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from dolly import source2viewer


class Source2ViewerSetupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "source2viewer"

    def _fixture_zip(self) -> tuple[Path, str]:
        archive = Path(self.folder.name) / "helper.zip"
        files = {
            "Source2Viewer-CLI.exe": b"fake-cli",
            "libSkiaSharp.dll": b"fake-skia",
            "spirv-cross.dll": b"fake-spirv",
        }
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in files.items():
                bundle.writestr(name, data)
        digest = hashlib.sha256(files["Source2Viewer-CLI.exe"]).hexdigest()
        return archive, digest

    def test_configured_viewer_takes_precedence(self):
        configured = Path(self.folder.name) / "Source2Viewer-CLI.exe"
        configured.write_bytes(b"exe")
        with patch.dict("os.environ", {"DOLLY_SOURCE2VIEWER": str(configured)}, clear=True):
            self.assertEqual(source2viewer.find_viewer(), configured)

    def test_cache_requires_the_complete_marker(self):
        cli = self.root / "Source2Viewer-CLI.exe"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"exe")
        with patch.object(source2viewer, "cache_root", return_value=self.root), \
                patch.object(source2viewer, "_candidate_paths", return_value=[]), \
                patch.object(source2viewer.shutil, "which", return_value=None):
            self.assertIsNone(source2viewer.find_viewer())
            (self.root / ".complete").write_text("{}", encoding="utf-8")
            self.assertEqual(source2viewer.find_viewer(), cli)

    def test_existing_valid_cache_is_reused_without_download(self):
        cli = self.root / "Source2Viewer-CLI.exe"
        cli.parent.mkdir(parents=True)
        cli.write_bytes(b"exe")
        (self.root / ".complete").write_text("{}", encoding="utf-8")
        with patch.object(source2viewer, "cache_root", return_value=self.root), \
                patch.object(source2viewer, "_candidate_paths", return_value=[]), \
                patch.object(source2viewer.shutil, "which", return_value=None), \
                patch.object(source2viewer, "_valid_cached_viewer", return_value=True), \
                patch.object(source2viewer, "_download") as download:
            self.assertEqual(source2viewer.ensure_viewer(), cli)
            download.assert_not_called()

    def test_first_use_downloads_verifies_and_writes_notice(self):
        archive, cli_digest = self._fixture_zip()
        progress = []

        def fake_download(partial, callback):
            partial.write_bytes(archive.read_bytes())
            callback(100, 100)

        with patch.object(source2viewer, "cache_root", return_value=self.root), \
                patch.object(source2viewer, "_candidate_paths", return_value=[]), \
                patch.object(source2viewer.shutil, "which", return_value=None), \
                patch.object(source2viewer, "SOURCE2VIEWER_CLI_SHA256", cli_digest), \
                patch.object(source2viewer, "_download", side_effect=fake_download):
            result = source2viewer.ensure_viewer(progress=lambda done, total: progress.append(done))
        self.assertEqual(result, self.root / "Source2Viewer-CLI.exe")
        self.assertTrue((self.root / "NOTICE.txt").is_file())
        self.assertTrue((self.root / "LICENSE.txt").is_file())
        self.assertTrue((self.root / ".complete").is_file())
        self.assertEqual(progress[0], 0)
        self.assertIn(100, progress)
        self.assertEqual(
            json.loads((self.root / ".complete").read_text())["version"],
            source2viewer.SOURCE2VIEWER_VERSION)

    def test_safe_extract_ignores_traversal_and_unknown_entries(self):
        archive = Path(self.folder.name) / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("Source2Viewer-CLI.exe", b"fake-cli")
            bundle.writestr("libSkiaSharp.dll", b"fake-skia")
            bundle.writestr("spirv-cross.dll", b"fake-spirv")
            bundle.writestr("../escape.txt", b"no")
            bundle.writestr("other.txt", b"no")
        digest = hashlib.sha256(b"fake-cli").hexdigest()
        with patch.object(source2viewer, "SOURCE2VIEWER_CLI_SHA256", digest):
            cli = source2viewer._safe_extract(archive, self.root)
        self.assertEqual(cli.name, "Source2Viewer-CLI.exe")
        self.assertFalse((Path(self.folder.name) / "escape.txt").exists())
        self.assertFalse((self.root / "other.txt").exists())
        self.assertTrue((self.root / "libSkiaSharp.dll").is_file())

    def test_failed_attempt_preserves_legacy_and_neighbor_files(self):
        self.root.mkdir()
        legacy = self.root / 'staging'
        legacy.mkdir()
        files = [self.root/'cli-windows-x64.zip.part', self.root/'cli-windows-x64.zip', legacy/'keep']
        for path in files:
            path.write_bytes(b'another attempt')
        with patch.object(source2viewer, 'find_viewer', return_value=None), \
                patch.object(source2viewer, 'cache_root', return_value=self.root), \
                patch.object(source2viewer, '_download', side_effect=OSError('download interrupted')):
            with self.assertRaises(RuntimeError):
                source2viewer.ensure_viewer()
        for path in files:
            self.assertEqual(path.read_bytes(), b'another attempt')

    def test_cleanup_denial_does_not_mask_installed_helper(self):
        archive, digest = self._fixture_zip()
        unlink = os.unlink
        def deny_archive(path, *args, **kwargs):
            if Path(path).name == 'cli-windows-x64.zip':
                raise PermissionError('archive is locked')
            return unlink(path, *args, **kwargs)
        with patch.object(source2viewer, 'find_viewer', return_value=None), \
                patch.object(source2viewer, 'cache_root', return_value=self.root), \
                patch.object(source2viewer, 'SOURCE2VIEWER_CLI_SHA256', digest), \
                patch.object(source2viewer, '_download', side_effect=lambda path, progress: path.write_bytes(archive.read_bytes())), \
                patch.object(os, 'unlink', side_effect=deny_archive):
            result = source2viewer.ensure_viewer()
        self.assertEqual(result.read_bytes(), b'fake-cli')
        self.assertTrue((self.root/'.complete').is_file())

    def test_other_completed_attempt_cannot_consume_paused_download(self):
        archive, digest = self._fixture_zip()
        ready, resume = threading.Event(), threading.Event()
        errors, results, paths = [], [], []
        def download(path, progress):
            paths.append(path)
            path.write_bytes(archive.read_bytes())
            if threading.current_thread().name == 'paused-helper':
                ready.set()
                if not resume.wait(5):
                    raise RuntimeError('test release timed out')
        def paused():
            try:
                results.append(source2viewer.ensure_viewer())
            except Exception as exc:
                errors.append(exc)
        with patch.object(source2viewer, 'find_viewer', return_value=None), \
                patch.object(source2viewer, 'cache_root', return_value=self.root), \
                patch.object(source2viewer, 'SOURCE2VIEWER_CLI_SHA256', digest), \
                patch.object(source2viewer, '_download', side_effect=download):
            worker = threading.Thread(target=paused, name='paused-helper', daemon=True)
            worker.start()
            try:
                self.assertTrue(ready.wait(5))
                result = source2viewer.ensure_viewer()
                self.assertEqual(result.read_bytes(), b'fake-cli')
            finally:
                resume.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [result])
        self.assertEqual(len(set(paths)), 2)


if __name__ == "__main__":
    unittest.main()
