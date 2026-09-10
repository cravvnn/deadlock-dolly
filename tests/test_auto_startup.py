"""Startup ordering uses observed scene/command readiness, never a timer guess."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dolly.controller import Controller
from tests.test_native_flight_controller import configured_controller


class AutoStartupTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()
        self.session = self.controller._session
        self.controller._session = None
        self.controller._console = None
        self.controller._unlocker_pid = None
        self.console.demo_output = "Error - Not currently playing back a demo."
        self.console.complete_replay_on_send = True
        self.session.restore_gameinfo.side_effect = lambda: self.console.events.append("restore_gameinfo")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.demo = Path(self.temp.name) / "example.dem"
        self.demo.write_bytes(b"fixture")

    def start(self, **kwargs):
        with patch("dolly.controller.launcher.launch", return_value=self.session), \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            return self.controller.start_editing("installation", self.demo, **kwargs)

    def test_one_click_waits_for_hideout_then_unlocks_once_before_replay(self):
        result = self.start()
        events = self.console.events
        command = 'playdemo "' + self.demo.as_posix() + '"'
        self.assertEqual(events.count("cvar_unhide"), 1)
        self.assertLess(events.index("cvar_unhide"), events.index("restore_gameinfo"))
        self.assertLess(events.index("restore_gameinfo"), events.index(command))
        self.assertLess(events.index(command), events.index("native.flight"))
        self.assertLess(events.index("hideconsole"), events.index("native.flight"))
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertTrue(result["paused_flight"])
        self.assertTrue(self.console.paused)
        evidence = self.controller._startup_evidence["automatic_readiness"]
        self.assertGreater(evidence["rendered_frame"], evidence["initial_frame"])

    def test_cancellation_before_launch_changes_no_game_state(self):
        event = threading.Event()
        event.set()
        with patch("dolly.controller.launcher.launch") as launch:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.controller.start_editing("installation", self.demo, cancel_event=event)
        launch.assert_not_called()
        self.assertEqual(self.console.sent, [])

    def test_unknown_or_partial_unlocker_completion_never_loads_demo(self):
        self.console.unhide_output = "Removed hidden flags from 12 cvars"
        with self.assertRaisesRegex(RuntimeError, "could not confirm"):
            self.start()
        self.assertEqual(self.console.events.count("cvar_unhide"), 1)
        self.assertEqual(self.console.sent, [])
        self.session.restore_gameinfo.assert_not_called()

    def test_cancellation_after_initialization_prevents_replay_load(self):
        event = threading.Event()
        self.session.restore_gameinfo.side_effect = event.set
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.start(cancel_event=event)
        self.assertEqual(self.console.events.count("cvar_unhide"), 1)
        self.assertEqual(self.console.sent, [])

    def test_socket_alone_is_not_hideout_readiness(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.bridge.advance = False
        self.bridge.frames = 0
        self.assertIsNone(self.controller._automatic_hideout_check(0))
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_rendered_scene_without_registered_unlocker_is_not_ready(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.console.supports = lambda name: False
        self.assertIsNone(self.controller._automatic_hideout_check(0))
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_missing_console_hide_keeps_flight_disabled(self):
        self.console.supports = lambda name: name != "hideconsole"
        with self.assertRaisesRegex(RuntimeError, "hideconsole"):
            self.start()
        self.assertNotIn("native.flight", self.console.events)
        self.assertFalse(self.controller.status()["paused_flight"])

    def test_preloaded_replay_refused_before_initialization(self):
        self.controller._session = self.session
        self.controller._console = self.console
        self.console.demo_output = None
        with self.assertRaisesRegex(RuntimeError, "before unlocker"):
            self.controller._automatic_hideout_check(0)
        self.assertNotIn("cvar_unhide", self.console.events)

    def test_wait_deadline_does_not_convert_missing_evidence_into_success(self):
        self.controller._session = self.session
        with patch("dolly.controller.time.perf_counter", side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                self.controller._startup_wait(lambda: None, "waiting for scene", None)

    def test_extra_launch_options_forwarded_without_embedded_demo_command(self):
        with patch("dolly.controller.launcher.launch", return_value=self.session) as launch, \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            self.controller.start_editing("installation", self.demo, launch_options="-windowed -w 1280 -h 720")
        self.assertEqual(launch.call_args.kwargs["launch_options"], "-windowed -w 1280 -h 720")
        self.assertNotIn("playdemo", str(launch.call_args))


if __name__ == "__main__":
    unittest.main()
