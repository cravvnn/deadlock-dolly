"""Video lifecycle and output guards without a Windows encoder or display."""
from datetime import datetime
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.path import Project
from dolly.gui import DollyApp
from dolly.bindings import DEFAULT_BINDING
from dolly.editor_actions import EditorBinding
from dolly.settings import AppSettings
from dolly.video_export import VideoExport, VideoOptions, default_video_path, format_video_status, recording_ready


READY = {"connected": True, "game_running": True, "camera_backend": "native", "startup_stage": "editing_ready"}


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class VideoExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "shot.mp4"
        self.state = {"state": "idle"}
        self.bridge = Mock()
        self.bridge.video_status.side_effect = lambda: dict(self.state)
        self.bridge.start_video.side_effect = self.started
        self.bridge.stop_video.side_effect = self.stopped
        self.controller = Mock()
        self.controller.status.return_value = dict(READY)
        self.controller._native_bridge.return_value = self.bridge
        self.export = VideoExport(self.controller)

    def started(self, path, **options):
        self.state = {"state": "recording", "fps": options["fps"], "frames_written": 0}

    def stopped(self, cancel=False):
        self.state = {"state": "cancelled" if cancel else "completed", "frames_written": 30, "duration": 1.0}

    def test_valid_start_calls_only_native_recording_with_explicit_options(self):
        result = self.export.start(VideoOptions(self.path, 30, 10_000_000))
        self.assertEqual(result["state"], "recording")
        self.bridge.start_video.assert_called_once_with(
            str(self.path), fps=30, bitrate=10_000_000,
            encoder=0, codec=0, quality=0, preset=0, ffmpeg_path="", fixed_step=False)
        self.assertFalse(self.path.exists())  # Only native creates the actual file.
        self.controller.play.assert_not_called()
        self.controller._request.assert_not_called()

    def test_high_frame_rates_are_accepted(self):
        for fps in (300, 600):
            with self.subTest(fps=fps):
                VideoOptions(self.path, fps, 20_000_000).validated()

    def test_120_fps_is_sent_to_native_without_changing_playback(self):
        self.export.start(VideoOptions(self.path, 120, 40_000_000))
        self.bridge.start_video.assert_called_once_with(
            str(self.path), fps=120, bitrate=40_000_000,
            encoder=0, codec=0, quality=0, preset=0, ffmpeg_path="", fixed_step=False)
        self.controller.play.assert_not_called()
        self.controller._request.assert_not_called()

    def test_existing_output_is_not_sent_to_native(self):
        self.path.write_bytes(b"existing video")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.export.start(VideoOptions(self.path))
        self.bridge.start_video.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b"existing video")

    def test_dangling_symlink_is_not_reused(self):
        # Creating symlinks needs extra privileges on some Windows runners.
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaisesRegex(ValueError, "already exists"):
            VideoOptions(self.path).validated()

    def test_invalid_options_are_rejected_before_native_start(self):
        for options in (VideoOptions(self.path, True), VideoOptions(self.path, 200),
                        VideoOptions(self.path, 60, 0), VideoOptions(self.path.with_suffix(".avi")),
                        VideoOptions(self.path.parent / "missing" / "shot.mp4")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.export.start(options)
        self.bridge.start_video.assert_not_called()

    def test_ffmpeg_codec_requires_a_path_and_uses_the_ffmpeg_encoder(self):
        with self.assertRaisesRegex(ValueError, "ffmpeg"):
            VideoOptions(self.path, codec="h264_nvenc").validated()
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "ffmpeg.exe"
            exe.write_bytes(b"MZ")
            self.export.start(VideoOptions(self.path, 30, 10_000_000, codec="h264_nvenc",
                                           quality=21, ffmpeg_path=exe))
        self.bridge.start_video.assert_called_once_with(
            str(self.path), fps=30, bitrate=10_000_000,
            encoder=1, codec=1, quality=21, preset=0, ffmpeg_path=str(exe), fixed_step=False)

    def test_lossless_requires_matroska_and_maps_to_ffv1(self):
        with self.assertRaisesRegex(ValueError, ".mkv"):
            VideoOptions(self.path, codec="lossless").validated()
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "ffmpeg.exe"
            exe.write_bytes(b"MZ")
            mk = Path(folder) / "shot.mkv"
            self.export.start(VideoOptions(mk, 60, 20_000_000, codec="lossless", ffmpeg_path=exe))
        self.assertEqual(self.bridge.start_video.call_args.kwargs["codec"], 10)
        self.assertEqual(self.bridge.start_video.call_args.kwargs["encoder"], 1)

    def test_auto_codec_uses_ffmpeg_when_available(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "ffmpeg.exe"
            exe.write_bytes(b"MZ")
            self.export.start(VideoOptions(self.path, 60, 20_000_000, ffmpeg_path=exe))
        self.assertEqual(self.bridge.start_video.call_args.kwargs["encoder"], 1)
        self.assertEqual(self.bridge.start_video.call_args.kwargs["codec"], 1)

    def test_fixed_step_sets_and_clears_controller_timing(self):
        self.export.start(VideoOptions(self.path, 60, 20_000_000, fixed_step=True))
        self.controller.set_export_timing.assert_called_once_with(60, 1.0)
        self.assertTrue(self.bridge.start_video.call_args.kwargs["fixed_step"])
        self.export.stop()
        self.controller.clear_export_timing.assert_called_once()

    def test_fixed_step_passes_the_export_speed(self):
        self.export.start(VideoOptions(self.path, 60, 20_000_000, fixed_step=True, speed=0.1))
        self.controller.set_export_timing.assert_called_once_with(60, 0.1)

    def test_export_speed_range_is_validated(self):
        for speed in (0.0, 0.04, 4.01, "fast", True):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                VideoOptions(self.path, codec="builtin", speed=speed).validated()

    def test_fixed_step_timing_failure_does_not_start_recording(self):
        self.controller.set_export_timing.side_effect = RuntimeError("not connected")
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            self.export.start(VideoOptions(self.path, 60, 20_000_000, fixed_step=True))
        self.bridge.start_video.assert_not_called()

    def test_not_ready_console_or_disconnected_cannot_record(self):
        for key, value in (("connected", False), ("game_running", False),
                           ("camera_backend", "console"), ("startup_stage", "loading_replay")):
            with self.subTest(key=key):
                self.controller.status.return_value = {**READY, key: value}
                with self.assertRaisesRegex(RuntimeError, "native editor"):
                    self.export.start(VideoOptions(self.path))
        self.bridge.start_video.assert_not_called()

    def test_replay_ready_and_playing_path_can_be_recorded(self):
        self.assertTrue(recording_ready({**READY, "startup_stage": "replay_ready", "playing": True}))

    def test_second_start_cannot_replace_active_recording(self):
        self.export.start(VideoOptions(self.path))
        with self.assertRaisesRegex(RuntimeError, "current recording"):
            self.export.start(VideoOptions(self.path.parent / "second.mp4"))
        self.assertEqual(self.bridge.start_video.call_count, 1)

    def test_start_timeout_retains_cancel_controls_until_native_acknowledges(self):
        self.state = {"state": "idle", "ack": 0}
        self.bridge.start_video.side_effect = RuntimeError("Native media did not acknowledge")
        with self.assertRaisesRegex(RuntimeError, "did not acknowledge"):
            self.export.start(VideoOptions(self.path))
        self.assertEqual(self.export.status()["state"], "starting")
        with self.assertRaisesRegex(RuntimeError, "current recording"):
            self.export.start(VideoOptions(self.path.parent / "second.mp4"))
        self.assertEqual(self.bridge.start_video.call_count, 1)
        self.assertEqual(self.export.stop(cancel=True)["state"], "cancelled")
        self.bridge.stop_video.assert_called_once_with(cancel=True)

    def test_late_success_after_start_timeout_recovers_recording_status(self):
        self.state = {"state": "idle", "ack": 0}
        self.bridge.start_video.side_effect = RuntimeError("Timeout")
        with self.assertRaises(RuntimeError):
            self.export.start(VideoOptions(self.path))
        self.state = {"state": "recording", "ack": 2}
        self.assertEqual(self.export.status()["state"], "recording")

    def test_confirmed_start_rejection_reports_failure_instead_of_unknown_start(self):
        self.state = {"state": "idle", "ack": 0}
        def rejected(*args, **kwargs):
            self.state = {"state": "idle", "ack": 2, "command_error": 1, "command_message": "Unsupported dimensions"}
            raise RuntimeError("Unsupported dimensions")
        self.bridge.start_video.side_effect = rejected
        with self.assertRaises(RuntimeError):
            self.export.start(VideoOptions(self.path))
        self.assertEqual(self.export.status()["state"], "failed")
        self.assertIn("Unsupported dimensions", self.export.status()["error"])

    def test_finish_waits_for_terminal_encoder_state(self):
        self.export.start(VideoOptions(self.path))
        self.bridge.stop_video.side_effect = lambda **_: self.state.update(state="finalizing")
        polls = iter(({"state": "recording"}, {"state": "finalizing"}, {"state": "completed"}))
        self.bridge.video_status.side_effect = lambda: next(polls)
        with patch("dolly.video_export.time.sleep") as sleep:
            self.assertEqual(self.export.stop()["state"], "completed")
        self.bridge.stop_video.assert_called_once_with(cancel=False)
        sleep.assert_called_once()

    def test_cancel_requests_native_discard(self):
        self.export.start(VideoOptions(self.path))
        self.assertEqual(self.export.stop(cancel=True)["state"], "cancelled")
        self.bridge.stop_video.assert_called_once_with(cancel=True)

    def test_encoder_failure_is_reported_on_finish(self):
        self.export.start(VideoOptions(self.path))
        self.bridge.stop_video.side_effect = lambda **_: self.state.update(state="failed", error="Disk full")
        with self.assertRaisesRegex(RuntimeError, "Disk full"):
            self.export.stop()

    def test_finalize_timeout_does_not_claim_a_saved_file_or_restart(self):
        self.export.start(VideoOptions(self.path))
        self.bridge.stop_video.side_effect = lambda **_: self.state.update(state="finalizing")
        with self.assertRaisesRegex(RuntimeError, "still finishing"):
            self.export.stop(timeout=0)
        self.assertEqual(self.export.status()["state"], "finalizing")
        with self.assertRaisesRegex(RuntimeError, "current recording"):
            self.export.start(VideoOptions(self.path))

    def test_lost_bridge_during_recording_reports_failure(self):
        self.export.start(VideoOptions(self.path))
        self.bridge.video_status.side_effect = RuntimeError("Game closed")
        self.assertEqual(self.export.status()["error"], "Game closed")
        self.assertEqual(self.export.status()["state"], "failed")

    def test_dead_game_with_stale_shared_status_cannot_look_still_recording(self):
        self.export.start(VideoOptions(self.path))
        self.controller.status.return_value = {**READY, "game_running": False}
        self.assertEqual(self.export.status()["state"], "failed")
        self.assertIn("incomplete", self.export.status()["error"])

    def test_completed_result_survives_bridge_close(self):
        self.export.start(VideoOptions(self.path))
        self.export.stop()
        self.bridge.video_status.side_effect = RuntimeError("Closed")
        self.assertEqual(self.export.status()["state"], "completed")

    def test_status_returns_detached_snapshot(self):
        self.export.start(VideoOptions(self.path))
        snapshot = self.export.status()
        snapshot["state"] = "completed"
        self.assertEqual(self.export.status()["state"], "recording")

    def test_default_filename_is_safe_unique_and_has_no_io_side_effects(self):
        args = dict(directory=Path(self.folder.name), now=datetime(2026, 9, 10, 12, 30))
        first = default_video_path('CON:?/ test', **args)
        self.assertEqual(first.suffix, ".mp4")
        self.assertNotIn(":", first.name)
        self.assertFalse(first.exists())
        first.touch()
        second = default_video_path('CON:?/ test', **args)
        self.assertNotEqual(first, second)
        self.assertTrue(second.stem.endswith("_2"))

    def test_native_dropped_frames_are_visible_in_status(self):
        result = format_video_status({"state": "completed", "duration": 2, "frames_written": 118,
                                      "frames_dropped": 2, "width": 1920, "height": 1080})
        self.assertIn("2 missed frames", result)
        self.assertIn("1920 × 1080", result)
        self.assertIn("Saved", result)


class VideoGuiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = DollyApp.__new__(DollyApp)
        self.app.busy = False
        self.app.project = Project()
        self.app.frozen = Var(False)
        self.app.playing = True
        self.app.video_path = Var(str(Path(self.folder.name) / "shot.mp4"))
        self.app.video_fps = Var("60")
        self.app.video_bitrate = Var("20 Mbps")
        self.app.video_codec = Var("")
        self.app.ffmpeg_path = Var("")
        self.app.video_fixed_step = Var(False)
        self.app.video_export_speed = Var("1")
        self.app.status_text = Var("")
        self.app.video_export = Mock()
        self.app._submit = Mock(return_value=True)
        self.app._error = Mock()

    def test_start_while_path_is_playing_is_queued_without_changing_playback(self):
        self.app._start_video_recording()
        self.app._submit.call_args.args[1]()
        self.app.video_export.start.assert_called_once()
        self.assertTrue(self.app.playing)
        self.app._error.assert_not_called()

    def test_busy_start_and_stop_do_not_queue_concurrent_mutations(self):
        self.app.busy = True
        self.app._start_video_recording()
        self.app._stop_video_recording()
        self.app._submit.assert_not_called()

    def test_cancel_can_finish_while_playback_is_running(self):
        self.app._stop_video_recording(cancel=True)
        self.app._submit.call_args.args[1]()
        self.app.video_export.stop.assert_called_once_with(cancel=True)

    def test_existing_output_shows_error_without_submitting(self):
        Path(self.app.video_path.get()).touch()
        self.app._start_video_recording()
        self.app._submit.assert_not_called()
        self.app._error.assert_called_once()


class ReShadeGuiTests(unittest.TestCase):
    def test_library_is_prepared_before_loading_the_selected_runtime(self):
        self.app.controller = Mock()
        self.app.controller.status.return_value = READY
        self.app._submit = Mock()
        self.app.reshade_status_text = Var("")
        self.app._log = Mock()
        order = []
        self.app.controller._native_bridge.return_value.configure_reshade.side_effect = lambda *_: order.append("load")
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "ReShade64.dll"
            runtime.write_bytes(b"selected user runtime")
            self.app.app_settings = AppSettings(reshade_runtime_path=str(runtime))
            self.app._configure_reshade(automatic=True)
            with patch("dolly.reshade_setup.prepare_config", side_effect=lambda *_: order.append("library")), \
                 patch("dolly.gui.save_settings"):
                self.app._submit.call_args.args[1]()
        self.assertEqual(order, ["library", "load"])

    def setUp(self):
        self.app = DollyApp.__new__(DollyApp)
        self.app.busy = False
        self.app.playing = False
        self.app.root = Mock()
        self.app.app_settings = AppSettings(reshade_runtime_path="C:/Effects/ReShade64.dll")
        self.app._persist_preferences = Mock()
        self.app._error = Mock()
        self.app.status_text = Var("")
        self.app.bindings_tree = Mock()
        self.app.bindings_tree.selection.return_value = ("reshade",)
        self.app.binding_key = Var("Mouse5")
        self.app.binding_ctrl = Var(False)
        self.app.binding_alt = Var(False)
        self.app.binding_shift = Var(False)

    def test_reshade_binding_row_preserves_capture_and_runtime_path(self):
        self.app._save_selected_binding()
        saved = self.app._persist_preferences.call_args.args[0]
        self.assertEqual(saved.reshade_binding, EditorBinding("Mouse5"))
        self.assertEqual(saved.capture_binding, DEFAULT_BINDING)
        self.assertEqual(saved.reshade_runtime_path, self.app.app_settings.reshade_runtime_path)
        self.assertNotIn("reshade", saved.action_bindings)

    def test_conflicting_reshade_binding_does_not_save(self):
        self.app.binding_key.set("F5")
        self.app._save_selected_binding()
        self.app._persist_preferences.assert_not_called()
        self.app._error.assert_called_once()

    def test_default_reset_avoids_invalid_intermediate_bindings(self):
        bindings = dict(self.app.app_settings.action_bindings)
        bindings["move_forward"] = None
        self.app.app_settings = self.app.app_settings.with_action_bindings(bindings).with_reshade_binding(EditorBinding("W"))
        with patch("dolly.gui.messagebox.askyesno", return_value=True):
            self.app._reset_editor_bindings()
        saved = self.app._persist_preferences.call_args.args[0]
        self.assertEqual(saved.reshade_binding, EditorBinding("F11"))
        self.assertEqual(saved.action_bindings["move_forward"], EditorBinding("W"))

    def test_autoload_attempts_selected_runtime_once_per_ready_bridge(self):
        bridge = Mock()
        bridge.media_status.return_value = {"reshade": {"state": 0}}
        self.app.controller = Mock()
        self.app.controller._native_bridge.return_value = bridge
        self.app.reshade_status_text = Var("")
        self.app.reshade_disable_button = Mock()
        self.app._configure_reshade = Mock()
        self.app._refresh_reshade(READY)
        self.app._refresh_reshade(READY)
        self.app._configure_reshade.assert_called_once_with(automatic=True)

    def test_reshade_status_failure_is_contained_and_logged_once(self):
        bridge = Mock()
        bridge.media_status.side_effect = RuntimeError("Unsupported native media status")
        self.app.controller = Mock()
        self.app.controller._native_bridge.return_value = bridge
        self.app.reshade_status_text = Var("")
        self.app.reshade_disable_button = Mock()
        self.app.reshade_configure_button = Mock()
        self.app._configure_reshade = Mock()
        self.app._log = Mock()
        self.app._refresh_reshade(READY)
        self.app._refresh_reshade(READY)
        self.assertIn("Unsupported native media status", self.app.reshade_status_text.get())
        self.app._log.assert_called_once()
        self.app._configure_reshade.assert_not_called()
        self.app.reshade_disable_button.configure.assert_called_with(state="disabled")
        bridge.media_status.side_effect = None
        bridge.media_status.return_value = {"reshade": {"state": 2, "open": False}}
        self.app._refresh_reshade(READY)
        self.assertIn("ReShade ready", self.app.reshade_status_text.get())

    def test_forget_disables_current_runtime_and_saves_no_future_autoload(self):
        self.app.controller = Mock()
        bridge = self.app.controller._native_bridge.return_value
        self.app.video_export = Mock()
        self.app.video_export.status.return_value = {"state": "idle"}
        self.app._submit = Mock(return_value=True)
        self.app.reshade_runtime_path = Var(self.app.app_settings.reshade_runtime_path)
        self.app.reshade_status_text = Var("")
        self.app._log = Mock()
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "ReShade64.dll"
            runtime.write_bytes(b"existing runtime")
            self.app.app_settings = self.app.app_settings.__class__(reshade_runtime_path=str(runtime))
            self.app._forget_reshade()
            job = self.app._submit.call_args.args
            with patch("dolly.gui.save_settings") as save:
                result = job[1]()
                job[2](result)
            bridge.disable_reshade.assert_called_once()
            self.assertEqual(save.call_args.args[0].reshade_runtime_path, "")
            self.assertEqual(self.app.reshade_runtime_path.get(), "")
            self.assertEqual(self.app.app_settings.reshade_runtime_path, "")
            self.assertEqual(runtime.read_bytes(), b"existing runtime")

    def test_forget_while_recording_cannot_change_effects_or_settings(self):
        self.app.video_export = Mock()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._submit = Mock()
        self.app._forget_reshade()
        self.app._submit.assert_not_called()
        self.app._error.assert_called_once()


class MediaPollIsolationTests(unittest.TestCase):
    def setUp(self):
        self.app = app = DollyApp.__new__(DollyApp)
        app.closed = False
        app.events = queue.Queue()
        app.controller = Mock()
        app.controller.status.return_value = {**READY, "tick": 128}
        app.busy = app.playing = app.dragging = False
        app.startup_cancel = None
        app.native_editor_active = False
        app.capture_buttons = []
        app._last_controller_message = ""
        for name in ("session_text", "startup_progress", "status_text", "busy_text", "video_status_text", "hotkey_label"):
            setattr(app, name, Var(""))
        for name in ("play_replay_button", "cancel_startup_button", "speed_combo", "rate_combo", "camera_driver_combo",
                     "smoothing_combo", "aspect_curve", "launch_button", "connect_button", "initialize_button",
                     "load_replay_button", "probe_button", "disconnect_button", "capture_hotkey_checkbox", "root",
                     "_poll_paused_camera", "_log", "_error", "_check_capture_listener", "_capture_binding_label"):
            setattr(app, name, Mock())
        self.editor_poll = patch("dolly.gui.editor_session.poll").start()
        self.addCleanup(patch.stopall)

    def test_optional_media_failure_preserves_session_tick_controls_and_editor_poll(self):
        self.app._refresh_video = Mock(side_effect=RuntimeError("Bad media protocol"))
        self.app._poll()
        self.app.controller.status.return_value["tick"] = 256
        self.app._poll()
        self.assertEqual(self.app.session_text.get(), "Paused editor ready")
        self.assertEqual(self.app.busy_text.get(), "Replay tick 256")
        self.assertEqual(self.editor_poll.call_count, 2)
        self.assertEqual(self.app.root.after.call_count, 2)
        self.app._log.assert_called_once_with("Media status unavailable: Bad media protocol")
        self.app.play_replay_button.configure.assert_called_with(state="normal")

    def test_start_timeout_releases_busy_and_keeps_finish_discard_available(self):
        app = self.app
        app.busy = True
        app.events.put(("error", "Starting video recording", RuntimeError("Start timed out")))
        app.video_export = Mock()
        app.video_export.status.return_value = {"state": "starting", "message": "Start not confirmed."}
        app._refresh_reshade = Mock()
        for name in ("video_start_button", "video_stop_button", "video_cancel_button", "video_path_entry", "video_browse_button",
                     "video_fps_combo", "video_bitrate_combo", "reshade_configure_button", "reshade_forget_button"):
            setattr(app, name, Mock())
        app._poll()
        self.assertFalse(app.busy)
        app.video_start_button.configure.assert_called_with(state="disabled")
        app.video_stop_button.configure.assert_called_with(state="normal")
        app.video_cancel_button.configure.assert_called_with(state="normal")
        self.editor_poll.assert_called_once()


if __name__ == "__main__":
    unittest.main()
