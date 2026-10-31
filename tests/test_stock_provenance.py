import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import stock_provenance as stock
from test_panorama_assets import write_vpk


def _varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128); value >>= 7
    result.append(value)
    return bytes(result)


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def manifest_bytes(files, *, depot=1422456, gid=1, names=None, encrypted=0):
    rows = []
    for name, data in files.items():
        actual = names.get(name, name) if names else name
        row = _field(1, actual.encode()) + _field(2, len(data)) + _field(3, 0) + _field(5, hashlib.sha1(data).digest())
        rows.append(_field(1, row))
    payload = b''.join(rows)
    metadata = _field(1, depot) + _field(2, gid) + _field(4, encrypted)
    return (struct.pack('<II', 0x71f617d0, len(payload)) + payload
            + struct.pack('<II', 0x1f4812be, len(metadata)) + metadata
            + struct.pack('<III', 0x1b81b817, 0, 0x32c415ab))


def video_fixture():
    return {name: ('reviewed stock video ' + name).encode() for name in stock.VIDEO_RESOURCES}


def install_provenance_fixture(paths, editing_root, *, files=None, gid=1):
    """Real local Steam layout and wire data, never production Steam paths."""
    editing_root.mkdir(exist_ok=True)
    if files is None:
        manifest = json.loads((editing_root/'cfg-manifest.json').read_text(encoding='utf-8'))
        files = {row['stock_path']: (editing_root/'cfg'/name).read_bytes()
                 for name, row in manifest['files'].items() if 'stock_path' in row}
        files['game/core/cfg/user_keys_default.vcfg'] = b'reviewed fallback bindings'
        files['game/citadel/gameinfo.gi'] = (editing_root/'gameinfo.gi').read_bytes()
    library = paths.root.parent.parent.parent
    app = library/'steamapps/appmanifest_1422450.acf'
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text('"AppState" { "appid" "1422450" "StateFlags" "4" "installdir" "'
                   + paths.root.name + '" "InstalledDepots" { "1422456" { "manifest" "'
                   + str(gid) + '" } } }', encoding='utf-8')
    cache = library/'depotcache'; cache.mkdir(exist_ok=True)
    manifest_path = cache/f'1422456_{gid}.manifest'
    manifest_path.write_bytes(manifest_bytes(files, gid=gid))
    videos = video_fixture()
    paths.citadel_dir.mkdir(parents=True, exist_ok=True)
    write_vpk(paths.citadel_dir/'pak01_dir.vpk', videos)
    expected = {'format': 1, 'files': {name: {'bytes': len(data), 'sha1': hashlib.sha1(data).hexdigest()}
                                      for name, data in sorted(files.items())},
                'reviewed_depots': [{'1422456': '1'}],
                'video_resources': {name: hashlib.sha256(data).hexdigest() for name, data in videos.items()}}
    raw = (json.dumps(expected, indent=2)+'\n').encode()
    (editing_root/'stock-provenance.json').write_bytes(raw)
    profile_path = editing_root/'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8')) if profile_path.exists() else {}
    profile.update(stock_provenance_sha256=hashlib.sha256(raw).hexdigest(),
                   stock_gameinfo_sha1=hashlib.sha1(files['game/citadel/gameinfo.gi']).hexdigest())
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    return files, app, manifest_path


class StockProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        root = self.folder/'Steam/steamapps/common/Deadlock'
        self.paths = SimpleNamespace(root=root, citadel_dir=root/'game/citadel')
        self.editing = self.folder/'editing'
        self.files, self.app, self.cache = install_provenance_fixture(self.paths, self.editing,
            files={'game/citadel/gameinfo.gi': b'stock GameInfo',
                   'game/citadel/cfg/machine_convars_default.vcfg': b'reviewed stock cfg'})
        self.profile = json.loads((self.editing/'profile.json').read_text(encoding='utf-8'))

    def verify(self):
        return stock.verify(self.paths, self.editing, self.profile)

    def unknown(self, files=None):
        self.app.write_text(self.app.read_text(encoding='utf-8').replace('"manifest" "1"', '"manifest" "2"'), encoding='utf-8')
        self.cache = self.cache.with_name('1422456_2.manifest')
        self.cache.write_bytes(manifest_bytes(files or self.files, gid=2))

    def test_exact_reviewed_install_does_not_require_a_cleaned_cache(self):
        self.cache.unlink()
        self.assertEqual(self.verify()['basis'], 'reviewed_installed_depots')

    def test_new_depot_identity_requires_and_reads_current_cache(self):
        self.unknown()
        before = {path: (path.read_bytes(), path.stat().st_mode) for path in (self.app, self.cache)}
        receipt = self.verify()
        self.assertEqual(receipt['basis'], 'current_cached_manifests')
        self.assertEqual(receipt['depots'][0]['manifest'], '2')
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mode) for path in before}, before)

    def test_unknown_map_missing_cache_refuses(self):
        self.unknown(); self.cache.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.verify()

    def test_changed_added_removed_or_nested_stock_cfg_refuses(self):
        variants = [dict(self.files, **{'game/citadel/gameinfo.gi': b'changed'}),
                    dict(self.files, **{'game/citadel/cfg/new.cfg': b'new'}),
                    {'game/citadel/gameinfo.gi': self.files['game/citadel/gameinfo.gi']},
                    dict(self.files, **{'game/core/cfg/nested/startup.cfg': b'exec competitive'})]
        self.unknown()
        for files in variants:
            with self.subTest(files=tuple(files)):
                self.cache.write_bytes(manifest_bytes(files, gid=2))
                with self.assertRaisesRegex(ValueError, 'stock GameInfo/cfg changed'):
                    self.verify()

    def test_wrong_metadata_depot_gid_or_encryption_refuses(self):
        self.unknown()
        for values in ({'depot': 1, 'gid': 2}, {'gid': 1}, {'gid': 2, 'encrypted': 1}):
            with self.subTest(values=values):
                self.cache.write_bytes(manifest_bytes(self.files, **values))
                with self.assertRaisesRegex(ValueError, 'metadata identity'):
                    self.verify()

    def test_duplicate_case_alias_or_unsafe_path_refuses(self):
        self.unknown()
        files = dict(self.files, **{'GAME/CITADEL/GAMEINFO.GI': b'alias'})
        for data in (manifest_bytes(files, gid=2),
                     manifest_bytes(self.files, gid=2, names={'game/citadel/gameinfo.gi': '../escape'}),
                     manifest_bytes(self.files, gid=2, names={'game/citadel/gameinfo.gi': 'C:/escape'})):
            self.cache.write_bytes(data)
            with self.assertRaises(ValueError):
                self.verify()

    def test_truncated_frames_overflow_varints_and_unsupported_wire_refuse(self):
        self.unknown()
        valid = manifest_bytes(self.files, gid=2)
        old_size = struct.unpack_from('<I', valid, 4)[0]
        def malformed_payload(payload):
            return struct.pack('<II', 0x71f617d0, len(payload))+payload+valid[8+old_size:]
        for data in (valid[:-1], valid[:10], valid+b'extra',
                     malformed_payload(b'\x80'*11), malformed_payload(b'\x0b')):
            self.cache.write_bytes(data)
            with self.assertRaises(ValueError):
                self.verify()

    def test_oversized_manifest_is_refused_before_read(self):
        self.unknown()
        with self.cache.open('wb') as stream:
            stream.seek(16 * 1024 * 1024); stream.write(b'x')
        with self.assertRaisesRegex(ValueError, 'Oversized'):
            self.verify()

    def test_duplicate_scalar_and_wrong_content_hash_size_refuse(self):
        self.unknown()
        valid = self.cache.read_bytes(); old_size = struct.unpack_from('<I', valid, 4)[0]
        name = b'game/citadel/gameinfo.gi'
        for row in (_field(1, name)+_field(1, name)+_field(2, 0)+_field(5, b'0'*20),
                    _field(1, name)+_field(2, 0)+_field(5, b'too short')):
            payload = _field(1, row)
            self.cache.write_bytes(struct.pack('<II', 0x71f617d0, len(payload))+payload+valid[8+old_size:])
            with self.assertRaises(ValueError):
                self.verify()

    def test_cache_change_after_read_is_refused(self):
        self.unknown(); original = stock._video
        def change_after_video(*args):
            original(*args)
            self.cache.write_bytes(self.cache.read_bytes()+b' ')
        with patch.object(stock, '_video', side_effect=change_after_video):
            with self.assertRaisesRegex(ValueError, 'install changed'):
                self.verify()

    def test_linked_cache_refuses(self):
        self.unknown()
        link = self.cache.parent
        actual = link.with_name('retained-cache')
        self.assertTrue(link.resolve().is_relative_to(self.folder.resolve()))
        self.assertTrue(actual.resolve().is_relative_to(self.folder.resolve()))
        link.rename(actual)
        before = (actual/self.cache.name).read_bytes()
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(actual), str(link))
        else:
            link.symlink_to(actual, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, 'Linked'):
                self.verify()
            self.assertEqual((actual/self.cache.name).read_bytes(), before)
        finally:
            if os.name == 'nt':
                link.rmdir()
            else:
                link.unlink()

    def test_tampered_inventory_or_duplicate_json_refuses(self):
        data = self.editing/'stock-provenance.json'
        data.write_bytes(data.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.verify()
        data.write_bytes(b'{"format":1,"format":1}')
        self.profile['stock_provenance_sha256'] = hashlib.sha256(data.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.verify()

    def test_current_appmanifest_wrong_install_update_state_or_duplicate_refuses(self):
        original = self.app.read_text(encoding='utf-8')
        for data in (original.replace('"Deadlock"', '"Other"'),
                     original.replace('"StateFlags" "4"', '"StateFlags" "6"'),
                     original.replace('"appid" "1422450"', '"appid" "1422450" "appid" "1422450"')):
            self.app.write_text(data, encoding='utf-8')
            with self.assertRaises(ValueError):
                self.verify()

    def test_changed_stock_video_refuses_even_for_reviewed_depots(self):
        videos = video_fixture(); videos['cfg/video_defaults_2.txt'] += b'changed'
        write_vpk(self.paths.citadel_dir/'pak01_dir.vpk', videos)
        with self.assertRaisesRegex(ValueError, 'video defaults changed'):
            self.verify()

    def test_install_change_during_read_is_refused(self):
        original = stock._video
        def change_after_video(*args):
            original(*args)
            self.app.write_bytes(self.app.read_bytes()+b' ')
        with patch.object(stock, '_video', side_effect=change_after_video):
            with self.assertRaisesRegex(ValueError, 'install changed'):
                self.verify()

    def test_unknown_duplicate_depot_mapping_refuses(self):
        text = self.app.read_text(encoding='utf-8')
        self.app.write_text(text.replace('"InstalledDepots" {', '"InstalledDepots" { "1422456" { "manifest" "2" }'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate Steam depot'):
            self.verify()

    def test_ambiguous_cache_refuses_and_bundle_never_writes_install_cfg(self):
        self.unknown()
        other = self.folder/'Other Steam'; (other/'depotcache').mkdir(parents=True)
        (other/'depotcache'/self.cache.name).write_bytes(self.cache.read_bytes()+b'other')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            stock.verify(self.paths, self.editing, self.profile, [other])
        self.assertFalse((self.paths.citadel_dir/'cfg').exists())

    def test_published_stock_variants_are_additive_and_bounded(self):
        changed = b'published stock cfg'
        files = dict(self.files, **{'game/citadel/gameinfo.gi': changed})
        self.unknown(files)
        with self.assertRaisesRegex(ValueError, 'stock GameInfo/cfg changed'):
            self.verify()
        extra = {'game/citadel/gameinfo.gi': ((len(changed), hashlib.sha1(changed).hexdigest()),)}
        receipt = stock.verify(self.paths, self.editing, self.profile, extra_files=extra)
        self.assertEqual(receipt['basis'], 'current_cached_manifests')
        self.assertIn({'stock_snapshot': 'published definitions'}, receipt['depots'])
        # A published row cannot invent a stock path that is not in the bundle.
        with self.assertRaisesRegex(ValueError, 'unreviewed path'):
            stock.verify(self.paths, self.editing, self.profile,
                         extra_files={'game/citadel/cfg/invented.cfg': ((1, 'a' * 40),)})
        # A published variant cannot widen a different file's content.
        with self.assertRaisesRegex(ValueError, 'stock GameInfo/cfg changed'):
            stock.verify(self.paths, self.editing, self.profile,
                         extra_files={'game/citadel/gameinfo.gi': ((1, 'a' * 40),)})
        # The bundled reviewed row stays accepted when the install matches it.
        self.unknown(self.files)
        self.assertEqual(self.verify()['basis'], 'current_cached_manifests')
