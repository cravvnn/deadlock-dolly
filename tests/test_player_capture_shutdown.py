"""Players cancellation keeps ownership until its own capture acknowledges stop."""
from pathlib import Path
from copy import deepcopy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import player_layer
from dolly.video_export import VideoExport, VideoOptions
import test_controller as controller_fixture


class PlayerCaptureShutdownTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.raw = self.directory / player_layer.BUNDLE_NAME
        self.raw.write_bytes(b"retained interrupted raw capture")
        self.controller = Mock()
        self.controller.status.return_value = {
            "connected": True, "game_running": True, "camera_backend": "native",
            "startup_stage": "editing_ready"}
        self.controller.deployment_directory.return_value = self.directory
        self.export = VideoExport(self.controller)
        self.options = VideoOptions(self.directory / "players.mp4", fps=120, fixed_step=True)
        self.export.start(self.options, capture_only=True, before_record=self.arm)
        self.controller.mark_recording_pending.reset_mock()
        self.now = 0.0
        self.sleep_action = lambda: None
        self.clock = patch("dolly.player_layer.time.monotonic", lambda: self.now)
        self.sleep = patch("dolly.player_layer.time.sleep", self.advance)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def arm(self):
        marker = player_layer.begin_capture(self.directory, 48, 424, fps=120)
        self.request_id = marker.read_text().split("request ")[1].split()[0]
        self.publish("armed bounded player sequence capture")

    def publish(self, state, request_id=None):
        (self.directory / player_layer.STATUS_NAME).write_text(
            state + "\nrequest " + (request_id or self.request_id), encoding="ascii")

    def advance(self, seconds):
        self.now += seconds
        self.sleep_action()

    def assert_pending(self):
        self.assertTrue(self.export._capture_only)
        self.assertTrue(self.export.finish_pending)
        self.assertEqual(self.export.status()["state"], "recording")
        self.controller.mark_recording_pending.assert_not_called()
        self.assertEqual(self.raw.read_bytes(), b"retained interrupted raw capture")

    def test_cancellation_waits_for_ack_before_releasing_reservation(self):
        order = []

        def acknowledge():
            self.assert_pending()
            self.assertTrue((self.directory / player_layer.STOP_NAME).is_file())
            order.append("acknowledged")
            self.publish("complete")

        self.sleep_action = acknowledge
        self.controller.mark_recording_pending.side_effect = lambda pending: order.append(pending)
        self.assertEqual(self.export.stop(cancel=True, timeout=1)["state"], "cancelled")
        self.assertEqual(order, ["acknowledged", False])
        self.assertFalse(self.export._capture_only)
        self.assertFalse(self.export.finish_pending)
        self.assertEqual(self.raw.read_bytes(), b"retained interrupted raw capture")

    def test_expected_short_capture_failure_acknowledges_cancel_without_deleting_raw(self):
        self.sleep_action = lambda: self.publish("failed player capture missed shot frames")
        result = self.export.stop(cancel=True, timeout=1)
        self.assertEqual(result["state"], "cancelled")
        self.controller.mark_recording_pending.assert_called_once_with(False)
        self.assertEqual(self.raw.read_bytes(), b"retained interrupted raw capture")

    def test_stale_terminal_status_cannot_release_capture_or_restore_settings(self):
        self.publish("complete", "b" * 32)
        with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
            self.export.stop(cancel=True, timeout=.2)
        self.assert_pending()
        self.controller.clear_export_timing.assert_not_called()
        self.controller.clear_export_resolution.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "Finish the current recording"):
            self.export.start(self.options, capture_only=True)

    def test_marker_removal_does_not_make_other_request_terminal_valid(self):
        stop = player_layer.request_stop

        def remove_marker(deployment):
            result = stop(deployment)
            (self.directory / player_layer.MARKER_NAME).unlink()
            self.publish("failed unrelated capture", "c" * 32)
            return result

        with patch("dolly.player_layer.request_stop", side_effect=remove_marker):
            with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
                self.export.stop(cancel=True, timeout=.2)
        self.assert_pending()
        self.publish("failed player capture missed shot frames")
        self.assertEqual(self.export.stop(cancel=True, timeout=.2)["state"], "cancelled")
        self.controller.mark_recording_pending.assert_called_once_with(False)

    def test_retrying_finish_after_cancel_timeout_keeps_cancel_intent(self):
        with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
            self.export.stop(cancel=True, timeout=.2)
        self.assert_pending()
        self.publish("complete")
        self.assertEqual(self.export.stop(cancel=False, timeout=.2)["state"], "cancelled")
        self.assertEqual(self.raw.read_bytes(), b"retained interrupted raw capture")

    def test_lost_deployment_while_game_alive_keeps_pending_capture(self):
        self.controller.deployment_directory.return_value = None
        with patch("dolly.player_layer.request_stop") as stop:
            with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
                self.export.stop(cancel=True, timeout=.2)
        stop.assert_not_called()
        self.assert_pending()

    def test_known_game_exit_releases_capture_without_writing_stop_marker(self):
        self.controller.status.return_value["game_running"] = False
        with patch("dolly.player_layer.request_stop") as stop:
            self.assertEqual(self.export.stop(cancel=True, timeout=.2)["state"], "cancelled")
        stop.assert_not_called()
        self.controller.mark_recording_pending.assert_called_once_with(False)
        self.assertFalse(self.export._capture_only)

    def test_changed_marker_after_timeout_cannot_cancel_the_new_request(self):
        with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
            self.export.stop(cancel=True, timeout=.2)
        player_layer.begin_capture(self.directory, 48, 12, fps=120)
        with patch("dolly.player_layer.request_stop") as stop:
            with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
                self.export.stop(cancel=True, timeout=.2)
        stop.assert_not_called()
        self.assert_pending()

    def test_replaced_marker_before_first_cancel_is_not_this_capture(self):
        player_layer.begin_capture(self.directory, 48, 12, fps=120)
        with patch("dolly.player_layer.request_stop") as stop:
            with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
                self.export.stop(cancel=True, timeout=.2)
        stop.assert_not_called()
        self.assert_pending()

    def test_default_cancel_wait_is_capped_at_five_seconds(self):
        with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
            self.export.stop(cancel=True)
        self.assertGreaterEqual(self.now, 5)
        self.assertLessEqual(self.now, 5.2)
        self.assert_pending()


class PlayerCaptureContextTests(unittest.TestCase):
    """Real controller/restoration commands with identity changing at the wait."""
    def setUp(self):
        controller_fixture.ControllerTests.setUp(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.controller._session.overlay_dir = self.directory / "owned-deployment"
        self.controller._session.native = SimpleNamespace(
            video_status=lambda: {"state": "idle"},
            status=lambda: {"state": "probe", "demo_name": self.console.demo_name,
                            "game_pid": 1234})
        self.controller._state["startup_stage"] = "editing_ready"
        self.console.values.update(host_framerate=0, r_wait_on_present=0, mat_viewportscale=.75)
        self.deployment = self.controller.deployment_directory()
        self.deployment.mkdir(parents=True)
        self.raw = self.deployment / player_layer.BUNDLE_NAME
        self.raw.write_bytes(b"owned raw capture must stay")
        self.export = VideoExport(self.controller)
        with patch.object(self.controller, "prepare_native_recording"):
            self.export.start(VideoOptions(self.directory / "shot.mp4", fps=120,
                fixed_step=True, full_resolution=True), capture_only=True, before_record=self.arm)
        self.original_timing = deepcopy(self.controller._export_timing)
        self.original_session = self.controller._session
        self.now = 0.0
        self.on_sleep = lambda: None
        clock = patch("dolly.player_layer.time.monotonic", lambda: self.now)
        sleep = patch("dolly.player_layer.time.sleep", self.advance)
        clock.start(); sleep.start()
        self.addCleanup(clock.stop); self.addCleanup(sleep.stop)
        self.console.requests.clear()

    def arm(self):
        marker = player_layer.begin_capture(self.deployment, 48, 424, fps=120)
        self.request_id = marker.read_text().split("request ")[1].split()[0]
        self.publish("armed bounded player sequence capture")

    def publish(self, state):
        (self.deployment / player_layer.STATUS_NAME).write_text(
            state + "\nrequest " + self.request_id, encoding="ascii")

    def advance(self, seconds):
        self.now += seconds
        self.on_sleep()

    def runtime_writes(self):
        names = {"host_framerate", "r_wait_on_present", "demo_timescale", "mat_viewportscale"}
        return [command for command in self.console.requests
                if any(len(part.split()) > 1 and part.split()[0] in names
                       for part in command.split(";"))]

    def assert_retained(self):
        self.assertTrue(self.export._capture_only)
        self.assertTrue(self.controller._recording_pending)
        self.assertTrue(self.export.finish_pending)
        self.assertEqual(self.controller._export_timing, self.original_timing)
        self.assertEqual(self.controller._export_resolution, .75)
        self.assertEqual(self.raw.read_bytes(), b"owned raw capture must stay")

    def test_gui_failure_sends_owned_stop_before_querying_blocked_console(self):
        from dolly.gui import DollyApp
        from dolly.console import ConsoleTimeout

        app = DollyApp.__new__(DollyApp)
        app.controller, app.video_export, app.clip_audio = self.controller, self.export, None
        require_demo = self.controller._require_demo
        queries = []

        def blocked_until_stopped(*args, **kwargs):
            queries.append((self.deployment / player_layer.STOP_NAME).exists())
            if not queries[-1]:
                raise ConsoleTimeout("busy capture console")
            return require_demo(*args, **kwargs)

        def acknowledge():
            self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
            self.publish("failed player capture missed shot frames")

        self.on_sleep = acknowledge
        with patch.object(self.controller, "_require_demo", side_effect=blocked_until_stopped):
            with self.assertRaisesRegex(RuntimeError, "Original camera failure"):
                app._abort_failed_export("Original camera failure")
        self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
        self.assertTrue(queries)
        self.assertTrue(all(queries))
        self.assertFalse(self.controller._recording_pending)
        self.assertIsNone(self.controller._export_timing)
        self.assertIsNone(self.controller._export_resolution)
        self.assertEqual(self.raw.read_bytes(), b"owned raw capture must stay")

    def test_gui_failure_with_blocked_console_retains_unacknowledged_capture(self):
        from dolly.gui import DollyApp
        from dolly.console import ConsoleTimeout

        app = DollyApp.__new__(DollyApp)
        app.controller, app.video_export, app.clip_audio = self.controller, self.export, None
        with patch.object(self.controller, "_require_demo", side_effect=ConsoleTimeout("busy console")):
            with self.assertRaisesRegex(RuntimeError, "Original camera failure"):
                app._abort_failed_export("Original camera failure")
        self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_gui_failure_keeps_journals_if_console_stays_blocked_after_ack(self):
        from dolly.gui import DollyApp
        from dolly.console import ConsoleTimeout

        app = DollyApp.__new__(DollyApp)
        app.controller, app.video_export, app.clip_audio = self.controller, self.export, None
        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        with patch.object(self.controller, "_require_demo", side_effect=ConsoleTimeout("busy console")):
            with self.assertRaisesRegex(RuntimeError, "Original camera failure"):
                app._abort_failed_export("Original camera failure")
        self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_gui_failure_cannot_stop_foreign_session_replay_or_request(self):
        from dolly.gui import DollyApp
        from dolly.console import ConsoleTimeout

        app = DollyApp.__new__(DollyApp)
        app.controller, app.video_export, app.clip_audio = self.controller, self.export, None
        for changed in ("session", "selected replay", "native replay", "request"):
            with self.subTest(changed=changed):
                with patch.object(self.controller, "_require_demo", side_effect=ConsoleTimeout("busy console")):
                    if changed == "session":
                        context = patch.object(self.controller, "_session", SimpleNamespace(**vars(self.original_session)))
                    elif changed == "selected replay":
                        context = patch.object(self.controller, "_demo", Path("other.dem"))
                    elif changed == "native replay":
                        context = patch.object(self.console, "demo_name", "other.dem")
                    else:
                        player_layer.begin_capture(self.deployment, 48, 12, fps=120)
                        from contextlib import nullcontext
                        context = nullcontext()
                    with context:
                        with self.assertRaisesRegex(RuntimeError, "Original camera failure"):
                            app._abort_failed_export("Original camera failure")
                self.assertFalse((self.deployment / player_layer.STOP_NAME).exists())
                self.assertEqual(self.runtime_writes(), [])
                self.assert_retained()

    def test_post_stop_transient_console_timeouts_recover_without_losing_originals(self):
        from dolly.console import ConsoleTimeout
        from dolly.gui import DollyApp

        app = DollyApp.__new__(DollyApp)
        app.controller, app.video_export, app.clip_audio = self.controller, self.export, None
        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        require_demo = self.controller._require_demo
        attempts = []

        def busy_then_ready(*args, **kwargs):
            attempts.append(True)
            if len(attempts) <= 2:
                self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
                self.assert_retained()
                self.assertEqual(self.runtime_writes(), [])
                raise ConsoleTimeout("render queue still draining")
            return require_demo(*args, **kwargs)

        with patch.object(self.controller, "_require_demo", side_effect=busy_then_ready):
            with self.assertRaisesRegex(RuntimeError, "Original camera failure") as raised:
                app._abort_failed_export("Original camera failure")
        self.assertNotIn("Recovery still needs attention", str(raised.exception))
        self.assertEqual(self.export.status()["state"], "cancelled")
        self.assertFalse(self.controller._recording_pending)
        self.assertIsNone(self.controller._export_timing)
        self.assertIsNone(self.controller._export_resolution)
        self.assertEqual(self.raw.read_bytes(), b"owned raw capture must stay")
        self.assertGreaterEqual(len(attempts), 3)

    def test_post_stop_console_recovery_is_bounded_and_retains_retry_state(self):
        from dolly.console import ConsoleTimeout

        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        with patch.object(self.controller, "_require_demo", side_effect=ConsoleTimeout("still busy")) as query:
            with self.assertRaisesRegex(RuntimeError, "acknowledged stop.*still busy.*Finish or discard"):
                self.export.stop(cancel=True)
        self.assertEqual(query.call_count, 3)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()
        # A later explicit retry can recover; the cancelled take remains cancelled.
        self.assertEqual(self.export.stop()["state"], "cancelled")
        self.assertFalse(self.controller._recording_pending)
        self.assertIsNone(self.controller._export_timing)
        self.assertEqual(self.raw.read_bytes(), b"owned raw capture must stay")

    def test_post_stop_retry_rechecks_ownership_before_another_console_query(self):
        from contextlib import ExitStack
        from dolly.console import ConsoleTimeout

        marker = self.deployment / player_layer.MARKER_NAME
        original_marker = marker.read_bytes()
        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        for changed in ("session", "selected replay", "native replay", "request"):
            with self.subTest(changed=changed), ExitStack() as stack:
                def timeout_then_replace(*args, **kwargs):
                    if changed == "session":
                        stack.enter_context(patch.object(self.controller, "_session", SimpleNamespace(**vars(self.original_session))))
                    elif changed == "selected replay":
                        stack.enter_context(patch.object(self.controller, "_demo", Path("other.dem")))
                    elif changed == "native replay":
                        stack.enter_context(patch.object(self.console, "demo_name", "other.dem"))
                    else:
                        player_layer.begin_capture(self.deployment, 48, 12, fps=120)
                    raise ConsoleTimeout("busy before ownership changed")

                try:
                    with patch.object(self.controller, "_require_demo", side_effect=timeout_then_replace) as query:
                        with self.assertRaises(RuntimeError):
                            self.export.stop(cancel=True)
                    self.assertEqual(query.call_count, 1)
                    self.assertEqual(self.runtime_writes(), [])
                    self.assert_retained()
                finally:
                    marker.write_bytes(original_marker)

    def test_replay_changed_during_ack_wait_gets_no_restoration_writes(self):
        self.on_sleep = lambda: setattr(self.console, "demo_name", "other.dem")
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_replaced_session_during_wait_is_not_blessed_by_same_replay_and_pid(self):
        self.on_sleep = lambda: setattr(self.controller, "_session", SimpleNamespace(**vars(self.original_session)))
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_lost_session_during_wait_preserves_runtime_journals(self):
        self.on_sleep = lambda: setattr(self.controller, "_session", None)
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assertEqual(self.export.status()["state"], "recording")
        self.assert_retained()

    def test_replaced_session_before_stop_gets_no_stop_request_or_runtime_write(self):
        self.controller._session = SimpleNamespace(**vars(self.original_session))
        with patch("dolly.player_layer.request_stop") as stop:
            with self.assertRaises(RuntimeError):
                self.export.stop(cancel=True, timeout=.2)
        stop.assert_not_called()
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_normal_finish_cannot_stop_a_replacement_session_capture(self):
        replacement = SimpleNamespace(**vars(self.original_session))
        replacement.overlay_dir = self.directory / "replacement-deployment"
        self.controller._session = replacement
        deployment = self.controller.deployment_directory()
        deployment.mkdir(parents=True)
        player_layer.begin_capture(deployment, 48, 12, fps=120)
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=False, timeout=.2)
        self.assertFalse((deployment / player_layer.STOP_NAME).exists())
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_normal_finish_cannot_stop_a_replacement_request_in_same_session(self):
        player_layer.begin_capture(self.deployment, 48, 12, fps=120)
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=False, timeout=.2)
        self.assertFalse((self.deployment / player_layer.STOP_NAME).exists())
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_normal_finish_requests_its_own_capture_then_yields_to_pipeline(self):
        self.on_sleep = lambda: self.publish("complete")
        self.assertEqual(self.export.stop(cancel=False)["state"], "completed")
        self.assertTrue((self.deployment / player_layer.STOP_NAME).exists())
        self.assertFalse(self.export._capture_only)
        self.assertFalse(self.controller._recording_pending)
        self.assertIsNone(self.controller._export_timing)
        self.assertIsNone(self.controller._export_resolution)
        self.assertEqual(self.raw.read_bytes(), b"owned raw capture must stay")

    def test_request_replaced_after_release_cannot_receive_pending_runtime_restores(self):
        self.on_sleep = lambda: self.publish("complete")
        release = self.controller.mark_recording_pending
        def release_then_replace(pending):
            release(pending)
            if not pending:
                player_layer.begin_capture(self.deployment, 48, 12, fps=120)
        with patch.object(self.controller, "mark_recording_pending", side_effect=release_then_replace):
            with self.assertRaises(RuntimeError):
                self.export.stop(cancel=False)
        self.assertEqual(self.runtime_writes(), [])
        self.assertEqual(self.controller._export_timing, self.original_timing)
        self.assertEqual(self.controller._export_resolution, .75)
        self.assertEqual(self.export._capture_stop_request, self.request_id)
        self.assertTrue(self.export.finish_pending)

    def test_request_replaced_after_timing_restore_cannot_receive_render_scale_restore(self):
        self.on_sleep = lambda: self.publish("complete")
        clear_timing = self.controller.clear_export_timing
        def timing_then_replace():
            clear_timing()
            player_layer.begin_capture(self.deployment, 48, 12, fps=120)
        with patch.object(self.controller, "clear_export_timing", side_effect=timing_then_replace):
            with self.assertRaises(RuntimeError):
                self.export.stop(cancel=False)
        self.assertEqual(self.runtime_writes(), ["host_framerate 0; r_wait_on_present 0; demo_timescale 1"])
        self.assertEqual(self.controller._export_resolution, .75)
        self.assertEqual(self.export._capture_stop_request, self.request_id)
        self.assertTrue(self.export.finish_pending)

    def test_known_exit_of_original_session_clears_without_console_writes(self):
        self.original_session.process.poll = lambda: 0
        with patch("dolly.player_layer.request_stop") as stop:
            self.assertEqual(self.export.stop(cancel=True)["state"], "cancelled")
        stop.assert_not_called()
        self.assertEqual(self.runtime_writes(), [])
        self.assertFalse(self.controller._recording_pending)
        self.assertIsNone(self.controller._export_timing)
        self.assertIsNone(self.controller._export_resolution)

    def test_unreadable_replay_after_wait_preserves_runtime_journals(self):
        def unreadable():
            self.console.goto_output = self.console.demo_output = "unreadable replay status"
        self.on_sleep = unreadable
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_selected_replay_replacement_cannot_authorize_old_capture_cleanup(self):
        def replace_selected():
            self.controller._demo = Path("/chosen/other.dem")
            self.console.demo_name = "other.dem"
        self.on_sleep = replace_selected
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_unchanged_context_timeout_retains_its_exact_timing_and_scale(self):
        with self.assertRaisesRegex(RuntimeError, "stop.*pending"):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_replay_changes_after_ack_before_reservation_release_is_rejected(self):
        def finish_other_replay():
            self.publish("failed player capture missed shot frames")
            self.console.demo_name = "other.dem"
        self.on_sleep = finish_other_replay
        with self.assertRaises(RuntimeError):
            self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), [])
        self.assert_retained()

    def test_replay_change_after_timing_restore_blocks_render_scale_restore(self):
        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        clear_timing = self.controller.clear_export_timing
        def timing_then_change():
            clear_timing()
            self.console.demo_name = "other.dem"
        with patch.object(self.controller, "clear_export_timing", side_effect=timing_then_change):
            with self.assertRaises(RuntimeError):
                self.export.stop(cancel=True, timeout=.2)
        self.assertEqual(self.runtime_writes(), ["host_framerate 0; r_wait_on_present 0; demo_timescale 1"])
        self.assertEqual(self.controller._export_resolution, .75)
        self.assertTrue(self.export.finish_pending)

    def test_replay_change_after_scale_restore_blocks_pov_restore(self):
        self.on_sleep = lambda: self.publish("failed player capture missed shot frames")
        self.export._pov = self.controller._pov_active = True
        clear_scale = self.controller.clear_export_resolution
        def scale_then_change():
            clear_scale()
            self.console.demo_name = "other.dem"
        with patch.object(self.controller, "clear_export_resolution", side_effect=scale_then_change), \
             patch.object(self.controller, "finish_pov_recording", wraps=self.controller.finish_pov_recording) as finish:
            with self.assertRaises(RuntimeError):
                self.export.stop(cancel=True, timeout=.2)
        finish.assert_not_called()
        self.assertTrue(self.controller._pov_active)
        self.assertTrue(self.export.finish_pending)


if __name__ == "__main__":
    unittest.main()
