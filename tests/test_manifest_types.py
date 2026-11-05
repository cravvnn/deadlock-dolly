"""Manifest shape validation and the staged-cache continuation boundary."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dolly import updater as u
from test_updater import package


class ManifestTypeTests(unittest.TestCase):
    def test_wrong_shapes_raise_validation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package(root, '1.0.1')
            template = u.read_manifest(root)
            malformed = [None, [], False, 1, 1.5, 'text']
            missing = dict(template)
            missing.pop('version')
            malformed.append(missing)
            for value in (None, False, 4, [], {}):
                malformed.append({**template, 'version': value})
                malformed.append({**template, 'files': {**template['files'], 'Dolly.exe': value}})
            for document in malformed:
                with self.subTest(document=document):
                    (root / u.MANIFEST).write_text(json.dumps(document), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        u.read_manifest(root)

    def test_integer_schemas_preserve_both_supported_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package(root, '1.0.1')
            template = u.read_manifest(root)
            app = root / '_internal/DollyApp.exe'
            app.write_bytes(b'private app fixture')
            template['files']['_internal/DollyApp.exe'] = u.digest_file(app)
            for schema in (1, 2):
                (root / u.MANIFEST).write_text(json.dumps({**template, 'schema': schema}), encoding='utf-8')
                self.assertEqual(u.verify_payload(root, '1.0.1')['schema'], schema)
            for schema in (True, 1.0, 2.0):
                with self.subTest(schema=schema):
                    (root / u.MANIFEST).write_text(json.dumps({**template, 'schema': schema}), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        u.read_manifest(root)

    def test_bad_cache_cannot_block_later_approved_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / '.dolly-update-000bad'
            good = root / '.dolly-update-999good'
            for work in (bad, good):
                package(work / 'payload', '1.0.1')
                (work / 'verified.json').write_text(json.dumps({'version': '1.0.1', 'sha256': 'a' * 64}), encoding='utf-8')
            template = u.read_manifest(good / 'payload')
            missing = dict(template)
            missing.pop('version')
            documents = [[], missing, {**template, 'schema': True},
                {**template, 'files': {**template['files'], 'Dolly.exe': None}}]
            release = {'version': '1.0.1', 'sha256': 'a' * 64, 'url': 'https://offline.invalid/never', 'size': 1}
            for document in documents:
                with self.subTest(document=document):
                    manifest = bad / 'payload' / u.MANIFEST
                    manifest.write_text(json.dumps(document), encoding='utf-8')
                    before = manifest.read_bytes()
                    with patch.object(u, 'open_url', side_effect=AssertionError('unexpected network')) as network:
                        self.assertEqual(u.download_update(release, root), good)
                        network.assert_not_called()
                    self.assertEqual(manifest.read_bytes(), before)

    def test_programming_errors_in_reader_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / '.dolly-update-test'
            work.mkdir()
            (work / 'verified.json').write_text('{}', encoding='utf-8')
            with patch.object(u, 'read_manifest', side_effect=RuntimeError('reader bug')):
                with self.assertRaisesRegex(RuntimeError, 'reader bug'):
                    u.staged_payload(root, '1.0.1', 'a' * 64)
