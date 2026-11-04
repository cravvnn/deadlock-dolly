"""Players must drain before camera/console restoration, including failed takes."""
from pathlib import Path
from unittest.mock import patch
import unittest

from dolly import player_layer
from dolly.console import ConsoleTimeout
from dolly.video_export import VideoOptions
import test_native_controller as native
import test_video_export as video


class PlayersEndpointTests(unittest.TestCase):
    setUp = native.NativeControllerTests.setUp
    prepare = native.NativeControllerTests.prepare
    run_native = native.NativeControllerTests.run_native

    def test_completed_players_shot_keeps_camera_and_hud_until_writer_finishes(self):
        self.prepare()
        self.controller.mark_recording_pending(True, player_layer=True)
        original = self.console.request

        def busy_console(command, **kwargs):
            if command == 'demo_pause' or 'citadel_hide_replay_hud' in command:
                raise ConsoleTimeout('Players disk flush still owns the render thread')
            return original(command, **kwargs)

        with patch.object(self.console, 'request', side_effect=busy_console):
            self.run_native()
        self.assertIsNone(self.controller.status()['playback_error'])
        self.assertFalse(self.controller.status()['playing'])
        self.assertIn('Finishing Players capture', self.controller.status()['message'])
        self.assertEqual(self.bridge.state, 'completed')
        self.assertTrue(self.controller._native_active)
        self.assertTrue(self.controller._playback_restore)
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.controller.mark_recording_pending(False)
        self.assertIsNone(self.controller._finish_native_playback())
        self.assertFalse(self.controller._playback_restore)
        self.assertTrue(self.console.paused)

    def test_players_reservation_does_not_hide_a_changed_replay(self):
        self.prepare()
        self.controller.mark_recording_pending(True, player_layer=True)
        self.bridge.change_demo = True
        self.run_native()
        self.assertIn('identity changed', self.controller.status()['playback_error'])
        self.assertNotEqual(self.bridge.state, 'completed')


class PlayersDrainTests(unittest.TestCase):
    setUp = video.VideoExportTests.setUp
    started = video.VideoExportTests.started
    stopped = video.VideoExportTests.stopped

    def arm(self):
        self.deployment = Path(self.folder.name)
        self.controller.deployment_directory.return_value = self.deployment
        self.controller.status.return_value['playing'] = False
        self.controller._finish_native_playback.return_value = None
        self.export.start(VideoOptions(self.path, fixed_step=True), capture_only=True,
                          before_record=lambda: player_layer.begin_capture(self.deployment, 48, 8))
        self.request = player_layer.capture_request_id(self.deployment)
        self.controller.reset_mock()

    def test_normal_finish_waits_for_exact_request_before_any_console_or_restoration(self):
        self.arm()

        def drain(deployment, *, timeout, request_id):
            self.assertEqual((deployment, timeout, request_id), (self.deployment, 120, self.request))
            self.assertTrue((deployment / player_layer.STOP_NAME).is_file())
            self.controller._require_demo.assert_not_called()
            self.controller.mark_recording_pending.assert_not_called()
            self.controller.clear_export_timing.assert_not_called()
            self.controller._finish_native_playback.assert_not_called()
            return 'complete\nrequest ' + request_id

        with patch('dolly.video_export.player_layer.wait_for_capture', side_effect=drain) as wait:
            self.assertEqual(self.export.stop()['state'], 'completed')
        wait.assert_called_once()
        self.controller.mark_recording_pending.assert_called_once_with(False)
        self.controller._finish_native_playback.assert_called_once_with()

    def test_unacknowledged_finish_retains_capture_and_all_originals_for_retry(self):
        self.arm()
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='armed'):
            with self.assertRaisesRegex(RuntimeError, 'stop is pending'):
                self.export.stop(timeout=.01)
        self.assertTrue(self.export._capture_only)
        self.assertTrue(self.export.finish_pending)
        self.controller.mark_recording_pending.assert_not_called()
        self.controller._finish_native_playback.assert_not_called()
        self.controller.clear_export_timing.assert_not_called()
        self.controller.clear_export_resolution.assert_not_called()
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='complete'):
            self.assertEqual(self.export.stop()['state'], 'completed')

    def test_changed_request_never_receives_stop_or_restoration(self):
        self.arm()
        marker = self.deployment / player_layer.MARKER_NAME
        marker.write_text(marker.read_text().replace(self.request, 'f' * 32))
        with self.assertRaisesRegex(RuntimeError, 'request changed'):
            self.export.stop()
        self.assertFalse((self.deployment / player_layer.STOP_NAME).exists())
        self.controller.clear_export_timing.assert_not_called()
        self.controller.mark_recording_pending.assert_not_called()

    def test_session_change_while_draining_blocks_console_and_restoration(self):
        self.arm()

        def drain(*args, **kwargs):
            self.controller._session = object()
            return 'complete'

        with patch('dolly.video_export.player_layer.wait_for_capture', side_effect=drain):
            with self.assertRaisesRegex(RuntimeError, 'session or selected replay changed'):
                self.export.stop()
        self.controller._require_demo.assert_not_called()
        self.controller.clear_export_timing.assert_not_called()
        self.controller.mark_recording_pending.assert_not_called()

    def test_unreadable_native_state_cannot_send_a_stop_or_restore(self):
        self.arm()
        self.bridge.status.side_effect = OSError('native state unreadable')
        with self.assertRaisesRegex(OSError, 'unreadable'):
            self.export.stop()
        self.assertFalse((self.deployment / player_layer.STOP_NAME).exists())
        self.controller._require_demo.assert_not_called()
        self.controller.clear_export_timing.assert_not_called()
        self.controller.mark_recording_pending.assert_not_called()

    def test_replay_change_during_drain_prevents_console_cleanup(self):
        self.arm()
        self.controller._require_native_demo.side_effect = [None, RuntimeError('replay identity changed')]
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='complete'):
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                self.export.stop()
        self.controller._require_demo.assert_not_called()
        self.controller.clear_export_timing.assert_not_called()
        self.controller.mark_recording_pending.assert_not_called()

    def test_disk_failure_is_terminal_but_kept_for_the_layer_finalizer(self):
        self.arm()
        status = 'failed players capture could not write to the scratch drive fast enough'
        (self.deployment / player_layer.STATUS_NAME).write_text(status + '\nrequest ' + self.request)
        self.assertEqual(self.export.stop()['state'], 'completed')
        self.assertTrue(player_layer.capture_status(self.deployment).startswith(status))
        self.controller._finish_native_playback.assert_called_once_with()

    def test_cancel_uses_bounded_acknowledgement_and_cannot_restore_early(self):
        self.arm()
        with patch('dolly.video_export.player_layer.wait_for_capture', return_value='armed') as wait:
            with self.assertRaisesRegex(RuntimeError, 'stop is pending'):
                self.export.stop(cancel=True)
        self.assertEqual(wait.call_args.kwargs['timeout'], 5)
        self.assertTrue(self.export._capture_cancel_pending)
        self.controller.clear_export_timing.assert_not_called()

    def test_local_scratch_rejection_is_not_a_native_stop_acknowledgement(self):
        self.arm()
        with patch('dolly.video_export.player_layer.wait_for_capture',
                   return_value='rejected scratch directory was not acknowledged by the native capture'):
            with self.assertRaisesRegex(RuntimeError, 'stop is pending'):
                self.export.stop()
        self.assertTrue(self.export._capture_only)
        self.controller.mark_recording_pending.assert_not_called()
        self.controller.clear_export_timing.assert_not_called()

    def test_known_game_exit_releases_capture_without_camera_handoff(self):
        self.arm()
        self.controller.status.return_value['game_running'] = False
        self.controller._finish_native_playback.side_effect = AssertionError('No camera control after game exit')
        self.assertEqual(self.export.stop(cancel=True)['state'], 'cancelled')
        self.controller._finish_native_playback.assert_not_called()
        self.controller._require_native_demo.assert_not_called()
        self.assertFalse((self.deployment / player_layer.STOP_NAME).exists())
