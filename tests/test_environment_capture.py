import json
from pathlib import Path
import tempfile
import unittest
from dolly.environment_capture import capture_environment, CaptureCancelled


class Backend:
    def __init__(self, mutate=None):
        self.calls=[]; self.mutate=mutate
    def snapshot(self): self.calls.append('snapshot'); return {'original': 1, 'replay_sha256':'a'*64, 'game_build':'synthetic'}
    def prepare(self, plan): self.calls.append('prepare'); self.plan=plan
    def capture(self, face, request, directory):
        self.calls.append(face['name'])
        file=directory/(face['name']+'.raw'); file.write_bytes(b'test frame')
        result=dict(face, request_id=request, file=str(file), width=512,height=512,
                    horizontal_fov=90,vertical_fov=90,scene_empty=True,hud_hidden=True,
                    exposure_signature='locked',color_encoding='display_srgb8',scene_time=1.0,
                    replay_sha256='a'*64,game_build='synthetic')
        if self.mutate: self.mutate(result)
        return result
    def restore(self, snapshot):
        assert snapshot['original']==1; self.calls.append('restore')


class CaptureTests(unittest.TestCase):
    def run_capture(self, backend, **kwargs):
        return capture_environment(backend, self.folder/'take', [1,2,3], 64, face_size=512,**kwargs)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
    def test_all_faces_and_restoration(self):
        backend=Backend(); report=self.run_capture(backend)
        self.assertEqual(report['status'],'captured'); self.assertTrue(report['restored'])
        self.assertEqual(len(report['faces']),6); self.assertEqual(backend.calls[-1],'restore')
    def test_camera_drift_fails_before_next_face(self):
        backend=Backend(lambda r:r.update(position=[1,2,4]))
        with self.assertRaisesRegex(ValueError,'optical centre'): self.run_capture(backend)
        self.assertEqual(backend.calls,['snapshot','prepare','front','restore'])
    def test_projection_cannot_be_inferred_from_square_size(self):
        backend=Backend(lambda r:r.update(horizontal_fov=75))
        with self.assertRaisesRegex(ValueError,'projection'): self.run_capture(backend)
        self.assertEqual(backend.calls[-1],'restore')
    def test_cancel_stops_capture_and_restores(self):
        backend=Backend()
        with self.assertRaises(CaptureCancelled):
            self.run_capture(backend,cancelled=lambda:'front' in backend.calls)
        self.assertEqual(backend.calls,['snapshot','prepare','front','restore'])
    def test_scene_time_drift_is_not_accepted_as_frozen(self):
        backend=Backend(lambda r:r.update(scene_time=1.01 if r['name']=='right' else 1))
        with self.assertRaisesRegex(ValueError,'scene time'): self.run_capture(backend)
        self.assertEqual(backend.calls[-1],'restore'); self.assertNotIn('back',backend.calls)
    def test_restoration_failure_prevents_success(self):
        backend=Backend()
        def fail(snapshot): raise RuntimeError('restore failed')
        backend.restore=fail
        with self.assertRaisesRegex(RuntimeError,'restore failed'): self.run_capture(backend)
        report=json.loads((self.folder/'take/capture.json').read_text())
        self.assertEqual(report['status'],'restoration_failed')
    def test_existing_directory_is_never_overwritten(self):
        (self.folder/'take').mkdir(); backend=Backend()
        with self.assertRaises(FileExistsError): self.run_capture(backend)
        self.assertEqual(backend.calls,[])
    def test_changed_replay_with_same_tick_rejected(self):
        backend=Backend(lambda r:r.update(replay_sha256='b'*64))
        with self.assertRaisesRegex(ValueError,'Owned replay'): self.run_capture(backend)
        self.assertEqual(backend.calls,['snapshot','prepare','front','restore'])
