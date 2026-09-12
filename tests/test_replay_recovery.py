"""Replay reset boundaries and failure behavior; no native game is launched."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_controller as fixtures
from test_native_controller import Bridge, Clock
from dolly.video_export import VideoExport, VideoOptions


class ReplayRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ControllerTests.setUp(self)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.controller._demo = Path(self.folder.name) / 'example.dem'
        self.controller._demo.write_bytes(b'fixture')
        self.clock = Clock()
        self.controller._stop_event = fixtures.CountedEvent(self.clock, 1000)
        timer = patch('dolly.controller.time.perf_counter', lambda: self.clock.now)
        timer.start(); self.addCleanup(timer.stop)
        self.bridge = Bridge(self.console, self.clock)
        self.bridge.state = 'stopped'
        self.bridge.video_status = Mock(return_value={'state': 'idle'})
        self.controller._session.native = self.bridge
        launch_patch = patch('dolly.controller.launcher.launch')
        self.launch = launch_patch.start()
        self.addCleanup(launch_patch.stop)
        self.after_load = lambda: None
        self.loading = False
        self.initial_ticks = iter((0, 1, 2))
        self.pause_ticks = []
        self.never_inactive = False
        original_request = self.console.request
        original_send = self.console.send
        def request(command, **kwargs):
            if command == 'disconnect' and not self.never_inactive:
                self.console.demo_output = 'Error - Not currently playing back a demo.'
                self.console.goto_output = self.console.demo_output
            if command == 'demo_goto' and self.loading and not self.console.paused:
                self.console.tick = next(self.initial_ticks, self.console.tick)
            if command == 'demo_pause':
                self.pause_ticks.append(self.console.tick)
            return original_request(command, **kwargs)
        def send(command):
            original_send(command)
            if command.startswith('playdemo '):
                self.loading = True
                self.console.paused = False
                self.console.demo_output = self.console.goto_output = None
                self.after_load()
        self.console.request = request
        self.console.send = send

    def tearDown(self):
        self.launch.assert_not_called()

    def recover(self):
        return self.controller._recover_replay_for_shot()

    def test_inactive_then_initial_update_before_pause_and_no_launch(self):
        result = self.recover()
        self.assertGreaterEqual(result['tick'], 2)
        self.assertEqual(self.pause_ticks, [2])
        self.assertEqual(self.console.requests.count('disconnect'), 1)
        self.assertEqual(len(self.console.sent), 1)
        self.assertEqual(self.controller._replay_recovery['stage'], 'ready')
        self.assertFalse(self.controller._replay_recovery_active)
        self.assertTrue(self.controller._probe_result['capabilities']['spec_goto'])

    def test_old_same_file_never_satisfies_inactive_boundary(self):
        self.never_inactive = True
        with self.assertRaisesRegex(RuntimeError, 'will not restart'):
            self.recover()
        self.assertEqual(self.console.sent, [])
        self.assertEqual(self.console.requests.count('disconnect'), 1)
        self.assertEqual(self.controller._probe_result, {})

    def test_crash_after_load_is_not_retried_or_relaunched(self):
        self.after_load = lambda: setattr(self.process, 'poll', lambda: 0xc0000005)
        with self.assertRaisesRegex(RuntimeError, 'will not restart'):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)
        self.assertEqual(self.pause_ticks, [])
        self.assertEqual(self.controller._probe_result, {})
        with self.assertRaises(RuntimeError):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)

    def test_disconnect_failure_never_sends_playdemo(self):
        self.console.fail_commands.add('disconnect')
        with self.assertRaisesRegex(RuntimeError, 'will not restart'):
            self.recover()
        self.assertEqual(self.console.sent, [])

    def test_load_failure_is_not_retried(self):
        def fail():
            raise RuntimeError('lost connection')
        self.after_load = fail
        with self.assertRaisesRegex(RuntimeError, 'lost connection'):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)

    def test_cancellation_after_load_leaves_replay_unarmed(self):
        self.after_load = lambda: self.assertTrue(self.controller.cancel_replay_recovery())
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)
        self.assertEqual(self.pause_ticks, [])
        self.assertEqual(self.controller._probe_result, {})
        self.assertFalse(self.controller.cancel_replay_recovery())

    def test_wrong_replay_fails_without_pause_or_another_load(self):
        self.after_load = lambda: setattr(self.console, 'demo_name', 'different.dem')
        with self.assertRaisesRegex(RuntimeError, 'different'):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)
        self.assertEqual(self.pause_ticks, [])

    def test_initial_full_packet_timeout_never_pauses_or_retries(self):
        self.initial_ticks = iter(())
        self.after_load = lambda: setattr(self.console, 'tick', 0)
        with self.assertRaisesRegex(RuntimeError, 'Timed out'):
            self.recover()
        self.assertEqual(len(self.console.sent), 1)
        self.assertEqual(self.pause_ticks, [])

    def test_pending_settings_and_active_recording_prevent_disconnect(self):
        self.controller._restore = {'r_aspectratio': 0}
        with self.assertRaisesRegex(RuntimeError, 'pending settings'):
            self.recover()
        self.controller._restore.clear()
        self.bridge.video_status.return_value = {'state': 'recording'}
        with self.assertRaisesRegex(RuntimeError, 'Finish recording'):
            self.recover()
        self.assertNotIn('disconnect', self.console.requests)

    def test_recording_reservation_is_one_use_and_never_reloads(self):
        self.bridge.video_status.return_value = {'state': 'recording'}
        self.controller._recording_replay = (self.controller._session, str(self.controller._demo), 100)
        self.controller._prepare_native_shot_replay()
        with self.assertRaisesRegex(RuntimeError, 'unused prepared shot'):
            self.controller._prepare_native_shot_replay()
        self.assertNotIn('disconnect', self.console.requests)
        self.assertEqual(self.console.sent, [])

    def test_recording_reservation_rejects_moved_or_unpaused_replay(self):
        self.bridge.video_status.return_value = {'state': 'recording'}
        for paused, tick in ((False, 100), (True, 101)):
            self.console.paused, self.console.tick = paused, tick
            self.controller._recording_replay = (self.controller._session, str(self.controller._demo), 100)
            with self.assertRaisesRegex(RuntimeError, 'unused prepared shot'):
                self.controller._prepare_native_shot_replay()
        self.assertNotIn('disconnect', self.console.requests)

    def test_prepared_shot_waits_for_the_live_recorder(self):
        self.bridge.video_status = Mock(side_effect=[
            {'state': 'starting'}, {'state': 'starting'}, {'state': 'recording'}])
        self.controller._recording_replay = (self.controller._session, str(self.controller._demo), 100)
        self.controller._prepare_native_shot_replay()
        self.assertEqual(self.bridge.video_status.call_count, 3)
        self.assertIsNone(self.controller._recording_replay)
        self.assertNotIn('disconnect', self.console.requests)
        self.assertEqual(self.console.sent, [])

    def test_prepared_shot_rejects_ended_or_failed_recorder(self):
        for status, message in (({'state': 'completed'}, 'no longer starting'),
                                ({'state': 'failed', 'error': 'encoder lost'}, 'encoder lost')):
            with self.subTest(status=status):
                self.controller.mark_recording_pending(True)
                self.bridge.video_status = Mock(return_value=status)
                self.controller._recording_replay = (self.controller._session, str(self.controller._demo), 100)
                with self.assertRaisesRegex(RuntimeError, message):
                    self.controller._prepare_native_shot_replay()
                self.assertIsNone(self.controller._recording_replay)
                self.assertNotIn('disconnect', self.console.requests)
                self.controller.mark_recording_pending(False)

    def test_prepared_shot_times_out_without_a_live_recorder(self):
        self.bridge.video_status.return_value = {'state': 'starting'}
        with self.assertRaisesRegex(RuntimeError, 'did not start in time'):
            self.controller._wait_for_recorder_ready(timeout=.1)

    def test_old_idle_recorder_poll_does_not_erase_fresh_preparation(self):
        prepared = (self.controller._session, str(self.controller._demo), 100)
        self.controller._recording_replay = prepared
        self.controller.mark_recording_pending(False)
        self.assertEqual(self.controller._recording_replay, prepared)
        self.controller.mark_recording_pending(True)
        self.controller.mark_recording_pending(False)
        self.assertIsNone(self.controller._recording_replay)

    def test_uncertain_recorder_start_blocks_reload_even_with_old_idle_status(self):
        self.controller.mark_recording_pending(True)
        with self.assertRaisesRegex(RuntimeError, 'Finish recording'):
            self.recover()
        self.assertNotIn('disconnect', self.console.requests)

    def test_frozen_recording_preserves_current_tick_and_camera(self):
        self.controller.prepare_native_recording(frozen=True)
        self.assertEqual(self.console.tick, 100)
        self.assertNotIn('disconnect', self.console.requests)
        self.assertNotIn('native.release', self.bridge.events)

    def test_explicit_seek_invalidates_recording_reservation(self):
        self.controller._recording_replay = (self.controller._session, str(self.controller._demo), 100)
        self.controller._stop_event.set()
        with self.assertRaises(RuntimeError):
            self.controller._seek_tick(100)
        self.assertIsNone(self.controller._recording_replay)


class RecordingPreparationTests(unittest.TestCase):
    def test_recovery_precedes_fixed_timing_and_file_open(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            bridge = Mock()
            bridge.video_status.return_value = {'state': 'idle'}
            bridge.start_video.side_effect = lambda *a, **k: calls.append('file')
            controller = Mock()
            controller.status.return_value = dict(connected=True, game_running=True,
                camera_backend='native', startup_stage='replay_ready')
            controller._native_bridge.return_value = bridge
            controller.prepare_native_recording.side_effect = lambda project, **kwargs: calls.append('recovery')
            controller.set_export_timing.side_effect = lambda *a: calls.append('timing')
            VideoExport(controller).start(VideoOptions(Path(folder)/'shot.mp4', fixed_step=True))
            self.assertEqual(calls, ['recovery', 'timing', 'file'])

    def test_recovery_failure_never_starts_encoder_or_sets_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Mock()
            controller.status.return_value = dict(connected=True, game_running=True,
                camera_backend='native', startup_stage='replay_ready')
            controller.prepare_native_recording.side_effect = RuntimeError('Deadlock closed')
            with self.assertRaisesRegex(RuntimeError, 'Deadlock closed'):
                VideoExport(controller).start(VideoOptions(Path(folder)/'shot.mp4', fixed_step=True))
            controller.set_export_timing.assert_not_called()
            controller._native_bridge().start_video.assert_not_called()
