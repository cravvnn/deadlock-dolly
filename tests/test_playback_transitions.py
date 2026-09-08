"""Resume/seek regressions model the measured weak paused-camera response.

Console simulations verify state transitions and bounded writes. They cannot
prove a particular Deadlock build resets its spectator interpolation on seek.
"""

from collections import deque
from copy import deepcopy
import unittest
import threading
from unittest.mock import MagicMock, patch

import test_camera_position as camera_fixture
from test_controller import CountedEvent, FakeClock, make_project
from dolly.navigation import CameraMotion
from dolly.controller import parse_camera_readback


class PausedNativeConsole(camera_fixture.OffsetConsole):
    def __init__(self):
        super().__init__(offset=(0, 0, 90))
        self.pose = [859, 4922, 813, -10, 317]
        self.anchor = list(self.pose)
        self.native_view = list(self.pose)
        self.weak = True
        self.refresh_recovers = True
        self.freeze_view = False
        self.gain = .19375

    def _execute(self, command):
        previous = list(self.pose)
        super()._execute(command)
        if command.startswith('demo_gototick ') and self.refresh_recovers:
            self.weak = False
            # A real seek can reset the free camera. Dolly must restore the
            # visible pose captured before the refresh, never this new pose.
            self.pose = [0, 0, 200, 0, 0]
        if command.startswith('spec_goto '):
            self.native_view = list(self.pose)
            if self.weak:
                self.pose[:3] = [self.anchor[a] + self.gain * (self.pose[a] - self.anchor[a])
                                 for a in range(3)]
            if self.freeze_view:
                self.pose = previous
        if command == 'demo_resume':
            # Native Resume reveals the underlying origin in the regression.
            self.pose = list(self.native_view)


class PlaybackTransitionTests(unittest.TestCase):
    def setUp(self):
        camera_fixture.CameraPositionTests.setUp(self)
        self.console = PausedNativeConsole()
        self.controller._console = self.console
        interval = patch('dolly.controller.SEEK_SETTLE_INTERVAL', 0)
        interval.start()
        self.addCleanup(interval.stop)

    def test_current_visible_pose_survives_refresh_and_native_resume_at_slow_speed(self):
        self.console.values['demo_timescale'] = .1
        wanted = list(self.console.pose)
        # A previous weak-response estimate must not seed the new correction.
        self.controller._camera_offset = {'x': -38.1, 'y': -6.5, 'z': 89.9}
        frame = self.controller.begin_paused_camera()
        self.assertEqual([frame[a] for a in ('x', 'y', 'z', 'pitch', 'yaw')], wanted)
        self.assertEqual(self.console.tick, 100)
        self.assertIn('demo_gototick 100 0 1', self.console.operations)
        self.assertNotIn('demo_resume', self.console.operations)
        self.assertEqual(self.controller._camera_offset, {'x': 0, 'y': 0, 'z': 90})
        calibration = self.controller._camera_calibration
        self.assertTrue(calibration['direct_response_verified'])
        self.assertEqual(calibration['translation_response']['gain'], {'x': 1, 'y': 1, 'z': 1})
        self.controller.nudge_paused_camera(CameraMotion(up=-1), .1)
        before_resume = list(self.console.pose)
        self.controller.stop_paused_flight()
        self.console.request('demo_resume')
        self.assertEqual(self.console.pose, before_resume)
        self.assertEqual(self.console.values['demo_timescale'], .1)

    def test_switch_refreshes_current_tick_without_seeking_authored_arrival(self):
        self.console.tick = self.console.goto_output = 112697
        project = make_project()
        saved = project.to_dict()
        self.controller.select_paused_camera(project, 10)
        seeks = [c for c in self.console.operations if c.startswith('demo_gototick ')]
        self.assertEqual(seeks, ['demo_gototick 112696 0 1', 'demo_gototick 112697 0 1'])
        self.assertEqual(project.to_dict(), saved)
        self.assertEqual(self.controller._paused_pose['time'], 10)
        self.assertEqual(self.console.pose[:3], [100, 200, 300])

    def test_nonrecovering_weak_translation_cannot_start_manual_controls(self):
        self.console.refresh_recovers = False
        self.console.offset = (0, 0, 0)
        with self.assertRaisesRegex(RuntimeError, 'only part'):
            self.controller.begin_paused_camera()
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIsNone(self.controller._paused_pose)
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertFalse(self.controller._camera_calibration['verified'])
        gains = self.controller._camera_calibration['translation_response']['gain']
        self.assertTrue(all(.18 < gain < .21 for gain in gains.values()))
        self.assertLess(len(self.console.camera_writes), 10)

    def test_seek_waits_for_exact_target_before_and_after_pause(self):
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 100)
        target = 112697
        # The first neighbour was the observed false-start error. Also model
        # one transient neighbour after Dolly reasserts pause.
        self.console.goto_outputs = deque([target + 1, target, target, target,
                                            target + 1, target, target, target])
        with patch('dolly.controller.time.perf_counter', clock.monotonic), \
             patch('dolly.controller.SEEK_SETTLE_INTERVAL', .04):
            info = self.controller._seek_tick(target)
        self.assertEqual(info['tick'], target)
        self.assertAlmostEqual(clock.now, .28)
        observed = self.controller._last_seek_details['samples']
        self.assertEqual([p['tick'] for p in observed[-3:]], [target] * 3)
        self.assertTrue(self.controller._last_seek_details['verified'])

    def test_cancel_during_seek_prevents_camera_write(self):
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 2)
        with patch('dolly.controller.time.perf_counter', clock.monotonic), \
             patch('dolly.controller.SEEK_SETTLE_INTERVAL', .04):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.controller._seek_tick(123)
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(self.controller._last_seek_details['verified'])

    def test_same_tick_refresh_rejects_a_permanent_neighbour(self):
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 1000)
        self.console.goto_outputs = deque([124] * 500)
        with patch('dolly.controller.time.perf_counter', clock.monotonic), \
             patch('dolly.controller.SEEK_SETTLE_INTERVAL', .04):
            with self.assertRaisesRegex(RuntimeError, 'did not reach tick 123'):
                self.controller._seek_tick(123)
        self.assertEqual(self.console.camera_writes, [])
        self.assertLessEqual(len(self.controller._last_seek_details['samples']), 32)

    def test_sustained_manual_drift_stops_accumulation_and_capture_reads_visible_pose(self):
        initial = self.controller.begin_paused_camera()
        self.console.freeze_view = True
        self.console.requests.clear()
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 200)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._reset_motion_observations(initial)
            self.controller._run_paused_flight(lambda: CameraMotion(up=-1, boost=True), 240, 60, 60)
        self.assertLess(clock.now, 2)
        self.assertFalse(self.controller.status()['paused_camera'])
        self.assertIn('visible camera stopped', self.controller._paused_details['error'])
        self.assertEqual(self.controller._applied_pose['z'], initial['z'])
        self.assertLess(len(self.console.camera_writes), 100)
        captured = self.controller.capture(0)
        self.assertEqual(captured.z, initial['z'])
        readbacks = [s for s in self.controller._paused_samples if 'observed_pose' in s]
        self.assertGreaterEqual(len(readbacks), 3)
        self.assertEqual(readbacks[-1]['consecutive_large_drift'], 3)

    def test_one_frame_late_render_readbacks_do_not_stop_fast_flight(self):
        initial = self.controller.begin_paused_camera()
        self.console.delay_reads = 1
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 100)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._reset_motion_observations(initial)
            self.controller._run_paused_flight(lambda: CameraMotion(forward=1), 10000, 60, 60)
        self.assertTrue(self.controller.status()['paused_camera'])
        self.assertNotIn('error', self.controller._paused_details)
        self.assertGreater(self.controller._paused_details['updates'], 90)
        self.assertTrue(all(s.get('visible_distance_from_recent_path', 0) < 1
                            for s in self.controller._paused_samples))

    def test_exact_final_key_is_applied_even_when_clock_lags_acknowledged_end(self):
        project = make_project()
        self.console.weak = False
        self.console.offset = (0, 0, 0)
        self.console.goto_outputs = deque([100, 200, 200])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 20)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._run(project, 0, .1, 60, False)
        self.assertEqual(self.console.pose[:3], [100, 200, 300])
        self.assertEqual(self.console.values['r_aspectratio'], 1)
        self.assertEqual(self.controller.status()['time'], project.duration)
        self.assertEqual(self.controller._playback_samples[-1]['time'], project.duration)
        self.assertTrue(self.console.paused)

    def test_playback_trace_is_bounded_and_keeps_startup_calibration_after_preview(self):
        project = make_project()
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch('dolly.controller.threading.Thread', return_value=worker):
            self.controller.play(project, speed=.1)
        startup = deepcopy(self.controller._playback_details['startup_calibration'])
        self.console.requests.clear()
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 400)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._reset_motion_observations(project.evaluate(0))
            self.controller._run(project, 0, .1, 60, False)
        self.assertEqual(len(self.controller._playback_samples), 256)
        self.assertTrue(any('observed_pose' in s for s in self.controller._playback_samples))
        self.controller._stop_event = threading.Event()
        self.controller.apply(project, 10)
        self.assertEqual(self.controller._playback_details['startup_calibration'], startup)
        self.assertNotEqual(self.controller._camera_calibration['frame'], startup['frame'])

    def test_readback_uses_printed_visible_pose_and_rejects_echo_only_output(self):
        echo = 'spec_goto 1 2 3 4 5; cl_citadel_forceangles 4 5 0; spec_pos; demo_goto'
        printed = '[Console] spec_goto 20 30 40 4 5'
        pose = parse_camera_readback(echo + '\n' + printed + '\n> ' + echo,
                                     roll=0, aspect_ratio=1)
        self.assertEqual((pose.x, pose.y, pose.z), (20, 30, 40))
        with self.assertRaisesRegex(ValueError, 'printed spec_pos'):
            parse_camera_readback(echo, roll=0, aspect_ratio=1)

    def test_external_large_forward_seek_stops_before_endpoint_camera_write(self):
        project = make_project()
        self.console.weak = False
        self.console.offset = (0, 0, 0)
        self.console.goto_outputs = deque([100, 10000, 10000])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 20)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._run(project, 0, .1, 60, False)
        self.assertEqual(len(self.console.camera_writes), 1)
        self.assertEqual(self.console.pose[:3], [0, 0, 200])
        self.assertIn('jumped forward', self.controller.status()['message'])
        self.assertTrue(self.console.paused)

    def test_seek_between_resume_and_first_worker_status_cannot_jump_to_endpoint(self):
        worker = MagicMock()
        worker.is_alive.return_value = False
        project = make_project()
        with patch('dolly.controller.threading.Thread', return_value=worker):
            self.controller.play(project, speed=.1)
        self.console.tick = self.console.goto_output = 10000
        self.console.requests.clear()
        self.controller._run(project, 0, .1, 60, False)
        self.assertEqual(self.console.camera_writes, [])
        self.assertIn('jumped forward', self.controller.status()['message'])
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)

    def test_frozen_tick_change_before_first_worker_status_stops_before_camera_write(self):
        worker = MagicMock()
        worker.is_alive.return_value = False
        project = make_project()
        with patch('dolly.controller.threading.Thread', return_value=worker):
            self.controller.play(project, frozen=True)
        self.console.tick = self.console.goto_output = 101
        self.console.requests.clear()
        self.controller._run(project, 0, 1, 60, True)
        self.assertEqual(self.console.camera_writes, [])
        self.assertIn('scene resumed', self.controller.status()['message'])

    def test_playback_drift_stops_writes_and_restores_hidden_hud(self):
        project = make_project()
        project.keyframes[-1].time = 100
        project.keyframes[-1].x = 10000
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch('dolly.controller.threading.Thread', return_value=worker):
            self.controller.play(project, speed=1)
        self.console.freeze_view = True
        self.console.requests.clear()
        self.console.goto_outputs = deque(range(100, 600))
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, 500)
        with patch('dolly.controller.time.perf_counter', clock.monotonic):
            self.controller._reset_motion_observations(project.evaluate(0))
            self.controller._run(project, 0, 1, 60, False)
        self.assertIn('visible camera stopped', self.controller.status()['message'])
        self.assertLess(len(self.console.camera_writes), 200)
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 0)
        self.assertEqual(self.controller._playback_samples[-1]['consecutive_large_drift'], 3)


if __name__ == '__main__':
    unittest.main()
