"""Controller integration tests with a launched-process/console simulation.

The fake responds at the transport boundary. Parsing, project interpolation,
guards, snapshot/restoration, command generation and playback are production
methods; these tests do not claim compatibility with a running game build.
"""

from collections import deque
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from dolly.controller import Controller, frame_commands, parse_camera, read_cvar_value
from dolly.console import ConsoleError
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT


def make_project():
    return Project(name="Test shot", start_tick=100, tick_rate=10,
                   interpolation="linear", lens_interpolation="linear", keyframes=[
                       Keyframe(0, 0, 0, 200, 0, 0, 0, 90, STANDARD_ASPECT),
                       Keyframe(10, 100, 200, 300, 30, 90, 10, 90, 1.0)])


def metadata_demo(name="example.dem"):
    """The metadata-only shape from the user's failed step-5 diagnostics."""
    return (
        "Demo contents for B:/SteamLibrary/steamapps/common/Deadlock/game/citadel/replays/"
        + name + ":\n"
        'DemoFileHeader: demo_file_stamp: "PBDEMS2\\000"\n'
        'patch_version: 48\nclient_name: "SourceTV Demo"\nmap_name: "start"\n'
        "build_num: 10854\nserver_start_tick: 1658\n\n"
        "DemoFileInfo: playback_time: 2522.15625\n"
        "playback_ticks: 161418\nplayback_frames: 161410\ngame_info {\n}\n"
    )


class VectorCvarConsoleTests(unittest.TestCase):
    def test_current_vector_readback_and_command_format(self):
        name = "r_dof_override_ranges"
        for text in ('r_dof_override_ranges = "-100 0 180 2000" (default "0 0 0 0")',
                     'r_dof_override_ranges: -100 0 180 2000'):
            self.assertEqual(read_cvar_value(name, text), (-100, 0, 180, 2000))
        for text in ('r_dof_override_ranges = 1 2 3', 'r_dof_override_ranges = 1 2 3 4 5',
                     'r_dof_override_ranges = invalid (default 1 2 3 4)'):
            with self.assertRaises(ValueError):read_cvar_value(name, text)
        frame = make_project().evaluate(0)
        frame["cvars"] = {name: (-100, 0, 180, 2000)}
        self.assertIn(name + " -100 0 180 2000", frame_commands(frame))
        for bad in ((1, 2, 3), "1 2 3 4;quit", (1, 2, 3, float("inf"))):
            frame["cvars"] = {name: bad}
            with self.assertRaises(ValueError):frame_commands(frame)


class FakeConsole:
    def __init__(self):
        self.is_connected = True
        self.demo_name = "example.dem"
        self.tick = 100
        self.demo_outputs = deque()
        self.demo_output = None
        self.goto_outputs = deque()
        self.goto_output = None
        self.requests = []
        self.sent = []
        self.events = []
        self.operations = []
        self.resume_snapshots = []
        self.paused = True
        self.unhide_output = "Removed hidden flags from 120 concommands\nRemoved hidden flags from 340 cvars"
        self.complete_replay_on_send = False
        self.values = {
            "r_aspectratio": 0.0,
            "citadel_camera_fov": 90.0,
            "citadel_camera_spectator_fov": 80.0,
            "r_citadel_depthoffield_enable": 0.0,
            "r_citadel_depthoffield_focus_distance": 600.0,
            "r_citadel_depthoffield_aperture_diameter": 0.5,
            "r_depth_of_field": 1.0,
            "demo_timescale": 0.5,
            "citadel_hud_visible": 1.0,
            "citadel_hide_replay_hud": 0.0,
            "hud_free_cursor": -1.0,
            "engine_no_focus_sleep": 20.0,
        }
        self.pose = [1, 2, 3, 4, 5]
        self.closed = False
        self.fail_commands = set()

    def connect(self, **kwargs):
        self.is_connected = True

    def close(self):
        self.is_connected = False
        self.closed = True

    @property
    def camera_writes(self):
        return [command for command in self.requests + self.sent if command.startswith("spec_goto ")]

    def supports(self, name):
        return True

    def send(self, command):
        self.sent.append(command)
        self.events.append(command)
        if command.startswith('playdemo "') and self.complete_replay_on_send:
            self.demo_name = Path(command[len('playdemo "'):-1]).name
            self.demo_output = None
        self._execute(command)

    def _execute(self, command):
        for item in command.split(";"):
            parts = item.strip().split()
            if not parts:
                continue
            if parts[0] == "demo_gototick":
                self.tick = int(parts[1])
            elif parts[0] == "spec_goto":
                self.pose = [float(value) for value in parts[1:6]]
            elif parts[0] == "demo_pause":
                self.paused = True
            elif parts[0] == "demo_resume":
                self.paused = False
                self.resume_snapshots.append({"tick": self.tick, "pose": list(self.pose),
                                              "values": dict(self.values)})
            elif parts[0] in self.values and len(parts) == 2:
                self.values[parts[0]] = float(parts[1])

    def request(self, command, timeout=3, completion_patterns=None):
        self.requests.append(command)
        self.events.append(command)
        if command in self.fail_commands:
            raise RuntimeError("Simulated console failure")
        # The engine executes semicolon-separated commands in order. A camera
        # batch may write the pose/lens and then return demo_goto's status.
        outputs = []
        for item in command.split(";"):
            item = item.strip()
            if not item:
                continue
            self.operations.append(item)
            if item in self.fail_commands:
                raise RuntimeError("Simulated console failure")
            output = self._request_item(item)
            if output:
                outputs.append(output)
        return "\n".join(outputs)

    def _request_item(self, command):
        if command == "demo_goto":
            if self.goto_outputs:
                self.goto_output = self.goto_outputs.popleft()
                if isinstance(self.goto_output, int):
                    self.tick = self.goto_output
            if isinstance(self.goto_output, int):
                return (f"Currently playing {self.tick} of 161418 ticks. "
                        f"Minutes:42.04 File:{self.demo_name}")
            return self.goto_output or ""
        if command == "demo_info":
            if self.demo_outputs:
                item = self.demo_outputs.popleft()
                if isinstance(item, int):
                    self.tick = item
                else:
                    self.demo_output = item
            return self.demo_output if self.demo_output is not None else (
                f"[Demo] Playing back demo: '{self.demo_name}' at tick {self.tick}")
        if command == "spec_pos":
            return "[Console] spec_goto " + " ".join(map(str, self.pose))
        if command == "cvar_unhide":
            return self.unhide_output
        if command in self.values:
            return f'[Console] "{command}" = "{self.values[command]}" ( def. "12345" )'
        self._execute(command)
        return ""


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class CountedEvent:
    """Advance a deterministic clock without sleeping; stop after N waits."""
    def __init__(self, clock, limit):
        self.clock = clock
        self.limit = limit
        self.waits = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def clear(self):
        self.stopped = False

    def wait(self, timeout):
        self.clock.now += timeout
        self.waits += 1
        self.stopped = self.waits >= self.limit
        return self.stopped


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.messages = []
        self.controller = Controller(self.messages.append)
        self.console = FakeConsole()
        self.controller._console = self.console
        self.process = SimpleNamespace(poll=lambda: None)
        self.controller._session = SimpleNamespace(process=self.process, pid=1234,
                                                    session_dir=Path("unused"), close=MagicMock(),
                                                    restore_gameinfo=MagicMock(), owns_console_port=lambda: True)
        self.controller._demo = Path("/chosen/example.dem")
        self.controller._unlocker_pid = self.controller._session.pid
        self.controller._probe_result = {"capabilities": {
            "spec_goto": True, "cl_citadel_forceangles": True, "r_aspectratio": True}}

    def test_guard_accepts_expected_demo_and_rejects_unknown_or_different(self):
        self.console.demo_name = r"D:\replays\EXAMPLE.DEM"
        self.assertEqual(self.controller._require_demo()["tick"], 100)
        for output in (
            "Error - Not currently playing back a demo.",
            "Unknown command: demo_info",
            "Demo paused at engine time 20, demo tick 100",
            "Playing back demo: 'different.dem' at tick 100",
            "Playing back demo: 'example.dem' at tick 100\nError - Not currently playing back a demo.",
        ):
            self.console.demo_output = output
            with self.subTest(output=output), self.assertRaises((RuntimeError, ValueError)):
                self.controller.apply(make_project(), 0)
        self.assertFalse(any("spec_goto " in command for command in self.console.requests))
        self.assertEqual(self.console.sent, [])

    def test_guard_requires_own_live_process_and_connection(self):
        self.process.poll = lambda: 0
        with self.assertRaisesRegex(RuntimeError, "Launch"):
            self.controller._require_demo()
        self.process.poll = lambda: None
        self.console.is_connected = False
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            self.controller._require_demo()
        self.assertEqual(self.console.requests, [])

    def test_dead_process_console_failure_reports_the_crash_not_the_socket(self):
        process = self.process

        def die_then_raise(command, timeout=3, completion_patterns=None):
            process.poll = lambda: -1073741819
            raise ConsoleError("[WinError 10054] An existing connection was forcibly closed by the remote host")

        self.console.request = die_then_raise
        with self.assertRaises(RuntimeError) as caught:
            self.controller._request("demo_goto")
        message = str(caught.exception)
        self.assertIn("crashed", message)
        self.assertIn("incompatible", message)
        self.assertIn("demo_goto", message)

    def test_live_process_console_failure_is_not_reported_as_a_crash(self):
        def fail(*args, **kwargs):
            raise ConsoleError("Simulated console timeout")

        self.console.request = fail
        with self.assertRaisesRegex(RuntimeError, "Simulated console timeout"):
            self.controller._request("demo_goto")

    def test_replay_begun_waits_until_playback_advances(self):
        self.console.tick = 0
        self.assertIsNone(self.controller._replay_begun())
        self.console.tick = 1
        self.assertIsNone(self.controller._replay_begun())
        self.console.tick = 2
        self.assertEqual(self.controller._replay_begun()["tick"], 2)

    def test_replay_begun_reraises_when_the_game_died(self):
        process = self.process

        def die(*args, **kwargs):
            process.poll = lambda: 1
            raise RuntimeError("Simulated console failure")

        self.console.request = die
        with self.assertRaisesRegex(RuntimeError, "Simulated console failure"):
            self.controller._replay_begun()

    def test_live_demo_guard_accepts_complete_custom_recording_name_only(self):
        self.controller._demo = Path("/chosen/practice.session.01.dem")
        for reported in ("practice.session.01", "practice.session.01.dem",
                         r"C:\Deadlock\game\citadel\practice.session.01.DEM"):
            self.console.demo_name = reported
            with self.subTest(reported=reported):
                self.assertEqual(self.controller._require_demo()["tick"], 100)
        for reported in ("practice.session", "practice.session.01.info",
                         "practice.session.01.dem.info", "practice.session.02.dem"):
            self.console.demo_name = reported
            with self.subTest(reported=reported), self.assertRaisesRegex(RuntimeError, "different"):
                self.controller._require_demo()
        self.assertEqual(self.console.sent, [])

    def test_native_demo_guard_preserves_custom_name_and_state_checks(self):
        self.controller._demo = Path("/chosen/practice.session.01.dem")
        for name in ("practice.session.01", "practice.session.01.dem",
                     r"C:\Deadlock\game\citadel\practice.session.01.DEM"):
            with self.subTest(name=name):
                self.controller._require_native_demo({"demo_name": name, "state": "playing"})
                self.controller._require_native_demo({"demo_name": name, "state": "probe"}, allow_idle=True)
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    self.controller._require_native_demo({"demo_name": name, "state": "fault"}, allow_idle=True)
        for name in (None, "practice.session", "practice.session.01.info",
                     "practice.session.01.dem.info", "practice.session.02.dem"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "identity changed"):
                self.controller._require_native_demo({"demo_name": name, "state": "playing"})

    def test_status_distinguishes_clean_game_close_from_unexpected_exit(self):
        self.controller._state.update(connected=True, startup_stage="replay_ready",
                                      message="Camera ready.")
        for exit_code, stage, message in (
                (0, "game_closed", "Deadlock closed."),
                (-1073741819, "failed", "Deadlock exited (code -1073741819).")):
            with self.subTest(exit_code=exit_code):
                self.process.poll = lambda code=exit_code: code
                status = self.controller.status()
                self.assertFalse(status["connected"])
                self.assertFalse(status["unlocker_ready"])
                self.assertEqual(status["startup_stage"], stage)
                self.assertIn(message, status["message"])
                if exit_code == 0:
                    self.assertNotIn("unexpected", status["message"])
                else:
                    self.assertIn("Export diagnostics", status["message"])
        self.assertEqual(self.console.requests, [])

    def test_launch_passes_protocol_and_only_existing_demo(self):
        self.controller._session = None
        self.controller._applied_pose = {"roll": 42}
        self.controller._demo_speed_changed = True
        self.controller._replay_requested = True
        self.controller._startup_evidence = {"initialized": True, "pid": 1234}
        self.controller._state.update(time=9, tick=190)
        with tempfile.TemporaryDirectory() as directory:
            replay = Path(directory) / "new.dem"
            replay.write_bytes(b"demo fixture")
            session = SimpleNamespace(pid=700, session_dir=Path(directory), process=self.process,
                                      restore_gameinfo=MagicMock(), owns_console_port=lambda: True)
            with patch("dolly.controller.launcher.launch", return_value=session) as launch:
                result = self.controller.launch("game", str(replay), protocol="netcon")
                launch.assert_called_once_with("game", str(replay.resolve()), port=29090, protocol="netcon")
            self.assertEqual(result["pid"], 700)
            self.assertEqual(self.controller._demo, replay.resolve())
            self.assertEqual(self.controller._protocol, "netcon")
            self.assertIsNone(self.controller._applied_pose)
            self.assertFalse(self.controller._demo_speed_changed)
            self.assertIsNone(self.controller._unlocker_pid)
            self.assertFalse(self.controller._replay_requested)
            self.assertEqual(self.controller._startup_evidence, {})
            self.assertFalse(self.controller.status()["unlocker_ready"])
            self.assertEqual(self.controller.status()["time"], 0)
            self.assertIsNone(self.controller.status()["tick"])
            self.assertEqual(self.controller._launch_attempt["status"], "started")
            self.assertEqual(self.controller._launch_attempt["pid"], 700)

    def test_failed_launch_diagnostics_retain_paths_and_actual_error_before_session_exists(self):
        self.controller._session = None
        with tempfile.TemporaryDirectory() as directory:
            replay = Path(directory) / "example.dem"
            replay.write_bytes(b"fixture")
            game = "B:/SteamLibrary/steamapps/common/Deadlock/game/bin/win64/deadlock.exe"
            with patch("dolly.controller.launcher.launch", side_effect=RuntimeError("Missing gameinfo.gi")), self.assertLogs("dolly", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "Missing gameinfo"):
                    self.controller.launch(game, str(replay), protocol="netcon")
            self.assertIn("Missing gameinfo.gi", self.controller.status()["message"])
            destination = self.controller.export_diagnostics(Path(directory) / "failed.zip")
            with zipfile.ZipFile(destination) as archive:
                report = json.loads(archive.read("diagnostics.json"))
            self.assertEqual(report["launch_attempt"], {
                "game_path": game, "demo_path": str(replay), "protocol": "netcon",
                "status": "failed", "error": "Missing gameinfo.gi", "error_type": "RuntimeError",
            })
            self.assertIsNone(self.controller._session)

    def test_launch_input_failure_is_recorded_without_starting_launcher(self):
        self.controller._session = None
        with patch("dolly.controller.launcher.launch") as launch, self.assertLogs("dolly", level="ERROR"):
            with self.assertRaisesRegex(ValueError, "Choose a local"):
                self.controller.launch("B:/Deadlock", "")
        launch.assert_not_called()
        self.assertEqual(self.controller._launch_attempt["status"], "failed")
        self.assertEqual(self.controller._launch_attempt["demo_path"], "")

    def test_probe_checks_replay_without_reinitializing_unlocker_or_restoring_gameinfo(self):
        report = self.controller.probe()
        self.controller._session.restore_gameinfo.assert_not_called()
        self.assertNotIn("cvar_unhide", self.console.requests)
        self.assertTrue(report["capabilities"]["r_aspectratio"])
        self.assertFalse(report["camera_effect_verified"])
        self.assertEqual(self.controller.status()["startup_stage"], "replay_ready")

    def test_probe_accepts_user_metadata_without_inventing_current_tick(self):
        self.controller._demo = Path("/chosen/99917728.dem")
        self.console.demo_output = metadata_demo("99917728.dem")
        report = self.controller.probe()
        self.assertTrue(report["demo"]["playing"])
        self.assertEqual(report["demo"]["total_ticks"], 161418)
        self.assertIsNone(report["demo"]["tick"])
        self.assertIsNone(self.controller.status()["tick"])
        self.assertFalse(report["live_tick_available"])
        self.assertEqual(self.controller.status()["startup_stage"], "replay_ready")
        self.assertIn("Frozen preview", self.controller.status()["message"])
        self.assertEqual(self.console.camera_writes, [])

    def test_current_tick_uses_live_goto_with_filename_without_metadata_query(self):
        self.console.tick = 112025
        self.console.goto_output = 112025
        self.console.demo_name = r"B:\SteamLibrary\steamapps\common\Deadlock\game\citadel\replays\EXAMPLE.DEM"
        self.console.fail_commands.add("demo_info")
        self.assertEqual(self.controller.current_tick(), 112025)
        self.assertEqual(self.controller.status()["tick"], 112025)
        self.assertEqual(self.console.requests, ["demo_goto"])

    def test_legacy_goto_live_tick_requires_fresh_matching_metadata_identity(self):
        self.console.goto_output = "Currently playing 112025 of 161418 ticks. Minutes:42.04"
        self.console.demo_output = metadata_demo()
        self.assertEqual(self.controller.current_tick(), 112025)
        self.assertEqual(self.console.requests, ["demo_goto", "demo_info"])
        self.console.demo_output = metadata_demo("other.dem")
        with self.assertRaisesRegex(RuntimeError, "different"):
            self.controller.current_tick()
        self.assertEqual(self.console.requests[-2:], ["demo_goto", "demo_info"])

    def test_goto_wrong_replay_is_rejected_without_trusting_old_metadata(self):
        self.console.goto_output = "Currently playing 112025 of 161418 ticks. Minutes:42.04 File:other.dem"
        self.console.demo_output = metadata_demo()
        with self.assertRaisesRegex(RuntimeError, "different"):
            self.controller.apply(make_project(), 0)
        self.assertNotIn("demo_info", self.console.requests)
        self.assertEqual(self.console.camera_writes, [])

    def test_goto_explicit_no_demo_overrides_previously_valid_metadata(self):
        self.console.goto_output = "Error - Not currently playing back a demo."
        self.console.demo_output = metadata_demo()
        with self.assertRaisesRegex(RuntimeError, "local demo must be playing"):
            self.controller._require_demo(require_tick=False)
        self.assertEqual(self.console.requests, ["demo_goto"])

    def test_current_tick_never_uses_metadata_total_or_cached_tick(self):
        self.controller._state["tick"] = 112025
        self.console.demo_output = metadata_demo()
        with self.assertRaisesRegex(RuntimeError, "current tick was not reported"):
            self.controller.current_tick()
        self.assertIsNone(self.controller.status()["tick"])
        self.assertEqual(self.console.requests, ["demo_goto", "demo_info"])

    def test_normal_play_requires_live_tick_before_seeking_or_writing_camera(self):
        self.console.demo_output = metadata_demo()
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaisesRegex(RuntimeError, "current tick was not reported"):
                self.controller.play(make_project(), frozen=False)
        worker.assert_not_called()
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(any(command.startswith("demo_gototick ") for command in self.console.requests))
        self.assertNotIn("demo_resume", self.console.requests)

    def test_frozen_metadata_playback_reasserts_pause_and_uses_shot_clock(self):
        self.console.demo_output = metadata_demo()
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch("dolly.controller.threading.Thread", return_value=worker):
            self.controller.play(make_project(), frozen=True)
        self.assertIn("demo_pause", self.console.requests)
        self.assertNotIn("demo_resume", self.console.requests)
        self.assertFalse(any(command.startswith("demo_gototick ") for command in self.console.requests))
        self.console.requests.clear()
        self.console.operations.clear()
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=3)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(make_project(), 0, 1, 60, True)
        self.assertEqual(len(self.console.camera_writes), 3)
        self.assertEqual(self.console.operations.count("demo_pause"), 4)
        self.assertAlmostEqual(self.controller.status()["time"], 2 / 60)
        self.assertIsNone(self.controller.status()["tick"])
        self.assertFalse(self.controller.status()["playing"])

    def test_metadata_capture_reads_actual_freecam_and_aspect_at_requested_shot_time(self):
        self.console.demo_output = metadata_demo()
        self.console.pose = [-133.25, 718.5, 2001, -20, 125]
        self.console.values["r_aspectratio"] = 1.25
        frame = self.controller.capture(2.5)
        self.assertEqual(frame, Keyframe(2.5, -133.25, 718.5, 2001, -20, 125, 0, 90, 1.25))
        self.assertIsNone(self.controller.status()["tick"])
        self.assertLess(self.console.requests.index("demo_pause"), self.console.requests.index("spec_pos"))
        self.assertEqual(self.console.camera_writes, [])
        with self.assertRaisesRegex(RuntimeError, "current tick was not reported"):
            self.controller.capture_at_replay(None, 64)

    def test_live_goto_drives_capture_timestamp_and_path_rewind(self):
        self.console.goto_outputs.extend([124, 125, 125])
        self.console.demo_output = metadata_demo()
        frame = self.controller.capture_at_replay(100, 10)
        self.assertEqual(frame.time, 2.5)
        self.assertEqual(self.controller.status()["tick"], 125)
        self.assertNotIn("demo_info", self.console.requests)
        self.console.requests.clear()
        self.console.goto_outputs.extend([100, 110, 120, 105, 105])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=4)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(make_project(), 0, 1, 60, False)
        x_positions = [float(command.split()[1]) for command in self.console.camera_writes]
        for position, acknowledged in zip(x_positions, (0, 10, 20, 5)):
            self.assertGreaterEqual(position, 0)
            self.assertLessEqual(position, acknowledged + 1,
                                 "Camera may lead by at most one replay tick")
        self.assertEqual(x_positions[:3], sorted(x_positions[:3]))
        self.assertGreaterEqual(x_positions[-1], 5, "A real rewind resets to its acknowledged tick")
        self.assertGreaterEqual(self.controller.status()["time"], 0.5)
        self.assertLessEqual(self.controller.status()["time"], 0.6)
        self.assertNotIn("demo_info", self.console.requests)

    def _prepare_hideout(self):
        self.controller._unlocker_pid = None
        self.controller._probe_result = {}
        self.console.demo_output = "Error - Not currently playing back a demo."

    def test_load_and_probe_require_unlocker_initialized_for_this_process(self):
        for stale_pid in (None, 9999):
            self.controller._unlocker_pid = stale_pid
            with self.subTest(pid=stale_pid):
                for operation in (self.controller.load_replay, self.controller.probe):
                    with self.assertRaisesRegex(RuntimeError, "Initialize the unlocker"):
                        operation()
        self.assertEqual(self.console.sent, [])
        self.assertNotIn("cvar_unhide", self.console.requests)

    def test_initialize_refuses_already_playing_replay_before_unlocker_command(self):
        self.controller._unlocker_pid = None
        with self.assertRaisesRegex(RuntimeError, "replay is already loaded"):
            self.controller.initialize_unlocker()
        self.assertNotIn("cvar_unhide", self.console.requests)
        self.controller._session.restore_gameinfo.assert_not_called()
        self.assertFalse(self.controller.status()["unlocker_ready"])

    def test_hideout_confirmation_and_restore_precede_async_replay_load(self):
        self._prepare_hideout()
        self.controller._session.restore_gameinfo.side_effect = lambda: self.console.events.append("restore_gameinfo")
        with tempfile.TemporaryDirectory(prefix="dolly replay ") as directory:
            replay = Path(directory) / "example.dem"
            replay.write_bytes(b"fixture")
            self.controller._demo = replay
            result = self.controller.initialize_unlocker()
            self.assertEqual(result["confirmation_counts"], {"concommands": 120, "cvars": 340})
            self.assertTrue(result["initialized"])
            self.assertEqual(self.console.sent, [], "Initialization must leave the game in the hideout")
            self.assertLess(self.console.events.index("cvar_unhide"), self.console.events.index("restore_gameinfo"))
            load = self.controller.load_replay()
            command = 'playdemo "' + replay.resolve().as_posix() + '"'
            self.assertEqual(self.console.sent, [command])
            self.assertNotIn(command, self.console.requests, "Map loading must not block on a command response deadline")
            self.assertLess(self.console.events.index("restore_gameinfo"), self.console.events.index(command))
            self.assertFalse(load["confirmed_loaded"])
            self.assertEqual(self.controller.status()["startup_stage"], "loading_replay")
            with self.assertRaisesRegex(RuntimeError, "local demo must be playing"):
                self.controller.probe()
            self.console.demo_output = None
            report = self.controller.probe()
            self.assertEqual(report["demo"]["name"], "example.dem")
            self.assertEqual(self.controller.status()["startup_stage"], "replay_ready")

    def test_missing_or_partial_unhide_confirmation_keeps_replay_blocked(self):
        for output in ("", "Removed hidden flags from 120 concommands",
                       "Removed hidden flags from 340 cvars", "Unknown command: cvar_unhide"):
            self._prepare_hideout()
            self.console.unhide_output = output
            with self.subTest(output=output):
                with self.assertRaises(RuntimeError):
                    self.controller.initialize_unlocker()
                with self.assertRaisesRegex(RuntimeError, "Initialize the unlocker"):
                    self.controller.load_replay()
                self.assertFalse(self.controller.status()["unlocker_ready"])
        self.controller._session.restore_gameinfo.assert_not_called()
        self.assertEqual(self.console.sent, [])

    def test_unknown_demo_info_records_uncertainty_but_still_requires_both_counts(self):
        for output in ("", "Unknown command: demo_info", "New demo status format"):
            with self.subTest(output=output):
                self._prepare_hideout()
                self.console.demo_output = output
                self.console.unhide_output = "Removed hidden flags from 0 cvars"
                with self.assertRaisesRegex(RuntimeError, "could not confirm"):
                    self.controller.initialize_unlocker()
                self.assertIsNone(self.controller._startup_evidence["before_unlocker"]["playing"])
                self.console.unhide_output = "Removed hidden flags from 0 concommands\nRemoved hidden flags from 0 cvars"
                result = self.controller.initialize_unlocker()
                self.assertTrue(result["initialized"], "Zero counts are valid after cvars were already unhidden")
                self.assertIsNone(result["before_unlocker"]["playing"])
                self.assertIsNone(result["after_unlocker"]["playing"])
                self.assertTrue(self.controller.status()["unlocker_ready"])
        self.assertEqual(self.console.sent, [])

    def test_demo_opening_during_unlocker_initialization_blocks_ready_state(self):
        self._prepare_hideout()
        self.console.demo_outputs.extend([
            "Error - Not currently playing back a demo.",
            "Playing back demo: 'example.dem' at tick 100",
        ])
        with self.assertRaisesRegex(RuntimeError, "opened while"):
            self.controller.initialize_unlocker()
        self.assertIn("cvar_unhide", self.console.requests)
        self.assertFalse(self.controller.status()["unlocker_ready"])
        self.controller._session.restore_gameinfo.assert_not_called()

    def test_restore_failure_prevents_replay_load_even_after_unhide_confirmation(self):
        self._prepare_hideout()
        self.controller._session.restore_gameinfo.side_effect = RuntimeError("Game config changed; backup retained")
        with self.assertRaisesRegex(RuntimeError, "backup retained"):
            self.controller.initialize_unlocker()
        with self.assertRaisesRegex(RuntimeError, "Initialize the unlocker"):
            self.controller.load_replay()
        self.assertFalse(self.controller.status()["unlocker_ready"])
        self.assertEqual(self.console.sent, [])

    def test_reconnect_same_live_process_preserves_unlocker_readiness(self):
        self._prepare_hideout()
        self.controller.initialize_unlocker()
        with patch("dolly.controller.ConsoleClient", return_value=self.console):
            self.controller.connect()
        self.assertTrue(self.controller.status()["unlocker_ready"])
        self.controller.initialize_unlocker()
        self.assertEqual(self.console.requests.count("cvar_unhide"), 1)
        self.controller._session.restore_gameinfo.assert_called_once_with()

    def test_replay_path_is_revalidated_and_console_separators_never_dispatched(self):
        with tempfile.TemporaryDirectory() as directory:
            self.controller._demo = Path(directory) / "deleted.dem"
            self.controller._demo.write_bytes(b"fixture")
            self.controller._demo.unlink()
            with self.assertRaisesRegex(RuntimeError, "existing"):
                self.controller.load_replay()
            for name in ("bad;quit.dem", "bad+quit.dem", 'bad"quit.dem', "bad\nquit.dem"):
                self.controller._demo = Path(directory) / name
                # Quotes/newlines cannot be created as Windows filenames. Keep
                # the command-boundary check independent of host filesystem.
                with self.subTest(name=name), patch("dolly.launcher.Path.is_file", return_value=True), self.assertRaisesRegex(RuntimeError, "separators"):
                    self.controller.load_replay()
        self.assertEqual(self.console.sent, [])
        self.assertFalse(self.controller._replay_requested)

    def test_instant_demo_load_is_only_confirmed_by_probe(self):
        self._prepare_hideout()
        with tempfile.TemporaryDirectory() as directory:
            replay = Path(directory) / "example.dem"
            replay.write_bytes(b"fixture")
            self.controller._demo = replay
            self.controller.initialize_unlocker()
            self.console.complete_replay_on_send = True
            result = self.controller.load_replay()
            self.assertFalse(result["confirmed_loaded"])
            self.assertEqual(self.controller._probe_result, {})
            self.assertEqual(self.controller.status()["startup_stage"], "loading_replay")
            self.controller.probe()
            self.assertEqual(self.controller.status()["startup_stage"], "replay_ready")

    def test_capture_parses_live_position_and_current_aspect(self):
        self.console.values["r_aspectratio"] = 1.25
        frame = self.controller.capture(2.5)
        self.assertEqual(frame, Keyframe(2.5, 1, 2, 3, 4, 5, 0, 90, 1.25))

    def test_capture_after_apply_preserves_applied_aspect(self):
        self.controller.apply(make_project(), 10)
        self.assertEqual(self.console.values["r_aspectratio"], 1.0)
        frame = self.controller.capture(11)
        self.assertEqual(frame.aspect_ratio, 1.0, "Capture must read the visible aspect ratio without restoring it first")

    def test_capture_at_replay_first_key_is_zero_at_acknowledged_paused_tick(self):
        self.console.demo_outputs.extend([119, 120, 120])
        frame = self.controller.capture_at_replay(None, 10)
        self.assertEqual(frame.time, 0)
        self.assertEqual(self.controller.status()["tick"], 120)
        self.assertLess(self.console.requests.index("demo_pause"), self.console.requests.index("spec_pos"))

    def test_capture_at_replay_existing_start_uses_relative_seconds(self):
        self.console.demo_outputs.extend([124, 125, 125])
        frame = self.controller.capture_at_replay(100, 10)
        self.assertEqual(frame.time, 2.5)
        self.assertEqual(self.controller.status()["tick"], 125)
        self.assertEqual(frame.fov, 90)

    def test_capture_at_replay_rejects_before_start_without_capturing_camera(self):
        self.console.tick = 90
        with self.assertRaisesRegex(ValueError, "before this shot"):
            self.controller.capture_at_replay(100, 10)
        self.assertNotIn("spec_pos", self.console.requests)
        self.assertEqual(self.console.camera_writes, [])

    def test_capture_at_replay_rejects_invalid_rate_or_start_before_requests(self):
        for start, rate in ((-1, 10), (1.5, 10), (math.nan, 10),
                            (100, 0), (100, -10), (None, math.inf), (None, math.nan)):
            self.console.requests.clear()
            with self.subTest(start=start, rate=rate), self.assertRaises(ValueError):
                self.controller.capture_at_replay(start, rate)
            self.assertEqual(self.console.requests, [])

    def test_stop_restores_actual_snapshot_when_no_explicit_override(self):
        project = make_project()
        project.setup_values = {"r_citadel_depthoffield_enable": 1}
        project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
                                   [TrackKey(0, 900), TrackKey(10, 1200)])]
        self.controller.apply(project, 5)
        self.assertAlmostEqual(self.console.values["r_aspectratio"], (STANDARD_ASPECT + 1) / 2)
        self.assertEqual(self.console.values["r_citadel_depthoffield_enable"], 1)
        self.assertEqual(self.console.values["r_citadel_depthoffield_focus_distance"], 1050)
        self.controller.stop()
        self.assertEqual(self.console.values["r_aspectratio"], 0.0)
        self.assertEqual(self.console.values["r_citadel_depthoffield_enable"], 0)
        self.assertEqual(self.console.values["r_citadel_depthoffield_focus_distance"], 600)
        self.assertEqual(self.controller._restore, {})

    def test_explicit_restore_override_is_honored_after_reading_current_value(self):
        project = make_project()
        project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
                                   [TrackKey(0, 900)], restore_value=111)]
        self.controller.apply(project, 0)
        self.assertIn("r_citadel_depthoffield_focus_distance", self.console.requests)
        self.controller.stop()
        self.assertEqual(self.console.values["r_citadel_depthoffield_focus_distance"], 111)

    def test_out_of_range_restore_override_is_rejected_before_camera_writes(self):
        cases = (("r_citadel_depthoffield_focus_distance", 600, -1, "linear"),
                 ("r_citadel_depthoffield_aperture_diameter", 1, 4, "linear"),
                 ("r_citadel_depthoffield_enable", 1, 0.5, "step"),
                 ("r_citadel_depthoffield_mode", 1, 3, "step"))
        for name, valid, invalid, interpolation in cases:
            project = make_project()
            project.tracks = [CvarTrack(name, [TrackKey(0, valid)], interpolation, invalid)]
            self.console.requests.clear()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.controller.apply(project, 0)
            self.assertEqual(self.console.camera_writes, [])
            self.assertEqual(self.controller._restore, {})

    def test_restore_is_retained_and_writes_blocked_after_demo_change(self):
        self.controller.apply(make_project(), 10)
        before = len(self.console.requests)
        self.console.demo_name = "other.dem"
        self.controller.stop()
        self.assertEqual(self.console.requests[before:], ["demo_goto", "demo_info"])
        self.assertEqual(self.controller._restore, {"r_aspectratio": 0.0})
        self.assertIn("Could not restore", self.messages[-1])

    def test_pause_keeps_current_values_and_stop_restores(self):
        self.controller.apply(make_project(), 10)
        self.controller.pause()
        self.assertEqual(self.console.values["r_aspectratio"], 1.0)
        self.assertTrue(self.controller._restore)
        self.controller.stop()
        self.assertEqual(self.console.values["r_aspectratio"], 0.0)

    def test_project_rejects_fractional_toggle_and_aspect_out_of_supported_range(self):
        project = make_project()
        project.keyframes[0].aspect_ratio = 0.49
        with self.assertRaisesRegex(ValueError, "0.5"):
            self.controller.apply(project, 0)
        project.keyframes[0].aspect_ratio = STANDARD_ASPECT
        project.tracks = [CvarTrack("r_citadel_depthoffield_enable", [TrackKey(0, 0), TrackKey(5, 1)])]
        with self.assertRaisesRegex(ValueError, "Step"):
            self.controller.apply(project, 0)
        project.tracks[0].interpolation = "step"
        project.tracks[0].keys[1].value = 0.5
        with self.assertRaisesRegex(ValueError, "whole-number"):
            self.controller.apply(project, 0)
        self.assertFalse(any("spec_goto " in command for command in self.console.requests))

    def test_seek_clamps_time_and_maps_shot_seconds_to_demo_ticks(self):
        self.controller.seek(make_project(), 2.5)
        self.assertIn("demo_gototick 125 0 1", self.console.requests)
        self.assertEqual(self.controller.status()["time"], 2.5)
        self.assertIn("spec_goto 25 50 225", self.console.camera_writes[-1])
        self.controller.seek(make_project(), 99)
        self.assertIn("demo_gototick 200 0 1", self.console.requests)
        self.assertEqual(self.controller.status()["time"], 10)

    def test_nonfinite_shot_time_rejected_before_any_camera_or_seek_write(self):
        for method in (self.controller.apply, self.controller.seek, self.controller.play):
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(method=method.__name__, value=value):
                    self.console.requests.clear()
                    self.console.sent.clear()
                    with patch("dolly.controller.threading.Thread"):
                        with self.assertRaises(ValueError):
                            method(make_project(), value)
                    self.assertFalse(any("spec_goto " in command or "demo_gototick " in command
                                         for command in self.console.requests + self.console.sent))

    def test_play_sets_requested_timescale_and_stop_resets_it_to_one(self):
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch("dolly.controller.threading.Thread", return_value=worker):
            self.controller.play(make_project(), time=0, speed=2)
        self.assertEqual(self.console.values["demo_timescale"], 2)
        self.controller.stop()
        self.assertEqual(self.console.values["demo_timescale"], 1)

    def _start_playback_without_worker(self, project=None, **options):
        """Hold only the OS thread boundary so tests can drive its real target."""
        project = project or make_project()
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch("dolly.controller.threading.Thread", return_value=worker):
            self.controller.play(project, **options)
        return project

    def test_shot_cleanup_and_pause_preserve_slow_motion_hotkey_until_stop(self):
        self._start_playback_without_worker(speed=.5)
        # Simulate the user's console hotkey overriding the speed mid-shot.
        self.console.request("demo_timescale 0.1")
        self.controller._finish_playback()
        self.controller.pause()
        self.assertEqual(self.console.values["demo_timescale"], .1)
        self.assertTrue(self.console.paused)
        self.assertTrue(self.controller._demo_speed_changed)
        self.controller.stop()
        self.assertEqual(self.console.values["demo_timescale"], 1)

    def test_clear_ragdolls_validates_replay_without_changing_camera_or_speed(self):
        self.controller.destroy_ragdolls()
        self.assertIn("cl_destroy_ragdolls", self.console.requests)
        self.assertFalse(self.console.camera_writes)
        self.assertEqual(self.console.values["demo_timescale"], .5)
        self.console.requests.clear()
        self.console.demo_name = "different.dem"
        with self.assertRaises(RuntimeError):
            self.controller.destroy_ragdolls()
        self.assertNotIn("cl_destroy_ragdolls", self.console.requests)

    def _run_with_clock(self, project, *, limit=4, speed=1, frozen=False):
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=limit)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(project, 0, speed, 60, frozen)
        return clock

    def test_normal_play_seeks_then_applies_camera_lens_and_cvars_before_resume(self):
        project = make_project()
        project.setup_values = {"r_citadel_depthoffield_enable": 1}
        project.tracks = [CvarTrack("r_citadel_depthoffield_focus_distance",
                                   [TrackKey(0, 900), TrackKey(10, 1200)])]
        self.console.goto_output = 100
        self.console.tick = 170
        self._start_playback_without_worker(project, speed=0.5)
        operations = self.console.operations
        seek = operations.index("demo_gototick 100 0 1")
        camera = operations.index("spec_goto 0 0 200 0 0")
        timescale = operations.index("demo_timescale 0.5")
        resume = operations.index("demo_resume")
        self.assertLess(seek, camera)
        self.assertLess(camera, timescale)
        self.assertLess(timescale, resume)
        self.assertLess(operations.index("citadel_hud_visible 0"), resume)
        self.assertLess(operations.index("citadel_hide_replay_hud 1"), resume)
        self.assertLess(operations.index("engine_no_focus_sleep 0"), resume)
        self.assertEqual(len(self.console.resume_snapshots), 1)
        at_resume = self.console.resume_snapshots[0]
        self.assertEqual(at_resume["tick"], 100)
        self.assertEqual(at_resume["pose"], [0, 0, 200, 0, 0])
        for name, expected in {"r_aspectratio": STANDARD_ASPECT,
                               "r_citadel_depthoffield_enable": 1,
                               "r_citadel_depthoffield_focus_distance": 900,
                               "citadel_hud_visible": 0, "demo_timescale": 0.5}.items():
            self.assertAlmostEqual(at_resume["values"][name], expected)
        self.assertFalse(self.console.paused)
        self.assertTrue(self.controller.status()["playing"])

    def test_natural_finish_restores_hud_and_background_and_keeps_final_lens_until_stop(self):
        # Automatic mode deliberately turns the HUD on after playback even if
        # the user had already hidden it before starting the shot.
        self.console.values["citadel_hud_visible"] = 0
        self.console.goto_output = 100
        project = self._start_playback_without_worker(speed=0.5)
        self.console.goto_outputs.extend([200, 200])
        self._run_with_clock(project, speed=0.5)
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.values["demo_timescale"], .5)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertEqual(self.console.values["r_aspectratio"], 1.0)
        self.assertEqual(self.controller.status()["time"], 10)
        self.assertIn("finished", self.controller.status()["message"])
        self.assertFalse(self.controller.status()["playing"])
        self.assertEqual(self.controller._playback_restore, {})
        self.controller.stop()
        self.assertEqual(self.console.values["r_aspectratio"], 0.0)

    def test_pause_and_stop_restore_hud_and_original_background_settings(self):
        for action in (self.controller.pause, self.controller.stop):
            with self.subTest(action=action.__name__):
                self._start_playback_without_worker()
                self.assertEqual(self.console.values["citadel_hud_visible"], 0)
                self.assertEqual(self.console.values["engine_no_focus_sleep"], 0)
                action()
                self.assertEqual(self.console.values["citadel_hud_visible"], 1)
                self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
                self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
                self.assertEqual(self.controller._playback_restore, {})
                self.assertFalse(self.controller.status()["playing"])

    def test_frame_error_restores_hud_and_background_even_if_camera_is_rejected(self):
        project = self._start_playback_without_worker(speed=2)
        self.console.fail_commands.add("spec_goto 0 0 200 0 0")
        with self.assertLogs("dolly", level="ERROR"):
            self._run_with_clock(project, speed=2)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertEqual(self.console.values["demo_timescale"], 2)
        self.assertTrue(self.console.paused)
        self.assertFalse(self.controller.status()["playing"])
        self.assertIn("Simulated console failure", self.controller.status()["message"])

    def test_partial_start_batch_failure_restores_hud_background_and_preserves_speed(self):
        self.console.fail_commands.add("demo_resume")
        with patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaisesRegex(RuntimeError, "Simulated console failure"):
                self.controller.play(make_project(), speed=2)
        worker.assert_not_called()
        self.assertIn("demo_timescale 2", self.console.operations,
                      "The failure must occur after a partial batch has applied")
        self.assertEqual(self.console.resume_snapshots, [])
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertEqual(self.console.values["demo_timescale"], 2)
        self.assertFalse(self.controller.status()["playing"])
        self.assertEqual(self.controller._playback_restore, {})

    def test_hide_hud_disabled_never_queries_or_writes_hud_settings(self):
        self.console.values["citadel_hud_visible"] = 0
        self._start_playback_without_worker(hide_hud=False)
        self.controller.stop()
        self.assertFalse(any(item.split()[0] in ("citadel_hud_visible", "citadel_hide_replay_hud")
                             for item in self.console.operations))
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)

    def test_unavailable_optional_background_and_replay_bar_cvars_do_not_block_playback(self):
        del self.console.values["engine_no_focus_sleep"]
        del self.console.values["citadel_hide_replay_hud"]
        self._start_playback_without_worker()
        self.assertFalse(self.console.paused)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        self.assertNotIn("engine_no_focus_sleep 0", self.console.operations)
        self.assertNotIn("citadel_hide_replay_hud 1", self.console.operations)
        self.controller.stop()
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.controller._playback_restore, {})

    def test_failed_playback_restoration_blocks_restart_until_stop_can_retry(self):
        self._start_playback_without_worker()
        self.console.fail_commands.add("citadel_hud_visible 1")
        with self.assertLogs("dolly", level="WARNING"):
            self.controller.stop()
        self.assertIn("citadel_hud_visible", self.controller._playback_restore)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        with self.assertLogs("dolly", level="WARNING"), patch("dolly.controller.threading.Thread") as worker:
            with self.assertRaisesRegex(RuntimeError, "Previous settings still need restoration"):
                self.controller.play(make_project())
        worker.assert_not_called()
        self.console.fail_commands.clear()
        self.controller.stop()
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertEqual(self.controller._playback_restore, {})

    def test_wrong_replay_feedback_stops_before_next_frame_and_restores_hud(self):
        self.console.goto_output = 100
        project = self._start_playback_without_worker()
        self.console.requests.clear()
        self.console.goto_outputs.extend([
            100, "Currently playing 101 of 161418 ticks. Minutes:42.04 File:other.dem",
        ])
        with self.assertLogs("dolly", level="ERROR"):
            self._run_with_clock(project)
        self.assertEqual(len(self.console.camera_writes), 1)
        self.assertTrue(self.console.camera_writes[0].endswith("; demo_goto"))
        self.assertNotIn("demo_info", self.console.requests)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertIn("different", self.controller.status()["message"])
        self.assertFalse(self.controller.status()["playing"])

    def test_pending_hud_restore_survives_disconnect_and_retries_after_reconnect(self):
        project = self._start_playback_without_worker()
        self.console.is_connected = False
        with self.assertLogs("dolly", level="ERROR"):
            self._run_with_clock(project)
        self.assertIn("citadel_hud_visible", self.controller._playback_restore)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        with self.assertLogs("dolly", level="WARNING"), patch("dolly.controller.ConsoleClient", return_value=self.console):
            self.controller.connect()
        # Connect itself must establish the transport before cleanup can retry.
        self.controller.stop()
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["engine_no_focus_sleep"], 20)
        self.assertEqual(self.controller._playback_restore, {})

    def test_frames_share_status_round_trip_and_diagnostics_are_bounded_with_metrics(self):
        self.console.goto_output = 100
        project = self._start_playback_without_worker()
        self.console.requests.clear()
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=80)
        original_request = self.console.request

        def delayed_transport(command, timeout=3, completion_patterns=None):
            response = original_request(command, timeout, completion_patterns)
            if command.startswith("spec_goto "):
                clock.now += 0.02
            return response

        self.console.request = delayed_transport
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(project, 0, 1, 60, False)
        self.assertEqual(len(self.console.camera_writes), 80)
        self.assertTrue(all(command.endswith("; demo_goto") for command in self.console.camera_writes))
        self.assertEqual(self.console.requests.count("demo_goto"), 2,
                         "Only initial validation and exit cleanup may have independent status round trips")
        self.assertNotIn("demo_info", self.console.requests)
        self.assertFalse(any(key.startswith("spec_goto ") for key in self.controller._last_output))
        self.assertLess(len(self.controller._last_output), 20)
        with tempfile.TemporaryDirectory() as directory:
            destination = self.controller.export_diagnostics(Path(directory) / "playback.zip")
            with zipfile.ZipFile(destination) as archive:
                report = json.loads(archive.read("diagnostics.json"))
        self.assertEqual(report["last_playback"]["project"], project.to_dict())
        self.assertFalse(report["last_playback"]["frozen"])
        self.assertTrue(report["last_playback"]["hide_hud"])
        metrics = report["playback_metrics"]
        self.assertEqual(metrics["updates"], 80)
        self.assertEqual(metrics["requested_updates_per_second"], 60)
        self.assertAlmostEqual(metrics["mean_round_trip_ms"], 20)
        self.assertAlmostEqual(metrics["max_round_trip_ms"], 20)
        self.assertAlmostEqual(metrics["achieved_updates_per_second"], 50)
        self.assertAlmostEqual(metrics["max_update_interval_ms"], 20)
        self.assertEqual(report["pending_playback_restoration"], {})

    def test_run_rewinds_with_demo_ticks_and_bounds_fractional_lead(self):
        project = make_project()
        self.console.demo_outputs.extend([100, 110, 120, 105, 105])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=4)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(project, 0, 1, 60, False)
        x_positions = [float(command.split()[1]) for command in self.console.camera_writes]
        for position, acknowledged in zip(x_positions, (0, 10, 20, 5)):
            self.assertGreaterEqual(position, 0)
            self.assertLessEqual(position, acknowledged + 1)
        self.assertEqual(x_positions[:3], sorted(x_positions[:3]))
        self.assertGreaterEqual(x_positions[-1], 5)
        self.assertGreaterEqual(self.controller.status()["time"], 0.5)
        self.assertLessEqual(self.controller.status()["time"], 0.6)
        self.assertFalse(self.controller.status()["playing"])

    def test_stalled_ticks_hold_pose_and_report_waiting(self):
        self.console.tick = 120
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=70)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic):
            self.controller._run(make_project(), 0, 1, 60, False)
        self.assertEqual(len(self.console.camera_writes), 70)
        positions = [float(command.split()[1]) for command in self.console.camera_writes]
        self.assertEqual(positions[0], 20)
        self.assertTrue(all(20 <= position <= 21 for position in positions))
        self.assertEqual(len(set(positions[10:])), 1,
                         "A stalled replay must hold after at most one tick of camera lead")
        self.assertAlmostEqual(self.controller.status()["time"], 2.1)
        self.assertTrue(any("holding" in message for message in self.messages))

    def test_demo_exit_mid_run_stops_before_another_camera_write(self):
        self.console.demo_outputs.extend([100, "Error - Not currently playing back a demo."])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=5)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic), self.assertLogs("dolly", level="ERROR"):
            self.controller._run(make_project(), 0, 1, 60, False)
        self.assertEqual(len(self.console.camera_writes), 1)
        self.assertNotIn("demo_pause", self.console.requests)
        self.assertFalse(self.controller.status()["playing"])

    def test_frozen_path_stops_if_demo_ticks_start_advancing(self):
        self.console.demo_outputs.extend([100, 100, 101])
        clock = FakeClock()
        self.controller._stop_event = CountedEvent(clock, limit=5)
        with patch("dolly.controller.time.perf_counter", side_effect=clock.monotonic), self.assertLogs("dolly", level="ERROR"):
            self.controller._run(make_project(), 0, 1, 60, True)
        self.assertEqual(len(self.console.camera_writes), 2)
        self.assertTrue(any("resumed" in message for message in self.messages))

    def test_frame_rejection_does_not_advance_applied_pose_or_time(self):
        project = make_project()
        self.controller._apply_frame(project, 0)
        original = dict(self.controller._applied_pose)
        command = frame_commands(project.evaluate(2), self.controller.lens_cvar)
        original_request = self.console.request

        def rejecting_request(requested, timeout=3):
            if requested == command:
                self.console.requests.append(requested)
                return "Error: cannot set r_aspectratio"
            return original_request(requested, timeout)

        self.console.request = rejecting_request
        with self.assertRaisesRegex(RuntimeError, "cannot set"):
            self.controller._apply_frame(project, 2)
        self.assertEqual(self.controller._applied_pose, original)
        self.assertEqual(self.controller.status()["time"], 0)

    def test_diagnostics_include_actual_launcher_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory)/"session"
            session_dir.mkdir()
            self.controller._session.session_dir = session_dir
            for name in ("session.json", "launch.log", "game_stdout.log"):
                (session_dir/name).write_text("fixture")
            destination = self.controller.export_diagnostics(Path(directory)/"report.zip")
            with zipfile.ZipFile(destination) as archive:
                for name in ("session.json", "launch.log", "game_stdout.log"):
                    self.assertIn("session/" + name, archive.namelist())

    def test_diagnostics_keep_previous_launch_and_raw_console_after_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "logs" / "20260907_203000_old"
            current = root / "logs" / "20260907_203810_current"
            old.mkdir(parents=True)
            current.mkdir()
            (old / "session.json").write_text('{"exit_code": -1073741819}')
            (old / "game_stdout.log").write_text("previous process output")
            (old / "native_diagnostics.json").write_text('{"graphics": {"latest": {"pending_count": 32000}}}')
            (old / "original.gameinfo.gi").write_text("private backup is not part of diagnostics")
            (current / "session.json").write_text('{"pid": 1234}')
            self.controller._session.session_dir = current
            self.console.recent_output = lambda: "DOLLY_END_marker\nlate unlocker completion\n"
            with patch("dolly.controller.ROOT", root):
                output = self.controller.export_diagnostics(root / "diagnostics.zip")
            with zipfile.ZipFile(output) as archive:
                self.assertIn("late unlocker completion", archive.read("logs/recent_console.txt").decode())
                self.assertIn("previous process output", archive.read("previous_sessions/" + old.name + "/game_stdout.log").decode())
                self.assertEqual(json.loads(archive.read("previous_sessions/" + old.name + "/native_diagnostics.json"))["graphics"]["latest"]["pending_count"], 32000)
                self.assertIn("session/session.json", archive.namelist())
                self.assertFalse(any("original.gameinfo.gi" in name for name in archive.namelist()))
                self.assertFalse(any("previous_sessions/" + current.name in name for name in archive.namelist()))


class CameraParsingTests(unittest.TestCase):
    def test_current_cvar_value_ignores_default(self):
        self.assertEqual(read_cvar_value("citadel_camera_fov",
            '[Console] "citadel_camera_fov" = "70.5" ( def. "90" ) min. 40 max. 170'), 70.5)
        self.assertEqual(read_cvar_value("r_depth_of_field", "r_depth_of_field = true"), 1)
        self.assertEqual(read_cvar_value("r_depth_of_field", "r_depth_of_field: false"), 0)
        with self.assertRaises(ValueError):
            read_cvar_value("citadel_camera_fov", '"citadel_camera_fov" ( def. "90" )')

    def test_camera_parser_supports_both_formats_and_rejects_unknown(self):
        self.assertEqual(parse_camera("[Console] spec_goto -1.2 2e2 .3 -4 +5", 90, 10),
                         Keyframe(0, -1.2, 200, .3, -4, 5, 10, 90))
        self.assertEqual(parse_camera("setpos_exact 1 2 3;setang_exact 4 5 6", 90),
                         Keyframe(0, 1, 2, 3, 4, 5, 6, 90))
        with self.assertRaises(ValueError):
            parse_camera("Unknown command: spec_pos", 90)

    def test_camera_parser_rejects_nonfinite_values(self):
        for output in ("spec_goto 1e999 2 3 4 5", "setpos 1 2 3;setang 4 5 1e999"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_camera(output, 90)

    def test_command_validation_for_native_dof_ranges_and_duplicate_aspect(self):
        frame = make_project().evaluate(0)
        for name, value in (("r_citadel_depthoffield_focus_distance", 10001),
                            ("r_citadel_depthoffield_aperture_diameter", -1),
                            ("r_citadel_depthoffield_sensor_size", 0.4),
                            ("r_citadel_depthoffield_mode", 1.5),
                            ("r_aspectratio", 1.2), ("citadel_camera_fov", 80),
                            ("citadel_camera_spectator_fov", 80), ("exec", 1)):
            frame["cvars"] = {name: value}
            with self.subTest(name=name), self.assertRaises(ValueError):
                frame_commands(frame, "r_aspectratio")


if __name__ == "__main__":
    unittest.main()
