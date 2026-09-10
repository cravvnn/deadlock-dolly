"""Exercise native manual ownership independently of a Windows game process."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from dolly.controller import Controller
from dolly.navigation import CameraMotion
from tests.test_controller import FakeConsole, make_project


class FlightBridge:
    def __init__(self, console):
        self.console = console
        self.state = "probe"
        self.pose = [1.0, 2.0, 60.0, 4.0, 5.0, 0.0, 16/9]
        self.original = list(self.pose)
        self.frames = 0
        self.advance = True
        self.events = console.events
        self.phase = 0.0
        self.owner = "panel"

    def status(self):
        self.frames += int(self.advance)
        has_demo = self.console.demo_output != "Error - Not currently playing back a demo."
        return {"state": self.state, "frame_count": self.frames, "tick": self.console.tick,
                "paused": self.console.paused, "demo_name": self.console.demo_name if has_demo else "",
                "phase": self.phase, "applied_pose": list(self.pose), "original_pose": list(self.original),
                "effect_count": 0}

    def start_flight(self, demo_name, pose=None, timeout=3):
        self.events.append("native.flight")
        if pose is not None:
            self.pose = list(pose)
        elif self.state in ("stopped", "probe"):
            self.pose = list(self.original)
        self.state = "armed"
        self.owner = "flight"
        return self.status()

    def hold(self):
        self.events.append("native.hold")
        self.state = "armed"

    def release(self):
        self.events.append("native.release")
        self.state = "stopped"

    def configure_editor(self, **values):
        self.owner = values.get("owner", self.owner)

    def prepare(self, project, start, speed, frozen, demo_name):
        self.events.append("native.prepare")
        self.phase = start
        frame = project.evaluate(start)
        self.pose = [frame[key] for key in ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")]
        self.state = "armed"


def configured_controller():
    controller = Controller()
    console = FakeConsole()
    bridge = FlightBridge(console)
    controller._console = console
    controller._session = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), pid=1234,
        session_dir=Path("unused"), close=MagicMock(), restore_gameinfo=MagicMock(),
        owns_console_port=lambda: True, native=bridge)
    controller._demo = Path("/chosen/example.dem")
    controller._unlocker_pid = 1234
    controller._probe_result = {"capabilities": {"spec_goto": True, "cl_citadel_forceangles": True,
                                                "r_aspectratio": True}}
    return controller, console, bridge


class NativeFlightControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()

    def test_paused_native_entry_uses_visible_height_without_console_calibration(self):
        self.console.pose[2] = 118  # Player-eye readback disagrees with visible native view.
        pose = self.controller.begin_paused_camera()
        self.assertEqual(pose["z"], 60)
        self.assertTrue(self.controller.status()["paused_flight"])
        self.assertTrue(self.controller.status()["native_editor_active"])
        self.assertEqual(self.console.camera_writes, [])
        self.assertFalse(any("demo_gototick" in cmd for cmd in self.console.events))

    def test_native_flight_does_not_poll_external_input_source(self):
        source = MagicMock(side_effect=AssertionError("External input must not run"))
        self.controller.start_paused_flight(source)
        source.assert_not_called()
        self.assertIsNone(self.controller._thread)

    def test_pause_capture_and_resume_keep_native_ownership_and_height(self):
        self.controller.begin_paused_camera()
        self.bridge.pose = [11, 22, 33, 12, 123, 0, 1.1]
        key = self.controller.capture(2)
        self.assertEqual((key.x, key.y, key.z), (11, 22, 33))
        self.assertEqual(key.aspect_ratio, 1.1)
        self.controller.start_paused_flight()
        self.assertEqual(self.bridge.pose[:3], [11, 22, 33])
        self.assertNotIn("native.release", self.console.events)
        self.assertEqual(self.console.camera_writes, [])

    def test_event_capture_uses_keypress_pose_not_later_moving_camera(self):
        self.controller.begin_paused_camera()
        snapshot = {"pose": [11, 22, 33, 12, 123, 1, 1.1], "tick": 100, "paused": True}
        self.bridge.pose[0] = 99
        frame, tick = self.controller.capture_native_snapshot(snapshot, 2)
        self.assertEqual((frame.x, frame.roll, frame.time, tick), (11, 1, 2, 100))
        self.assertTrue(self.controller.status()["paused_flight"])
        self.assertNotIn("native.hold", self.console.events)

    def test_stale_paused_capture_fails_before_any_camera_write(self):
        self.controller.begin_paused_camera()
        before = len(self.console.events)
        self.console.tick = 101
        with self.assertRaisesRegex(RuntimeError, "replay moved"):
            self.controller.capture_native_snapshot({"pose": self.bridge.pose, "tick": 100, "paused": True})
        self.assertNotIn("native.hold", self.console.events[before:])

    def test_saved_view_preview_remains_native_and_uses_authored_lens(self):
        self.controller.begin_paused_camera()
        frame = self.controller.select_paused_camera(make_project(), 10)
        self.assertEqual(frame["aspect_ratio"], 1)
        self.assertEqual(frame["z"], 300)
        self.assertEqual(self.console.camera_writes, [])
        self.controller.start_paused_flight()
        self.assertEqual(self.bridge.pose[2], 300)

    def test_native_nudge_retains_camera_instead_of_spec_goto(self):
        self.controller.begin_paused_camera()
        frame = self.controller.nudge_paused_camera(CameraMotion(up=1), .1, move_speed=240)
        self.assertEqual(frame["z"], 84)
        self.assertEqual(self.console.camera_writes, [])

    def test_stop_releases_and_return_from_game_ui_uses_new_hero_view(self):
        self.controller.begin_paused_camera()
        self.controller.toggle_game_ui(True)
        self.assertFalse(self.controller._native_active)
        self.assertFalse(self.controller.status()["native_editor_active"])
        self.bridge.original[2] = 900
        self.controller.toggle_game_ui(False)
        self.assertEqual(self.controller._paused_pose["z"], 900)
        self.assertTrue(self.console.values["citadel_hud_visible"])

    def test_explicit_stop_opens_controls_instead_of_leaving_inert_flight_input(self):
        self.controller.begin_paused_camera()
        self.bridge.editor_status = lambda: {"enabled": True, "console_open": False}
        self.controller.stop()
        self.assertEqual(self.bridge.owner, "panel")
        self.assertFalse(self.controller._native_active)
        self.assertIn("native.release", self.console.events)

    def test_queued_console_and_game_ui_reassert_input_after_an_intervening_seek(self):
        self.controller.begin_paused_camera()
        self.bridge.owner = "flight"  # A seek completed after F7 was queued.
        self.controller.toggle_console(enabled=True)
        self.assertEqual(self.bridge.owner, "console")
        self.controller.toggle_console(enabled=False)
        self.assertEqual(self.bridge.owner, "panel")
        self.bridge.owner = "flight"  # The same race can happen to F9.
        self.controller.toggle_game_ui(enabled=True)
        self.assertEqual(self.bridge.owner, "game_ui")

    def test_console_toggle_never_resumes_or_repositions_paused_camera(self):
        self.controller.begin_paused_camera()
        self.controller.toggle_console()
        self.assertIn("showconsole", self.console.events)
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.camera_writes, [])

    def test_explicit_console_close_is_idempotent_not_a_blind_toggle(self):
        self.controller.toggle_console(True)
        self.controller.toggle_console(False)
        self.controller.toggle_console(False)
        self.assertEqual(self.console.events.count("hideconsole"), 2)
        self.assertNotIn("toggleconsole", self.console.events)

    def test_manual_replay_toggle_holds_view_then_returns_flight(self):
        self.controller.begin_paused_camera()
        self.controller.toggle_replay()
        self.assertFalse(self.console.paused)
        self.assertEqual(self.bridge.owner, "panel")
        self.controller.toggle_replay()
        self.assertTrue(self.console.paused)
        self.assertEqual(self.bridge.owner, "flight")

    def test_relative_seek_releases_before_seek_and_restores_displayed_pose(self):
        self.controller.begin_paused_camera()
        self.bridge.pose[2] = 777
        self.controller.seek_relative(2, tick_rate=10)
        self.assertEqual(self.console.tick, 120)
        self.assertEqual(self.controller._paused_pose["z"], 777)
        self.assertLess(self.console.events.index("native.release"), self.console.events.index("demo_gototick 120 0 1"))


if __name__ == "__main__":
    unittest.main()
