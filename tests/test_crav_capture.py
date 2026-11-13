"""No-video orchestration with real replay framing and file publication.

Native camera acquisition is simulated here; native_camera_only_smoke owns the
actual C++/WARP boundary. These tests do not launch the game.
"""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from dolly import crav_capture, crav_replay, media_wire
from dolly.camera_export import CAMERA_FIELDS_V2
from dolly.path import Keyframe, Project
from dolly.video_export import VideoOptions
from test_replay_repair import record, varint


def replay_bytes():
    header = record(1, 0xffffffff, b"\x0a\x08PBDEMS2\0\x2a\x0adl_midtown\x68" + varint(10932))
    body = header + record(7, 64, b"\0") + record(0, 640)
    spawn = 16 + len(body)
    body += record(15, 640)
    info = 16 + len(body)
    body += record(2, 640, b"\x0d" + struct.pack("<f", 10.0) + b"\x10" + varint(640))
    return b"PBDEMS2\0" + struct.pack("<II", info, spawn) + body


class CravCaptureTests(unittest.TestCase):
    def test_path_creation_time_can_differ_from_handle_change_time(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "test.dem"
            data = replay_bytes()
            path.write_bytes(data)
            actual_stat = Path.stat

            class PathStat:
                def __init__(self, original):
                    self.original = original
                    self.st_ctime_ns = original.st_ctime_ns - 86400 * 10**9

                def __getattr__(self, name):
                    return getattr(self.original, name)

            def creation_time(selected, *args, **kwargs):
                value = actual_stat(selected, *args, **kwargs)
                return PathStat(value) if selected == path else value

            # Reproduces the observed Windows API discrepancy, independently
            # of production's identity tuple. The actual byte digest stays fixed.
            with patch.object(Path, "stat", creation_time):
                self.assertEqual(crav_replay.pin_replay(path)["sha256"], hashlib.sha256(data).hexdigest())

    def test_pin_uses_actual_bytes_metadata_and_detects_mutation_and_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "test.dem"
            raw = replay_bytes()
            path.write_bytes(raw)
            pinned = crav_replay.pin_replay(path)
            self.assertEqual(pinned, dict(path="test.dem", sha256=hashlib.sha256(raw).hexdigest(),
                build=10932, map="dl_midtown", tick_rate=64, total_ticks=640))
            reader = crav_replay.packet_index

            def mutate(*args, **kwargs):
                result = reader(*args, **kwargs)
                with path.open("ab") as stream:
                    stream.write(b"changed")
                return result

            with patch.object(crav_replay, "packet_index", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "changed"):
                    crav_replay.pin_replay(path)
            with self.assertRaisesRegex(RuntimeError, "cancel"):
                crav_replay.pin_replay(path, check_cancelled=lambda: (_ for _ in ()).throw(RuntimeError("cancel")))

    def test_camera_only_wire_requires_fixed_step_and_a_real_frame_budget(self):
        with tempfile.TemporaryDirectory() as root:
            options = VideoOptions(Path(root) / "camera.json", fps=30, fixed_step=True,
                shot_only=True, camera_track=True, camera_only=True, max_frames=3).validated()
            payload = media_wire.pack_command(2, "start_video", path=str(options.path), fps=30,
                fixed_step=True, shot_only=True, camera_track=True, camera_only=True, max_frames=3)
            self.assertEqual(media_wire.COMMAND.unpack(payload)[6], (3 << 16) | 1 | 8 | 32 | 64)
            for change in ({"max_frames": 0}, {"fixed_step": False}, {"depth": True},
                           {"shot_only": False}, {"camera_track": False}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    values = dict(path=options.path, fps=30, fixed_step=True, shot_only=True,
                        camera_track=True, camera_only=True, max_frames=3)
                    values.update(change)
                    VideoOptions(**values).validated()

    def test_capture_publishes_only_complete_restored_identity_bound_frames(self):
        for case in ("success", "replay_changed", "session_changed", "cancelled", "short", "restore_failed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                replay = root / "test.dem"
                replay.write_bytes(replay_bytes())
                output = root / "shot.crav"
                events, cancel = [], threading.Event()

                class Controller:
                    _session, _demo = object(), replay
                    _op_lock = threading.RLock()

                    def play(self, *args, **kwargs):
                        events.append("play")
                        if case == "session_changed": self._session = object()
                        if case == "cancelled": cancel.set()
                        recorder.state = "completed"
                        data = dict(format="deadlock-dolly-camera-track", version=2, fps=30,
                            fixed_step=True, complete=True, partial_reason="", source="native-view-hook",
                            frames_written=3, first_qpc=123, qpc_frequency=10000000,
                            width=1280, height=720, depth=False, fields=CAMERA_FIELDS_V2,
                            frames=[[i, i/30, i/30, 10+i, 20, 30, 0, 90, 0, 90, i/30] for i in range(3)])
                        if case == "short": data["frames"].pop()
                        recorder.options.path.write_text(json.dumps(data))

                    def stop(self):
                        events.append("restore_controller")
                        if case == "replay_changed": replay.write_bytes(b"different replay")

                class Recorder:
                    state, finish_pending = "idle", False

                    def start(self, options, **kwargs):
                        self.options = options.validated()
                        self.state, self.finish_pending = "recording", True
                        events.append("start")

                    def status(self):
                        return dict(state=self.state, frames_written=3, video_first_qpc=123,
                                    qpc_frequency=10000000, width=1280, height=720, fps=30)

                    def stop(self, **kwargs):
                        events.append("cancel" if kwargs.get("cancel") else "restore_recorder")
                        if case == "restore_failed": raise RuntimeError("restore failed")
                        self.finish_pending = False

                controller, recorder = Controller(), Recorder()
                project = Project(name="No video", start_tick=64,
                    keyframes=[Keyframe(0, 10, 20, 30, 0, 90, 0), Keyframe(2/30, 12, 20, 30, 0, 90, 0)])
                if case == "success":
                    result = crav_capture.capture_to_crav(controller, recorder, project, output,
                                                        fps=30, cancelled=cancel.is_set)
                    document = json.loads(output.read_bytes())
                    self.assertTrue(result["no_video"])
                    self.assertEqual(document["shots"][0]["camera"]["samples"][2]["position"], [12, 20, 30])
                    self.assertEqual(events, ["start", "play", "restore_recorder", "restore_controller"])
                    self.assertEqual(sorted(p.name for p in root.iterdir()), ["shot.crav", "test.dem"])
                else:
                    with self.assertRaises((ValueError, RuntimeError)):
                        crav_capture.capture_to_crav(controller, recorder, project, output,
                                                    fps=30, cancelled=cancel.is_set)
                    self.assertFalse(output.exists())
                    self.assertEqual(events[:2], ["start", "play"])
                    if case == "session_changed":
                        self.assertEqual(events, ["start", "play"])
                self.assertFalse(any(p.suffix in (".mp4", ".mkv", ".mov", ".wav") for p in root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
