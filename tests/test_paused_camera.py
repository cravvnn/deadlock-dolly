"""Paused-camera behavior at the real console transport boundary.

These simulations verify fixed replay time, calibration reuse, capture/restart,
input validation and restoration. They do not establish native game behavior.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from dolly.controller import ASPECT_CVAR
from dolly.navigation import CameraMotion
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT
import test_camera_position as camera_fixture
from test_controller import CountedEvent, FakeClock, make_project


class PausedCameraTests(unittest.TestCase):
    def setUp(self):
        camera_fixture.CameraPositionTests.setUp(self)
        self.console.pose = [240.1, 3816.2, 421.3, -10.5, 317.4]

    def prepare(self):
        with patch('dolly.controller.client_aspect_ratio', return_value=STANDARD_ASPECT):
            return self.controller.begin_paused_camera()

    def run_flight(self, source=None, waits=5):
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, waits)
        self.controller._state['paused_flight'] = True
        with patch('dolly.controller.time.monotonic', clock.monotonic):
            self.controller._run_paused_flight(source or (lambda: CameraMotion(up=1)), 240, 60, 60)
        return clock

    def assert_no_seek_resume(self):
        """Refreshing this exact tick is permitted; arrival-time seeking is not."""
        for command in self.console.operations:
            if command.startswith('demo_gototick '):
                self.assertEqual(int(command.split()[1]), self.console.tick, command)
            self.assertNotEqual(command, 'demo_resume')
            self.assertFalse(command.startswith('demo_goto '), command)

    def test_begin_keeps_current_tick_and_restores_automatic_aspect_on_stop(self):
        self.console.tick = self.console.goto_output = 3456
        original = list(self.console.pose)
        frame = self.prepare()
        self.assertEqual(self.console.tick, 3456)
        self.assertEqual([frame[k] for k in ('x', 'y', 'z', 'pitch', 'yaw')], original)
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assertEqual(self.controller.status()['paused_tick'], 3456)
        self.assertEqual(self.controller._restore[ASPECT_CVAR], 0)
        self.assertAlmostEqual(self.console.pose[2], original[2])
        self.controller.stop()
        self.assertEqual(self.console.values[ASPECT_CVAR], 0)
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIsNone(self.controller.status()['paused_tick'])
        self.assert_no_seek_resume()

    def test_switch_applies_pose_lens_and_dof_without_arrival_time_seek(self):
        project = make_project()
        project.tracks = [CvarTrack('r_citadel_depthoffield_focus_distance',
            keys=[TrackKey(0, 100), TrackKey(10, 900)])]
        original = deepcopy(project.to_dict())
        self.console.tick = self.console.goto_output = 9000
        self.controller.select_paused_camera(project, 10)
        self.assertEqual(self.console.tick, 9000)
        self.assertEqual(self.controller.status()['paused_tick'], 9000)
        self.assertEqual(self.console.values[ASPECT_CVAR], 1)
        self.assertEqual(self.console.values['r_citadel_depthoffield_focus_distance'], 900)
        self.assertEqual(project.to_dict(), original)
        for actual, wanted in zip(self.console.pose[:3], (100, 200, 300)):
            self.assertAlmostEqual(actual, wanted)
        self.controller.select_paused_camera(project, 0)
        self.assertEqual(self.console.tick, 9000)
        self.assertEqual(self.controller._restore[ASPECT_CVAR], 0)
        self.controller.stop()
        self.assertEqual(self.console.values['r_citadel_depthoffield_focus_distance'], 600)
        self.assert_no_seek_resume()

    def test_switch_to_a_single_saved_camera_keeps_current_replay_moment(self):
        project = Project(start_tick=1000, keyframes=[
            Keyframe(0, 12, 34, 560, -20, 300, 8, aspect_ratio=1.25)])
        self.console.tick = self.console.goto_output = 12345
        frame = self.controller.select_paused_camera(project, 0)
        self.assertEqual(self.console.tick, 12345)
        self.assertEqual(frame['z'], 560)
        self.assertEqual(frame['roll'], 8)
        self.assertEqual(self.console.values[ASPECT_CVAR], 1.25)
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assert_no_seek_resume()

    def test_upward_flight_changes_height_with_existing_offset_and_no_calibration(self):
        frame = self.prepare()
        calibration = deepcopy(self.controller._camera_calibration)
        self.console.requests.clear()
        self.run_flight()
        self.assertGreater(self.console.pose[2], frame['z'])
        self.assertAlmostEqual(self.console.pose[2], self.controller._paused_pose['z'])
        self.assertEqual(self.controller._camera_calibration, calibration)
        self.assertNotIn('spec_pos', self.console.requests)
        self.assertEqual(self.console.tick, 100)
        self.assertGreater(self.controller._paused_details['updates'], 0)
        self.assertFalse(self.controller.status()['playing'])
        self.assertFalse(self.controller.status()['paused_flight'])
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assert_no_seek_resume()

    def test_neutral_input_only_checks_status_without_camera_writes(self):
        self.prepare()
        self.console.requests.clear()
        self.run_flight(lambda: CameraMotion())
        self.assertEqual(self.console.camera_writes, [])
        self.assertEqual(self.controller._paused_details['updates'], 0)
        self.assertTrue(self.controller.status()['paused_camera'])

    def test_escape_stops_flight_and_keeps_current_view_prepared(self):
        pose = self.prepare()
        self.console.requests.clear()
        self.run_flight(lambda: CameraMotion(stop=True))
        self.assertEqual(self.console.requests, [])
        self.assertEqual(self.controller._paused_pose, pose)
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assertFalse(self.controller.status()['paused_flight'])
        self.assertIn('remain paused', self.controller.status()['message'])

    def test_external_tick_change_stops_before_any_future_camera_write(self):
        self.prepare()
        self.console.requests.clear()
        self.console.tick = self.console.goto_output = 101
        self.run_flight()
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIn('replay moved', self.controller.status()['message'])

    def test_tick_change_reported_after_write_allows_no_second_write(self):
        self.prepare()
        self.console.requests.clear()
        original_request = self.console.request
        def advancing_request(command, **kwargs):
            if command.startswith('spec_goto '):
                self.console.tick = self.console.goto_output = 101
            return original_request(command, **kwargs)
        with patch.object(self.console, 'request', side_effect=advancing_request):
            self.run_flight()
        self.assertEqual(len(self.console.camera_writes), 1)
        self.assertFalse(self.controller.status()['paused_camera'])

    def test_switched_demo_during_idle_stops_without_writing_to_other_replay(self):
        self.prepare()
        self.console.requests.clear()
        self.console.demo_name = 'another.dem'
        self.run_flight(lambda: CameraMotion())
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIn('different', self.controller.status()['message'])

    def test_capture_rebases_measured_pose_and_restarts_without_calibration(self):
        self.prepare()
        self.controller.nudge_paused_camera(CameraMotion(up=1))
        # Model a small native camera motion after manual controls stop.
        self.console.pose[2] += 7
        wanted = self.console.pose[2]
        captured = self.controller.capture(2)
        self.assertAlmostEqual(captured.z, wanted, places=1)
        self.assertEqual(self.controller._paused_pose['z'], captured.z)
        self.assertEqual(self.controller.status()['paused_tick'], 100)
        calibration = deepcopy(self.controller._camera_calibration)
        with patch('dolly.controller.threading.Thread') as worker:
            worker.return_value.is_alive.return_value = False
            self.controller.start_paused_flight(lambda: CameraMotion())
            self.assertTrue(self.controller.status()['paused_flight'])
            self.controller.stop_paused_flight()
        self.assertEqual(self.controller._camera_calibration, calibration)
        self.assertAlmostEqual(self.console.values[ASPECT_CVAR], STANDARD_ASPECT)
        self.assertTrue(self.controller.status()['paused_camera'])

    def test_capture_after_external_seek_invalidates_preparation(self):
        self.prepare()
        self.console.tick = self.console.goto_output = 101
        with self.assertRaisesRegex(RuntimeError, 'replay moved'):
            self.controller.capture(0)
        self.assertFalse(self.controller.status()['paused_camera'])

    def test_capture_at_replay_retains_fixed_tick_and_can_resume(self):
        self.prepare()
        captured = self.controller.capture_at_replay(90, 10)
        self.assertEqual(captured.time, 1)
        self.assertEqual(self.controller._paused_pose['time'], 1)
        with patch('dolly.controller.threading.Thread') as worker:
            worker.return_value.is_alive.return_value = False
            self.controller.start_paused_flight(lambda: CameraMotion())
            self.controller.stop_paused_flight()
        self.assertEqual(self.console.tick, 100)

    def _interrupt_blocked_flight(self, operation):
        """A pending console reply must finish before the next camera owner."""
        self.prepare()
        blocked = threading.Event()
        release = threading.Event()
        halting = threading.Event()
        completed = threading.Event()
        requests = []
        errors = []
        result = []
        original_request = self.console.request
        original_halt = self.controller._halt

        def delayed_request(command, **kwargs):
            owner = threading.current_thread().name
            if owner == 'DollyPausedCamera' and command.startswith('spec_goto ') and not blocked.is_set():
                blocked.set()
                if not release.wait(3):
                    raise RuntimeError('Test did not release the console reply')
            requests.append((owner, command))
            return original_request(command, **kwargs)

        def observed_halt():
            if threading.current_thread().name == 'PausedCameraAction':
                halting.set()
            return original_halt()

        def action():
            try:
                result.append(operation())
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        pending = None
        try:
            with patch.object(self.console, 'request', side_effect=delayed_request), \
                 patch.object(self.controller, '_halt', side_effect=observed_halt):
                self.controller.start_paused_flight(lambda: CameraMotion(up=1))
                self.assertTrue(blocked.wait(2), 'Flight did not issue a camera frame')
                pending = threading.Thread(target=action, name='PausedCameraAction', daemon=True)
                pending.start()
                self.assertTrue(halting.wait(2), 'Action did not begin halting flight')
                self.assertTrue(self.controller._stop_event.wait(2))
                self.assertFalse(completed.is_set(), 'Action raced ahead of the pending camera reply')
                release.set()
                self.assertTrue(completed.wait(2), 'Action deadlocked after the reply completed')
                pending.join(timeout=1)
                self.assertEqual(errors, [])
                first_action = next(i for i, (owner, _) in enumerate(requests)
                                    if owner == 'PausedCameraAction')
                self.assertFalse(any(owner == 'DollyPausedCamera' for owner, _ in requests[first_action:]),
                                 'Flight sent commands after ownership passed to the user action')
        finally:
            release.set()
            self.controller._halt()
            if pending:
                pending.join(timeout=2)
        return result[0]

    def test_capture_waits_for_inflight_camera_reply_then_rebases_without_deadlock(self):
        frame = self._interrupt_blocked_flight(lambda: self.controller.capture(0))
        self.assertEqual(self.controller._paused_pose['z'], frame.z)
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assertFalse(self.controller.status()['paused_flight'])
        # Restart directly from the capture, without re-running calibration.
        with patch.object(self.controller, '_position_frame') as calibration, \
             patch('dolly.controller.threading.Thread') as worker:
            worker.return_value.is_alive.return_value = False
            self.controller.start_paused_flight(lambda: CameraMotion())
            self.controller.stop_paused_flight()
            calibration.assert_not_called()

    def test_saved_camera_switch_waits_for_inflight_reply_before_repositioning(self):
        frame = self._interrupt_blocked_flight(
            lambda: self.controller.select_paused_camera(make_project(), 10))
        self.assertEqual(frame['z'], 300)
        self.assertAlmostEqual(self.console.pose[2], 300)
        self.assertEqual(self.console.tick, 100)
        self.assert_no_seek_resume()

    def test_stop_waits_for_inflight_reply_before_restoring_aspect(self):
        self._interrupt_blocked_flight(self.controller.stop)
        self.assertEqual(self.console.values[ASPECT_CVAR], 0)
        self.assertFalse(self.controller.status()['paused_camera'])

    def test_nudge_preserves_roll_lens_dof_and_moves_source_z(self):
        project = make_project()
        project.setup_values['r_citadel_depthoffield_focus_distance'] = 500
        self.controller.select_paused_camera(project, 10)
        before = deepcopy(self.controller._paused_pose)
        after = self.controller.nudge_paused_camera(CameraMotion(up=-1), seconds=.1)
        self.assertEqual(after['z'], before['z'] - 24)
        self.assertEqual(after['roll'], before['roll'])
        self.assertEqual(after['aspect_ratio'], before['aspect_ratio'])
        self.assertEqual(after['cvars'], before['cvars'])
        self.assertEqual(after['time'], before['time'])
        after['cvars']['r_citadel_depthoffield_focus_distance'] = 999
        self.assertEqual(self.controller._paused_pose['cvars']['r_citadel_depthoffield_focus_distance'], 500)
        self.assert_no_seek_resume()

    def test_start_requires_preparation_and_valid_inputs(self):
        with self.assertRaisesRegex(RuntimeError, 'Start Paused camera'):
            self.controller.start_paused_flight(lambda: CameraMotion())
        for kwargs in ({'input_source': None}, {'input_source': lambda: CameraMotion(), 'rate': 0},
                       {'input_source': lambda: CameraMotion(), 'move_speed': float('nan')},
                       {'input_source': lambda: CameraMotion(), 'turn_speed': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.controller.start_paused_flight(**kwargs)
        self.assertEqual(self.console.camera_writes, [])

    def test_bad_input_source_stops_worker_with_diagnostic(self):
        self.prepare()
        self.console.requests.clear()
        self.run_flight(lambda: {'forward': 1})
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIn('CameraMotion', self.controller._paused_details['error'])

    def test_begin_rejects_missing_tick_before_camera_writes(self):
        self.console.goto_output = ''
        self.console.demo_output = "Demo contents for example.dem:\nDemoFileInfo: playback_ticks: 161418\n"
        with self.assertRaises((RuntimeError, ValueError)):
            self.prepare()
        self.assertEqual(self.console.camera_writes, [])

    def test_begin_rejects_unsupported_camera_before_camera_writes(self):
        self.controller._probe_result = {}
        with self.assertRaisesRegex(RuntimeError, 'Check camera support'):
            self.prepare()
        self.assertEqual(self.console.camera_writes, [])

    def test_cancelled_panel_prevents_preparation_before_any_console_request(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.controller.begin_paused_camera(cancelled=cancel.is_set)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.controller.select_paused_camera(make_project(), 0, cancelled=cancel.is_set)
        self.assertEqual(self.console.requests, [])
        self.assertIsNone(self.controller._paused_cancelled)

    def test_panel_cancel_during_calibration_prevents_further_camera_writes(self):
        cancel = threading.Event()
        original_request = self.console.request
        def cancelling_request(command, **kwargs):
            output = original_request(command, **kwargs)
            if command.startswith('spec_goto '):
                cancel.set()
            return output
        with patch.object(self.console, 'request', side_effect=cancelling_request):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.controller.select_paused_camera(make_project(), 0, cancelled=cancel.is_set)
        self.assertEqual(len(self.console.camera_writes), 1)
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIsNone(self.controller._paused_cancelled)
        self.assertEqual(self.controller._restore[ASPECT_CVAR], 0)
        # The closed panel's token must not cancel a subsequent normal preview.
        self.controller.apply(make_project(), 0)
        self.assertTrue(self.controller._camera_calibration['verified'])

    def test_panel_cancel_after_calibration_prevents_final_pose_commit(self):
        cancel = threading.Event()
        original_request = self.console.request
        def cancelling_status(command, **kwargs):
            output = original_request(command, **kwargs)
            if command == 'demo_goto' and self.controller._camera_calibration.get('verified'):
                cancel.set()
            return output
        with patch.object(self.console, 'request', side_effect=cancelling_status), \
             patch('dolly.controller.client_aspect_ratio', return_value=STANDARD_ASPECT):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.controller.begin_paused_camera(cancelled=cancel.is_set)
        self.assertTrue(cancel.is_set())
        self.assertFalse(self.controller._camera_calibration['verified'])
        self.assertIn('cancelled', self.controller._camera_calibration['error'])
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIsNone(self.controller._paused_pose)
        self.assertIsNone(self.controller._paused_cancelled)

    def test_stop_restoration_failure_keeps_original_value_for_retry(self):
        self.prepare()
        self.console.fail_commands.add('r_aspectratio 0')
        self.controller.stop()
        self.assertEqual(self.controller._restore[ASPECT_CVAR], 0)
        self.assertFalse(self.controller.status()['paused_camera'])
        self.console.fail_commands.clear()
        self.controller.stop()
        self.assertEqual(self.console.values[ASPECT_CVAR], 0)
        self.assertEqual(self.controller._restore, {})

    def test_play_seek_disconnect_invalidate_manual_preparation(self):
        for operation in ('seek', 'play', 'disconnect'):
            with self.subTest(operation=operation):
                self.setUp()
                self.prepare()
                with patch('dolly.controller.threading.Thread') as worker:
                    worker.return_value.is_alive.return_value = False
                    if operation == 'disconnect':
                        self.controller.disconnect()
                    else:
                        getattr(self.controller, operation)(make_project(), 0)
                self.assertFalse(self.controller.status()['paused_camera'])
                self.assertFalse(self.controller.status()['paused_flight'])
                self.assertIsNone(self.controller._paused_pose)

    def test_begin_after_stop_does_not_reapply_old_dof(self):
        project = make_project()
        project.setup_values['r_citadel_depthoffield_focus_distance'] = 500
        self.controller.select_paused_camera(project, 0)
        self.controller.stop()
        self.assertEqual(self.console.values['r_citadel_depthoffield_focus_distance'], 600)
        self.prepare()
        self.assertEqual(self.console.values['r_citadel_depthoffield_focus_distance'], 600)

    def test_manual_operations_do_not_change_hud_or_demo_speed(self):
        self.console.values['citadel_hud_visible'] = 1
        self.console.values['demo_timescale'] = .5
        self.prepare()
        self.controller.nudge_paused_camera(CameraMotion(right=1))
        self.controller.stop_paused_flight()
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['demo_timescale'], .5)
        self.assertFalse(any('citadel_hud_visible ' in c for c in self.console.requests))
        self.assert_no_seek_resume()

    def test_diagnostics_record_last_manual_pose_and_bounded_metrics(self):
        self.prepare()
        self.run_flight()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'paused.zip'
            self.controller.export_diagnostics(destination)
            with zipfile.ZipFile(destination) as archive:
                report = json.loads(archive.read('diagnostics.json'))
        manual = report['paused_camera']
        self.assertEqual(manual['tick'], 100)
        self.assertEqual(manual['pose']['z'], self.controller._paused_pose['z'])
        self.assertGreater(manual['updates'], 0)
        self.assertFalse(manual['runtime_verified_in_Deadlock'])
        self.assertLess(len(json.dumps(manual)), 30000)
        self.assertTrue(manual['startup_calibration']['direct_response_verified'])


if __name__ == '__main__':
    unittest.main()
