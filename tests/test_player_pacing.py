"""Owned progress distinguishes fixed-step backpressure from an unresponsive game."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dolly import player_layer
from dolly.video_export import format_video_status
from dolly.video_export import VideoOptions
import test_native_controller as native
import test_video_export as video


def report(request_id, **changes):
    data = dict(format=1, request=request_id, pid=1234, heartbeat=100000,
                progress_ms=99500, stage='storage', written=2, sealed=4,
                cap=120, bytes=33177600, progress=12, error=0)
    data.update(changes)
    return data


class PacingProtocolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        player_layer.begin_capture(self.directory, 48, 120)
        self.request = player_layer.capture_request_id(self.directory)
        self.path = self.directory / player_layer.PACING_NAME

    def test_progress_is_bounded_and_matches_the_current_request(self):
        expected = report(self.request)
        self.path.write_text(json.dumps(expected))
        self.assertEqual(player_layer.capture_pacing(self.directory, self.request), expected)
        for changes in (dict(request='f' * 32), dict(format=True), dict(pid=True),
                        dict(progress_ms=100001), dict(written=5), dict(cap=2_000_001),
                        dict(stage='unknown'), dict(bytes=-1)):
            with self.subTest(changes=changes):
                self.path.write_text(json.dumps(report(self.request, **changes)))
                self.assertEqual(player_layer.capture_pacing(self.directory, self.request), {})
        self.path.write_bytes(b' ' * 2049)
        self.assertEqual(player_layer.capture_pacing(self.directory, self.request), {})

    def test_replaced_marker_cannot_bless_an_old_progress_file(self):
        self.path.write_text(json.dumps(report(self.request)))
        marker = self.directory / player_layer.MARKER_NAME
        marker.write_text(marker.read_text().replace(self.request, 'e' * 32))
        self.assertEqual(player_layer.capture_pacing(self.directory, self.request), {})

    def test_waiting_message_keeps_the_selected_output_fps(self):
        text = format_video_status(dict(state='recording', capture_waiting='storage',
                                        frames_written=2, capture_total=120, fps=60))
        self.assertIn('Waiting for storage', text)
        self.assertIn('2 / 120 frames', text)
        self.assertIn('60 FPS output', text)


class PacingControllerTests(unittest.TestCase):
    setUp = native.NativeControllerTests.setUp
    prepare = native.NativeControllerTests.prepare
    run_native = native.NativeControllerTests.run_native

    def pin_capture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.controller._session.overlay_dir = Path(directory.name)
        self.directory = self.controller.deployment_directory()
        self.directory.mkdir(parents=True)
        player_layer.begin_capture(self.directory, 48, 120)
        self.request = player_layer.capture_request_id(self.directory)
        self.path = self.directory / player_layer.PACING_NAME
        self.controller.mark_recording_pending(True, player_layer=True)
        self.controller._export_timing = dict(fps=60, speed=1)

    def publish(self, **changes):
        self.path.write_text(json.dumps(report(self.request, **changes)))

    def test_paced_storage_can_take_twenty_seconds_without_changing_shot_clock(self):
        self.prepare()
        self.pin_capture()
        status = self.bridge.status
        start = self.clock.now

        def slow_writer():
            self.bridge.freeze_frames = self.clock.now - start < 20
            current = status()
            now = int(self.clock.now * 1000) + 100000
            self.publish(heartbeat=now, progress_ms=now,
                         bytes=int((self.clock.now - start) * 1000),
                         progress=int((self.clock.now - start) * 1000))
            return current

        with patch.object(self.bridge, 'status', side_effect=slow_writer):
            self.run_native()
        self.assertIsNone(self.controller.status()['playback_error'])
        self.assertEqual(self.controller.status()['time'], self.project.duration)
        self.assertGreater(self.clock.now - start, 20)
        self.assertEqual(self.controller._export_timing['fps'], 60)

    def test_backpressure_starting_during_console_query_does_not_abort_capture(self):
        from dolly.console import ConsoleTimeout
        self.prepare()
        self.pin_capture()
        original = self.controller._require_demo
        misses = []

        def delayed(*args, **kwargs):
            if len(misses) < 2:
                misses.append(True)
                self.clock.advance(3)
                now = int(self.clock.now * 1000) + 100000
                self.publish(heartbeat=now, progress_ms=now, progress=12 + len(misses))
                raise ConsoleTimeout('capture began waiting during demo_goto')
            return original(*args, **kwargs)

        # At query admission the writer is idle; only the post-timeout read can
        # observe its new report. Real files exercise the ownership validator.
        status = self.bridge.status
        def idle_before_query():
            self.clock.advance(.6)
            current = status()
            self.publish(stage='idle')
            self.controller._player_pacing['next_read'] = 0
            return current

        with patch.object(self.bridge, 'status', side_effect=idle_before_query), \
             patch.object(self.controller, '_require_demo', side_effect=delayed):
            self.run_native()
        self.assertEqual(len(misses), 2)
        self.assertIsNone(self.controller.status()['playback_error'])
        self.assertEqual(self.controller.status()['time'], self.project.duration)

    def test_repeated_console_timeouts_without_owned_progress_still_abort(self):
        from dolly.console import ConsoleTimeout
        self.prepare()
        self.pin_capture()
        self.publish(pid=9999)
        def timeout(*args, **kwargs):
            self.clock.advance(3)
            raise ConsoleTimeout('unverified wait')
        status = self.bridge.status
        def next_window():
            self.clock.advance(.6)
            return status()
        with patch.object(self.bridge, 'status', side_effect=next_window), \
             patch.object(self.controller, '_require_demo', side_effect=timeout):
            self.run_native()
        self.assertIn('unverified wait', self.controller.status()['playback_error'])

    def test_old_heartbeat_and_no_real_progress_do_not_authorize_a_long_wait(self):
        self.pin_capture()
        self.publish()
        self.assertTrue(self.controller._player_capture_pacing_active(1))
        self.assertFalse(self.controller._player_capture_pacing_active(3))
        self.publish(heartbeat=131000)
        self.assertFalse(self.controller._player_capture_pacing_active(4))
        self.publish(heartbeat=132000, progress_ms=132000)
        self.assertFalse(self.controller._player_capture_pacing_active(32))

    def test_wrong_pid_request_session_and_replay_never_relax_the_guard(self):
        self.pin_capture()
        for changes in (dict(pid=9999), dict(request='f' * 32), dict(error=1)):
            self.publish(**changes)
            self.controller._player_pacing['next_read'] = 0
            self.assertFalse(self.controller._player_capture_pacing_active(1))
        self.publish()
        session = self.controller._session
        self.controller._session = type(session)(**vars(session))
        self.assertFalse(self.controller._player_capture_pacing_active(2))
        self.controller._session = session
        self.controller._demo = Path('/another.dem')
        self.assertFalse(self.controller._player_capture_pacing_active(2))

    def test_live_native_replay_identity_still_wins_over_pacing_progress(self):
        self.prepare()
        self.pin_capture()
        self.publish()
        self.bridge.change_demo = True
        self.run_native()
        self.assertIn('identity changed', self.controller.status()['playback_error'])

    def test_missing_progress_keeps_the_sustained_hang_failure(self):
        self.prepare()
        self.pin_capture()
        self.bridge.freeze_frames = True
        self.run_native()
        self.assertIn('stopped receiving rendered views', self.controller.status()['playback_error'])


class PacingExportTests(unittest.TestCase):
    setUp = video.VideoExportTests.setUp
    started = video.VideoExportTests.started
    stopped = video.VideoExportTests.stopped

    def test_only_this_capture_reports_storage_progress_in_the_export_status(self):
        directory = Path(self.folder.name)
        self.controller.deployment_directory.return_value = directory
        self.controller.game_pid.return_value = 1234
        self.export.start(VideoOptions(self.path, fps=120, fixed_step=True), capture_only=True,
                          before_record=lambda: player_layer.begin_capture(directory, 48, 120, fps=120))
        request = player_layer.capture_request_id(directory)
        path = directory / player_layer.PACING_NAME
        path.write_text(json.dumps(report(request)))
        status = self.export.status()
        self.assertEqual((status['frames_written'], status['capture_total'], status['fps']), (2, 120, 120))
        self.assertIn('Waiting for storage', format_video_status(status))
        path.write_text(json.dumps(report('f' * 32)))
        self.assertNotIn('capture_waiting', self.export.status())


if __name__ == '__main__':
    unittest.main()
