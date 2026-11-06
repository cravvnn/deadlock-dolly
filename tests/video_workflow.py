"""Headless VideoExport lifecycle checks shared by the core motion test.

The bridge/controller are simulated; validation, orchestration and marker files
are real. This does not run the game, encoder, GPU, or desktop UI.
"""
from pathlib import Path
from unittest.mock import Mock, patch

from dolly import player_layer
from dolly.path import Keyframe, Project
from dolly.video_export import VideoExport, VideoOptions


READY = {"connected": True, "game_running": True, "camera_backend": "native",
         "startup_stage": "editing_ready", "playing": False}


def exercise_video_workflow(case, root):
    root = Path(root)

    def rig():
        events = []
        state = {"state": "idle", "ack": 0}
        bridge = Mock()
        controller = Mock()
        controller._session = object()
        controller._demo = "replay.dem"
        controller._export_timing = None
        controller._export_resolution = None
        controller.status.return_value = dict(READY)
        controller._native_bridge.return_value = bridge
        controller.deployment_directory.return_value = root
        controller._finish_native_playback.return_value = None
        bridge.video_status.side_effect = lambda: dict(state)
        controller.prepare_native_recording.side_effect = lambda project, **kw: events.append(("prepare", project, kw))
        def timing(fps, speed):
            events.append(("timing", fps, speed))
            controller._export_timing = {"fps": fps}
        def clear_timing():
            events.append(("clear_timing",))
            controller._export_timing = None
        controller.set_export_timing.side_effect = timing
        controller.clear_export_timing.side_effect = clear_timing
        controller.clear_export_resolution.side_effect = lambda: events.append(("clear_resolution",))
        controller.mark_recording_pending.side_effect = lambda active, **kw: events.append(("reservation", active, kw))
        def begin(path, **kw):
            events.append(("begin", path, kw))
            state.update(state="recording", ack=1)
        def end(*, cancel):
            events.append(("stop", cancel))
            state.update(state="cancelled" if cancel else "completed", ack=2)
        bridge.start_video.side_effect = begin
        bridge.stop_video.side_effect = end
        return VideoExport(controller), controller, bridge, state, events

    # Validation and readiness precede any preparation, timing or native start.
    export, controller, bridge, state, events = rig()
    path = root / "existing.mp4"
    path.write_bytes(b"original take")
    with case.assertRaises(ValueError):
        export.start(VideoOptions(path))
    case.assertEqual(path.read_bytes(), b"original take")
    for options in (VideoOptions(root / "bad.mp4", fps=True),
                    VideoOptions(root / "bad.mp4", max_frames=-1),
                    VideoOptions(root / "bad.avi"),
                    VideoOptions(root / "bad.mp4", speed=0)):
        with case.subTest(options=options), case.assertRaises(ValueError):
            export.start(options)
    for field, value in (("connected", False), ("game_running", False),
                         ("camera_backend", "console"), ("startup_stage", "loading")):
        controller.status.return_value = {**READY, field: value}
        with case.subTest(field=field), case.assertRaises(RuntimeError):
            export.start(VideoOptions(root / "not-ready.mp4"))
    controller.prepare_native_recording.assert_not_called()
    controller.set_export_timing.assert_not_called()
    bridge.start_video.assert_not_called()

    # A real project remains unchanged and reaches native admission in order.
    export, controller, bridge, state, events = rig()
    project = Project(keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                 Keyframe(1, 10, 0, 0, 0, 0, 0)])
    from copy import deepcopy
    original = deepcopy(project)
    target = root / "shot.mp4"
    options = VideoOptions(target, fps=120, bitrate=10_000_000, codec="builtin",
                           fixed_step=True, speed=.5, max_frames=30)
    case.assertEqual(export.start(options, project=project,
                                  before_record=lambda: events.append(("filter",)))["state"], "recording")
    case.assertEqual(project, original)
    case.assertEqual(events[:5], [
        ("prepare", project, {"frozen": False}), ("timing", 120, .5), ("filter",),
        ("reservation", True, {}),
        ("begin", str(target), {"fps": 120, "bitrate": 10_000_000, "encoder": 0,
          "codec": 0, "quality": 0, "preset": 0, "ffmpeg_path": "", "fixed_step": True,
          "depth": False, "depth_exr": False, "shot_only": True, "white_clear": False,
          "max_frames": 30})])
    case.assertFalse(target.exists())
    with case.assertRaises(RuntimeError):
        export.start(VideoOptions(root / "second.mp4"))
    case.assertEqual(export.stop()["state"], "completed")
    case.assertLess(events.index(("stop", False)), events.index(("clear_timing",)))
    case.assertIn(("reservation", False, {}), events)
    case.assertFalse(export.finish_pending)
    case.assertIsNone(controller._export_timing)

    # Discard propagates to native and releases timing and the reservation.
    export, controller, bridge, state, events = rig()
    export.start(VideoOptions(root / "cancel.mp4", fixed_step=True))
    case.assertEqual(export.stop(cancel=True)["state"], "cancelled")
    case.assertLess(events.index(("stop", True)), events.index(("clear_timing",)))
    case.assertIn(("reservation", False, {}), events)

    # A rejected native start is terminal; an unacknowledged one stays reserved
    # until discard, preventing a second start while a late command may execute.
    for acknowledged in (True, False):
        export, controller, bridge, state, events = rig()
        def failed_begin(*args, **kwargs):
            if acknowledged:
                state.update(ack=1, command_error=1, command_message="bad dimensions")
            raise RuntimeError("native begin failed")
        bridge.start_video.side_effect = failed_begin
        with case.assertRaises(RuntimeError):
            export.start(VideoOptions(root / "error.mp4", fixed_step=True))
        case.assertIsNone(controller._export_timing)
        case.assertEqual(export.status()["state"], "failed" if acknowledged else "starting")
        if not acknowledged:
            with case.assertRaises(RuntimeError):
                export.start(VideoOptions(root / "replacement.mp4"))
            case.assertEqual(bridge.start_video.call_count, 1)
            case.assertEqual(export.stop(cancel=True)["state"], "cancelled")
            bridge.stop_video.assert_called_once_with(cancel=True)
            case.assertIn(("reservation", False, {}), events)

    # Real Players request/stop markers pin the capture identity. Unconfirmed
    # drain keeps timing, reservation and playback originals until retry.
    export, controller, bridge, state, events = rig()
    export.start(VideoOptions(root / "players.mp4", fixed_step=True), capture_only=True,
                 before_record=lambda: player_layer.begin_capture(root, 48, 8))
    request = player_layer.capture_request_id(root)
    case.assertTrue(request)
    bridge.start_video.assert_not_called()
    events.clear()
    with patch("dolly.video_export.player_layer.wait_for_capture", return_value="armed") as wait:
        with case.assertRaises(RuntimeError):
            export.stop(cancel=True)
    case.assertEqual(wait.call_args.kwargs, {"timeout": 5.0, "request_id": request})
    case.assertTrue((root / player_layer.STOP_NAME).is_file())
    case.assertTrue(export.finish_pending)
    case.assertFalse(any(event[0] in ("clear_timing", "reservation") for event in events))
    controller._finish_native_playback.assert_not_called()
    with patch("dolly.video_export.player_layer.wait_for_capture", return_value="complete"):
        case.assertEqual(export.stop()["state"], "cancelled")
    case.assertIn(("reservation", False, {}), events)
    case.assertIn(("clear_timing",), events)
    controller._finish_native_playback.assert_called_once_with()

    # A replay/session switch while native drains must not hand old timing or
    # playback originals to the new owner, even after native acknowledges.
    for changed in ("session", "demo"):
        with case.subTest(owner_changed=changed):
            capture_dir = root / ("owner-" + changed)
            capture_dir.mkdir()
            export, controller, bridge, state, events = rig()
            controller.deployment_directory.return_value = capture_dir
            export.start(VideoOptions(capture_dir / "players.mp4", fixed_step=True), capture_only=True,
                         before_record=lambda: player_layer.begin_capture(capture_dir, 48, 8))
            request = player_layer.capture_request_id(capture_dir)
            events.clear()
            def switch_owner(deployment, *, timeout, request_id):
                case.assertEqual((deployment, request_id), (capture_dir, request))
                case.assertTrue((capture_dir / player_layer.STOP_NAME).is_file())
                if changed == "session":
                    controller._session = object()
                else:
                    controller._demo = "other.dem"
                return "complete"
            with patch("dolly.video_export.player_layer.wait_for_capture", side_effect=switch_owner):
                with case.assertRaisesRegex(RuntimeError, "session or selected replay changed"):
                    export.stop()
            case.assertTrue(export.finish_pending)
            case.assertIsNotNone(controller._export_timing)
            case.assertFalse(any(event[0] in ("clear_timing", "clear_resolution", "reservation")
                                 for event in events))
            controller._require_demo.assert_not_called()
            controller._finish_native_playback.assert_not_called()
