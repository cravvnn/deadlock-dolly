"""Native controller workflows with simulated game/bridge and virtual time.

No game, GPU, native DLL, encoder, or GUI is launched. Real controller and
filesystem logic operate against owned process/console/bridge boundaries.
"""
from contextlib import ExitStack
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import player_layer
from dolly.console import ConsoleTimeout
from dolly.controller import Controller
from dolly.path import CvarTrack, Keyframe, Project, TrackKey
from dolly.playback import ReplayClock
from dolly.video_export import VideoExport, VideoOptions
from dolly.ui.shot_commands import finish_capture
from native_helpers import FakeConsole, Clock, Bridge, CountedEvent


def shot():
    return Project(start_tick=100, tick_rate=64, interpolation='linear', keyframes=[
        Keyframe(0, 0, 0, 100, 0, 350, 0),
        Keyframe(1, 80, 40, 120, 25, 380, 10, aspect_ratio=1)])


class NativeWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.messages = []
        self.controller = Controller(self.messages.append)
        self.clock = Clock()
        self.clock.now = 0
        self.controller._stop_event = CountedEvent(self.clock)
        self.console = FakeConsole()
        self.bridge = Bridge(self.console, self.clock)
        self.bridge.configure_editor = Mock()
        self.bridge.video_status = Mock(return_value={'state': 'idle'})
        self.process = SimpleNamespace(poll=lambda: None)
        self.session = SimpleNamespace(process=self.process, pid=1234, native=self.bridge,
            session_dir=self.root/'session', overlay_dir=self.root/'overlay',
            close=Mock(), restore_gameinfo=Mock(), owns_console_port=lambda: True)
        self.controller._console = self.console
        self.controller._session = self.session
        self.controller._demo = self.root/'example.dem'
        self.controller._demo.write_bytes(b'replay')
        self.controller._unlocker_pid = 1234
        self.controller._probe_result = {'capabilities': {'spec_goto': True, 'cl_citadel_forceangles': True,
                                                          'r_aspectratio': True}}
        self.project = shot()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('dolly.controller.time.perf_counter', lambda: self.clock.now))
        self.stack.enter_context(patch('dolly.controller.time.sleep', self.clock.advance))
        self.stack.enter_context(patch('dolly.controller.threading.Event',
                                       lambda: SimpleNamespace(wait=self.clock.advance)))
        self.stack.enter_context(patch('dolly.controller.FrameWait', lambda: SimpleNamespace(
            wait=lambda delay, event: self.clock.advance(delay), close=lambda: None)))
        self.worker = Mock()
        self.worker.is_alive.return_value = False
        self.stack.enter_context(patch('dolly.controller.threading.Thread', return_value=self.worker))

    def fresh(self):
        other = NativeWorkflows()
        other.setUp()
        self.addCleanup(other.doCleanups)
        return other

    def prepared_recording(self):
        # Simulate the read-only process-memory monitor and game reload; run
        # controller recovery, seek and native flight preparation for real.
        self.console.complete_replay_on_send = True
        self.controller._replay_camera_monitor = SimpleNamespace(
            memory=object(), identity={'fixture': True},
            sample_startup=lambda: {'coherent': True, 'ready': True}, close=lambda: None)
        self.controller._replay_camera_session = self.session
        self.controller.prepare_native_recording(self.project)
        self.assertEqual(self.controller._recording_replay,
                         (self.session, str(self.controller._demo), 100))
        self.assertEqual(self.console.tick, 100)
        self.assertTrue(self.console.paused)
        self.assertLess(self.bridge.events.index('disconnect'), self.bridge.events.index('native.start_flight'))
        self.assertEqual(sum(command.startswith('playdemo ') for command in self.console.sent), 1)
        self.assertEqual(self.bridge.state, 'armed')
        self.controller.mark_recording_pending(True)
        self.bridge.video_status.return_value = {'state': 'recording'}

    def start(self, frozen=True):
        self.controller.play(self.project, speed=.1, rate=120, frozen=frozen)
        self.controller._thread = None

    def run_shot(self, frozen=True):
        self.controller._run_native(self.project, 0, .1, 120, frozen)

    def test_p05_native_playback_lifecycle_and_watchdog(self):
        self.prepared_recording()
        self.start(frozen=False)
        events = self.bridge.events
        self.assertLess(events.index('native.prepare'), events.index('native.play'))
        self.assertLess(events.index('native.play'), next(i for i, e in enumerate(events) if 'demo_resume' in e))
        self.run_shot(frozen=False)
        active = [c for state, c in self.bridge.requests if state == 'playing']
        self.assertTrue(active)
        for c in active:
            self.assertNotIn('spec_goto ', c)
            self.assertNotIn('r_aspectratio ', c)
            self.assertNotIn('cl_citadel_forceangles ', c)
        self.assertIn('Native shot finished', self.controller.status()['message'])
        self.controller.mark_recording_pending(False)
        self.bridge.video_status.return_value = {'state': 'idle'}
        self.controller.stop()
        self.assertTrue(self.controller._native_handoff_details['verified'])
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['demo_timescale'], 1)
        self.assertEqual(self.bridge.state, 'stopped')
        for reason in ('different', 'missing', 'dead'):
            with self.subTest(reason=reason):
                case = self.fresh()
                if reason == 'different': case.console.demo_name = 'different.dem'
                elif reason == 'missing': case.console.demo_output = 'Error - Not currently playing back a demo.'
                else: case.process.poll = lambda: 0
                with self.assertRaises((RuntimeError, ValueError)): case.start()
                self.assertFalse(any('spec_goto ' in c or 'demo_resume' in c for c in case.console.requests))
                self.assertNotIn('native.prepare', case.bridge.events)
        case = self.fresh()
        case.start()
        case.bridge.fail_handoff = True
        case.run_shot()
        self.assertTrue(case.controller._native_active)
        self.assertIn('final camera held', case.controller.status()['message'])
        before = len(case.bridge.events)
        with patch.object(case.bridge, 'release', side_effect=RuntimeError('release timed out')):
            with self.assertRaisesRegex(RuntimeError, 'release timed out'): case.start()
        self.assertEqual(case.bridge.events[before:], [])
        case.bridge.fail_handoff = False
        case.controller.stop()
        self.assertFalse(case.controller._native_active)
        for kind in ('rewind', 'replay change'):
            with self.subTest(kind=kind):
                case = self.fresh()
                case.start()
                if kind == 'rewind': case.bridge.rewind = True
                else: case.bridge.change_demo = True
                with self.assertLogs('dolly', level='ERROR'): case.run_shot()
                self.assertIsNotNone(case.controller.status()['playback_error'])
                self.assertFalse(case.controller.status()['playing'])
        # An owned pause samples the actual replay clock on three fresh
        # rendered views; resume then continues the same native shot.
        case = self.fresh()
        case.prepared_recording()
        case.start(frozen=False)
        case.controller._thread = case.worker
        case.worker.is_alive.return_value = True
        get_status = case.bridge.status
        transition = {'samples': 0, 'nested': False}
        def pause_then_resume():
            if transition['nested']:
                result = get_status()
                result.update(phase=.25, tick=116, state='playing')
                case.bridge.phase = .25
                case.console.tick = 116
                return result
            transition['samples'] += 1
            if transition['samples'] in (3, 5):
                case.console.tick = 116
                transition['nested'] = True
                try:
                    case.controller.toggle_replay()
                finally:
                    transition['nested'] = False
            result = get_status()
            if transition['samples'] in (2, 3, 4):
                result.update(phase=.25, tick=116, state='playing')
                case.bridge.phase = .25
                case.console.tick = 116
            return result
        with patch.object(case.bridge, 'status', side_effect=pause_then_resume):
            case.run_shot(frozen=False)
        self.assertIsNone(case.controller.status()['playback_error'])
        self.assertEqual(case.controller.status()['time'], 1)
        check = case.controller._playback_details['pause_clock_checks'][0]
        self.assertEqual((check['source'], check['before_replay_tick'], check['replay_tick']),
                         ('owned_pause', 116, 116))
        self.assertEqual(check['post_pause_ticks'], [116, 116, 116])
        self.assertTrue(all(b > a for a, b in zip(check['post_pause_frames'], check['post_pause_frames'][1:])))
        self.assertTrue(any(item['paused'] for item in case.controller._playback_samples))
        self.assertFalse(case.controller._playback_samples[-1]['paused'])
        # The callback owns the animated effect, including its endpoint. It
        # remains held until Stop restores the exact original engine value.
        case = self.fresh()
        case.project.tracks = [CvarTrack('r_citadel_depthoffield_focus_distance',
                                         [TrackKey(0, 600), TrackKey(1, 1200)])]
        case.start()
        case.run_shot()
        self.assertIsNone(case.controller.status()['playback_error'])
        self.assertEqual(case.console.values['r_citadel_depthoffield_focus_distance'], 1200)
        self.assertTrue(case.controller._native_active)
        case.controller.stop()
        self.assertEqual(case.console.values['r_citadel_depthoffield_focus_distance'], 600)
        # Distinct callback failures must stop the shot before claiming a
        # synchronized final take; each is injected only at the bridge.
        faults = (('native identity', 'Native camera replay identity changed'),
                  ('interrupted', 'Native playback was interrupted'),
                  ('counter reset', 'callback restarted'),
                  ('effect count', 'effect count'),
                  ('effect error', 'did not acknowledge'),
                  ('effect phase', 'did not acknowledge'))
        for fault, message in faults:
            with self.subTest(native_fault=fault):
                case = self.fresh()
                if fault.startswith('effect'):
                    case.project.tracks = [CvarTrack('r_citadel_depthoffield_focus_distance',
                                                     [TrackKey(0, 600), TrackKey(1, 1200)])]
                case.start()
                get_status = case.bridge.status
                samples = []
                def corrupt():
                    result = get_status()
                    if result['state'] == 'playing':
                        samples.append(result['frame_count'])
                        if fault == 'native identity': result['demo_name'] = 'other-native.dem'
                        elif fault == 'interrupted': result['state'] = 'armed'
                        elif fault == 'counter reset' and len(samples) > 1: result['frame_count'] = 0
                        elif fault == 'effect count': result['effect_count'] = 0
                        elif fault == 'effect error': result['effect_error'] = 1
                        elif fault == 'effect phase': result['effect_phase'] += .1
                    return result
                with patch.object(case.bridge, 'status', side_effect=corrupt), self.assertLogs('dolly', level='ERROR'):
                    case.run_shot()
                self.assertIn(message, case.controller.status()['playback_error'])
                self.assertFalse(case.controller.status()['playing'])
                self.assertLess(case.controller.status()['time'], 1)
        for seconds, expect_failure in ((4, False), (20, True)):
            with self.subTest(hitch_seconds=seconds):
                case = self.fresh()
                case.start()
                case.controller._export_timing = {'fps': 300, 'speed': 1}
                status = case.bridge.status
                began = case.clock.now
                def render():
                    case.bridge.freeze_frames = case.clock.now - began < seconds
                    return status()
                if expect_failure:
                    with patch.object(case.bridge, 'status', side_effect=render), self.assertLogs('dolly', level='ERROR'): case.run_shot()
                else:
                    with patch.object(case.bridge, 'status', side_effect=render): case.run_shot()
                error = case.controller.status()['playback_error']
                if expect_failure:
                    self.assertIn('stopped receiving rendered views', error)
                    self.assertGreaterEqual(case.clock.now-began, 15)
                else:
                    self.assertIsNone(error)
                    self.assertEqual(case.controller.status()['time'], 1)
                    self.assertGreater(case.clock.now-began, 4)

    def test_p06_players_backpressure_and_safe_drain(self):
        self.controller._state['startup_stage'] = 'editing_ready'
        self.console.values.update(host_framerate=90, r_wait_on_present=0, r_aspectratio=1.5)
        directory = self.controller.deployment_directory()
        directory.mkdir(parents=True)
        export = VideoExport(self.controller)
        export.start(VideoOptions(self.root/'players.mp4', fixed_step=True), frozen=True,
                     capture_only=True, before_record=lambda: player_layer.begin_capture(directory, 48, 120))
        request = player_layer.capture_request_id(directory)
        pacing_file = directory / player_layer.PACING_NAME
        def publish(**changes):
            values = dict(format=1, request=request, pid=1234, heartbeat=100000,
                          progress_ms=99500, stage='storage', written=2, sealed=4,
                          cap=120, bytes=33177600, progress=12, error=0)
            values.update(changes)
            pacing_file.write_text(json.dumps(values))
        self.start()
        original_request = self.console.request
        original_status = self.bridge.status
        misses = []
        def timeout_then_progress(command, **kwargs):
            if command == 'demo_goto' and len(misses) < 2:
                misses.append(True)
                self.clock.advance(3)
                now = int(self.clock.now * 1000) + 100000
                publish(heartbeat=now, progress_ms=now, progress=12 + len(misses))
                raise ConsoleTimeout('game busy draining capture')
            return original_request(command, **kwargs)
        def status():
            self.clock.advance(.6)
            result = original_status()
            publish(stage='idle')
            self.controller._player_pacing['next_read'] = 0
            return result
        with patch.object(self.console, 'request', side_effect=timeout_then_progress), \
             patch.object(self.bridge, 'status', side_effect=status):
            self.run_shot()
        self.assertEqual(len(misses), 2)
        self.assertIsNone(self.controller.status()['playback_error'])
        self.assertEqual(self.controller.status()['time'], 1)
        self.assertTrue(self.controller._player_capture_pending)
        self.assertTrue(self.controller._native_active)
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        # Ownership, heartbeat and actual progress each independently gate
        # backpressure; malformed or foreign reports do not suspend guards.
        for changes in (dict(pid=9999), dict(request='f'*32), dict(error=1), dict(stage='idle'), dict(stage='complete')):
            with self.subTest(report=changes):
                self.controller._player_pacing.update(heartbeat=None, progress=None, next_read=0)
                publish(heartbeat=100000, progress_ms=99500, progress=12)
                self.assertTrue(self.controller._player_capture_pacing_active(1))
                # Only the field under test differs from the accepted report;
                # advance heartbeat and progress so freshness cannot mask it.
                publish(heartbeat=100001, progress_ms=100001, progress=13, **changes)
                self.controller._player_pacing['next_read'] = 0
                self.assertFalse(self.controller._player_capture_pacing_active(1.2))
        self.controller._player_pacing.update(heartbeat=None, progress=None, next_read=0)
        publish(heartbeat=132000, progress_ms=132000, progress=100)
        self.assertTrue(self.controller._player_capture_pacing_active(2))
        publish(heartbeat=131000, progress_ms=131000, progress=101)
        self.controller._player_pacing['next_read'] = 0
        self.assertFalse(self.controller._player_capture_pacing_active(3))
        # Fresh reports with unchanged progress cannot extend the 30-second
        # bound; a stale heartbeat alone also expires after 1.5 seconds.
        self.controller._player_pacing.update(heartbeat=None, progress=None, next_read=0)
        publish(heartbeat=200000, progress_ms=200000, progress=100)
        self.assertTrue(self.controller._player_capture_pacing_active(2))
        self.controller._player_pacing['next_read'] = 0
        self.assertFalse(self.controller._player_capture_pacing_active(3.6))
        publish(heartbeat=200001, progress_ms=200001, progress=100)
        self.controller._player_pacing['next_read'] = 0
        self.assertFalse(self.controller._player_capture_pacing_active(33))
        # The exact owned request receives Stop before any restoration. No
        # response retains camera, HUD, timing and reservation for a retry.
        before = len(self.bridge.events)
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='armed'):
            with self.assertRaisesRegex(RuntimeError, 'not acknowledged stop'): export.stop(cancel=True)
        self.assertTrue((directory / player_layer.STOP_NAME).is_file())
        self.assertTrue(export.finish_pending)
        self.assertTrue(self.controller._player_capture_pending)
        self.assertTrue(self.controller._native_active)
        self.assertEqual(self.console.values['host_framerate'], 60)
        self.assertFalse(any('demo_pause' in event or 'spec_goto' in event for event in self.bridge.events[before:]))
        old_demo = self.controller._demo
        self.controller._demo = self.root/'other.dem'
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            export.stop()
        self.assertTrue(self.controller._player_capture_pending)
        self.assertEqual(self.console.values['host_framerate'], 60)
        self.controller._demo = old_demo
        self.controller._session = SimpleNamespace(**vars(self.session))
        with self.assertRaisesRegex(RuntimeError, 'changed'): export.stop()
        self.assertTrue(self.controller._player_capture_pending)
        self.assertEqual(self.console.values['host_framerate'], 60)
        self.controller._session = self.session
        drain_request = self.console.request
        drain_misses = []
        def delayed_restore_query(command, **kwargs):
            if command == 'demo_goto' and len(drain_misses) < 2:
                drain_misses.append(True)
                self.assertTrue((directory / player_layer.STOP_NAME).is_file())
                self.assertTrue(self.controller._player_capture_pending)
                self.assertEqual(self.console.values['host_framerate'], 60)
                raise ConsoleTimeout('capture stopped; console still busy')
            return drain_request(command, **kwargs)
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='complete'), \
             patch.object(self.console, 'request', side_effect=delayed_restore_query):
            self.assertEqual(export.stop()['state'], 'cancelled')
        self.assertEqual(len(drain_misses), 2)
        self.assertFalse(self.controller._player_capture_pending)
        self.assertFalse(self.controller._native_active)
        self.assertEqual(self.console.values['host_framerate'], 90)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        # A foreign progress producer cannot excuse repeated console timeouts.
        case = self.fresh()
        case.start()
        directory = case.controller.deployment_directory()
        directory.mkdir(parents=True)
        player_layer.begin_capture(directory, 48, 120)
        request = player_layer.capture_request_id(directory)
        case.controller.mark_recording_pending(True, player_layer=True)
        case.controller._export_timing = {'fps':60, 'speed':1}
        (directory/player_layer.PACING_NAME).write_text(json.dumps(dict(
            format=1, request=request, pid=9999, heartbeat=100000, progress_ms=99500,
            stage='storage', written=2, sealed=4, cap=120, bytes=100, progress=12, error=0)))
        request_console = case.console.request
        def timeout(command, **kwargs):
            if command == 'demo_goto':
                case.clock.advance(3)
                raise ConsoleTimeout('unverified wait')
            return request_console(command, **kwargs)
        get_status = case.bridge.status
        def elapsed_status():
            case.clock.advance(.6)
            return get_status()
        with patch.object(case.console, 'request', side_effect=timeout), \
             patch.object(case.bridge, 'status', side_effect=elapsed_status), \
             self.assertLogs('dolly', level='ERROR'):
            case.run_shot()
        self.assertIn('unverified wait', case.controller.status()['playback_error'])

    def test_p08_native_seek_and_replay_clock(self):
        self.console.values['r_aspectratio'] = 1.5
        settled = self.controller.seek_replay_tick(125)
        self.assertEqual(settled['tick'], 125)
        self.assertEqual(self.console.tick, 125)
        self.assertEqual(self.bridge.manual_pose, [1, 2, 3, 4, 5, 0, 1.5])
        self.assertTrue(self.console.paused)
        self.controller.seek(self.project, .5)
        self.assertEqual(self.console.tick, 132)
        self.assertAlmostEqual(self.bridge.phase, .5)
        self.assertAlmostEqual(self.bridge.status()['applied_pose'][0], 40)
        self.controller.seek_replay_tick(0)
        self.assertEqual(self.console.tick, 0)
        for invalid in (math.nan, math.inf, -math.inf, True, -1, 1.5):
            with self.subTest(target=invalid):
                self.console.requests.clear()
                with self.assertRaises(ValueError): self.controller.seek_replay_tick(invalid)
                self.assertEqual(self.console.requests, [])
        case = self.fresh()
        case.console.goto_seek_output = ('Demo Skipping: skipping to demo tick 125 (game tick 1000) '
                                         'from full packet 12 (900).  Current playback is 22 (100)')
        case.console.goto_outputs.extend([124] * 12)
        result = case.controller._seek_tick(125)
        self.assertEqual(result['seek_boundary'], {'requested_tick':125, 'actual_tick':124, 'reason':'recorded_packet'})
        self.assertTrue(case.controller._last_seek_details['verified'])
        case = self.fresh()
        case.console.goto_seek_output = ('Demo Skipping: skipping forward to demo tick 125 (game tick 1000) '
                                         'from current 50 (925)')
        case.console.goto_outputs.extend([124] * 500)
        with self.assertRaisesRegex(RuntimeError, 'did not reach tick 125'): case.controller._seek_tick(125)
        self.assertFalse(case.controller._last_seek_details['verified'])
        clock = ReplayClock(64, .1)
        clock.observe(100, 0)
        self.assertAlmostEqual(clock.position(.1), 100.64)
        self.assertEqual(clock.position(10), 101)
        clock.observe(103, 10)
        self.assertEqual(clock.position(10), 101)
        clock.observe(50, 11)
        self.assertEqual(clock.position(11), 50)
        self.assertAlmostEqual(clock.position(11.05), 50.32)

    def test_p09_native_capture_rendered_pose_and_tick(self):
        self.console.values['r_aspectratio'] = 1.5
        self.console.tick = 164
        self.console.paused = False
        self.console.pose = [10, -20, 30, 15, 40]
        self.bridge.roll = 12
        self.bridge.original_fov = 90
        captured = self.controller.capture_at_replay(100, 64)
        self.assertEqual((captured.time, captured.x, captured.y, captured.z), (1, 10, -20, 30))
        self.assertEqual((captured.pitch, captured.yaw, captured.roll, captured.aspect_ratio), (15, 40, 12, 1.5))
        self.assertAlmostEqual(captured.lens_scale, 2/3)
        project = shot()
        project.set_camera_tangent(1, 'x', 'flat')
        previous_tangents = dict(project.keyframes[1].tangents)
        finish_capture(project, 'replace', captured, 164, selected=1)
        self.assertEqual(len(project.keyframes), 2)
        self.assertEqual(project.keyframes[1].time, 1)
        self.assertEqual(project.keyframes[1].x, 10)
        self.assertEqual(project.keyframes[1].tangents, previous_tangents)
        self.assertTrue(self.console.paused)
        snapshot = {'pose':[4, 5, 6, 7, 8, 9, 1.25], 'tick':164, 'paused':False, 'horizontal_fov':90}
        self.console.paused = False
        frame, tick = self.controller.capture_native_snapshot(snapshot, time=.25, pause=False)
        self.assertEqual((frame.time, frame.x, frame.y, frame.z, frame.roll, tick), (.25, 4, 5, 6, 9, 164))
        self.assertAlmostEqual(frame.lens_scale, .8)
        self.assertFalse(self.console.paused)
        frame, tick = self.controller.capture_native_snapshot(snapshot, time=.25, pause=True)
        self.assertTrue(self.console.paused)
        self.assertEqual((frame.x, tick), (4, 164))
        self.console.demo_name = 'another.dem'
        with self.assertRaises((RuntimeError, ValueError)):
            self.controller.capture_native_snapshot(snapshot, time=.25)
        case = self.fresh()
        case.console.values['r_aspectratio'] = 1.5
        case.console.tick = 99
        with self.assertRaisesRegex(ValueError, 'before this shot'):
            case.controller.capture_at_replay(100, 64)
