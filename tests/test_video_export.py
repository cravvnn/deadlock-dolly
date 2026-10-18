"""Video lifecycle and output guards without a Windows encoder or display."""
from datetime import datetime
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.path import Keyframe, Project
from dolly.gui import DollyApp
from dolly.bindings import DEFAULT_BINDING
from dolly.editor_actions import EditorBinding
from dolly.settings import AppSettings
from dolly.video_export import (VideoExport, VideoOptions, default_video_path, format_video_status,
                                recording_ready, unsupported_environment_reason, video_suffix)


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

    def test_lossless_codec_requires_mkv_and_others_mp4(self):
        from dolly.video_export import resolve_container
        self.assertEqual(video_suffix("lossless", "mp4"), ".mkv")
        self.assertEqual(video_suffix("auto"), ".mp4")
        self.assertEqual(video_suffix("h264_nvenc", "mkv"), ".mkv")
        self.assertEqual(video_suffix("h264_nvenc", "mov"), ".mov")
        self.assertEqual(resolve_container("auto", "mkv", ffmpeg_available=False), "mp4")
        self.assertEqual(resolve_container("auto", "mkv", ffmpeg_available=True), "mkv")
        self.assertEqual(resolve_container("builtin", "mov"), "mp4")

    def test_valid_start_calls_only_native_recording_with_explicit_options(self):
        result = self.export.start(VideoOptions(self.path, 30, 10_000_000))
        self.assertEqual(result["state"], "recording")
        self.bridge.start_video.assert_called_once_with(
            str(self.path), fps=30, bitrate=10_000_000,
            encoder=0, codec=0, quality=0, preset=0, ffmpeg_path="", fixed_step=False,
            depth=False, depth_exr=False, shot_only=False, white_clear=False)
        self.assertFalse(self.path.exists())  # Only native creates the actual file.
        self.controller.play.assert_not_called()
        self.controller._request.assert_not_called()
        self.controller.set_export_resolution.assert_not_called()

    def test_depth_capture_is_refused_under_wine_before_a_take_starts(self):
        options = VideoOptions(self.path, 60, 20_000_000, depth=True)
        with patch("dolly.video_export.wine_version", return_value="wine-11.0"):
            self.assertIn("not supported under Wine", unsupported_environment_reason(options))
        with patch("dolly.video_export.wine_version", return_value=""):
            self.assertEqual(unsupported_environment_reason(options), "")
        with patch("dolly.video_export.wine_version", return_value="wine-11.0"):
            plain = VideoOptions(self.path, 60, 20_000_000)
            self.assertEqual(unsupported_environment_reason(plain), "")

    def test_bounded_export_preserves_limit_through_validation_and_native_start(self):
        options = VideoOptions(self.path, max_frames=2, fixed_step=True, shot_only=True)
        self.assertEqual(options.validated().max_frames, 2)
        self.export.start(options)
        self.assertEqual(self.bridge.start_video.call_args.kwargs['max_frames'], 2)
        self.assertTrue(self.bridge.start_video.call_args.kwargs['shot_only'])
        for value in (-1, True, 1.5, 65536):
            with self.subTest(value=value), self.assertRaises(ValueError):
                VideoOptions(self.path, max_frames=value).validated()

    def test_camera_path_export_skips_setup_frames_without_changing_selected_timing(self):
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                    Keyframe(1, 10, 0, 0, 0, 0, 0)])
        self.export.start(VideoOptions(self.path, fps=120, speed=.5, fixed_step=True,
                                       max_frames=30), project=project)
        self.controller.prepare_native_recording.assert_called_once_with(project, frozen=False)
        self.controller.set_export_timing.assert_called_once_with(120, .5)
        self.assertTrue(self.bridge.start_video.call_args.kwargs["shot_only"])
        self.assertEqual(self.bridge.start_video.call_args.kwargs["max_frames"], 30)

    def test_timed_bone_take_reaches_native_preparation_without_changing_the_authored_camera(self):
        import copy
        from dolly.bone_picker import timed_attachment
        from dolly.path import AttachKey
        authored = Project(start_tick=128, tick_rate=64, keyframes=[Keyframe(
            0, 1, 2, 3, 4, 5, 6, source="attach", attach=AttachKey(
                handle=11, entity_id=11, model="models/heroes/astro/astro.vmdl",
                point="bone", bone="head", smoothing=.2))])
        original = copy.deepcopy(authored)
        for seconds in (2, 15):
            take = timed_attachment(authored, seconds)
            self.export.start(VideoOptions(self.path, shot_only=True), project=take)
            prepared = self.controller.prepare_native_recording.call_args.args[0]
            self.assertEqual(prepared, take)
            self.assertEqual(prepared.duration, seconds)
            self.assertEqual([key.time for key in prepared.keyframes], [0, seconds])
            self.assertEqual(authored, original)
            self.assertTrue(self.bridge.start_video.call_args.kwargs["shot_only"])
            self.export.stop()

    def test_frozen_recording_keeps_its_existing_current_scene_admission(self):
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                    Keyframe(1, 10, 0, 0, 0, 0, 0)])
        self.export.start(VideoOptions(self.path), project=project, frozen=True)
        self.assertFalse(self.bridge.start_video.call_args.kwargs["shot_only"])

    def test_audio_take_places_video_inside_its_export_folder(self):
        self.export.start(VideoOptions(self.path, bundle_audio=True))
        folder = self.path.with_suffix("")
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.export.output_path, folder / self.path.name)
        self.assertEqual(self.export.output_directory, self.path.parent)
        self.assertEqual(Path(self.bridge.start_video.call_args.args[0]), folder / self.path.name)

    def test_fixed_step_audio_uses_movie_wav_timing(self):
        self.export.start(VideoOptions(self.path, fixed_step=True, bundle_audio=True))
        self.controller.set_export_timing.assert_called_once_with(60, 1.0)
        self.assertTrue(self.bridge.start_video.called)
        self.assertTrue(self.path.with_suffix("").is_dir())

    def test_players_low_space_rejected_before_any_take_or_replay_preparation(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.controller.deployment_directory.return_value = self.path.parent
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                     Keyframe(2.734, 0, 0, 0, 0, 0, 0)])
        options = VideoOptions(self.path, fps=600, speed=.5, fixed_step=True,
                               depth=True, layers=("world", "players"), ffmpeg_path=ffmpeg)
        with patch("dolly.video_export.client_size", return_value=(2560, 1440)), \
             patch("dolly.player_layer.shutil.disk_usage",
                   return_value=SimpleNamespace(free=int(38.4 * 1024**3))):
            with self.assertRaisesRegex(RuntimeError, "temporary space"):
                self.export.start(options, project)
        self.controller.prepare_native_recording.assert_not_called()
        self.controller.set_export_timing.assert_not_called()
        self.bridge.start_video.assert_not_called()
        self.assertFalse(self.path.with_suffix("").exists())

    def test_players_preflight_includes_slow_motion_and_endpoint_margin(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.controller.deployment_directory.return_value = self.path.parent
        project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                     Keyframe(2, 0, 0, 0, 0, 0, 0)])
        options = VideoOptions(self.path, fps=600, speed=.5, fixed_step=True,
                               layers=("world", "players"), ffmpeg_path=ffmpeg)
        with patch("dolly.video_export.client_size", return_value=(2560, 1440)), \
             patch("dolly.player_layer.check_capture_space") as check:
            self.export.start(options, project)
        check.assert_called_once_with(self.path.parent, 2560, 1440, 2402)
        self.bridge.start_video.assert_called_once()

    def test_completed_take_attaches_validation_findings(self):
        folder = self.path.with_suffix("")
        depth = folder / "depth"
        depth.mkdir(parents=True)
        (depth / "depth.mov").write_bytes(b"depth")
        (depth / "manifest.json").write_text(json.dumps(
            {"frames": 0, "complete": True, "master": "depth.mov"}), encoding="utf-8")
        self.export._layer_folder = folder
        status = self.export._audit_take({"state": "completed"})
        self.assertFalse(status["audit"]["ok"])
        self.assertTrue(any("zero frames" in finding for finding in status["audit"]["findings"]))

    def test_take_audit_without_a_layer_folder_leaves_status_unchanged(self):
        self.export._layer_folder = None
        status = {"state": "completed"}
        self.assertIs(self.export._audit_take(status), status)
        self.assertNotIn("audit", status)

    def test_depth_resolution_is_set_after_preparation_and_restored_on_finish(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        calls = []
        self.controller.prepare_native_recording.side_effect = lambda *a, **k: calls.append("prepare")
        self.controller.set_export_resolution.side_effect = lambda: calls.append("resolution")
        options = VideoOptions(self.path, depth=True, ffmpeg_path=ffmpeg).validated()
        self.assertTrue(options.full_resolution)
        self.export.start(options, before_record=lambda: calls.append("capture"))
        self.assertEqual(calls, ["prepare", "resolution", "capture"])
        with patch.object(self.export, "_encode_depth_preview", side_effect=lambda status: status):
            self.export.stop()
        self.controller.clear_export_resolution.assert_called_once()

    def test_resolution_rejection_restores_without_starting_encoder(self):
        self.controller.set_export_resolution.side_effect = RuntimeError("render scale rejected")
        with self.assertRaisesRegex(RuntimeError, "render scale rejected"):
            self.export.start(VideoOptions(self.path, full_resolution=True))
        self.bridge.start_video.assert_not_called()
        self.controller.clear_export_resolution.assert_called_once()

    def test_depth_native_completion_still_finishes_preview_metadata_and_restoration_once(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.export.start(VideoOptions(self.path, depth=True, fixed_step=True,
                                       max_frames=30, ffmpeg_path=ffmpeg))
        folder = self.path.with_suffix("")
        depth = folder / "depth"
        depth.mkdir()
        (depth / "preview_64x64.raw").write_bytes(b"x" * 4096)
        sidecar = Path(str(self.export.output_path) + ".shot.json")
        sidecar.write_text('{"frames_written":30}', encoding="utf-8")
        # The hard frame cap can finish the encoder before the worker calls Stop.
        self.state = {"state": "completed", "frames_written": 30}
        with self.assertRaisesRegex(RuntimeError, "Finish the current recording"):
            self.export.start(VideoOptions(self.path.parent / "next.mp4"))
        self.assertEqual(self.bridge.start_video.call_count, 1)

        def encode(args, **kwargs):
            Path(args[-1]).write_bytes(b"preview")
            return SimpleNamespace(returncode=0, stderr="")

        with patch("dolly.video_export.subprocess.run", side_effect=encode) as encoder:
            result = self.export.stop()
            again = self.export.stop()
        self.assertTrue((folder / "shot.json").is_file())
        self.assertEqual((folder / "shot.json").read_text(encoding="utf-8"), '{"frames_written":30}')
        self.assertFalse(sidecar.exists())
        self.assertEqual(result["depth_preview"], str(depth / "preview.mp4"))
        self.assertEqual(again["depth_preview"], result["depth_preview"])
        self.assertEqual(encoder.call_count, 1)
        self.assertFalse(self.export.finish_pending)
        self.bridge.stop_video.assert_not_called()
        self.controller.clear_export_timing.assert_called()
        self.controller.clear_export_resolution.assert_called()

    def test_cancelled_native_depth_does_not_encode_preview(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.export.start(VideoOptions(self.path, depth=True, ffmpeg_path=ffmpeg))
        self.state = {"state": "cancelled"}
        with patch.object(self.export, "_encode_depth_preview") as encode:
            self.assertEqual(self.export.stop()["state"], "cancelled")
        encode.assert_not_called()
        self.assertFalse(self.export.finish_pending)
        self.controller.clear_export_resolution.assert_called()

    def test_completed_output_waits_for_restoration_retry_without_reencoding(self):
        self.export.start(VideoOptions(self.path, full_resolution=True))
        self.state = {"state": "completed"}
        self.controller.clear_export_resolution.side_effect = [RuntimeError("restore failed"), None]
        with patch.object(self.export, "_encode_depth_preview", side_effect=lambda s: s) as encode:
            with self.assertRaisesRegex(RuntimeError, "restore failed"):
                self.export.stop()
            self.assertTrue(self.export.finish_pending)
            self.assertEqual(self.export.stop()["state"], "completed")
        encode.assert_called_once()
        self.assertFalse(self.export.finish_pending)

    def test_timing_restore_failure_keeps_take_pending_and_still_restores_resolution(self):
        self.export.start(VideoOptions(self.path, fixed_step=True, full_resolution=True))
        self.state = {"state": "completed"}
        self.controller.clear_export_timing.side_effect = [RuntimeError("timing restore failed"), None]
        with patch.object(self.export, "_encode_depth_preview", side_effect=lambda s: s) as encode:
            with self.assertRaisesRegex(RuntimeError, "timing restore failed"):
                self.export.stop()
            self.controller.clear_export_resolution.assert_called_once()
            self.assertTrue(self.export.finish_pending)
            with self.assertRaisesRegex(RuntimeError, "Finish the current recording"):
                self.export.start(VideoOptions(self.path.parent / "next.mp4"))
            self.assertEqual(self.export.stop()["state"], "completed")
        encode.assert_called_once()
        self.assertFalse(self.export.finish_pending)

    def test_failed_preparation_timing_restore_blocks_replacement_before_game_changes(self):
        self.controller._export_timing = {"restore_pending": True, "restore": {"host_framerate": 0}}
        self.assertTrue(self.export.finish_pending)
        with self.assertRaisesRegex(RuntimeError, "Finish the current recording"):
            self.export.start(VideoOptions(self.path))
        self.controller.prepare_native_recording.assert_not_called()
        self.bridge.start_video.assert_not_called()
        self.controller.clear_export_timing.side_effect = lambda: setattr(self.controller, "_export_timing", None)
        self.export.stop()
        self.assertFalse(self.export.finish_pending)

    def test_discard_restores_resolution_but_rejected_second_start_does_not(self):
        self.export.start(VideoOptions(self.path, full_resolution=True))
        with self.assertRaisesRegex(RuntimeError, "current recording"):
            self.export.start(VideoOptions(self.path.parent / "next.mp4"))
        self.controller.clear_export_resolution.assert_not_called()
        self.export.stop(cancel=True)
        self.controller.clear_export_resolution.assert_called_once()

    def test_high_frame_rates_are_accepted(self):
        for fps in (300, 600):
            with self.subTest(fps=fps):
                VideoOptions(self.path, fps, 20_000_000).validated()

    def test_120_fps_is_sent_to_native_without_changing_playback(self):
        self.export.start(VideoOptions(self.path, 120, 40_000_000))
        self.bridge.start_video.assert_called_once_with(
            str(self.path), fps=120, bitrate=40_000_000,
            encoder=0, codec=0, quality=0, preset=0, ffmpeg_path="", fixed_step=False,
            depth=False, depth_exr=False, shot_only=False, white_clear=False)
        self.controller.play.assert_not_called()
        self.controller._request.assert_not_called()

    def test_filter_is_applied_after_preparation_before_encoder(self):
        calls = []
        self.controller.prepare_native_recording.side_effect = lambda *a, **k: calls.append("prepare")
        original = self.bridge.start_video.side_effect
        def start(*a, **k):
            calls.append("encode"); return original(*a, **k)
        self.bridge.start_video.side_effect = start
        self.export.start(VideoOptions(self.path), before_record=lambda: calls.append("filter"))
        self.assertEqual(calls, ["prepare", "filter", "encode"])

    def test_before_record_failure_removes_the_just_created_take_folder(self):
        ffmpeg = self.path.parent / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        options = VideoOptions(self.path, depth=True, ffmpeg_path=ffmpeg).validated()
        folder = self.path.with_suffix("")

        def explode():
            raise RuntimeError("filter failed")

        with self.assertRaisesRegex(RuntimeError, "filter failed"):
            self.export.start(options, before_record=explode)
        self.assertFalse(folder.exists())
        self.bridge.start_video.assert_not_called()
        self.controller.set_export_timing.assert_not_called()

    def test_pov_prepares_game_view_and_restores_after_capture_stops(self):
        project = object()
        self.export.start(VideoOptions(self.path, shot_only=True), project=project, pov=True)
        self.controller.prepare_pov_recording.assert_called_once_with(project)
        self.controller.prepare_native_recording.assert_not_called()
        self.assertTrue(self.bridge.start_video.call_args.kwargs["shot_only"])
        self.export.stop()
        self.controller.finish_pov_recording.assert_called_once()

    def test_pov_start_failure_restores_hud_and_timing(self):
        self.controller.set_export_timing.side_effect = RuntimeError("Timing rejected")
        with self.assertRaisesRegex(RuntimeError, "Timing rejected"):
            self.export.start(VideoOptions(self.path, fixed_step=True), project=object(), pov=True)
        self.controller.finish_pov_recording.assert_called_once()
        self.controller.clear_export_timing.assert_called()
        self.bridge.start_video.assert_not_called()

    def test_capture_only_reserves_playback_without_an_mp4_encoder(self):
        from dolly import player_layer
        self.controller.deployment_directory.return_value = Path(self.folder.name)
        armed = Mock(side_effect=lambda: player_layer.begin_capture(Path(self.folder.name), 48, 8))
        result = self.export.start(VideoOptions(self.path, fps=600, fixed_step=True),
                                   before_record=armed, capture_only=True)
        self.assertEqual(result["state"], "recording")
        armed.assert_called_once_with()
        self.bridge.start_video.assert_not_called()
        self.controller.mark_recording_pending.assert_called_once_with(True, player_layer=True)
        self.assertEqual(self.export.output_path, self.path.with_suffix(".mov"))
        self.assertEqual(self.export.stop()["state"], "completed")
        self.bridge.stop_video.assert_not_called()
        self.assertTrue((Path(self.folder.name)/"dolly_owner_stop.txt").is_file())
        self.controller.mark_recording_pending.assert_called_with(False)

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

    def test_missing_ffmpeg_encoder_is_rejected_before_native_start(self):
        exe = self.path.parent / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        with patch("dolly.video_export.ffmpeg_encoder_names",
                   return_value=frozenset({"h264_nvenc", "ffv1"})):
            with self.assertRaisesRegex(ValueError, "libx264"):
                VideoOptions(self.path, codec="libx264", ffmpeg_path=exe).validated()
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
            encoder=1, codec=1, quality=21, preset=0, ffmpeg_path=str(exe), fixed_step=False,
            depth=False, depth_exr=False, shot_only=False, white_clear=False)

    def test_depth_master_defaults_off_and_layered_take_creates_its_folder(self):
        self.assertFalse(VideoOptions(self.path).validated().depth)
        exe = self.path.parent / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.export.start(VideoOptions(self.path, 60, 20_000_000, depth=True,
                                       ffmpeg_path=exe))
        folder = self.path.with_suffix("")
        self.assertTrue(folder.is_dir())
        self.assertEqual(self.bridge.start_video.call_args.args[0],
                         str(folder / self.path.name))
        self.assertTrue(self.bridge.start_video.call_args.kwargs["depth"])
        self.assertTrue(self.bridge.start_video.call_args.kwargs["shot_only"])
        self.assertFalse(self.bridge.start_video.call_args.kwargs["depth_exr"])
        with self.assertRaisesRegex(ValueError, "Depth export"):
            VideoOptions(self.path, depth=1).validated()

    def test_depth_master_requires_ffmpeg_and_a_new_folder(self):
        with self.assertRaisesRegex(ValueError, "FFmpeg encoder"):
            VideoOptions(self.path, 60, 20_000_000, depth=True, codec="builtin").validated()
        exe = self.path.parent / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.path.with_suffix("").mkdir()
        with self.assertRaisesRegex(ValueError, "folder"):
            VideoOptions(self.path, 60, 20_000_000, depth=True, ffmpeg_path=exe).validated()

    def test_depth_exr_requires_the_depth_master(self):
        with self.assertRaisesRegex(ValueError, "depth master"):
            VideoOptions(self.path, depth_exr=True).validated()

    def depth_preview_fixture(self, codec="h264_nvenc"):
        directory = self.path.parent / "depth"
        directory.mkdir(exist_ok=True)
        (directory / "preview_64x64.raw").write_bytes(b"x" * 4096)
        exe = self.path.parent / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.export.output_path = self.path
        self.export._depth_options = VideoOptions(self.path, 60, 20_000_000, codec=codec,
                                                  ffmpeg_path=exe, depth=True)
        return directory

    def test_depth_preview_encodes_the_raw_stream_with_the_chosen_codec(self):
        directory = self.depth_preview_fixture()
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            Path(args[-1]).write_bytes(b"mp4")
            return SimpleNamespace(returncode=0, stderr="")

        with patch("dolly.video_export.subprocess.run", side_effect=fake_run):
            status = self.export._encode_depth_preview({"state": "completed"})
        self.assertEqual(status["depth_preview"], str(directory / "preview.mp4"))
        self.assertIn("h264_nvenc", captured["args"])
        self.assertIn("-framerate", captured["args"])
        self.assertIn("60", captured["args"])
        self.assertFalse((directory / "preview_64x64.raw").exists())

    def test_depth_preview_uses_ffv1_and_mkv_for_lossless(self):
        directory = self.depth_preview_fixture(codec="lossless")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            Path(args[-1]).write_bytes(b"mkv")
            return SimpleNamespace(returncode=0, stderr="")

        with patch("dolly.video_export.subprocess.run", side_effect=fake_run):
            status = self.export._encode_depth_preview({"state": "completed"})
        self.assertTrue(status["depth_preview"].endswith("preview.mkv"))
        self.assertIn("ffv1", captured["args"])
        self.assertNotIn("-b:v", captured["args"])

    def test_depth_preview_failure_keeps_the_raw_source_intact(self):
        directory = self.depth_preview_fixture()
        with patch("dolly.video_export.subprocess.run",
                   return_value=SimpleNamespace(returncode=1, stderr="encoder exploded")):
            status = self.export._encode_depth_preview({"state": "completed"})
        self.assertNotIn("depth_preview", status)
        self.assertTrue((directory / "preview_64x64.raw").exists())
        self.assertFalse((directory / "preview.mp4").exists())

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

    def test_auto_codec_uses_the_detected_vendor_encoder(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "ffmpeg.exe"
            exe.write_bytes(b"MZ")
            with patch("dolly.encoder_select.detect_vendor", return_value="amd"):
                self.export.start(VideoOptions(self.path, 60, 20_000_000, ffmpeg_path=exe))
        self.assertEqual(self.bridge.start_video.call_args.kwargs["encoder"], 1)
        self.assertEqual(self.bridge.start_video.call_args.kwargs["codec"], 8)
        self.assertIn("AMD H.264 (AMF)", self.export.status()["codec"])

    def test_auto_codec_without_a_known_gpu_uses_media_foundation(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "ffmpeg.exe"
            exe.write_bytes(b"MZ")
            with patch("dolly.encoder_select.detect_vendor", return_value=""):
                self.export.start(VideoOptions(self.path, 60, 20_000_000, ffmpeg_path=exe))
        self.assertEqual(self.bridge.start_video.call_args.kwargs["encoder"], 1)
        self.assertEqual(self.bridge.start_video.call_args.kwargs["codec"], 3)

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

    def test_finish_waits_as_long_as_the_native_finalizer(self):
        # The native encoder waits up to 120 s for FFmpeg to finalize a large
        # take; the desktop wait must not report "still finishing" sooner.
        import inspect
        default = inspect.signature(self.export.stop).parameters["timeout"].default
        self.assertGreaterEqual(default, 120.0)
        self.export.start(VideoOptions(self.path))
        clock = {"now": 0.0}
        self.bridge.stop_video.side_effect = lambda **_: self.state.update(state="finalizing")

        def status():
            clock["now"] += 60.0  # Two +60 s polls is under the native budget.
            return {"state": "finalizing"} if clock["now"] < 130.0 else {"state": "completed"}
        self.bridge.video_status.side_effect = status
        with patch("dolly.video_export.time.monotonic", side_effect=lambda: clock["now"]), \
             patch("dolly.video_export.time.sleep"):
            self.assertEqual(self.export.stop()["state"], "completed")

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
        self.app.video_game_audio = Var(False)
        self.app.video_reconstructed_audio = Var(False)
        self.app.clip_audio = None
        self.app.video_codec = Var("")
        self.app.video_format = Var("MP4 video")
        self.app.ffmpeg_path = Var("")
        self.app.video_fixed_step = Var(False)
        self.app.video_depth = Var(False)
        self.app.video_depth_exr = Var(False)
        self.app.video_layer_world = Var(False)
        self.app.video_layer_players = Var(False)
        self.app.video_layer_effects = Var(False)
        self.app.video_export_speed = Var("1")
        self.app.status_text = Var("")
        self.app.video_status_text = Var("")
        for name in ("video_game_audio_check", "video_reconstructed_audio_check"):
            setattr(self.app, name, Mock())
        self.app._pending_auto_play = False
        self.app.video_export = Mock()
        self.app._submit = Mock(return_value=True)
        self.app._error = Mock()

    @staticmethod
    def two_camera_project():
        return Project(keyframes=[
            Keyframe(time=0.0, x=0, y=0, z=0, pitch=0, yaw=0, roll=0),
            Keyframe(time=1.0, x=100, y=0, z=0, pitch=0, yaw=90, roll=0)])

    def test_desktop_start_returns_focus_to_the_owned_game(self):
        self.app.controller = Mock()
        self.app.controller.game_pid.return_value = 4321
        with patch("dolly.gui.focus_window") as focus:
            self.app._start_video_recording()
        focus.assert_called_once_with(4321)
        self.app._submit.assert_called_once()

    def test_lossless_choice_corrects_a_typed_mp4_output_to_mkv(self):
        ffmpeg = Path(self.folder.name) / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(ffmpeg))
        self.app.video_codec.set("Lossless FFV1 (.mkv)")
        self.app.controller = Mock()
        self.app._start_video_recording()
        self.assertTrue(self.app.video_path.get().endswith(".mkv"))
        self.assertEqual(self.app.video_format.get(), "Matroska video (MKV)")
        self.app._error.assert_not_called()
        self.app._submit.assert_called_once()

    def test_mkv_format_writes_mkv_for_a_lossy_codec(self):
        ffmpeg = Path(self.folder.name) / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(ffmpeg))
        self.app.video_format.set("Matroska video (MKV)")
        self.app.controller = Mock()
        self.app._start_video_recording()
        self.assertTrue(self.app.video_path.get().endswith(".mkv"))
        self.app._error.assert_not_called()
        self.app._submit.assert_called_once()

    def test_format_change_keeps_encoder_and_extension_in_step(self):
        ffmpeg = Path(self.folder.name) / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(ffmpeg))
        self.app.video_format.set("Matroska video (MKV)")
        self.app._format_changed()
        self.assertTrue(self.app.video_path.get().endswith(".mkv"))
        self.app.video_codec.set("Lossless FFV1 (.mkv)")
        self.app.video_format.set("MP4 video")
        self.app._format_changed()
        self.assertEqual(self.app.video_codec.get(), "Auto (hardware when available)")
        self.assertTrue(self.app.video_path.get().endswith(".mp4"))

    def test_export_folder_browse_and_typed_edit_persist_only_parent(self):
        from dolly.settings import load_settings, save_settings
        settings_path = Path(self.folder.name) / 'settings.json'
        first = Path(self.folder.name) / 'Vidéos'
        second = Path(self.folder.name) / 'Other exports'
        first.mkdir(); second.mkdir()
        self.app.app_settings = AppSettings(game_path='D:/Deadlock')
        self.app.root = None
        with patch('dolly.gui.save_settings', side_effect=lambda settings: save_settings(settings, settings_path)), \
                patch('dolly.gui.filedialog.asksaveasfilename', return_value=str(first / 'one.mp4')):
            self.app._browse_video()
            self.assertEqual(load_settings(settings_path).export_folder, str(first))
            self.app.video_path.set(str(second / 'two.mp4'))
            self.app._remember_export_folder()
            self.assertEqual(load_settings(settings_path).export_folder, str(second))
            self.assertEqual(load_settings(settings_path).game_path, 'D:/Deadlock')
            self.app.video_path.set(str(second / 'missing' / 'three.mp4'))
            self.app._remember_export_folder()
            self.assertEqual(load_settings(settings_path).export_folder, str(second))
        self.app._error.assert_not_called()

    def test_export_folder_failed_save_keeps_previous_preference(self):
        self.app.app_settings = AppSettings(export_folder='D:/Previous exports')
        with patch('dolly.gui.save_settings', side_effect=OSError('disk full')):
            self.app._remember_export_folder()
        self.assertEqual(self.app.app_settings.export_folder, 'D:/Previous exports')
        self.app._error.assert_called_once()

    def test_reconstructed_audio_offers_one_time_helper_download(self):
        self.app.root = Mock()
        self.app.video_reconstructed_audio.set(True)
        with patch("dolly.gui.find_viewer", return_value=None), \
                patch("dolly.gui.ask_reconstructed_helper", return_value=False) as prompt:
            self.app._start_video_recording()
        prompt.assert_called_once()
        self.app._submit.assert_not_called()
        self.assertIn("cancelled", self.app.status_text.get())

    def test_fixed_step_audio_starts_a_movie_wave_take(self):
        self.app.game_path = Var("C:/Deadlock/game/bin/win64/deadlock.exe")
        self.app.project = self.two_camera_project()
        self.app.controller = Mock()
        self.app.controller.game_pid.return_value = 4321
        self.app.controller._native_bridge.return_value = Mock()
        self.app.video_fixed_step.set(True)
        self.app.video_game_audio.set(True)
        audio = Mock()
        with patch("dolly.gui.ClipAudioCapture", return_value=audio), \
                patch("dolly.gui.validate_game") as validate, \
                patch("dolly.gui.focus_window"):
            validate.return_value = Mock(executable=Path("C:/Deadlock/game/bin/win64/deadlock.exe"))
            self.app._start_video_recording()
            self.app._submit.call_args.args[1]()
        self.app._submit.assert_called_once()
        self.app.video_export.start.assert_called_once()
        self.assertEqual(self.app.video_export.start.call_args.kwargs["before_record"],
                         audio.start_movie)

    def test_recording_with_a_shot_auto_plays_after_arming(self):
        self.app.project = self.two_camera_project()
        self.app._play = Mock()
        self.app._start_video_recording()
        self.assertTrue(self.app._pending_auto_play)
        self.app._submit.call_args.args[1]()
        self.app._video_operation_done({"state": "starting"})
        self.app._play.assert_not_called()
        self.assertTrue(self.app._pending_auto_play)
        self.app._video_operation_done({"state": "recording"})
        self.app._play.assert_called_once()
        self.assertFalse(self.app._pending_auto_play)

    def write_recorded_camera(self, frames=61, *, width=1920, height=1080, first_qpc=123456):
        from dolly.camera_export import CAMERA_FIELDS_V2
        rows = [[i, i/60, i/60, i, 0, 64, 0, 90, 0, 100, i/60] for i in range(frames)]
        path = Path(self.folder.name) / "shot.mp4.camera-track.json"
        path.write_text(json.dumps(dict(format="deadlock-dolly-camera-track", version=2, fps=60,
            fixed_step=True, complete=True, source="native-view-hook", depth=False,
            frames_written=frames, first_qpc=first_qpc, qpc_frequency=10000000,
            width=width, height=height, fields=CAMERA_FIELDS_V2, frames=rows)))
        return dict(state="completed", frames_written=frames, fps=60, width=width, height=height,
                    video_first_qpc=first_qpc, qpc_frequency=10000000)

    def finish_color_take(self, status=None, *, camera=True):
        self.app._layer_queue = []
        self.app._active_layer_take = None
        self.app.video_export.output_path = Path(self.folder.name) / "shot.mp4"
        self.app.video_export.output_directory = Path(self.folder.name)
        self.app.video_export.finish_pending = False
        self.app._log = Mock()
        status = status or (self.write_recorded_camera() if camera else {"state": "completed"})
        self.app._video_operation_done(status)
        # _submit enqueues work; exercise the actual operation and UI callback.
        if self.app._submit.call_args and self.app._submit.call_args.args[0] == "Exporting recorded camera":
            _, work, done = self.app._submit.call_args.args
            done(work())

    def test_completed_take_waits_for_worker_before_consuming_camera_exports(self):
        self.app.busy = True
        self.app._last_video_state = "recording"
        self.app.video_export.status.return_value = {"state": "completed"}
        self.app._ae_camera_take = (self.two_camera_project(), 60, 1, False)
        self.app._video_operation_done = Mock()
        self.app._refresh_video({})
        self.app._video_operation_done.assert_not_called()
        self.assertIsNotNone(self.app._ae_camera_take)
        self.assertEqual(self.app._last_video_state, "recording")

    def test_ae_camera_toggle_saves_the_recorded_shot_beside_the_video(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        self.app.project.keyframes[-1].x = 999
        self.finish_color_take()
        script = Path(self.folder.name) / "shot_camera.jsx"
        self.assertIn("var FRAMES = 61;", script.read_text("ascii"))
        self.assertNotIn("999", script.read_text("ascii"))
        self.app._log.assert_any_call("After Effects camera saved to " + str(script))
        self.assertIsNone(self.app._ae_camera_take)

    def test_camera_export_refuses_authored_fallback_when_recording_has_no_data(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        self.finish_color_take(camera=False)
        self.assertFalse((Path(self.folder.name) / "shot_camera.jsx").exists())
        self.assertIn("video is preserved", self.app._log.call_args.args[0])

    def test_camera_export_refuses_stale_recording_identity(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        status = self.write_recorded_camera()
        self.finish_color_take(dict(status, video_first_qpc=999))
        self.assertFalse((Path(self.folder.name) / "shot_camera.jsx").exists())
        self.assertIn("different recording", self.app._log.call_args.args[0])

    def test_camera_exports_match_the_recorded_frame_size(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app.video_usd_camera = Var(True)
        for width, height, aperture in ((2560,1080,"15.1875"), (1920,1200,"22.5"),
                                        (1440,1080,"27"), (3440,1440,"15.069767")):
            with self.subTest(size=(width,height)):
                self.app._start_video_recording()
                self.finish_color_take(self.write_recorded_camera(width=width, height=height))
                folder = Path(self.folder.name)
                self.assertIn(f"var COMP_WIDTH = {width};", (folder / "shot_camera.jsx").read_text("ascii"))
                self.assertIn(f"var COMP_HEIGHT = {height};", (folder / "shot_camera.jsx").read_text("ascii"))
                self.assertIn(f"float verticalAperture = {aperture}\n", (folder / "shot_camera.usda").read_text("ascii"))
                # Each take owns new filenames; tests reuse a temporary fixture folder.
                (folder / "shot_camera.jsx").unlink()
                (folder / "shot_camera.usda").unlink()

    def start_pov_take(self):
        self.app.project = Project()
        self.app.video_source = Var("Player POV")
        self.app.video_pov_duration = Var("5")
        self.app.controller = Mock()
        self.app.controller.status.return_value = {"tick": 640, "game_ui_visible": True}
        self.app.video_ae_camera = Var(True)
        self.app.video_usd_camera = Var(True)
        self.app._start_video_recording()
        self.app._error.assert_not_called()

    def test_pov_camera_exports_from_the_recorded_camera_data(self):
        self.start_pov_take()
        folder = Path(self.folder.name)
        status = self.write_recorded_camera(frames=3)
        path = folder / "shot.mp4.camera-track.json"
        data = json.loads(path.read_text())
        for i, row in enumerate(data['frames']): row[3] = 900+i
        path.write_text(json.dumps(data))
        self.finish_color_take(status)
        script = (folder / "shot_camera.jsx").read_text("ascii")
        self.assertIn('var SHOT_NAME = "Player POV";', script)
        self.assertIn("var FRAMES = 3;", script)
        self.assertIn("var ORIGIN = [900,-64,0];", script)
        self.assertIn("var dollyKeys = [];", script)  # The POV keys are placeholders, never markers.
        layer = (folder / "shot_camera.usda").read_text("ascii")
        self.assertNotIn("Key_", layer)
        self.app._log.assert_any_call("USD camera saved to " + str(folder / "shot_camera.usda"))

    def test_pov_camera_without_camera_data_is_not_saved(self):
        self.start_pov_take()
        self.finish_color_take(camera=False)
        # Never fall back to the placeholder keys, which would export a camera stuck at 0,0,0.
        self.assertEqual(sorted(p.name for p in Path(self.folder.name).glob("*_camera.*")), [])
        self.app._log.assert_any_call(
            "After Effects camera was not saved: Recording saved no camera data; video is preserved")
        self.app._log.assert_any_call(
            "USD camera was not saved: Recording saved no camera data; video is preserved")

    def test_ae_camera_prefers_camera_data(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        self.finish_color_take(self.write_recorded_camera(frames=2))
        text = (Path(self.folder.name) / "shot_camera.jsx").read_text("ascii")
        self.assertIn("var FRAMES = 2;", text)
        self.assertIn("var frameNumbers = [0,1];", text)

    def test_usd_camera_toggle_saves_a_layer_beside_the_video(self):
        self.app.project = self.two_camera_project()
        self.app.video_usd_camera = Var(True)
        self.app._start_video_recording()
        self.finish_color_take()
        layer = Path(self.folder.name) / "shot_camera.usda"
        self.assertTrue(layer.read_text("ascii").startswith("#usda 1.0"))
        self.assertEqual(list(Path(self.folder.name).glob("*.jsx")), [])
        self.app._log.assert_any_call("USD camera saved to " + str(layer))
        self.assertIsNone(self.app._usd_camera_take)

    def test_both_camera_formats_come_from_one_take(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app.video_usd_camera = Var(True)
        self.app._start_video_recording()
        with patch("dolly.camera_export.export_jsx", side_effect=OSError("disk full")):
            self.finish_color_take()
        # One format failing must not stop the other.
        self.app._log.assert_any_call("After Effects camera was not saved: disk full")
        self.assertTrue((Path(self.folder.name) / "shot_camera.usda").is_file())

    def test_ae_camera_is_not_written_when_the_toggle_is_off(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(False)
        self.app._start_video_recording()
        self.finish_color_take()
        self.assertEqual(list(Path(self.folder.name).glob("*.jsx")), [])

    def test_ae_camera_is_dropped_when_the_take_fails(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        self.app._video_operation_done({"state": "failed", "error": "encoder stopped"})
        self.assertIsNone(self.app._ae_camera_take)
        self.finish_color_take()
        self.assertEqual(list(Path(self.folder.name).glob("*.jsx")), [])

    def test_ae_camera_write_failure_is_logged_without_failing_the_take(self):
        self.app.project = self.two_camera_project()
        self.app.video_ae_camera = Var(True)
        self.app._start_video_recording()
        with patch("dolly.camera_export.export_jsx", side_effect=OSError("disk full")):
            self.finish_color_take()
        self.app._log.assert_any_call("After Effects camera was not saved: disk full")
        self.app._error.assert_not_called()

    def test_poll_delivers_recorder_ready_to_pending_playback(self):
        self.app._pending_auto_play = True
        self.app.busy = False
        self.app._last_video_state = "starting"
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._video_operation_done = Mock(side_effect=LookupError("ready delivered"))
        with self.assertRaisesRegex(LookupError, "ready delivered"):
            self.app._refresh_video({})
        self.app._video_operation_done.assert_called_once_with({"state": "recording"})

    def test_picker_inspection_cannot_start_recording(self):
        self.app._bone_picker_context = {"request": 12}
        self.app._start_video_recording()
        self.app._submit.assert_not_called()
        self.assertIn("Finish or cancel", self.app.status_text.get())

    def test_single_bone_camera_records_timed_native_attachment(self):
        from dolly.path import AttachKey
        self.app.project = self.two_camera_project()
        self.app.project.keyframes = self.app.project.keyframes[:1]
        key = self.app.project.keyframes[0]
        key.source = "attach"
        key.attach = AttachKey(handle=11, entity_id=11,
                               model="models/heroes/astro/astro.vmdl", point="bone", bone="head",
                               offset=(4, 0, 6, 0, 0, 0), smoothing=.2)
        self.app.video_pov_duration = Var("5")
        self.app._mark_dirty = Mock()
        self.app._refresh_keys = Mock()
        self.app._play = Mock()
        self.app._start_video_recording()
        self.app._error.assert_not_called()
        self.assertEqual(len(self.app.project.keyframes), 1)
        self.assertEqual(self.app.project.duration, 0)
        self.app._mark_dirty.assert_not_called()
        self.app._submit.call_args.args[1]()
        options = self.app.video_export.start.call_args.args[0]
        exported = self.app.video_export.start.call_args.kwargs["project"]
        self.assertTrue(options.shot_only)
        self.assertFalse(self.app.video_export.start.call_args.kwargs["pov"])
        self.assertEqual(exported.keyframes[0].attach, key.attach)
        self.assertEqual(exported.keyframes[1].attach, key.attach)
        self.assertTrue(self.app._auto_finish_layered)
        self.app._video_operation_done({"state": "recording"})
        self.app._play.assert_called_once_with(project=exported)

    def test_bone_duration_remains_editable_after_first_take_without_authoring_an_endpoint(self):
        import copy
        from dolly import editor_session
        from dolly.path import AttachKey
        from dolly.shot_history import ShotHistory
        app = self.app
        app.project = Project(start_tick=1234, tick_rate=64, keyframes=[
            Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach", attach=AttachKey(
                handle=11, entity_id=11, model="models/heroes/astro/astro.vmdl",
                point="bone", bone="head", offset=(4, 0, 6, 0, 0, 0), smoothing=.2))])
        authored = copy.deepcopy(app.project)
        app.shot_history = ShotHistory(app.project)
        revision = app.shot_history.revision
        app.app_settings = AppSettings()
        app.native_editor_active = True
        app.closed = app.playing = False
        app.controller = Mock()
        app.controller.game_pid = None
        app.controller.status.return_value = dict(READY, native_editor_active=True, paused=True)
        bridge = app.controller._native_bridge.return_value
        app.camera_tree = object()
        app._selection_index = lambda _: 0
        app.shot_time, app.speed, app.rate = Var("0"), Var("1"), Var("60")
        app.video_source, app.video_pov_duration = Var("Camera path"), Var("10")
        app.attach_fields = {"scene_node": 816}
        app._remember_export_folder = Mock()
        app._mark_dirty, app._refresh_keys, app._play = Mock(), Mock(), Mock()
        app.video_export.finish_pending = False
        app.video_export.output_path = None
        takes = []
        for seconds in (5, 15):
            editor_session.dispatch(app, {"action": "set_pov_duration", "value": seconds}, bridge)
            editor_session.configure(app)
            self.assertEqual(bridge.configure_editor.call_args.kwargs["pov_duration"], seconds)
            self.assertEqual(bridge.configure_editor.call_args.kwargs["camera_count"], 1)
            self.assertEqual(bridge.configure_editor_attach.call_args.args[1]["point"], 2)
            app._start_video_recording()
            app._error.assert_not_called()
            self.assertEqual(app.project, authored)
            self.assertEqual(app.shot_history.revision, revision)
            self.assertFalse(app.shot_history.can_undo)
            app._mark_dirty.assert_not_called()
            app._submit.call_args.args[1]()
            take = app.video_export.start.call_args.kwargs["project"]
            takes.append(take)
            self.assertEqual([key.time for key in take.keyframes], [0, seconds])
            self.assertEqual(take.start_tick, 1234)
            self.assertEqual(take.tick_rate, 64)
            self.assertEqual(take.keyframes[0].attach, authored.keyframes[0].attach)
            self.assertEqual(take.keyframes[1].attach, authored.keyframes[0].attach)
            app._video_operation_done({"state": "recording"})
            app._play.assert_called_with(project=take)
            app._video_operation_done({"state": "completed"})
            editor_session.configure(app)
            self.assertEqual(bridge.configure_editor.call_args.kwargs["camera_count"], 1)
        self.assertEqual(takes[0].duration, 5)
        self.assertEqual(takes[1].duration, 15)
        self.assertEqual(app.project, authored)

    def test_bone_take_playback_uses_its_frozen_project_instead_of_the_single_editor_camera(self):
        import copy
        take = self.two_camera_project()
        take.keyframes[-1].time = 15
        self.app.project = Project(keyframes=[copy.deepcopy(take.keyframes[0])])
        self.app.controller = Mock()
        self.app._guard = lambda _title, operation: operation()
        self.app._snapshot = Mock(side_effect=AssertionError("Read the mutable editor shot"))
        self.app._offer_replay_tick_rate = Mock()
        self.app._set_time = Mock()
        self.app.speed, self.app.rate = Var("1"), Var("60")
        self.app.hide_hud, self.app.smoothing = Var(True), Var("Off")
        self.app._play(project=take)
        self.app._submit.call_args.args[1]()
        played = self.app.controller.play.call_args.args[0]
        self.assertEqual(played, take)
        self.assertIsNot(played, take)
        self.app._snapshot.assert_not_called()
        self.app._offer_replay_tick_rate.assert_not_called()

    def test_bone_layer_takes_keep_the_color_take_snapshot_until_the_queue_finishes(self):
        import copy
        from dolly.bone_picker import timed_attachment
        from dolly.path import AttachKey
        self.app.project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6,
            source="attach", attach=AttachKey(handle=11, entity_id=11,
                model="models/heroes/astro/astro.vmdl", point="bone", bone="head"))])
        take = timed_attachment(self.app.project, 5)
        self.app._bone_take_project = copy.deepcopy(take)
        self.app.project.keyframes[0].attach.offset = (100, 0, 0, 0, 0, 0)
        ffmpeg = Path(self.folder.name)/"ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")
        self.app._base_capture = VideoOptions(Path(self.folder.name)/"layers.mp4", layers=("world",), ffmpeg_path=ffmpeg)
        self.app._layer_queue = [("world", "black")]
        self.app._snapshot = Mock(side_effect=AssertionError("Re-read the changed authored shot"))
        self.app._start_next_layer_take()
        exported = self.app.video_export.start.call_args.kwargs["project"]
        self.assertEqual(exported, take)
        self.assertIsNot(exported, self.app._bone_take_project)
        exported.keyframes[-1].time = 30
        self.assertEqual(self.app._bone_take_project.duration, 5)
        self.app.video_export.finish_pending = False
        self.app.video_export.output_path = None
        self.app._pending_auto_play = False
        self.app._report_take_audit = Mock()
        self.app._audit_finished_layers = Mock()
        self.app._video_operation_done({"state": "completed"})
        self.assertIsNone(self.app._bone_take_project)
        self.app._snapshot.assert_not_called()

    def test_failed_and_cancelled_bone_takes_clear_only_the_take_snapshot(self):
        import copy
        for state in ("failed", "cancelled"):
            with self.subTest(state=state):
                original = copy.deepcopy(self.app.project)
                self.app._bone_take_project = self.two_camera_project()
                self.app._video_operation_done({"state": state})
                self.assertIsNone(self.app._bone_take_project)
                self.assertEqual(self.app.project, original)

    def test_players_pass_uses_the_same_bone_take_instead_of_the_changed_editor_camera(self):
        import copy
        from dolly.bone_picker import timed_attachment
        from dolly.path import AttachKey
        authored = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
            attach=AttachKey(handle=11, entity_id=11, model="models/heroes/astro/astro.vmdl",
                             point="bone", bone="head"))])
        self.app._bone_take_project = timed_attachment(authored, 5)
        expected = copy.deepcopy(self.app._bone_take_project)
        self.app.project = authored
        self.app.project.keyframes[0].attach.offset = (100, 0, 0, 0, 0, 0)
        self.app._base_capture = VideoOptions(Path(self.folder.name)/"color.mp4")
        self.app.controller = Mock()
        deployment = Path(self.folder.name)/"deployment"
        deployment.mkdir()
        self.app.controller.deployment_directory.return_value = deployment
        self.app.controller._request.side_effect = lambda command, timeout=5: (
            "0 816 C_BaseEntity m_pGameSceneNode CGameSceneNode*\n" if "C_BaseEntity" in command else
            "0 408 CGameSceneNode m_pOwner CGameSceneNode*\n")
        self.app.video_export.status.return_value = {"width": 64, "height": 64}
        self.app._snapshot = Mock(side_effect=AssertionError("Re-read the changed editor shot"))
        with patch("dolly.gui.player_layer.reference_frame_count", return_value=301), \
                patch("dolly.gui.player_layer.check_capture_space", return_value=1000):
            self.app._start_player_capture("players")
        exported = self.app.video_export.start.call_args.kwargs["project"]
        self.assertEqual(exported, expected)
        self.assertIsNot(exported, self.app._bone_take_project)
        self.assertTrue(self.app.video_export.start.call_args.kwargs["capture_only"])
        self.assertFalse(self.app.video_export.start.call_args.kwargs["pov"])
        self.app._snapshot.assert_not_called()

    def test_recording_without_a_shot_does_not_auto_play(self):
        self.app._play = Mock()
        self.app._start_video_recording()
        self.assertFalse(self.app._pending_auto_play)
        self.app._submit.call_args.args[1]()
        self.app._video_operation_done({"state": "recording"})
        self.app._play.assert_not_called()

    def test_players_frame_limit_is_checked_before_color_recording(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(exe))
        self.app.project = self.two_camera_project()
        self.app.video_layer_players.set(True)
        self.app.video_fixed_step.set(True)
        self.app.video_fps.set("300")
        with patch("dolly.gui.player_layer.MAX_FRAMES", 256):
            self.app._start_video_recording()
        self.app._submit.assert_not_called()
        self.app.video_export.start.assert_not_called()
        self.assertIn("256 frames", str(self.app._error.call_args.args[1]))

    def test_player_frame_budget_follows_long_camera_path_at_600fps(self):
        self.app.project = self.two_camera_project()
        self.app.project.keyframes[-1].time = 10.0
        options = VideoOptions(Path(self.folder.name)/"long.mp4", fps=600)
        self.assertEqual(self.app._player_layer_frames(options), 6000)

    def test_players_without_path_is_checked_before_color_recording(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(exe))
        self.app.video_layer_players.set(True)
        self.app.video_fixed_step.set(True)
        self.app._start_video_recording()
        self.app._submit.assert_not_called()
        self.assertIn("camera path", str(self.app._error.call_args.args[1]))

    def test_failed_recorder_start_clears_auto_play_without_playing(self):
        self.app.project = self.two_camera_project()
        self.app._play = Mock()
        self.app._start_video_recording()
        self.app._video_operation_done({"state": "failed", "error": "encoder rejected the settings"})
        self.app._play.assert_not_called()
        self.assertFalse(self.app._pending_auto_play)

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

    def test_depth_toggle_defaults_to_fixed_step_and_clears_orphan_exr(self):
        self.app.video_depth.set(True)
        self.app.video_depth_exr.set(True)
        self.app._depth_toggled()
        self.assertTrue(self.app.video_fixed_step.get())
        self.app.video_depth.set(False)
        self.app._depth_toggled()
        self.assertFalse(self.app.video_depth_exr.get())

    def _video_widgets(self):
        for name in ("video_start_button", "video_stop_button", "video_cancel_button",
                     "video_path_entry", "video_browse_button", "video_fps_combo",
                     "video_bitrate_combo", "reshade_configure_button", "reshade_forget_button"):
            setattr(self.app, name, Mock())
        self.app._refresh_reshade = Mock()

    def test_async_depth_failure_restores_on_worker_after_current_operation(self):
        self._video_widgets()
        self.app.controller = SimpleNamespace(_export_resolution=.6667)
        self.app._log = Mock()
        self.app.video_export.status.return_value = {"state": "failed", "error": "Depth rejected"}
        self.app.busy = True
        self.app._refresh_video(READY)
        self.app._submit.assert_not_called()
        self.app.busy = False
        self.app._refresh_video(READY)
        self.app._submit.assert_called_once_with("Restoring recording settings", self.app.video_export.stop)
        self.assertEqual(self.app._layer_queue, [])
        self.assertFalse(self.app._auto_finish_layered)
        self.app.controller._export_resolution = None
        self.app._submit.reset_mock()
        self.app._refresh_video(READY)
        self.app._submit.assert_not_called()

    def test_async_timing_failure_restores_on_worker_after_current_operation(self):
        self._video_widgets()
        self.app.controller = SimpleNamespace(_export_timing={"fps": 30, "speed": 1.0})
        self.app._log = Mock()
        self.app.video_export.status.return_value = {"state": "failed", "error": "Encoder missing"}
        self.app.busy = True
        self.app._refresh_video(READY)
        self.app._submit.assert_not_called()
        self.app.busy = False
        self.app._refresh_video(READY)
        self.app._submit.assert_called_once_with("Restoring recording settings", self.app.video_export.stop)
        self.app.controller._export_timing = None
        self.app._submit.reset_mock()
        self.app._refresh_video(READY)
        self.app._submit.assert_not_called()

    def test_async_playback_failure_aborts_players_on_worker_before_showing_original_error(self):
        self._video_widgets()
        self.app.controller = Mock(pending_export_restoration=Mock(return_value=[]))
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._base_capture = Mock()
        self.app._active_layer_take = ("players", "capture")
        self.app._layer_queue = [("effects", "black"), ("effects", "white")]
        self.app._auto_finish_layered = True
        self.app._take_playing_seen = True
        self.app._pending_combine = "effects"
        self.app._pending_capture = "players"
        self.app._pipeline_advance = True
        audio = self.app.clip_audio = Mock()
        order = []
        self.app.video_export.stop.side_effect = lambda **kw: order.append(("recording", kw))
        self.app.controller.stop.side_effect = lambda: order.append("camera")
        self.app.controller.end_matte_layer.side_effect = lambda: order.append("matte")
        self.app.controller.reset_layer_modes.side_effect = lambda: order.append("classes")
        audio.cancel.side_effect = lambda: order.append("audio")
        reason = "Native playback stalled before the next authored frame"

        self.app._refresh_video({**READY, "playing": False, "playback_error": reason})

        self.app._submit.assert_called_once()
        self.assertEqual(order, [])  # Polling only schedules the worker.
        self.assertEqual(self.app._layer_queue, [])
        self.assertIsNone(self.app._base_capture)
        self.assertIsNone(self.app._active_layer_take)
        self.assertIsNone(self.app._pending_combine)
        self.assertIsNone(self.app._pending_capture)
        self.assertFalse(self.app._pipeline_advance)
        self.assertFalse(self.app._auto_finish_layered)
        self.app._error.assert_not_called()
        with self.assertRaises(RuntimeError) as raised:
            self.app._submit.call_args.args[1]()
        self.assertIn(reason, str(raised.exception))
        self.assertRegex(str(raised.exception), r"(?i)retry|try.*again|prepare|new take")
        self.assertEqual([event for event in order if event != "audio"],
                         [("recording", {"cancel": True}), "camera", "matte", "classes"])
        audio.cancel.assert_called_once_with()
        self.assertIsNone(self.app.clip_audio)

    def test_async_playback_failure_waits_for_the_current_worker(self):
        self._video_widgets()
        self.app.controller = Mock()
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._active_layer_take = ("players", "capture")
        self.app._layer_queue = [("effects", "black")]
        self.app.busy = True
        failed = {**READY, "playing": False, "playback_error": "Replay camera lost"}

        self.app._refresh_video(failed)
        self.app._submit.assert_not_called()
        self.assertEqual(self.app._active_layer_take, ("players", "capture"))
        self.assertEqual(self.app._layer_queue, [("effects", "black")])
        self.app.video_export.stop.assert_not_called()
        self.app.controller.stop.assert_not_called()

        self.app.busy = False
        self.app._refresh_video(failed)
        self.app._submit.assert_called_once()
        self.assertEqual(self.app._layer_queue, [])

    def test_failed_recovery_is_not_automatically_repeated_on_every_poll(self):
        self._video_widgets()
        self.app.controller = Mock(pending_export_restoration=Mock(return_value=[]))
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.video_export.stop.side_effect = RuntimeError("Players capture is still stopping")
        failed = {**READY, "playing": False, "playback_error": "Original camera failure"}
        self.app._refresh_video(failed)
        with self.assertRaisesRegex(RuntimeError, "Original camera failure.*still stopping"):
            self.app._submit.call_args.args[1]()
        self.app._submit.reset_mock()
        self.app._refresh_video(failed)
        self.app._submit.assert_not_called()
        self.app.controller.stop.assert_called_once_with()

    def test_async_recovery_reports_all_scene_errors_and_retained_journals(self):
        from dolly.controller import Controller

        controller = self.app.controller = Controller()
        controller._require_demo = Mock()
        controller.stop = Mock()
        controller._restore = {"fov_desired": "90"}
        controller._playback_restore = {"host_timescale": "1"}
        controller._game_ui_restore = {"citadel_hud_visible": "1"}
        controller._matte_cvar_restore = {"r_effects_bloom": "1"}
        controller._layer_hidden = {"SkinnedObject"}
        controller._export_timing = {"restore_pending": True}
        controller._export_resolution = .6667
        controller.end_matte_layer = Mock(side_effect=RuntimeError("matte restore rejected"))
        controller.reset_layer_modes = Mock(side_effect=RuntimeError("class restore rejected"))
        self.app.video_export.stop.side_effect = RuntimeError("capture stop unconfirmed")
        audio = self.app.clip_audio = Mock()
        audio.cancel.side_effect = RuntimeError("audio stop unconfirmed")

        with self.assertRaises(RuntimeError) as raised:
            self.app._abort_failed_export("Original camera failure")

        text = str(raised.exception)
        for detail in ("Original camera failure", "matte restore rejected", "class restore rejected",
                       "capture stop unconfirmed", "audio stop unconfirmed", "camera values",
                       "playback settings", "game UI settings", "matte settings", "scene classes",
                       "export timing", "render scale"):
            self.assertIn(detail, text)
        controller.stop.assert_called_once_with()
        controller.end_matte_layer.assert_called_once_with()
        controller.reset_layer_modes.assert_called_once_with()
        self.assertEqual(controller._layer_hidden, {"SkinnedObject"})
        self.assertIs(self.app.clip_audio, audio)

    def test_async_recovery_refuses_every_action_if_the_selected_replay_is_lost(self):
        from dolly.controller import Controller
        from test_controller import FakeConsole

        for failure in ("changed", "unreadable", "closed", "disconnected"):
            with self.subTest(failure=failure):
                controller = self.app.controller = Controller()
                console = controller._console = FakeConsole()
                controller._session = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), pid=1234)
                controller._demo = Path("example.dem")
                controller._layer_hidden = {"SkinnedObject"}
                controller._matte_cvar_restore = {"r_effects_bloom": '"r_effects_bloom" = "1"'}
                controller._export_timing = {"restore_pending": True}
                if failure == "changed":
                    console.demo_name = "other.dem"
                elif failure == "unreadable":
                    console.goto_output = "Unknown command: demo_goto"
                    console.demo_output = "Unknown command: demo_info"
                elif failure == "closed":
                    controller._session.process.poll = lambda: 0
                else:
                    console.is_connected = False
                self.app.video_export.stop.reset_mock()
                audio = self.app.clip_audio = Mock()

                with self.assertRaises(RuntimeError) as raised:
                    self.app._abort_failed_export("Original camera failure")

                text = str(raised.exception)
                self.assertIn("Original camera failure", text)
                self.assertIn("Recovery still needs attention", text)
                self.assertIn("export timing", text)
                self.app.video_export.stop.assert_not_called()
                audio.cancel.assert_not_called()
                self.assertIs(self.app.clip_audio, audio)
                self.assertTrue(all(command in ("demo_goto", "demo_info") for command in console.requests))
                self.assertEqual(controller._layer_hidden, {"SkinnedObject"})
                self.assertIsNotNone(controller._matte_cvar_restore)
                self.assertEqual(controller._export_timing, {"restore_pending": True})

    def test_a_new_capture_generation_can_recover_without_a_video_start_callback(self):
        self._video_widgets()
        self.app.controller = Mock()
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        failed = {**READY, "playing": False, "playback_error": "Camera failure", "recording_generation": 1}
        self.app._refresh_video(failed)
        self.app._submit.assert_called_once()
        self.app._submit.reset_mock()

        # An attempted explicit restart cannot retry a still-reserved request.
        self.app._playback_failure_recovery_pending = False
        self.app._refresh_video(failed)
        self.app._submit.assert_not_called()

        # Screenshot/programmatic capture starts do not call the video button.
        self.app._playback_failure_recovery_pending = True
        self.app._refresh_video({**failed, "recording_generation": 2})
        self.app._submit.assert_called_once()

    def test_paused_recording_without_a_playback_error_does_not_abort(self):
        self._video_widgets()
        self.app.controller = Mock()
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._active_layer_take = ("players", "capture")
        self.app._layer_queue = [("effects", "black")]

        self.app._refresh_video({**READY, "playing": False, "playback_error": "",
                                 "message": "Replay paused"})

        self.app._submit.assert_not_called()
        self.assertEqual(self.app._active_layer_take, ("players", "capture"))
        self.assertEqual(self.app._layer_queue, [("effects", "black")])
        self.app.video_export.stop.assert_not_called()
        self.app.controller.stop.assert_not_called()

    def test_pending_auto_play_cannot_consume_an_earlier_playback_error(self):
        self._video_widgets()
        self.app.controller = Mock()
        self.app.playing = False
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._pending_auto_play = True
        self.app._active_layer_take = ("players", "capture")
        self.app._layer_queue = [("effects", "black")]
        self.app._play = Mock()

        self.app._refresh_video({**READY, "playing": False,
                                 "playback_error": "Earlier shot failed"})

        self.app._play.assert_called_once_with()
        self.app._submit.assert_not_called()
        self.assertEqual(self.app._active_layer_take, ("players", "capture"))
        self.assertEqual(self.app._layer_queue, [("effects", "black")])
        self.app.video_export.stop.assert_not_called()
        self.app.controller.stop.assert_not_called()

    def test_async_depth_completion_waits_for_worker_before_advancing_pipeline(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "completed"}
        self.app.video_export.finish_pending = True
        self.app._video_operation_done = Mock()
        self.app._stop_video_recording = Mock()
        self.app._last_video_state = "recording"
        self.app.busy = True
        self.app._refresh_video(READY)
        self.app._video_operation_done.assert_not_called()
        self.app._stop_video_recording.assert_not_called()
        self.assertEqual(self.app._last_video_state, "recording")
        self.app.busy = False
        self.app._refresh_video(READY)
        self.app._stop_video_recording.assert_called_once_with()
        self.app._video_operation_done.assert_not_called()

    def test_completion_callback_finishes_native_output_before_layer_handoff(self):
        self.app.video_export.finish_pending = True
        self.app._stop_video_recording = Mock()
        self.app._advance_layer_pipeline = Mock()
        self.app._video_operation_done({"state": "completed"})
        self.app._stop_video_recording.assert_called_once_with()
        self.app._advance_layer_pipeline.assert_not_called()

    def test_a_plain_colour_shot_take_arms_the_same_auto_finish(self):
        # A finished shot stops producing frames, so a plain colour take that
        # stayed open looked complete while its video was still unfinalized.
        self.app.project = self.two_camera_project()
        self.app._play = Mock()
        self.app._start_video_recording()
        self.app._submit.call_args.args[1]()
        options = self.app.video_export.start.call_args.args[0]
        self.assertFalse(options.depth)
        self.assertEqual(options.layers, ())
        self.assertFalse(options.shot_only)
        self.assertTrue(self.app._auto_finish_layered)

    def test_a_free_camera_take_still_finishes_manually(self):
        # No authored project means no path to end the take at.
        self.app.project = Project()
        self.app._play = Mock()
        self.app._start_video_recording()
        self.assertTrue(self.app._pending_auto_play is False)
        self.assertFalse(self.app._auto_finish_layered)

    def test_layered_take_finishes_itself_when_the_shot_completes(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.playing = False
        self.app._auto_finish_layered = True
        self.app._take_playing_seen = True
        self.app._refresh_video({**READY, "playing": False,
                                 "message": "Native shot finished. Replay paused and final camera held."})
        self.app._submit.assert_called_once()
        self.assertFalse(self.app._auto_finish_layered)

    def test_stale_shot_finished_message_cannot_finish_a_fresh_take(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.playing = False
        self.app._auto_finish_layered = True
        self.app._take_playing_seen = False
        self.app._refresh_video({**READY, "playing": False,
                                 "message": "Native shot finished. Replay paused and final camera held."})
        self.app._submit.assert_not_called()
        self.assertTrue(self.app._auto_finish_layered)

    def test_the_new_shot_playing_arms_the_auto_finish(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.playing = True
        self.app._auto_finish_layered = True
        self.app._take_playing_seen = False
        self.app._refresh_video({**READY, "playing": True,
                                 "message": "Playing native camera path at render time."})
        self.assertTrue(self.app._take_playing_seen)
        self.app.playing = False
        self.app._refresh_video({**READY, "playing": False,
                                 "message": "Native shot finished. Replay paused and final camera held."})
        self.app._submit.assert_called_once()

    def test_layered_take_does_not_finish_before_the_shot_runs(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.playing = False
        self.app._auto_finish_layered = True
        self.app._refresh_video({**READY, "playing": False,
                                 "message": "Replay prepared for recording. Play the shot once, then finish recording."})
        self.app._submit.assert_not_called()
        self.assertTrue(self.app._auto_finish_layered)

    def test_color_only_take_waits_for_a_manual_finish(self):
        self._video_widgets()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app.playing = False
        self.app._auto_finish_layered = False
        self.app._refresh_video({**READY, "playing": False,
                                 "message": "Native shot finished. Replay paused and final camera held."})
        self.app._submit.assert_not_called()

    def test_layer_selection_queues_isolated_takes(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(exe))
        self.app.video_layer_world.set(True)
        self.app._layer_toggled()
        self.app._start_video_recording()
        self.app._submit.call_args.args[1]()
        options = self.app.video_export.start.call_args.args[0]
        self.assertEqual(options.layers, ("world",))
        self.assertEqual(self.app._layer_queue, [("world", "black")])
        self.assertTrue(self.app.video_fixed_step.get())

    def test_players_layer_queues_a_native_capture(self):
        self.app.project = self.two_camera_project()
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        self.app.ffmpeg_path.set(str(exe))
        self.app.video_layer_players.set(True)
        self.app._layer_toggled()
        self.app._start_video_recording()
        self.assertEqual(self.app._layer_queue, [("players", "capture")])

    def test_players_frame_count_matches_export_speed(self):
        self.app.project = self.two_camera_project()
        for speed, expected in ((.5, 600), (1, 300), (2, 150)):
            with self.subTest(speed=speed):
                base = VideoOptions(Path(self.folder.name)/"shot.mp4", fps=300, speed=speed)
                self.assertEqual(self.app._player_layer_frames(base), expected)

    def test_players_capture_keeps_a_partial_final_frame_interval(self):
        self.app.project = self.two_camera_project()
        self.app.project.keyframes[-1].time = 2.5 / 600
        base = VideoOptions(Path(self.folder.name)/"shot.mp4", fps=600)
        self.assertEqual(self.app._player_layer_frames(base), 3)

    def test_next_layer_take_arms_the_players_capture(self):
        self.app.video_export.status.return_value = {"state": "completed", "width": 64, "height": 64}
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("players",),
                            ffmpeg_path=exe, depth=True).validated()
        (Path(self.folder.name) / "shot").mkdir()
        (Path(self.folder.name) / "shot" / "shot.mp4").write_bytes(b"color output")
        (Path(self.folder.name) / "shot" / "shot.json").write_text(json.dumps({
            "format": "deadlock-dolly-shot", "version": 1, "video_file": "shot.mp4",
            "fps": 60, "fixed_step": True, "frames_written": 62,
            "first_frame": 0, "last_frame": 61}), encoding="utf-8")
        deploy = Path(self.folder.name) / "deploy"
        deploy.mkdir()
        layout = ("          0          408      CGameSceneNode                           "
                  "m_pOwner                                 CGameSceneNode*\n")
        self.app._base_capture = base
        self.app._layer_queue = [("players", "capture")]
        self.app.project = self.two_camera_project()
        self.app.controller = Mock()
        self.app.controller.deployment_directory.return_value = deploy
        entity_layout = ("          0          816      C_BaseEntity                             "
                         "m_pGameSceneNode                         CGameSceneNode*\n")
        self.app.controller._request.side_effect = lambda command, timeout=5: (
            entity_layout if "C_BaseEntity" in command else layout)
        self.app._snapshot = Mock(return_value=Mock())
        result = self.app._start_next_layer_take()
        self.app.controller.apply_layer_mode.assert_not_called()
        self.assertFalse((deploy / "dolly_owner_start.txt").exists())
        self.assertTrue(self.app.video_export.start.call_args.kwargs["capture_only"])
        with patch("dolly.gui.player_layer.wait_until_armed"):
            self.app.video_export.start.call_args.kwargs["before_record"]()
        marker = (deploy / "dolly_owner_start.txt").read_text(encoding="ascii")
        self.assertRegex(marker, r"^color-sequence-v4 0 62 players 408 816 fps 60 request [0-9a-f]{32} speed 1 scratch [0-9a-f]+\n$")
        options = self.app.video_export.start.call_args.args[0]
        self.assertTrue(options.full_resolution)
        self.assertEqual(options.path, Path(self.folder.name) / "shot" / "players" / "players.mp4")
        self.assertEqual(options.layers, ())
        self.assertFalse(options.white_clear)
        self.assertTrue(options.fixed_step)
        self.assertEqual(result, self.app.video_export.start.return_value)
        self.assertEqual(self.app._active_layer_take, ("players", "capture"))

    def test_player_completion_returns_master_and_cleans_only_after_encode(self):
        base = VideoOptions(Path(self.folder.name)/"shot.mp4", fps=600)
        self.app._base_capture = base
        self.app.controller = Mock()
        deploy = Path(self.folder.name)/"deploy"; deploy.mkdir()
        self.app.controller.deployment_directory.return_value = deploy
        master = Path(self.folder.name)/"shot/players/players.mov"
        with patch("dolly.gui.player_layer.wait_for_capture", return_value="complete"), \
             patch("dolly.gui.resolve_ffmpeg", return_value=Path("ffmpeg.exe")), \
             patch("dolly.player_encode.encode_in_worker", return_value=master) as encode, \
             patch("dolly.gui.player_layer.cleanup_capture", return_value=[]) as cleanup:
            result = self.app._finish_player_capture("players")
            self.assertEqual(result, {"state":"completed", "layer":"players", "master":str(master)})
            self.assertEqual(encode.call_args.kwargs["fps"], 600)
            cleanup.assert_called_once()
            encode.side_effect = RuntimeError("encoder failed")
            cleanup.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                self.app._finish_player_capture("players")
            cleanup.assert_not_called()

            encode.side_effect = InterruptedError("cancelled")
            result = self.app._finish_player_capture("players")
            self.assertEqual(result["state"], "cancelled")
            self.assertIsNone(self.app._player_encode_cancel)
            cleanup.assert_not_called()

    def test_players_finalize_after_teardown_is_a_logged_no_op(self):
        self.app._base_capture = None
        self.app.controller = Mock()
        result = self.app._finish_player_capture("players")
        self.assertEqual(result, {"state": "cancelled", "layer": "players", "reason": "run_torn_down"})

    def test_layer_alpha_combine_after_teardown_restores_matte_and_stops(self):
        self.app._base_capture = None
        self.app.controller = Mock()
        result = self.app._combine_layer("players")
        self.assertEqual(result, {"state": "cancelled", "layer": "players", "reason": "run_torn_down"})
        self.app.controller.end_matte_layer.assert_called_once_with()

    def test_mkv_container_is_kept_for_a_lossy_codec_layer_take(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mkv", 60, 20_000_000,
                            codec="auto", container="mkv", fixed_step=True, speed=1.0,
                            layers=("world",), ffmpeg_path=exe).validated()
        self.assertEqual(base.container, "mkv")
        (Path(self.folder.name) / "shot").mkdir()
        self.app._base_capture = base
        self.app.controller = Mock()
        self.app.controller.apply_layer_mode.return_value = {"hidden": []}
        self.app._snapshot = Mock(return_value=Mock())
        self.app._layer_queue = [("world", "black")]
        self.app._start_next_layer_take()
        options = self.app.video_export.start.call_args.args[0]
        self.assertEqual(options.path.name, "world.mkv")
        self.assertEqual(options.container, "mkv")

    def test_effects_matte_combine_reads_the_lossless_mkv_passes(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mkv", 60, 20_000_000,
                            codec="lossless", fixed_step=True, speed=1.0,
                            layers=("effects",), ffmpeg_path=exe).validated()
        layer_dir = Path(self.folder.name) / "shot" / "effects"
        layer_dir.mkdir(parents=True)
        (layer_dir / "effects.mkv").write_bytes(b"black")
        (layer_dir / "effects_white.mkv").write_bytes(b"white")
        self.app._base_capture = base
        self.app.controller = Mock()
        def run(args, **_kwargs):
            Path(args[-1]).write_bytes(b"master")
            return SimpleNamespace(returncode=0, stderr="")
        with patch("dolly.gui.resolve_ffmpeg", return_value=exe), \
                patch("dolly.gui.subprocess.run", side_effect=run) as started:
            result = self.app._combine_layer("effects")
        self.assertEqual(result["state"], "completed")
        args = started.call_args.args[0]
        self.assertIn(str(layer_dir / "effects.mkv"), args)
        self.assertIn(str(layer_dir / "effects_white.mkv"), args)

    def test_next_layer_take_hides_the_layer_and_starts_a_fixed_step_take(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("world",),
                            ffmpeg_path=exe, depth=True).validated()
        (Path(self.folder.name) / "shot").mkdir()
        self.app._base_capture = base
        self.app._layer_queue = [("world", "black")]
        self.app.controller = Mock()
        self.app.controller.apply_layer_mode.return_value = {"hidden": ["SkinnedObject"]}
        self.app._snapshot = Mock(return_value=Mock())
        result = self.app._start_next_layer_take()
        self.app.controller.apply_layer_mode.assert_not_called()
        self.app.video_export.start.call_args.kwargs["before_record"]()
        self.app.controller.apply_layer_mode.assert_called_once_with("world")
        options = self.app.video_export.start.call_args.args[0]
        self.assertTrue(options.full_resolution)
        self.assertEqual(options.path, Path(self.folder.name) / "shot" / "world.mp4")
        self.assertFalse(options.depth)
        self.assertEqual(options.layers, ("world",))
        self.assertFalse(options.white_clear)
        self.assertTrue(options.fixed_step)
        self.assertEqual(result, self.app.video_export.start.return_value)
        self.assertEqual(self.app._active_layer_take, ("world", "black"))
        self.assertTrue(self.app._pending_auto_play)
        self.assertTrue(self.app._auto_finish_layered)

    def test_players_matte_pass_disables_post_processing(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("players",),
                            ffmpeg_path=exe).validated()
        (Path(self.folder.name) / "shot").mkdir()
        self.app._base_capture = base
        self.app._layer_queue = [("players", "black")]
        self.app.controller = Mock()
        self.app.controller.apply_layer_mode.return_value = {"hidden": ["SkinnedObject"]}
        self.app._snapshot = Mock(return_value=Mock())
        self.app._start_next_layer_take()
        self.app.video_export.start.call_args.kwargs["before_record"]()
        self.app.controller.begin_matte_layer.assert_called_once_with()

    def test_lossless_color_take_keeps_mkv_for_every_layer_file(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mkv", 60, 20_000_000,
                            codec="lossless", fixed_step=True, speed=1.0,
                            layers=("world", "players"), ffmpeg_path=exe).validated()
        (Path(self.folder.name) / "shot" / "players").mkdir(parents=True)
        self.app._base_capture = base
        self.app.controller = Mock()
        self.app.controller.apply_layer_mode.return_value = {"hidden": []}
        self.app._snapshot = Mock(return_value=Mock())
        self.app._layer_queue = [("world", "black")]
        self.app._start_next_layer_take()
        self.assertEqual(self.app.video_export.start.call_args.args[0].path.name, "world.mkv")
        self.app._layer_queue = [("players", "white")]
        self.app._start_next_layer_take()
        white = self.app.video_export.start.call_args.args[0]
        self.assertEqual(white.path.name, "players_white.mkv")

    def test_white_matte_pass_writes_beside_the_black_pass(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("players",),
                            ffmpeg_path=exe, depth=True).validated()
        (Path(self.folder.name) / "shot" / "players").mkdir(parents=True)
        self.app._base_capture = base
        self.app._layer_queue = [("players", "white")]
        self.app.controller = Mock()
        self.app.controller.apply_layer_mode.return_value = {"hidden": ["SkinnedObject"]}
        self.app._snapshot = Mock(return_value=Mock())
        self.app._start_next_layer_take()
        options = self.app.video_export.start.call_args.args[0]
        self.assertTrue(options.full_resolution)
        self.assertEqual(options.path,
                         Path(self.folder.name) / "shot" / "players" / "players_white.mp4")
        self.assertTrue(options.white_clear)
        self.assertTrue(options.shot_only)
        self.assertEqual(options.layers, ())

    def test_white_pass_completion_builds_the_alpha_master(self):
        self.app.video_export.output_path = None
        self.app.video_export.output_directory = None
        self.app.controller = Mock()
        self.app._base_capture = Mock()
        self.app._layer_queue = [("players", "black")]
        self.app._active_layer_take = ("players", "white")
        self.app._submit = Mock()
        result = {"state": "completed", "layer": "players", "master": "players.mov"}
        self.app._combine_layer = Mock(return_value=result)
        self.app._video_operation_done({"state": "completed"})
        self.app._submit.assert_called_once()
        self.app._combine_layer.assert_not_called()
        self.assertIs(self.app._submit.call_args.args[1](), result)
        self.app._combine_layer.assert_called_once_with("players")
        self.assertIsNone(self.app._active_layer_take)

    def test_layered_completion_suggests_a_path_beside_the_take_folder(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("players",),
                            ffmpeg_path=exe).validated()
        (Path(self.folder.name) / "shot" / "players").mkdir(parents=True)
        self.app._base_capture = base
        self.app._layer_queue = []
        self.app._pending_combine = None
        self.app._active_layer_take = None
        self.app.video_export.output_path = Path(self.folder.name) / "shot" / "players" / "players_white.mp4"
        self.app.video_export.output_directory = Path(self.folder.name) / "shot" / "players"
        self.app.video_export.status.return_value = {"state": "completed"}
        self.app._submit = Mock()
        self.app._log = Mock()
        self.app._video_operation_done({"state": "completed", "master": str(Path(self.folder.name) / "shot" / "players" / "players.mov")})
        suggested = Path(self.app.video_path.get())
        self.assertEqual(suggested.parent, Path(self.folder.name))
        self.assertNotIn("shot", suggested.parts)

    def test_color_take_completion_announces_the_queued_passes(self):
        self.app._base_capture = SimpleNamespace(path=Path(self.folder.name) / "shot.mp4")
        self.app._layer_queue = [("players", "black"), ("players", "white")]
        self.app._active_layer_take = None
        self.app.video_export.output_path = Path(self.folder.name) / "shot" / "shot.mp4"
        self.app.video_export.status.return_value = {"state": "completed"}
        self.app._submit = Mock()
        self.app._log = Mock()
        self.app._video_operation_done({"state": "completed"})
        self.assertIn("Color take saved", self.app.status_text.get())

    def test_combine_layer_writes_rgba_mov_and_removes_intermediates(self):
        exe = Path(self.folder.name) / "ffmpeg.exe"
        exe.write_bytes(b"MZ")
        base = VideoOptions(Path(self.folder.name) / "shot.mp4", 60, 20_000_000,
                            fixed_step=True, speed=1.0, layers=("players",),
                            ffmpeg_path=exe).validated()
        layer_dir = Path(self.folder.name) / "shot" / "players"
        layer_dir.mkdir(parents=True)
        (layer_dir / "players.mp4").write_bytes(b"black")
        (layer_dir / "players_white.mp4").write_bytes(b"white")
        (layer_dir / "shot.json").write_text("{}", encoding="utf-8")
        self.app._base_capture = base
        self.app.controller = Mock()

        def fake_run(args, **kwargs):
            Path(args[-1]).write_bytes(b"master")
            return SimpleNamespace(returncode=0, stderr="")

        with patch("dolly.gui.subprocess.run", side_effect=fake_run) as run:
            status = self.app._combine_layer("players")
        self.app.controller.end_matte_layer.assert_called_once_with()
        self.assertEqual(status, {"state": "completed", "layer": "players",
                                  "master": str(layer_dir / "players.mov")})
        self.assertIn("alphamerge", run.call_args.args[0][run.call_args.args[0].index("-filter_complex") + 1])
        self.assertIn("prores_ks", run.call_args.args[0])
        self.assertTrue((layer_dir / "players.mov").is_file())
        self.assertFalse((layer_dir / "players.mp4").exists())
        self.assertFalse((layer_dir / "players_white.mp4").exists())
        self.assertEqual(json.loads((layer_dir / "shot.json").read_text(encoding="utf-8"))["video_file"],
                         "players.mov")

    def test_completed_layer_take_advances_then_restores_scene_layers(self):
        self.app.video_export.output_path = None
        self.app.video_export.output_directory = None
        self.app.controller = Mock()
        self.app._base_capture = Mock()
        self.app._layer_queue = [("players", "black")]
        self.app._active_layer_take = ("world", "black")
        self.app._submit = Mock()
        self.app._start_next_layer_take = Mock(return_value="started next take")
        self.app._restore_scene_state = Mock()
        self.app.video_export.status.return_value = {"state": "recording"}
        self.app._video_operation_done({"state": "completed"})
        self.app._submit.assert_not_called()
        self.app._start_next_layer_take.assert_not_called()
        self.assertTrue(self.app._pipeline_advance)
        self.assertEqual(self.app._layer_queue, [("players", "black")])
        self.app.video_export.status.return_value = {"state": "completed"}
        self.app._advance_layer_pipeline()
        self.app._submit.assert_called_once()
        self.assertFalse(self.app._pipeline_advance)
        self.assertEqual(self.app._submit.call_args.args[1](), "started next take")
        self.app._start_next_layer_take.assert_called_once_with()
        self.app._layer_queue = []
        self.app._submit.reset_mock()
        self.app._video_operation_done({"state": "completed"})
        self.app._submit.assert_called_once()
        self.assertIsNone(self.app._base_capture)
        self.app._restore_scene_state.assert_not_called()
        self.assertIsNone(self.app._submit.call_args.args[1]())
        self.app._restore_scene_state.assert_called_once_with()

    def test_failed_layer_take_clears_the_queue_and_restores_layers(self):
        self.app.video_export.output_path = None
        self.app.video_export.output_directory = None
        self.app.controller = Mock()
        self.app._base_capture = Mock()
        self.app._layer_queue = [("effects", "black")]
        self.app._active_layer_take = ("effects", "black")
        self.app._submit = Mock()
        self.app._video_operation_done({"state": "failed", "error": "encoder stopped"})
        self.assertEqual(self.app._layer_queue, [])
        self.assertIsNone(self.app._base_capture)
        self.assertIsNone(self.app._active_layer_take)
        self.app._submit.assert_called_once()

    def test_layer_prep_exception_restores_before_delivering_error(self):
        self.app._base_capture = Mock()
        self.app.controller = Mock()
        self.app.events = queue.Queue()
        self.app.jobs = queue.Queue()
        problem = RuntimeError("Players capture needs approximately 45.1 GiB")
        self.app.jobs.put(("Recording layer take", Mock(side_effect=problem), None))
        self.app.jobs.put(None)
        order = []
        self.app.video_export.stop.side_effect = lambda: order.append("recording")
        self.app.controller.stop.side_effect = lambda: order.append("control")
        self.app._restore_scene_state = Mock(side_effect=lambda: order.append("layers"))
        self.app._worker_loop()
        self.assertEqual(order, ["recording", "control", "layers"])
        self.assertEqual(self.app.events.get_nowait(), ("export_error", "Recording layer take", problem))
        # Cleanup finalizes rather than discarding completed Color/Depth/World.
        self.app.video_export.stop.assert_called_once_with()

    def test_export_cleanup_attempts_control_restoration_if_recorder_stop_fails(self):
        self.app.controller = Mock()
        self.app.video_export.stop.side_effect = RuntimeError("bridge unavailable")
        self.app._recover_export_failure()
        self.app.controller.stop.assert_called_once_with()
        self.app.controller.reset_layer_modes.assert_called_once_with()

    def test_export_error_event_clears_queue_before_showing_dialog(self):
        self.app.closed = False
        self.app.busy = True
        self.app.busy_text = Var("Recording layer take")
        self.app.startup_cancel = None
        self.app._base_capture = Mock()
        self.app._layer_queue = [("effects", "black")]
        self.app._pending_auto_play = True
        self.app._pipeline_advance = True
        self.app._active_layer_take = ("world", "black")
        self.app.events = queue.Queue()
        self.app.events.put(("export_error", "Recording layer take", RuntimeError("Disk space")))
        # Stop the poll body at the dialog boundary, where all UI state must be
        # safe. The outer _poll wrapper is covered by GuiPumpTests.
        self.app._error.side_effect = LookupError("dialog reached")
        with self.assertRaisesRegex(LookupError, "dialog reached"):
            self.app._poll_once()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app._layer_queue, [])
        self.assertIsNone(self.app._base_capture)
        self.assertIsNone(self.app._active_layer_take)
        self.assertFalse(self.app._pending_auto_play)
        self.assertFalse(self.app._pipeline_advance)


class ReShadeGuiTests(unittest.TestCase):
    def test_closed_game_refresh_does_not_read_the_stale_native_mapping(self):
        self.app.controller = Mock()
        self.app.controller._native_bridge.side_effect = RuntimeError('closed native mapping')
        self.app.app_settings = AppSettings()
        self.app.reshade_status_text = Var('')
        self.app.reshade_disable_button = Mock()
        self.app.reshade_configure_button = Mock()
        self.app._refresh_reshade({'game_running': False})
        self.app.controller._native_bridge.assert_not_called()

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
            with patch("dolly.reshade_setup.prepare_config", side_effect=lambda *_, **__: order.append("library")), \
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
        self.app.binding_feedback = Var("")
        self.app.bindings_tree = Mock()
        self.app.bindings_tree.selection.return_value = ("reshade",)
        self.app.binding_key = Var("Mouse5")
        self.app.binding_ctrl = Var(False)
        self.app.binding_alt = Var(False)
        self.app.binding_shift = Var(False)

    def test_reshade_binding_row_preserves_capture_and_runtime_path(self):
        self.app._save_editor_binding("reshade", EditorBinding("Mouse5"))
        saved = self.app._persist_preferences.call_args.args[0]
        self.assertEqual(saved.reshade_binding, EditorBinding("Mouse5"))
        self.assertEqual(saved.capture_binding, DEFAULT_BINDING)
        self.assertEqual(saved.reshade_runtime_path, self.app.app_settings.reshade_runtime_path)
        self.assertNotIn("reshade", saved.action_bindings)

    def test_conflicting_reshade_binding_does_not_save(self):
        self.app._save_editor_binding("reshade", EditorBinding("F5"))
        self.app._persist_preferences.assert_not_called()
        self.assertIn("Play camera path", self.app.binding_feedback.get())
        self.app._error.assert_not_called()

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
        self.app.reshade_configure_button = Mock()
        self.app._configure_reshade = Mock()
        self.app._refresh_reshade(READY)
        self.app._refresh_reshade(READY)
        self.app._configure_reshade.assert_called_once_with(automatic=True)

    def test_selecting_a_runtime_persists_it_and_explains_the_next_step(self):
        self.app.controller = Mock()
        self.app.controller.status.return_value = {}
        self.app.controller._native_bridge.return_value = None
        self.app.reshade_runtime_path = Var("")
        self.app.reshade_status_text = Var("")
        self.app._log = Mock()
        self.app._configure_reshade = Mock()
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "ReShade64.dll"
            runtime.write_bytes(b"selected runtime")
            with patch("dolly.gui.save_settings") as save:
                accepted = self.app._select_reshade_runtime(str(runtime))
        self.assertTrue(accepted)
        self.assertEqual(save.call_args.args[0].reshade_runtime_path, str(runtime))
        self.assertEqual(self.app.app_settings.reshade_runtime_path, str(runtime))
        self.assertIn("Launch a replay", self.app.reshade_status_text.get())
        self.app._configure_reshade.assert_not_called()

    def test_selecting_a_missing_runtime_is_refused_and_not_saved(self):
        self.app.reshade_runtime_path = Var("")
        self.app.reshade_status_text = Var("")
        self.app._log = Mock()
        with patch("dolly.gui.save_settings") as save:
            accepted = self.app._select_reshade_runtime(r"C:\none\ReShade64.dll")
        self.assertFalse(accepted)
        save.assert_not_called()
        self.assertIn("missing", self.app.reshade_status_text.get().lower())

    def test_enable_button_unlocks_once_a_runtime_is_selected(self):
        bridge = Mock()
        bridge.media_status.return_value = {"reshade": {"state": 0}}
        self.app.controller = Mock()
        self.app.controller._native_bridge.return_value = bridge
        self.app.reshade_status_text = Var("")
        self.app.reshade_disable_button = Mock()
        self.app.reshade_configure_button = Mock()
        self.app._configure_reshade = Mock()
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "ReShade64.dll"
            runtime.write_bytes(b"selected runtime")
            self.app.reshade_runtime_path = Var(str(runtime))
            self.app._refresh_reshade({})
            self.app.reshade_configure_button.configure.assert_called_with(state="normal")
            self.assertIn("Launch a replay", self.app.reshade_status_text.get())
        self.app.reshade_runtime_path = Var("C:/none/ReShade64.dll")
        self.app._refresh_reshade({})
        self.app.reshade_configure_button.configure.assert_called_with(state="disabled")

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
                     "smoothing_combo", "smoothing_row", "full_editor_switch", "aspect_curve", "launch_button", "connect_button", "initialize_button",
                     "load_replay_button", "probe_button", "disconnect_button", "capture_hotkey_checkbox", "root",
                     "_log", "_error", "_check_capture_listener", "_capture_binding_label"):
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
        # Caption updates follow state updates; require the final effective
        # button state rather than assuming the last call only changes state.
        configured = {}
        for invocation in self.app.play_replay_button.configure.call_args_list:
            configured.update(invocation.kwargs)
        self.assertEqual(configured, {"state": "normal", "text": "Play replay"})

    def test_start_timeout_releases_busy_and_keeps_finish_discard_available(self):
        app = self.app
        app.busy = True
        app.events.put(("error", "Starting video recording", RuntimeError("Start timed out")))
        app.video_export = Mock()
        app.video_export.status.return_value = {"state": "starting", "message": "Start not confirmed."}
        app._refresh_reshade = Mock()
        for name in ("video_start_button", "video_stop_button", "video_cancel_button", "video_path_entry", "video_browse_button",
                     "video_fps_combo", "video_bitrate_combo", "reshade_configure_button", "reshade_forget_button",
                     "video_game_audio_check", "video_reconstructed_audio_check"):
            setattr(app, name, Mock())
        app.clip_audio = None
        app._poll()
        self.assertFalse(app.busy)
        app.video_start_button.configure.assert_called_with(state="disabled")
        app.video_stop_button.configure.assert_called_with(state="normal")
        app.video_cancel_button.configure.assert_called_with(state="normal")
        self.editor_poll.assert_called_once()


if __name__ == "__main__":
    unittest.main()
