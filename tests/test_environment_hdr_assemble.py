import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from dolly.environment_panorama import capture_plan
from dolly.environment_hdr import write_float_rgb_exr, read_float_rgb_exr
from dolly.environment_assemble import assemble_hdr_capture


class HdrAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.output=self.folder/'panorama.exr'
        self.plan=capture_plan([1,2,3],15300,face_size=512,panorama_width=64)
        color=np.tile(np.array([4.0,.25,16.0],dtype='<f4'),(512,1))
        rows=[]
        for face in self.plan['faces']:
            path=self.folder/(face['name']+'.exr')
            write_float_rgb_exr(path,512,512,(color for _ in range(512)))
            rows.append(dict(face,face=face['name'],request_id=face['name'],file=path.name,
                width=512,height=512,horizontal_fov=90,vertical_fov=90,
                scene_empty=True,hud_hidden=True,exposure_signature='synthetic-fixed',
                color_encoding='scene_linear_float',scene_time=1.0,
                replay_sha256='a'*64,game_build='synthetic',sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.report=dict(schema='dolly-environment-capture-v1',status='captured',restored=True,
                         plan=self.plan,faces=rows)
        self.save()

    def save(self):
        (self.folder/'capture.json').write_text(json.dumps(self.report))

    def test_hdr_survives_complete_face_to_panorama_path(self):
        assemble_hdr_capture(self.folder,self.output)
        pixels=read_float_rgb_exr(self.output,64,32)
        np.testing.assert_allclose(pixels,np.broadcast_to([4,.25,16],pixels.shape),rtol=1e-6)
        self.assertGreater(float(pixels.max()),1)
        before=self.output.read_bytes()
        with self.assertRaises(ValueError):assemble_hdr_capture(self.folder,self.output)
        self.assertEqual(self.output.read_bytes(),before)

    def test_overlap_metadata_reaches_exr_and_rejects_wrong_face_lens(self):
        self.report['plan']=capture_plan([1,2,3],15300,face_size=512,panorama_width=64,face_fov=100)
        self.save()
        with self.assertRaisesRegex(ValueError,'projection'):
            assemble_hdr_capture(self.folder,self.output)
        self.assertFalse(self.output.exists())
        for row in self.report['faces']:
            row.update(horizontal_fov=100,vertical_fov=100)
        self.save()
        assemble_hdr_capture(self.folder,self.output,width=128)
        pixels=read_float_rgb_exr(self.output,128,64)
        np.testing.assert_array_equal(pixels,np.broadcast_to([4,.25,16],pixels.shape))

    def test_reference_hdr_requires_explicit_reference_mode(self):
        self.report['schema']='dolly-environment-reference-v1'
        for row in self.report['faces']:
            row.update(scene_empty=False,scene_filter='world_classes',color_encoding='rendered_hdr_float')
        self.save()
        with self.assertRaises(ValueError):assemble_hdr_capture(self.folder,self.output)
        self.assertFalse(self.output.exists())
        assemble_hdr_capture(self.folder,self.output,rendered_reference=True)
        pixels=read_float_rgb_exr(self.output,64,32)
        np.testing.assert_allclose(pixels,np.broadcast_to([4,.25,16],pixels.shape),rtol=1e-6)

    def test_cancel_during_assembly_preserves_faces_without_final_panorama(self):
        from dolly.environment_capture import CaptureCancelled
        calls=0
        def cancelled():
            nonlocal calls
            calls+=1
            return calls>=9  # preflight, six face loads, then two row blocks
        with self.assertRaises(CaptureCancelled):
            assemble_hdr_capture(self.folder,self.output,cancelled=cancelled)
        self.assertFalse(self.output.exists())
        # The writer deliberately retains incomplete evidence under .partial.
        self.assertTrue(self.output.with_name(self.output.name+'.partial').exists())
        self.assertTrue((self.folder/'front.exr').is_file())

    def test_screenshot_exposure_drift_and_unrestored_reports_rejected(self):
        baseline=json.dumps(self.report)
        for mutation in ('display','exposure','restoration','camera'):
            with self.subTest(mutation=mutation):
                self.report=json.loads(baseline)
                if mutation=='display':self.report['faces'][0]['color_encoding']='display_srgb8'
                if mutation=='exposure':self.report['faces'][-1]['exposure_signature']='changed'
                if mutation=='restoration':self.report['restored']=False
                if mutation=='camera':self.report['faces'][0]['position']=[9,9,9]
                self.save()
                with self.assertRaises(ValueError):assemble_hdr_capture(self.folder,self.output)
                self.assertFalse(self.output.exists())

    def test_modified_face_rejected_before_output(self):
        with (self.folder/self.report['faces'][0]['file']).open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'Captured face changed'):
            assemble_hdr_capture(self.folder,self.output)
        self.assertFalse(self.output.exists())

    def test_decoder_rejects_wrong_dimensions_truncation_and_version(self):
        path=self.folder/self.report['faces'][0]['file'];original=path.read_bytes()
        with self.assertRaises(ValueError):read_float_rgb_exr(path,256,256)
        path.write_bytes(original[:-1])
        with self.assertRaises(ValueError):read_float_rgb_exr(path,512,512)
        path.write_bytes(original[:4]+b'\x02\x10\0\0'+original[8:])
        with self.assertRaises(ValueError):read_float_rgb_exr(path,512,512)

    def test_bad_output_width_rejected_before_decode(self):
        for width in (True,7,63,16386):
            with self.assertRaises(ValueError):assemble_hdr_capture(self.folder,self.output,width=width)
        self.assertFalse(self.output.exists())
