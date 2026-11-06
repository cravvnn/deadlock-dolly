import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dolly import player_layer, player_scratch


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.deploy=self.root/'game';self.deploy.mkdir()
        self.scratch=self.root/'Unicode-α scratch';self.scratch.mkdir()
        self.layer=self.root/'output';self.layer.mkdir()

    def begin(self):
        player_layer.begin_capture(self.deploy,408,30,fps=30,speed=.5,
                                   scratch_root=self.scratch,output=self.layer/'players.mov')
        return player_scratch.from_marker(self.deploy)

    def test_unique_jobs_and_unicode_protocol_preserve_prior_capture(self):
        first=self.begin();(first/player_layer.BUNDLE_NAME).write_bytes(b'prior')
        second=self.begin()
        self.assertNotEqual(first,second)
        self.assertEqual((first/player_layer.BUNDLE_NAME).read_bytes(),b'prior')
        marker=(self.deploy/player_layer.MARKER_NAME).read_text()
        self.assertTrue(marker.startswith('color-sequence-v4'))
        self.assertIn(' speed 0.5 scratch ',marker)
        self.assertEqual(first.parent,self.scratch)
        self.assertEqual(json.loads((second/player_scratch.MANIFEST).read_text())['fps'],30)

    def test_cleanup_does_not_touch_neighbor_or_output(self):
        folder=self.begin();neighbor=self.scratch/'unrelated';neighbor.write_bytes(b'keep')
        (folder/player_layer.BUNDLE_NAME).write_bytes(b'raw')
        (folder/player_layer.META_NAME).write_bytes(b'meta')
        (self.layer/'players.mov').write_bytes(b'video')
        self.assertEqual(player_layer.cleanup_capture(self.deploy,self.layer),[])
        self.assertFalse(folder.exists())
        self.assertEqual(neighbor.read_bytes(),b'keep')
        self.assertEqual((self.layer/'players.mov').read_bytes(),b'video')

    def test_wrong_ownership_refuses_cleanup(self):
        folder=self.begin();raw=folder/player_layer.BUNDLE_NAME;raw.write_bytes(b'raw')
        (folder/player_scratch.OWNER).write_text('b'*32)
        with self.assertRaises(ValueError): player_layer.cleanup_capture(self.deploy,self.layer)
        self.assertEqual(raw.read_bytes(),b'raw')

    def test_unknown_files_are_never_removed(self):
        folder=self.begin();(folder/'keep.txt').write_text('keep')
        player_layer.cleanup_capture(self.deploy,self.layer)
        self.assertTrue((folder/'keep.txt').exists())
        self.assertTrue((folder/player_scratch.OWNER).exists())

    def test_selected_volume_and_failed_space_preflight(self):
        with patch('dolly.player_layer.shutil.disk_usage') as usage:
            usage.return_value.free=0
            with self.assertRaisesRegex(RuntimeError,'scratch drive'):
                player_layer.check_capture_space(self.scratch,1920,1080,30)
            usage.assert_called_once_with(self.scratch)

    def test_job_state_and_legacy_protocol(self):
        folder=self.begin();player_scratch.update(folder,'cancelled')
        self.assertEqual(json.loads((folder/player_scratch.MANIFEST).read_text())['state'],'cancelled')
        player_layer.begin_capture(self.deploy,408,30)
        self.assertEqual(player_scratch.from_marker(self.deploy),self.deploy)
        self.assertTrue(folder.is_dir())

    def test_native_must_acknowledge_requested_scratch_directory(self):
        folder=self.begin()
        request=(folder/player_scratch.OWNER).read_text()
        status=self.deploy/player_layer.STATUS_NAME
        status.write_text('armed\nrequest '+request+'\n')
        self.assertTrue(player_layer.capture_status(self.deploy).startswith('rejected scratch'))
        status.write_text('armed\nrequest '+request+'\nscratch '+str(folder).encode('utf-8').hex()+'\n')
        self.assertTrue(player_layer.capture_status(self.deploy).startswith('armed'))

    def test_scratch_root_survives_game_deployment_removal(self):
        folder=self.begin();raw=folder/player_layer.BUNDLE_NAME;raw.write_bytes(b'raw')
        # Simulate only the known control marker's removal, never a recursive delete.
        (self.deploy/player_layer.MARKER_NAME).unlink()
        self.deploy.rmdir()
        self.assertEqual(raw.read_bytes(),b'raw')
        self.assertTrue((folder/player_scratch.MANIFEST).is_file())

    def test_missing_write_permission_retains_prior_job(self):
        first=self.begin();(first/player_layer.BUNDLE_NAME).write_bytes(b'previous')
        with patch.object(Path,'mkdir',side_effect=PermissionError('read only')):
            with self.assertRaises(PermissionError): self.begin()
        self.assertEqual(player_scratch.from_marker(self.deploy),first)
        self.assertEqual((first/player_layer.BUNDLE_NAME).read_bytes(),b'previous')

    def test_missing_or_linked_root_refused(self):
        with self.assertRaises(ValueError): player_scratch.create(self.root/'missing',fps=30,frames=30)
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaisesRegex(ValueError,'links'):
                player_scratch.checked_directory(self.scratch)

    def test_interrupted_status_write_preserves_manifest_and_allows_retry(self):
        folder = self.begin()
        manifest = folder/player_scratch.MANIFEST
        original = manifest.read_bytes()
        raw = folder/player_layer.BUNDLE_NAME
        raw.write_bytes(b'keep raw capture')
        def fail(data, stream, **kwargs):
            stream.write('{"state":')
            raise OSError('disk full')
        with patch.object(player_scratch.json, 'dump', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'disk full'):
                player_scratch.update(folder, 'captured')
        self.assertEqual(manifest.read_bytes(), original)
        player_scratch.update(folder, 'encode_failed', error='retry reason')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual((data['state'], data['error']), ('encode_failed', 'retry reason'))
        self.assertEqual(raw.read_bytes(), b'keep raw capture')

    def test_legacy_temporary_does_not_block_completed_cleanup_or_get_deleted(self):
        folder = self.begin()
        legacy = folder/'capture.tmp'
        legacy.write_bytes(b'unknown interrupted file')
        (folder/player_layer.BUNDLE_NAME).write_bytes(b'raw')
        (self.layer/'players.mov').write_bytes(b'completed master')
        self.assertEqual(player_layer.cleanup_capture(self.deploy, self.layer), [])
        self.assertEqual(json.loads((folder/player_scratch.MANIFEST).read_text(encoding='utf-8'))['state'], 'completed')
        self.assertEqual(legacy.read_bytes(), b'unknown interrupted file')
        self.assertTrue((folder/player_scratch.OWNER).exists())
        self.assertEqual((self.layer/'players.mov').read_bytes(), b'completed master')

    def test_exclusive_name_collision_never_authorizes_foreign_cleanup(self):
        folder = self.begin()
        token = type('Token', (), {'hex': 'a'*32})()
        foreign = folder/('.dolly-capture-' + token.hex + '.tmp')
        foreign.write_bytes(b'another owner')
        with patch.object(player_scratch.uuid, 'uuid4', return_value=token):
            with self.assertRaises(FileExistsError):
                player_scratch.update(folder, 'captured')
        self.assertEqual(foreign.read_bytes(), b'another owner')
        player_scratch.update(folder, 'captured')

    def test_cleanup_error_keeps_successful_status_result(self):
        folder = self.begin()
        with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup denied')):
            self.assertIsNone(player_scratch.update(folder, 'captured'))
        self.assertEqual(json.loads((folder/player_scratch.MANIFEST).read_text(encoding='utf-8'))['state'], 'captured')


if __name__=='__main__':unittest.main()
