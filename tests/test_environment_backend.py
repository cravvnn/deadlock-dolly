from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch

from dolly.environment_backend import EnvironmentBackend
from dolly.environment_panorama import capture_plan
from dolly.environment_capture import capture_environment,validate_face


class EnvironmentBackendTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.folder=Path(folder.name);self.demo=self.folder/'test.dem';self.demo.write_bytes(b'synthetic replay')
        self.state=dict(paused=True,error=0,tick=64,demo_name=str(self.demo),ack_command=7,frame_count=12)
        self.pose=[1,2,3,0,0,0,1]
        self.bridge=SimpleNamespace(status=lambda:dict(self.state),editor_status=lambda:dict(input_mode='flight'),
            start_flight=Mock(return_value=dict(frame_count=20)),configure_editor=Mock())
        self.session=object();self.hud=1;self.commands=[]
        def request(command):
            self.commands.append(command)
            if command.startswith('citadel_hud_visible '):
                value=command.split()[1]
                if value not in ('0','1'):raise RuntimeError('String cannot be converted to bool')
                self.hud=float(value)
            return 'citadel_hud_visible = '+str(self.hud)
        self.controller=SimpleNamespace(_session=self.session,_native_bridge=lambda:self.bridge,
            _require_connection=Mock(),_require_native_demo=Mock(),game_pid=lambda:123,
            _sample_paused_native_view=lambda:dict(pose=self.pose[:],tick=64,horizontal_fov=75),
            _request=request,_layer_hidden=None,_playback_restore={},
            apply_layer_mode=Mock(return_value=dict(mode='world',hidden=['SkinnedObject','ParticleSystem'])),
            reset_layer_modes=Mock(),_wait_paused_native_view=Mock(),
            set_export_resolution=Mock(),clear_export_resolution=Mock())
        self.edition=patch('dolly.edition.is_environment',return_value=True);self.edition.start();self.addCleanup(self.edition.stop)
        self.size=patch('dolly.environment_backend.measure_source',return_value=dict(width=1024,height=1024));self.size.start();self.addCleanup(self.size.stop)

    def backend(self,**kwargs):return EnvironmentBackend(self.controller,**kwargs)

    def test_prepare_restore_keeps_existing_hud_restore_obligation(self):
        self.controller._playback_restore['citadel_hud_visible']='1'
        backend=self.backend();view=backend.view();snapshot=backend.snapshot()
        backend.prepare(capture_plan(view['position'],view['tick'],face_size=1024))
        self.assertEqual(self.hud,0)
        backend.restore(snapshot)
        self.assertEqual(self.hud,1)
        self.assertEqual(self.controller._playback_restore['citadel_hud_visible'],'1')
        self.bridge.configure_editor.assert_called_with(owner='flight')
        self.controller.reset_layer_modes.assert_called_once()

    def test_restore_preserves_environment_projection_mode(self):
        import math
        self.bridge._mode=5
        self.pose[6]=math.tan(math.radians(50))
        self.controller._sample_paused_native_view=lambda:dict(pose=self.pose[:],tick=64,horizontal_fov=100)
        backend=self.backend();backend.view();snapshot=backend.snapshot()
        backend.prepare(capture_plan([1,2,3],64,face_size=1024,face_fov=100))
        self.bridge._mode=4  # Later state cannot overwrite the saved mode.
        backend.restore(snapshot)
        self.bridge.start_flight.assert_called_once_with(str(self.demo),self.pose,owner='panel',environment_face=True)

    def test_filter_failure_still_restores_hud_and_camera(self):
        backend=self.backend();backend.view()
        self.controller.apply_layer_mode.side_effect=RuntimeError('filter failed')
        with self.assertRaisesRegex(RuntimeError,'filter failed'):
            capture_environment(backend,self.folder/'capture',[1,2,3],64,face_size=1024,rendered_reference=True)
        self.assertEqual(self.hud,1)
        self.assertEqual(self.controller._playback_restore,{})
        self.bridge.start_flight.assert_called_once()

    def test_hidden_hud_restores_using_boolean_console_syntax(self):
        self.hud=0.0
        self.controller._playback_restore['citadel_hud_visible']='1'
        backend=self.backend();backend.view();snapshot=backend.snapshot()
        backend.prepare(capture_plan([1,2,3],64,face_size=1024))
        backend.restore(snapshot)
        self.assertEqual(self.hud,0)
        self.assertEqual(self.controller._playback_restore['citadel_hud_visible'],'1')
        self.assertEqual(self.commands[-2:],[ 'citadel_hud_visible 0','citadel_hud_visible'])

    def test_non_square_or_stale_key_fails_before_scene_changes(self):
        with patch('dolly.environment_backend.measure_source',return_value=dict(width=2560,height=1440)):
            with self.assertRaisesRegex(RuntimeError,'square'):self.backend().view()
        for event in (dict(tick=63,paused=True,pose=self.pose),dict(tick=64,paused=True,pose=[]),
                      dict(tick=64,paused=True,pose=[9]*7)):
            with self.assertRaises(RuntimeError):self.backend(event=event).view()
        self.assertEqual(self.commands,[])

    def test_environment_removes_ziplines_and_records_restore_obligation(self):
        backend=self.backend();backend.view();backend.snapshot()
        backend.prepare(capture_plan([1,2,3],64,face_size=1024))
        self.assertIn('sc_setclassflags ZipLineRopeSegment 8',self.commands)
        self.assertIn('ZipLineRopeSegment',self.controller._layer_hidden)
        self.assertIn('ZipLineRopeSegment',backend.filter['hidden'])
        self.assertEqual(backend.filter['environment_profile'],'world_without_ziplines')

    def test_zipline_command_failure_keeps_restore_obligation(self):
        backend=self.backend();backend.view()
        request=self.controller._request
        def fail_zipline(command):
            if command=='sc_setclassflags ZipLineRopeSegment 8':
                self.assertIn('ZipLineRopeSegment',self.controller._layer_hidden)
                raise RuntimeError('zipline filter failed')
            return request(command)
        self.controller._request=fail_zipline
        with self.assertRaisesRegex(RuntimeError,'zipline filter failed'):
            capture_environment(backend,self.folder/'capture',[1,2,3],64,face_size=1024,rendered_reference=True)
        self.controller.reset_layer_modes.assert_called_once()
        self.assertEqual(self.hud,1)
        self.bridge.start_flight.assert_called_once()

    def test_view_forces_full_render_scale_and_restore_releases_it(self):
        backend=self.backend();backend.view()
        self.controller.set_export_resolution.assert_called_once()
        snapshot=backend.snapshot()
        backend.prepare(capture_plan([1,2,3],64,face_size=1024))
        backend.restore(snapshot)
        self.controller.clear_export_resolution.assert_called_once()

    def test_view_failure_releases_forced_render_scale(self):
        # A non-square source is rejected after the scale was forced; it must
        # be released so the session is not left at full render scale.
        with patch('dolly.environment_backend.measure_source',
                   return_value=dict(width=2560,height=1440)):
            with self.assertRaisesRegex(RuntimeError,'square'):
                self.backend().view()
        self.controller.set_export_resolution.assert_called_once()
        self.controller.clear_export_resolution.assert_called_once()

    def test_changed_session_never_restores_into_replacement(self):
        backend=self.backend();backend.view();snapshot=backend.snapshot()
        backend.prepare(capture_plan([1,2,3],64,face_size=1024))
        self.controller._session=object();count=len(self.commands)
        with self.assertRaisesRegex(RuntimeError,'session changed'):backend.restore(snapshot)
        self.assertEqual(len(self.commands),count)
        self.controller.reset_layer_modes.assert_not_called()

    def test_reference_receipt_cannot_pass_strict_empty_scene_validation(self):
        plan=capture_plan([1,2,3],64,face_size=1024)
        receipt=dict(plan['faces'][0],request_id='test',replay_sha256='a'*64,game_build='synthetic',
            width=1024,height=1024,horizontal_fov=90,vertical_fov=90,hud_hidden=True,
            scene_empty=False,scene_filter='world_classes',color_encoding='rendered_hdr_float',
            exposure_signature='measured',scene_time=64.)
        validate_face(plan,plan['faces'][0],receipt,'test',rendered_reference=True)
        with self.assertRaisesRegex(ValueError,'Empty-environment'):
            validate_face(plan,plan['faces'][0],receipt,'test')
