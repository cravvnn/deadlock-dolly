"""Console replies that are late, flooded or unreadable, and the paths that recover from them."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from dolly import controller as C
from dolly.console import ConsoleError, ConsoleTimeout

FLOOD = ("Console response exceeds the configured size limit (1048576 bytes) for 'demo_goto'; "
         "the game console is flooding.")


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


def make_controller(console, native=None):
    process = Process()
    c = C.Controller(lambda *a, **k: None)
    c._session = SimpleNamespace(pid=42, process=process, native=native, session_dir=Path("x"), command=[])
    c._console = console
    c._crash_suffix = lambda: ""
    return c, process


class HideoutConsole:
    is_connected = True

    def __init__(self, status_error):
        self.status_error = status_error
        self.on_status = None
        self.calls = []

    def request(self, command, timeout=2.0, **kw):
        self.calls.append(command)
        if command == "demo_info":
            return "Error - Not currently playing back a demo.\n"
        if command == "status":
            if self.on_status:
                self.on_status()
            raise self.status_error
        return ""

    def supports(self, name, timeout=2.0):
        return True

    def recent_output(self, **kw):
        return ""


class HideoutStatusTests(unittest.TestCase):
    def test_late_status_reply_polls_again(self):
        for error in (ConsoleTimeout("No complete response to 'status'."), ConsoleError(FLOOD)):
            with self.subTest(error=type(error).__name__):
                c, _ = make_controller(HideoutConsole(error))
                c._capture_game_build = lambda: None
                self.assertIsNone(c._automatic_hideout_check(0))

    def test_dead_game_during_status_still_surfaces(self):
        console = HideoutConsole(ConsoleError("socket closed"))
        c, process = make_controller(console)
        c._capture_game_build = lambda: None
        console.on_status = lambda: setattr(process, "code", 3)
        with self.assertRaises(RuntimeError) as caught:
            c._automatic_hideout_check(0)
        self.assertNotIsInstance(caught.exception, ConsoleError)
        self.assertIn("Deadlock crashed", str(caught.exception))


class RecoveryConsole:
    is_connected = True

    def __init__(self, demo_info=(), demo_goto=()):
        self.demo_info = list(demo_info)
        self.demo_goto = list(demo_goto)
        self.sent = []
        self.calls = []
        self.truncated = []

    def send(self, command):
        self.sent.append(command)

    def request(self, command, timeout=2.0, **kw):
        self.calls.append(command)
        if kw.get("allow_truncated"):
            self.truncated.append(command)
        queue = {"demo_info": self.demo_info, "demo_goto": self.demo_goto}.get(command)
        if queue is None:
            return ""
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item):
            item = item()
        if isinstance(item, BaseException):
            raise item
        return item


NOT_PLAYING = "Error - Not currently playing back a demo.\n"


def playing(tick=5):
    return f"Currently playing {tick} of 1000 ticks. Minutes:1.00 File:match.dem\n"


class ShotReplayReloadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.demo = Path(folder.name) / "match.dem"
        self.demo.write_bytes(b"x")

    def make(self, console):
        native = SimpleNamespace(status=lambda: {"frame_count": 1, "tick": 5, "paused": True})
        c, process = make_controller(console, native)
        c._demo = self.demo
        for name in ("_require_unlocker", "_require_probe", "_remember_spectator_hero",
                     "_require_native_demo", "_wait_replay_camera"):
            setattr(c, name, lambda *a, **k: None)
        c._remember_replay_header = lambda p: {}
        c._recorder_active = lambda: False
        c._console_command_available = lambda n, t=2.0: True
        c._wait_paused_native_view = lambda *a, **k: {"tick": 5}
        c._probe_result = {"capabilities": {}}
        real_require_demo = c._require_demo
        self.require_demo_calls = 0

        def require_demo(*a, **k):
            # The first call is recovery's own entry check; later ones read the console.
            self.require_demo_calls += 1
            if self.require_demo_calls == 1:
                return {"playing": True, "tick": 100, "name": self.demo.name}
            return real_require_demo(*a, **k)
        c._require_demo = require_demo
        return c, process

    def test_flooded_or_partial_replies_poll_again(self):
        console = RecoveryConsole(
            demo_info=[ConsoleError(FLOOD), "Demo contents for match.dem:\n", NOT_PLAYING],
            demo_goto=[ConsoleError(FLOOD), ConsoleTimeout("late"), playing()])
        c, _ = self.make(console)
        checked = c._recover_replay_for_shot()
        self.assertEqual(checked["tick"], 5)
        self.assertEqual(sum(1 for call in console.sent if call.startswith("playdemo ")), 1)
        self.assertGreaterEqual(console.calls.count("demo_goto"), 3)
        self.assertIn("demo_info", console.truncated)
        self.assertIn("demo_goto", console.truncated)

    def test_dead_game_during_reload_still_surfaces(self):
        console = RecoveryConsole(demo_info=[NOT_PLAYING])
        c, process = self.make(console)

        def die():
            process.code = 3
            return ConsoleError("socket closed")
        console.demo_goto = [die]
        with self.assertRaises(RuntimeError) as caught:
            c._recover_replay_for_shot()
        self.assertIn("Deadlock crashed", str(caught.exception))
        self.assertEqual(console.calls.count("demo_goto"), 1)

    def test_cancel_reports_unloaded_replay(self):
        console = RecoveryConsole(demo_info=["Playing back demo: 'match.dem' at tick 5\n"])
        c, _ = self.make(console)
        messages = []
        c._message = lambda message, **k: messages.append((message, k.get("startup_stage")))
        c._replay_recovery_active = True
        threading.Timer(.3, c.cancel_replay_recovery).start()
        with self.assertRaises(RuntimeError) as caught:
            c._recover_replay_for_shot()
        expected = "Replay preparation cancelled; the replay is unloaded. Load replay, then Check camera support."
        self.assertEqual(str(caught.exception), expected)
        self.assertEqual(messages[-1], (expected, "cancelled"))
        self.assertEqual(c._replay_recovery["stage"], "cancelled")
        self.assertNotIn("close it", str(caught.exception))

    def test_failed_glow_reapply_fails_closed(self):
        console = RecoveryConsole(demo_info=[NOT_PLAYING], demo_goto=[playing()])
        c, _ = self.make(console)
        c._glow_disabled = True
        c._apply_citadel_glow = Mock(side_effect=RuntimeError("glow write refused"))
        with self.assertRaises(RuntimeError) as caught:
            c._recover_replay_for_shot()
        self.assertIn("glow write refused", str(caught.exception))
        self.assertNotEqual(c.status().get("startup_stage"), "replay_ready")


class PreshowTimescaleTests(unittest.TestCase):
    def test_failed_restore_after_intro_is_tracked(self):
        c, _ = make_controller(SimpleNamespace(is_connected=True))
        c._replay_camera = lambda: SimpleNamespace(
            identity={}, sample_startup=lambda: {"coherent": True, "ready": True})
        ticks = iter(range(5, 100))
        c._require_demo = lambda *a, **k: {"playing": True, "tick": next(ticks)}
        sent = []

        def request(command, **kw):
            sent.append(command)
            if command == "demo_timescale 1":
                raise ConsoleTimeout("late")
            return ""
        c._request = request
        with patch.object(C, "REPLAY_PRESHOW_TIMESCALE", 4.0):
            c._wait_replay_camera(None, None)
        self.assertIn("demo_timescale 1", sent)
        self.assertTrue(c._demo_speed_changed)


class DiagnosticsExportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "logs").mkdir()
        (self.root / "logs" / "dolly.log").write_text("hello")
        self.session_dir = self.root / "logs" / "20261118_120000_abcdef0123"
        self.session_dir.mkdir()
        (self.session_dir / "launch.log").write_text("y")
        (self.session_dir / "engine_console.log").write_text("x")
        self.out = self.root / "out"
        self.out.mkdir()

    def test_unreadable_log_becomes_placeholder(self):
        real_tail = C._tail_file

        def locked(path, limit=2_000_000):
            if Path(path).name == "launch.log":
                raise PermissionError(13, "in use by another process", str(path))
            return real_tail(path, limit)
        engine_locked = PermissionError(13, "engine log in use", "engine_console.log")
        with patch.object(C, "ROOT", self.root), patch.object(C, "_tail_file", locked), \
                patch("dolly.game_logging.diagnostic_log", side_effect=engine_locked):
            result = C._write_diagnostics_archive(
                self.out / "dolly-diagnostics.zip", {"a": 1}, "hist",
                ("engine_console.log", "launch.log"), SimpleNamespace(session_dir=self.session_dir), [])
        with zipfile.ZipFile(result) as archive:
            self.assertEqual(json.loads(archive.read("diagnostics.json")), {"a": 1})
            self.assertIn(b"could not read", archive.read("session/launch.log"))
            self.assertIn(b"could not read", archive.read("session/engine_console.log"))
            self.assertEqual(archive.read("logs/dolly.log"), b"hello")

    def test_failed_archive_write_leaves_no_partial_zip(self):
        with patch.object(C, "ROOT", self.root):
            with self.assertRaises(TypeError):
                C._write_diagnostics_archive(self.out / "dolly-diagnostics.zip", {"bad": object()},
                                             None, (), None, [])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_crash_dump_vanishing_during_sort_keeps_the_others(self):
        stable = self.root / "deadlock_a.mdmp"
        gone = self.root / "deadlock_b.mdmp"
        stable.write_bytes(b"a")
        gone.write_bytes(b"b")
        real_stat = Path.stat
        seen = {}

        def stat(path, *a, **k):
            if path.name == gone.name:
                seen[path.name] = seen.get(path.name, 0) + 1
                if seen[path.name] > 1:
                    raise FileNotFoundError(2, "gone", str(path))
            return real_stat(path, *a, **k)
        c = C.Controller(lambda *a, **k: None)
        with patch.object(C, "ROOT", self.root), patch.object(Path, "stat", stat):
            found = c._crash_dumps()
        self.assertIn(stable.resolve(), [path.resolve() for path in found])

    def test_crash_dump_vanishing_mid_export_does_not_fail(self):
        dump = self.root / "deadlock_a.mdmp"
        dump.write_bytes(b"a")
        real_stat = Path.stat
        seen = []

        def stat(path, *a, **k):
            if path.name == dump.name:
                seen.append(1)
                if len(seen) > 1:
                    raise FileNotFoundError(2, "gone", str(path))
            return real_stat(path, *a, **k)
        c = C.Controller(lambda *a, **k: None)
        c._crash_dumps = lambda: [dump]
        with patch.object(C, "ROOT", self.root), patch.object(Path, "stat", stat):
            result = c.export_diagnostics(self.out / "report.zip")
        with zipfile.ZipFile(result) as archive:
            report = json.loads(archive.read("diagnostics.json"))
        self.assertEqual(report["crash_dumps"], [{"name": dump.name, "bytes": 1}])


class PovRecorderTests(unittest.TestCase):
    def make(self, state):
        bridge = SimpleNamespace(video_status=lambda: {"state": state})
        c, _ = make_controller(SimpleNamespace(is_connected=True), bridge)
        c._pov_active = True
        c._recover_replay_for_shot = Mock(side_effect=AssertionError("POV reached replay recovery"))
        c.finish_pov_recording = Mock()
        return c

    def test_inactive_recorder_never_reaches_replay_recovery(self):
        c = self.make("idle")
        with self.assertRaises(RuntimeError) as caught:
            c.play_pov(SimpleNamespace())
        c._recover_replay_for_shot.assert_not_called()
        self.assertIn("start a new recording", str(caught.exception))
        c.finish_pov_recording.assert_called_once()

    def test_recorder_timeout_asks_for_a_new_recording(self):
        c = self.make("starting")
        with self.assertRaises(RuntimeError) as caught:
            c._wait_for_recorder_ready(timeout=0)
        self.assertIn("Finish recording and start a new recording", str(caught.exception))


class PlaybackGuardTests(unittest.TestCase):
    def test_relative_seek_refused_while_shot_plays(self):
        c, _ = make_controller(SimpleNamespace(is_connected=True))
        c._state["playing"] = True
        c._require_demo = Mock()
        with self.assertRaises(RuntimeError) as caught:
            c.seek_relative(5)
        self.assertIn("Stop shot playback", str(caught.exception))
        c._require_demo.assert_not_called()

    def test_speed_change_after_finished_console_shot(self):
        c, _ = make_controller(SimpleNamespace(is_connected=True))
        c._demo = Path("match.dem")
        c._require_demo = Mock(return_value={"playing": True})
        sent = []
        c._request = lambda command, **kw: sent.append(command) or ""
        c._playback_details = {"camera_backend": "console", "speed": 1.0}
        c._thread = None
        c.set_playback_speed(2)
        self.assertEqual(sent, ["demo_timescale 2"])
        c._thread = SimpleNamespace(is_alive=lambda: True)
        with self.assertRaises(RuntimeError):
            c.set_playback_speed(3)


if __name__ == "__main__":
    unittest.main()
