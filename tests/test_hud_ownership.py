"""Game HUD ownership: playback, replay-HUD, POV, Follow, Stop and disconnect."""
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import dolly.follow_capabilities
import dolly.follow_target
import dolly.replay_camera
from dolly.controller import Controller, OWN_HEALTH_HUD
from dolly.follow_camera import FollowSettings
from core_helpers import FakeConsole

HUD = ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")


class Session:
    """A live game process without the exact-build health panel control."""
    command = ()
    pid = 1

    class process:
        @staticmethod
        def poll():
            return None


def controller():
    c = Controller(lambda *args, **kwargs: None)
    console = FakeConsole()
    c._console = console
    c._request = lambda command, **kwargs: console.request(command)
    c._session = Session()
    c._require_connection = Mock()
    return c, console


def failing_once(console, prefix):
    state = {"failed": False}

    def request(command, **kwargs):
        if not state["failed"] and command.startswith(prefix):
            state["failed"] = True
            raise RuntimeError("Console timed out")
        return console.request(command)
    return request


class ShotHud(unittest.TestCase):
    def shot(self, *, show_hud, hide_hud=True):
        c, console = controller()
        c._show_replay_hud = show_hud
        c._require_demo = lambda **kwargs: {"tick": 100}
        c._require_native_demo = Mock()
        c.status = lambda: {}
        bridge = Mock()
        bridge.status.side_effect = lambda: {"paused": console.paused, "state": "idle", "frame_count": 1}
        c._native_bridge = lambda: bridge
        console.values["hud_free_cursor"] = 1.0
        console.paused = False
        c._prepare_playback_settings(hide_hud)
        worker = threading.Thread(target=c._stop_event.wait, daemon=True)
        worker.start()
        self.addCleanup(c._stop_event.set)
        c._thread = worker
        return c, console

    def test_pause_and_resume_during_shot_keep_the_users_hud_original(self):
        c, console = self.shot(show_hud=False)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(c._replay_hud_restore, {})
        c._finish_playback()
        c._halt(native_action="release")
        self.assertEqual(console.values["citadel_hud_visible"], 1)
        self.assertEqual(console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(c._replay_hud_restore, {})
        self.assertEqual(c._playback_restore, {})

    def test_resume_during_hidden_shot_does_not_record_the_hud(self):
        c, console = self.shot(show_hud=True)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertEqual(console.values["citadel_hide_replay_hud"], 1)

    def test_shot_finish_returns_the_replay_hud_before_playback_settings(self):
        c, console = self.shot(show_hud=False, hide_hud=False)
        c.toggle_replay()
        c.toggle_replay()
        self.assertEqual(console.values["citadel_hud_visible"], 0)
        self.assertIsNone(c._finish_playback())
        self.assertEqual({name: console.values[name] for name in HUD},
                         {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0, "hud_free_cursor": 1})
        self.assertEqual(c._replay_hud_restore, {})


class StopHud(unittest.TestCase):
    def test_halt_stops_the_worker_before_a_failed_replay_hud_restore(self):
        c, console = controller()
        c._require_demo = lambda **kwargs: {"tick": 100}
        console.values.update(citadel_hud_visible=0.0)
        c._replay_hud_restore = {"citadel_hud_visible": 1.0}
        c._replay_hud_session = c._session
        c._request = failing_once(console, "citadel_hud_visible 1")
        worker = threading.Thread(target=c._stop_event.wait, daemon=True)
        worker.start()
        self.addCleanup(c._stop_event.set)
        c._thread = worker
        error = c._halt(native_action="release")
        self.assertFalse(worker.is_alive())
        self.assertIsNone(c._thread)
        self.assertIn("Console timed out", error)
        self.assertEqual(c._replay_hud_restore, {"citadel_hud_visible": 1.0})
        self.assertIsNone(c._halt(native_action="release"))
        self.assertEqual(console.values["citadel_hud_visible"], 1)

if __name__ == "__main__":
    unittest.main()
