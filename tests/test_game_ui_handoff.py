"""Replay HUD/cursor handoffs using the readable Deadlock cvars."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly.console import ConsoleClient, error_text
from dolly import editor_session
from tests.test_native_flight_controller import configured_controller


class GameUiHandoffTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()
        self.controller.begin_paused_camera()

    def test_source2_missing_command_help_is_not_support(self):
        rejection = "help:  no cvar or command named demoui"
        client = ConsoleClient()
        client.request = Mock(return_value=rejection)
        self.assertEqual(error_text(rejection), rejection)
        self.assertFalse(client.supports("demoui"))
        client.request.return_value = "hud_free_cursor = -1 (default: -1)"
        self.assertTrue(client.supports("hud_free_cursor"))

    def test_game_ui_opens_even_when_replay_hud_was_hidden(self):
        self.console.values["citadel_hide_replay_hud"] = 1
        self.controller.toggle_game_ui(True)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["hud_free_cursor"], 1)
        self.assertEqual(self.bridge.owner, "game_ui")
        self.assertFalse(self.controller._native_active)
        self.assertFalse(any("demoui" in command for command in self.console.requests))

    def test_repeated_show_and_hide_are_idempotent_and_do_not_restart_flight(self):
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(True)
        self.assertEqual(self.console.values["hud_free_cursor"], 1)
        self.controller.toggle_game_ui(False)
        count = self.console.events.count("native.flight")
        self.controller.toggle_game_ui(False)
        self.assertEqual(self.console.events.count("native.flight"), count)
        self.assertEqual(self.console.values["hud_free_cursor"], 0)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 1)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        self.assertEqual(self.bridge.owner, "flight")

    def test_f7_closes_console_back_to_game_ui_with_cursor_available(self):
        self.controller.toggle_game_ui(True)
        self.controller.toggle_console(True)
        self.assertEqual(self.bridge.owner, "console")
        self.controller.toggle_console(False)
        self.assertEqual(self.bridge.owner, "game_ui")
        self.assertTrue(self.controller._game_ui_visible)
        self.assertEqual(self.console.values["hud_free_cursor"], 1)

    def test_f8_from_console_over_game_ui_closes_both_and_opens_panel(self):
        self.controller.toggle_game_ui(True)
        self.controller.toggle_console(True)
        app = SimpleNamespace(controller=self.controller, busy=False,
                              _submit=lambda _label, function: function())
        editor_session.dispatch(app, {"action": "panel", "value": 1}, self.bridge)
        self.assertFalse(self.controller._console_open)
        self.assertFalse(self.controller._game_ui_visible)
        self.assertEqual(self.console.values["hud_free_cursor"], 0)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 1)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        self.assertTrue(self.controller._native_manual)
        self.assertEqual(self.bridge.owner, "panel")

    def test_stop_restores_original_automatic_cursor_and_hud_values(self):
        self.console.values["citadel_hud_visible"] = 0
        originals = {name: self.console.values[name] for name in
                     ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")}
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(False)
        self.controller.toggle_game_ui(True)
        self.controller.stop()
        self.assertEqual({name: self.console.values[name] for name in originals}, originals)
        self.assertFalse(self.controller._game_ui_restore)
        self.assertFalse(self.controller._game_ui_visible)

    def test_failed_cvar_readback_does_not_claim_game_ui_open(self):
        request = self.console.request
        def ignore_cursor(command, *args, **kwargs):
            result = request(command, *args, **kwargs)
            if "hud_free_cursor 1" in command:
                self.console.values["hud_free_cursor"] = -1
            return result
        self.console.request = ignore_cursor
        with self.assertRaisesRegex(RuntimeError, "did not apply hud_free_cursor"):
            self.controller.toggle_game_ui(True)
        self.assertFalse(self.controller._game_ui_visible)
        self.assertTrue(self.controller._game_ui_restore)

    def test_missing_cursor_cvar_fails_before_releasing_camera(self):
        del self.console.values["hud_free_cursor"]
        self.console.events.clear()
        with self.assertRaisesRegex(ValueError, "hud_free_cursor"):
            self.controller.toggle_game_ui(True)
        self.assertNotIn("native.release", self.console.events)
        self.assertTrue(self.controller._native_active)

    def test_failed_restore_preserves_originals_for_retry(self):
        self.controller.toggle_game_ui(True)
        self.console.fail_commands.add("hud_free_cursor -1")
        with self.assertLogs("dolly", level="WARNING"):
            self.controller.stop()
        self.assertEqual(self.controller._game_ui_restore["hud_free_cursor"], -1)
        self.console.fail_commands.clear()
        self.controller.stop()
        self.assertFalse(self.controller._game_ui_restore)
        self.assertEqual(self.console.values["hud_free_cursor"], -1)

    def test_open_during_hidden_shot_restores_the_pre_shot_hud_on_stop(self):
        self.console.values["citadel_hud_visible"] = 0
        self.console.values["citadel_hide_replay_hud"] = 1
        self.controller._playback_restore = {"citadel_hud_visible": 1, "citadel_hide_replay_hud": 0}
        self.controller.toggle_game_ui(True)
        self.controller.stop()
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)

    def test_failed_optimistic_f9_open_restores_dolly_panel(self):
        self.bridge.editor_status = lambda: {"enabled": True, "game_ui": True}
        app = SimpleNamespace(controller=self.controller, busy=False,
                              _submit=lambda _label, function: function())
        del self.console.values["hud_free_cursor"]
        with self.assertRaisesRegex(ValueError, "hud_free_cursor"):
            editor_session.dispatch(app, {"action": "game_ui", "value": 1}, self.bridge)
        self.assertEqual(self.bridge.owner, "panel")
        self.assertTrue(self.controller._native_active)

    def test_direct_flight_entry_closes_underlying_game_ui_and_console(self):
        self.controller.toggle_game_ui(True)
        self.controller.toggle_console(True)
        self.controller.enter_native_flight()
        self.assertFalse(self.controller._game_ui_visible)
        self.assertFalse(self.controller._console_open)
        self.assertEqual(self.console.values["hud_free_cursor"], 0)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 1)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        self.assertEqual(self.bridge.owner, "flight")

    def test_f9_round_trip_hides_character_hud_and_writes_it_only_once(self):
        self.controller.toggle_game_ui(True)
        self.console.requests.clear()
        self.controller.toggle_game_ui(False)
        self.assertEqual(self.console.values["citadel_hud_visible"], 0)
        writes = [cmd for cmd in self.console.requests if "citadel_hide_replay_hud 1" in cmd]
        self.assertEqual(len(writes), 1)
        self.assertIn("citadel_hud_visible 0", writes[0])
        self.controller.toggle_game_ui(True)
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)

    def test_f9_after_stop_restores_the_pre_edit_hud_and_cursor(self):
        originals = dict(self.console.values)
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(False)
        self.controller.stop()
        self.assertEqual(self.console.values, originals)

    def test_repeated_f9_does_not_resnapshot_temporary_hidden_values(self):
        self.controller.toggle_game_ui(True)
        saved = dict(self.controller._game_ui_restore)
        self.controller.toggle_game_ui(False)
        self.console.requests.clear()
        self.controller.toggle_game_ui(True)
        self.assertEqual(self.controller._game_ui_restore, saved)
        self.assertFalse(any(cmd in ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")
                             for cmd in self.console.requests))


if __name__ == "__main__":
    unittest.main()
