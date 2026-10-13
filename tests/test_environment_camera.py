import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dolly.environment_camera import position_face, record_face_probe
from dolly.environment_capture import CaptureCancelled
from dolly.environment_panorama import capture_plan


class EnvironmentCameraTests(unittest.TestCase):
    def setUp(self):
        self.plan = capture_plan([1, 2, 3], 15300, face_size=512)
        self.c = Mock()
        self.c._console = object()  # No imaginary ConsoleClient.demo_name property.
        self.bridge = self.c._native_bridge.return_value
        self.state = dict(demo_name='108605183.dem', paused=True, tick=15300, error=0,
                          applied_pose=[1, 2, 3, 0, 0, 0, 1], applied_fov=90,
                          ack_command=42, frame_count=100)
        self.bridge.status.side_effect = lambda: dict(self.state)
        def start(name, pose, **kwargs):
            self.assertEqual(name, '108605183.dem')
            self.assertTrue(kwargs['environment_face'])
            self.state.update(applied_pose=pose, ack_command=self.state['ack_command']+1)
            return dict(self.state)
        self.bridge.start_flight.side_effect = start
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ffmpeg = self.folder/'ffmpeg.exe'
        self.ffmpeg.write_bytes(b'fake')
        self.media = dict(state='completed', ack=10, frames_written=12, frames_dropped=0,
                          width=512, height=512, error_code=0)
        self.bridge.video_status.side_effect = lambda: dict(self.media)
        def record(path, **kw):
            Path(path).write_bytes(b'fake recording')
            return {'ack': 10}
        self.bridge.start_video.side_effect = record
        self.decode = patch('dolly.environment_camera.extract_plate', side_effect=self.extract)
        self.decode_mock = self.decode.start()
        self.addCleanup(self.decode.stop)

    def extract(self, ffmpeg, video, index, path, *, timeout):
        self.assertEqual(index, 8)
        self.assertEqual(timeout, 15)
        path.write_bytes(b'fake png')

    def record(self, **kw):
        return record_face_probe(self.c, self.plan, self.plan['faces'][0],
                                 self.folder/'take', self.ffmpeg, **kw)

    def test_six_views_same_centre_verified_native_identity(self):
        for face in self.plan['faces']:
            result = position_face(self.c, self.plan, face)
            self.assertEqual(result['applied_pose'], [1,2,3,face['pitch'],face['yaw'],0,1])
        self.assertEqual(self.bridge.start_flight.call_count, 6)
        self.assertTrue(self.c._native_active and self.c._native_manual)
        self.c._request.assert_not_called()

    def test_overlap_lens_transmitted_and_verified(self):
        import math
        plan=capture_plan([1,2,3],15300,face_size=512,face_fov=100)
        self.state['applied_fov']=100
        result=position_face(self.c,plan,plan['faces'][0])
        self.assertAlmostEqual(result['applied_pose'][6],math.tan(math.radians(50)))
        self.state['applied_fov']=90
        with self.assertRaisesRegex(RuntimeError,'projection changed'):
            position_face(self.c,plan,plan['faces'][0])

    def test_bad_plan_rejected_before_camera_write(self):
        self.plan['faces'][0]['yaw'] = 44
        with self.assertRaisesRegex(ValueError, 'canonical'):
            position_face(self.c, self.plan, self.plan['faces'][0])
        self.bridge.start_flight.assert_not_called()

    def test_cancel_before_camera_write(self):
        with self.assertRaises(CaptureCancelled):
            position_face(self.c, self.plan, self.plan['faces'][0], cancelled=lambda: True)
        self.bridge.start_flight.assert_not_called()

    def test_wrong_tick_or_unpaused_rejected_before_write(self):
        for change in ({'tick': 15301}, {'tick': 15300, 'paused': False}):
            self.state.update(change)
            with self.assertRaisesRegex(RuntimeError, 'paused replay tick'):
                position_face(self.c, self.plan, self.plan['faces'][0])
        self.bridge.start_flight.assert_not_called()

    def test_native_identity_rejection_prevents_write(self):
        self.c._require_native_demo.side_effect = RuntimeError('wrong replay')
        with self.assertRaisesRegex(RuntimeError, 'wrong replay'):
            position_face(self.c, self.plan, self.plan['faces'][0])
        self.bridge.start_flight.assert_not_called()

    def test_changed_projection_retains_controller_cleanup_ownership(self):
        self.c._wait_paused_native_view.side_effect = lambda *a, **kw: self.state.update(applied_fov=75)
        with self.assertRaisesRegex(RuntimeError, 'projection changed'):
            position_face(self.c, self.plan, self.plan['faces'][0])
        self.assertTrue(self.c._native_active and self.c._native_manual)

    def test_capped_recording_is_diagnostic_not_qualified_output(self):
        result = self.record()
        self.assertEqual(result['status'], 'diagnostic_only')
        self.assertFalse(result['qualified_environment'])
        options = self.bridge.start_video.call_args.kwargs
        self.assertEqual(options['max_frames'], 12)
        self.assertFalse(options['shot_only'] or options['fixed_step'])
        self.assertEqual((options['encoder'], options['codec']), (1, 10))
        self.c._request.assert_not_called()
        self.bridge.stop_video.assert_not_called()

    def test_existing_recording_is_preserved(self):
        self.media['state'] = 'recording'
        with self.assertRaisesRegex(RuntimeError, 'current recording'):
            self.record()
        self.bridge.start_video.assert_not_called()
        self.bridge.stop_video.assert_not_called()

    def test_camera_drift_during_recording_cancels_recorder(self):
        def start(path, **kw):
            self.state['applied_pose'] = [2,2,3,0,0,0,1]
            return {'ack': 10}
        self.bridge.start_video.side_effect = start
        with self.assertRaisesRegex(RuntimeError, 'projection changed'):
            self.record()
        self.bridge.stop_video.assert_called_once_with(cancel=True)
        self.decode_mock.assert_not_called()

    def test_cancel_after_start_cancels_without_extracting(self):
        with self.assertRaises(CaptureCancelled):
            self.record(cancelled=lambda: bool(self.bridge.start_video.call_count))
        self.bridge.stop_video.assert_called_once_with(cancel=True)
        self.decode_mock.assert_not_called()

    def test_stalled_recorder_times_out_without_retry(self):
        self.bridge.video_status.side_effect = [dict(state='idle'), dict(state='recording', ack=10)]
        with self.assertRaises(TimeoutError):
            self.record(clock=Mock(side_effect=[0, 16]))
        self.bridge.stop_video.assert_called_once_with(cancel=True)
        self.assertEqual(self.bridge.start_video.call_count, 1)

    def test_bad_dimensions_and_missing_frames_fail(self):
        for change in ({'width': 1024}, {'frames_written': 11}, {'frames_dropped': 1}):
            with self.subTest(change=change):
                original = dict(self.media)
                self.media.update(change)
                with self.assertRaisesRegex(RuntimeError, 'frame count'):
                    record_face_probe(self.c, self.plan, self.plan['faces'][0],
                                      self.folder/next(iter(change)), self.ffmpeg)
                self.media = original
        self.decode_mock.assert_not_called()

    def test_uncertain_start_failure_still_requests_cleanup(self):
        self.bridge.start_video.side_effect = RuntimeError('ack timeout')
        self.bridge.stop_video.side_effect = RuntimeError('cleanup timeout')
        with self.assertRaisesRegex(RuntimeError, 'ack timeout'):
            self.record()
        saved = json.loads((self.folder/'take/probe.json').read_text())
        self.assertIn('cleanup timeout', saved['recorder_cleanup_error'])
        self.decode_mock.assert_not_called()

    def test_stale_media_ack_is_rejected(self):
        self.media['ack'] = 8
        with self.assertRaisesRegex(RuntimeError, 'command was replaced'):
            self.record()
        self.bridge.stop_video.assert_not_called()

    def test_existing_directory_never_overwritten(self):
        (self.folder/'take').mkdir()
        with self.assertRaises(FileExistsError):
            self.record()
        self.bridge.start_flight.assert_not_called()


if __name__ == '__main__':
    unittest.main()
