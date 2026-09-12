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
        # Keep an unresolved spelling on every OS: Windows runners may use an
        # 8.3 TEMP alias (RUNNER~1), which Path.resolve expands before launch.
        # A parent segment reproduces that distinction without Windows-only
        # filesystem setup; spaces also exercise the quoted console argument.
        replay_dir = Path(self.temp.name) / "Replay files"
        (replay_dir / "nested").mkdir(parents=True)
        self.demo = replay_dir / "nested" / ".." / "example.dem"
        self.demo.write_bytes(b"fixture")

    def start(self, **kwargs):
        with patch("dolly.controller.launcher.launch", return_value=self.session), \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            return self.controller.start_editing("installation", self.demo, **kwargs)

    def test_one_click_waits_for_hideout_then_unlocks_once_before_replay(self):
        result = self.start()
        events = self.console.events
        command = 'playdemo "' + self.demo.resolve().as_posix() + '"'
        self.assertEqual([event for event in events if event.startswith("playdemo ")], [command])
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

    def test_native_rendered_tick_zero_does_not_allow_early_pause(self):
        self.console.goto_outputs.extend([0, 0, 1, 2])
        request = self.console.request
        pause_ticks = []

        def observe_pause(command, *args, **kwargs):
            if command == "demo_pause":
                pause_ticks.append(self.console.tick)
            return request(command, *args, **kwargs)

        self.console.request = observe_pause
        result = self.start()
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertEqual(pause_ticks[0], 2)
        self.assertTrue(all(tick >= 2 for tick in pause_ticks))
        self.assertGreater(self.bridge.frames, 0)

    def test_cancel_initial_native_update_never_pauses_or_arms_camera(self):
        event = threading.Event()
        self.console.goto_outputs.extend([0, 0])
        request = self.console.request
        observations = 0

        def cancel_while_loading(command, *args, **kwargs):
            nonlocal observations
            result = request(command, *args, **kwargs)
            if command == "demo_goto":
                observations += 1
                if observations == 2:
                    event.set()
            return result

        self.console.request = cancel_while_loading
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.start(cancel_event=event)
        self.assertNotIn("demo_pause", self.console.events)
        self.assertNotIn("native.flight", self.console.events)
        self.assertFalse(any(command.startswith("demo_gototick ")
                             for command in self.console.events))

    def test_one_click_accepts_dotted_recording_without_reported_dem_suffix(self):
        self.demo = self.demo.with_name("practice.session.01.dem")
        self.demo.write_bytes(b"fixture")
        send = self.console.send

        def report_without_suffix(command):
            send(command)
            if command.startswith('playdemo "'):
                self.console.demo_name = self.demo.name[:-4]

        self.console.send = report_without_suffix
        result = self.start()
        self.assertEqual(result["startup_stage"], "editing_ready")
        self.assertTrue(result["paused_flight"])

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

    def test_console_readiness_accepts_named_hideout(self):
        evidence = Controller._console_hideout_evidence(
            "map : dl_hideout\nClient:  Connected", {"playing": False})
        self.assertEqual(evidence["method"], "named_hideout_status")
        self.assertEqual(evidence["map"], "dl_hideout")

    def test_console_readiness_accepts_settled_status_without_map_name(self):
        evidence = Controller._console_hideout_evidence(
            "Server:  Inactive\nClient:  Connected", {"playing": False})
        self.assertEqual(evidence["method"], "settled_status")

    def test_console_readiness_rejects_level_load(self):
        self.assertIsNone(Controller._console_hideout_evidence(
            "Client:  Connected\n@ Current  :  levelload", {"playing": False}))
        self.assertIsNone(Controller._console_hideout_evidence(
            "CL:  prerequisite   :  .'CAsyncShaderCompilePrerequisite'", {"playing": False}))
        self.assertIsNone(Controller._console_hideout_evidence("", {"playing": False}))

    def test_extra_launch_options_forwarded_without_embedded_demo_command(self):
        with patch("dolly.controller.launcher.launch", return_value=self.session) as launch, \
             patch("dolly.controller.ConsoleClient", return_value=self.console):
            self.controller.start_editing("installation", self.demo, launch_options="-windowed -w 1280 -h 720")
        self.assertEqual(launch.call_args.kwargs["launch_options"], "-windowed -w 1280 -h 720")
        self.assertNotIn("playdemo", str(launch.call_args))


if __name__ == "__main__":
    unittest.main()
