"""Replay HUD/cursor handoffs using the readable Deadlock cvars."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.console import ConsoleClient, error_text
from dolly import editor_session
from tests.test_native_flight_controller import configured_controller


class GameUiHandoffTests(unittest.TestCase):
    def saved_hero(self):
        player = {'handle': 0x18007, 'entity_index': 7, 'model': 0x123456,
                  'model_path': 'models/heroes_wip/frank/frank.vmdl'}
        self.controller._game_hero_restore = (self.controller._session, self.controller._demo, player['model_path'])
        self.bridge.editor_roster = Mock(return_value={'available': True, 'players': [player]})
        return player

    def test_regular_shot_holds_endpoint_until_explicit_f9_restores_fresh_hero(self):
        name = self.health_panel()
        player = self.saved_hero()
        self.controller._prepare_playback_settings(True)
        self.console.requests.clear()
        self.controller._finish_playback()
        self.assertFalse(any('spec_target' in command for command in self.console.requests))
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        monitor = Mock()
        monitor.sample_health_hud_context.return_value = {'handle': player['handle'], 'mode': 2}
        with patch('dolly.follow_target.FollowTargetMonitor', return_value=monitor):
            self.controller.toggle_game_ui(True)
        self.assertIn('spec_target 7', self.console.requests)
        self.assertIsNone(self.controller._game_hero_restore)
        self.assertEqual(self.console.values[name], 0)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)

    def test_new_shot_preserves_verified_hero_when_internal_stop_consumes_it(self):
        self.saved_hero()
        intent = self.controller._game_hero_restore
        def returned_to_game(**kwargs):
            self.controller._game_hero_restore = None
        with patch.object(self.controller, 'stop', side_effect=returned_to_game) as stop:
            self.controller._stop_before_new_shot(preserve_layers=True)
        stop.assert_called_once_with(preserve_layers=True)
        self.assertIs(self.controller._game_hero_restore, intent)

    def test_new_shot_does_not_carry_hero_into_another_replay_or_session(self):
        self.saved_hero()
        def changed_replay(**kwargs):
            self.controller._game_hero_restore = None
            self.controller._demo = 'another.dem'
        with patch.object(self.controller, 'stop', side_effect=changed_replay):
            self.controller._stop_before_new_shot()
        self.assertIsNone(self.controller._game_hero_restore)

    def test_ambiguous_saved_hero_retains_originals_and_never_selects_fallback(self):
        self.health_panel()
        player = self.saved_hero()
        self.bridge.editor_roster.return_value['players'].append(dict(player, handle=0x18008, entity_index=8))
        self.controller._halt(native_action='release')
        self.console.requests.clear()
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            self.controller._restore_spectator_hero()
        self.assertIsNotNone(self.controller._game_hero_restore)
        self.assertFalse(any('spec_target' in command for command in self.console.requests))

    def test_failed_hero_readback_keeps_hud_hidden_and_retries_no_command(self):
        self.health_panel()
        self.saved_hero()
        self.controller._halt(native_action='release')
        monitor = Mock()
        monitor.sample_health_hud_context.side_effect = RuntimeError('target unavailable')
        self.console.requests.clear()
        with patch('dolly.follow_target.FollowTargetMonitor', return_value=monitor), \
                patch('dolly.controller.time.monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, 'handoff remains pending'):
                self.controller._restore_spectator_hero()
        self.assertEqual(self.console.requests.count('spec_target 7'), 1)
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertIsNotNone(self.controller._game_hero_restore)

    def test_unverifiable_saved_hero_still_opens_game_ui_with_pending_panel(self):
        name = self.health_panel()
        self.saved_hero()
        with patch.object(self.controller, '_restore_spectator_hero',
                          side_effect=RuntimeError('Health HUD requires a settled stock gameplay camera.')):
            self.controller.toggle_game_ui(True)
        self.assertTrue(self.controller._game_ui_visible)
        self.assertIsNotNone(self.controller._game_hero_restore)
        self.assertEqual(self.console.values[name], 1)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 0)
        self.assertEqual(self.controller._game_ui_restore[name], 0)

    def test_transient_health_reverify_defers_only_the_panel_not_the_replay_ui(self):
        name = self.health_panel()
        self.console.requests.clear()
        calls = [None, RuntimeError('Game Follow requires a selected player chase view (observer mode 4).')]
        with patch.object(self.controller, '_verify_health_hud_handoff', side_effect=calls):
            self.controller.toggle_game_ui(True)
        self.assertTrue(self.controller._game_ui_visible)
        self.assertEqual(self.console.values[name], 1)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 0)
        self.assertEqual(self.console.values['hud_free_cursor'], 1)
        self.assertEqual(self.controller._game_ui_restore[name], 0)

    def test_saved_hero_refuses_other_demo_or_native_owner_before_command(self):
        self.health_panel()
        self.saved_hero()
        self.console.requests.clear()
        with self.assertRaisesRegex(RuntimeError, 'Release Dolly'):
            self.controller._restore_spectator_hero()
        self.controller._game_hero_restore = (self.controller._session, 'different.dem', 'frank')
        with self.assertRaisesRegex(RuntimeError, 'different replay'):
            self.controller._restore_spectator_hero()
        self.assertFalse(any('spec_target' in command for command in self.console.requests))

    def health_panel(self, original=0):
        name = 'citadel_hud_hide_own_health'
        self.console.values[name] = original
        capability = patch.object(self.controller, '_own_health_hud_value',
                                  side_effect=lambda: self.console.values[name])
        capability.start()
        self.addCleanup(capability.stop)
        context = patch('dolly.follow_target.FollowTargetMonitor')
        context.start()
        self.addCleanup(context.stop)
        return name

    def test_wine_restores_the_saved_panel_without_the_verified_handoff(self):
        # Under Wine the panel handoff can never be verified (it reads the game's
        # process memory), so the saved values must be applied directly instead of
        # holding the session's settings pending forever.
        name = self.health_panel()
        with patch('dolly.game_ui_handoff.wine_version', return_value='wine-11.0'), \
                patch.object(self.controller, '_own_health_hud_value', return_value=None):
            self.controller._game_ui_restore[name] = 0
            result = self.controller._restore_game_ui_settings()
        self.assertIsNone(result)
        self.assertEqual(self.console.values[name], 0)
        self.assertEqual(self.controller._game_ui_restore, {})

    def test_unverifiable_panel_still_reports_pending_without_wine(self):
        name = self.health_panel()
        with patch.object(self.controller, '_own_health_hud_value', return_value=None):
            self.controller._game_ui_restore[name] = 0
            result = self.controller._restore_game_ui_settings()
        self.assertIn('pending', result)
        self.assertEqual(self.controller._game_ui_restore[name], 0)

    def test_free_camera_hides_health_panel_and_f9_stop_restore_exactly(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(False)
        self.assertEqual(self.console.values[name], 1)
        self.controller.toggle_game_ui(True)
        self.assertEqual(self.console.values[name], 0)
        self.controller.stop()
        self.assertEqual(self.console.values[name], 0)
        self.assertFalse(any('citadel_unit_status_enabled' in command or
                             'citadel_hud_objective_health_enabled' in command
                             for command in self.console.requests))

    def test_health_panel_preserves_user_hidden_preference(self):
        name = self.health_panel(1)
        self.controller.toggle_game_ui(True)
        self.controller.set_replay_hud(True)
        self.controller.toggle_replay()
        self.assertEqual(self.console.values[name], 1)
        self.controller.toggle_replay()
        self.controller.stop()
        self.assertEqual(self.console.values[name], 1)

    def test_replay_hud_option_uses_pre_edit_health_preference(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(False)
        self.controller.toggle_replay()
        self.assertEqual(self.console.values[name], 1)
        self.controller.set_replay_hud(True)
        self.assertEqual(self.console.values[name], 1)
        self.controller.toggle_replay()
        self.assertEqual(self.console.values[name], 1)
        self.controller.stop()
        self.assertEqual(self.console.values[name], 0)

    def test_shot_completion_keeps_health_hidden_until_explicit_handoff(self):
        name = self.health_panel()
        self.controller._prepare_playback_settings(True)
        self.controller._prepare_playback_settings(True)
        self.assertEqual(self.console.values[name], 1)
        self.assertNotIn(name, self.controller._playback_restore)
        self.assertEqual(self.controller._game_ui_restore[name], 0)
        self.console.requests.clear()
        self.assertIsNone(self.controller._finish_playback())
        self.assertEqual(self.console.values[name], 1)
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 1)
        self.assertFalse(any(name + ' 0' in command for command in self.console.requests))
        self.assertFalse(any('citadel_hud_visible 1' in command for command in self.console.requests))
        self.controller._prepare_playback_settings(True)
        self.assertEqual(self.controller._game_ui_restore[name], 0)
        self.controller.stop()
        self.assertEqual(self.console.values[name], 0)

    def test_failed_health_restore_preserves_outer_original(self):
        name = self.health_panel()
        self.controller._prepare_playback_settings(True)
        self.controller._halt(native_action='release')
        request = self.controller._request
        def ignore_restore(command, **kwargs):
            result = request(command, **kwargs)
            if name + ' 0' in command:
                self.console.values[name] = 1
            return result
        with patch.object(self.controller, '_request', side_effect=ignore_restore), self.assertLogs('dolly', level='WARNING'):
            self.assertIsNotNone(self.controller._restore_game_ui_settings())
        self.assertEqual(self.controller._game_ui_restore[name], 0)
        self.assertIsNone(self.controller._restore_game_ui_settings())
        self.assertEqual(self.console.values[name], 0)

    def test_lost_health_capability_preserves_pending_original_without_writes(self):
        name = self.health_panel()
        self.controller._prepare_playback_settings(True)
        self.controller._halt(native_action='release')
        self.console.requests.clear()
        with patch.object(self.controller, '_own_health_hud_value', return_value=None), self.assertLogs('dolly', level='WARNING'):
            self.assertIsNotNone(self.controller._restore_game_ui_settings())
        self.assertFalse(any(name in command for command in self.console.requests))
        self.assertEqual(self.controller._game_ui_restore[name], 0)
        self.assertIsNone(self.controller._restore_game_ui_settings())

    def test_missing_hud_player_keeps_panel_hidden_but_opens_selection_ui(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(False)
        with patch('dolly.follow_target.FollowTargetMonitor') as monitor:
            monitor.return_value.sample_health_hud_context.side_effect = RuntimeError('no controller')
            self.controller.toggle_game_ui(True)
            self.assertTrue(self.controller._game_ui_visible)
            self.assertEqual(self.console.values[name], 1)
            self.controller.stop()
            self.assertEqual(self.console.values[name], 1)
            # The explicit handoff restores the original HUD/replay/cursor
            # values, so only the unverified health panel stays pending.
            self.assertEqual(self.controller._game_ui_restore, {name: 0})
            self.assertTrue(self.controller.status()['health_panel_restore_pending'])
        self.controller.stop()
        self.assertEqual(self.console.values[name], 0)
        self.assertFalse(self.controller._game_ui_restore)
        self.assertFalse(self.controller.status()['health_panel_restore_pending'])

    def test_native_owner_cannot_expose_panel_even_with_show_hud_enabled(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(False)
        self.controller.toggle_replay()
        self.console.requests.clear()
        self.controller.set_replay_hud(True)
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertEqual(self.console.values[name], 1)
        self.assertFalse(any(name + ' 0' in command for command in self.console.requests))
        self.controller.stop()
        self.assertEqual(self.console.values[name], 0)

    def test_health_reveal_follows_native_release_and_mode_readback(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(False)
        self.console.requests.clear()
        self.console.events.clear()
        def verify():
            self.assertFalse(self.controller._native_active)
            self.assertFalse(self.controller._native_manual)
            self.assertIn('native.release', self.console.events)
            self.assertNotIn('citadel_spectator_mode', self.controller._game_ui_restore)
            self.assertEqual(self.console.values[name], 1)
        with patch.object(self.controller, '_verify_health_hud_handoff', side_effect=verify):
            self.controller.toggle_game_ui(True)
        self.assertEqual(self.console.values[name], 0)

    def test_stock_mode_transition_suppresses_visible_abilities_first(self):
        name = self.health_panel()
        self.controller.toggle_game_ui(True)
        self.controller._game_ui_restore['citadel_spectator_mode'] = 1
        request = self.controller._request
        def mode_request(command, **kwargs):
            if command.startswith('citadel_spectator_mode 1'):
                self.assertEqual(self.console.values[name], 1)
                self.assertEqual(self.console.values['citadel_hud_visible'], 0)
                self.assertEqual(self.console.values['citadel_hide_replay_hud'], 1)
            return request(command, **kwargs)
        with patch.object(self.controller, '_request', side_effect=mode_request):
            self.controller._restore_spectator_view()
        self.assertEqual(self.controller._game_ui_restore[name], 0)

    def test_old_per_shot_original_migrates_without_reveal(self):
        name = self.health_panel()
        self.console.values[name] = 1
        self.controller._playback_restore[name] = 0
        self.console.requests.clear()
        self.assertIsNone(self.controller._restore_playback_settings())
        self.assertNotIn(name, self.controller._playback_restore)
        self.assertEqual(self.controller._game_ui_restore[name], 0)
        self.assertEqual(self.console.values[name], 1)
        self.assertFalse(any(name + ' 0' in command for command in self.console.requests))

    def game_owned_view(self):
        self.controller.toggle_game_ui(True)
        return {name: self.console.values[name] for name in
                ('citadel_hud_visible', 'citadel_hide_replay_hud', 'hud_free_cursor')}

    def test_replay_hides_hud_and_pause_restores_original_values(self):
        before = self.game_owned_view()
        self.controller.toggle_replay()
        self.assertEqual(self.console.values['citadel_hud_visible'], 0)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 1)
        self.assertEqual(self.console.values['hud_free_cursor'], 0)
        self.controller.toggle_replay()
        self.assertEqual({name: self.console.values[name] for name in before}, before)
        self.assertFalse(self.controller._replay_hud_restore)

    def test_hud_preference_changes_running_view_without_replacing_snapshot(self):
        before = self.game_owned_view()
        self.controller.toggle_replay()
        self.controller.set_replay_hud(True)
        self.assertEqual(self.console.values['citadel_hud_visible'], 1)
        self.assertEqual(self.console.values['citadel_hide_replay_hud'], 0)
        self.assertEqual(self.controller._replay_hud_restore, before)
        self.controller.set_replay_hud(False)
        self.controller.toggle_replay()
        self.assertEqual({name: self.console.values[name] for name in before}, before)

    def test_failed_resume_restores_hud(self):
        before = self.game_owned_view()
        original = self.controller._request
        def request(command, **kwargs):
            if command == 'demo_resume':
                raise RuntimeError('resume rejected')
            return original(command, **kwargs)
        with patch.object(self.controller, '_request', side_effect=request):
            with self.assertRaisesRegex(RuntimeError, 'resume rejected'):
                self.controller.toggle_replay()
        self.assertEqual({name: self.console.values[name] for name in before}, before)
        self.assertFalse(self.controller._replay_hud_restore)

    def test_stop_restores_hud_and_pre_edit_settings(self):
        self.game_owned_view()
        self.controller.toggle_replay()
        self.controller.stop()
        self.assertEqual(self.console.values, self.original_values)

    def test_pending_hud_restore_rejects_another_session(self):
        self.game_owned_view()
        self.controller.toggle_replay()
        originals = dict(self.controller._replay_hud_restore)
        self.controller._session = object()
        with self.assertRaisesRegex(RuntimeError, 'different session'):
            self.controller._restore_replay_hud()
        self.assertEqual(self.controller._replay_hud_restore, originals)

    def test_hud_preference_requires_boolean(self):
        with self.assertRaises(ValueError):
            self.controller.set_replay_hud(1)

    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()
        self.original_values = dict(self.console.values)
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

    def test_startup_console_close_returns_input_to_flight_not_the_panel(self):
        # Automatic startup closes the console and immediately enters flight.
        # Returning input to the panel there made the in-game panel appear for a
        # frame and vanish (a visible flash under Wine).
        self.controller.toggle_game_ui(True)
        self.controller.toggle_console(True)
        self.assertEqual(self.bridge.owner, "console")
        self.controller.toggle_console(False, editor_owner="flight")
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

    def test_unverified_f8_preserves_console_then_explicit_recheck_restores_editor(self):
        self.controller.toggle_game_ui(True)
        self.controller.toggle_console(True)
        self.controller._invalidate_probe('replay preparation was cancelled')
        self.bridge.editor_status = lambda: {'console_open': self.controller._console_open}
        app = SimpleNamespace(controller=self.controller, busy=False,
                              _submit=lambda _label, function: function())
        self.console.requests.clear()
        with self.assertRaisesRegex(RuntimeError, 'replay preparation was cancelled'):
            editor_session.dispatch(app, {'action': 'panel', 'value': 1}, self.bridge)
        self.assertTrue(self.controller._console_open)
        self.assertEqual(self.bridge.owner, 'console')
        self.assertEqual(self.console.requests, [])
        # The documented explicit support check enables return in this same
        # healthy replay; no process launch or playdemo retry is involved.
        self.controller.probe()
        editor_session.dispatch(app, {'action': 'panel', 'value': 1}, self.bridge)
        self.assertFalse(self.controller._console_open)
        self.assertTrue(self.controller._native_manual)
        self.assertEqual(self.bridge.owner, 'panel')
        self.assertEqual(self.console.sent, [])

    def test_stop_restores_original_automatic_cursor_and_hud_values(self):
        self.controller.stop()
        self.console.values["citadel_hud_visible"] = 0
        originals = {name: self.console.values[name] for name in
                     ("citadel_hud_visible", "citadel_hide_replay_hud", "hud_free_cursor")}
        self.controller.begin_paused_camera()
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

    def test_failed_bone_arm_restores_visible_replay_controls_and_can_retry(self):
        self.controller.toggle_game_ui(True)
        self.bridge.editor_status = lambda: {"enabled": True}
        app = SimpleNamespace(controller=self.controller, busy=False,
                              _submit=lambda _label, function: function())
        originals = dict(self.controller._game_ui_restore)
        with patch.object(self.bridge, "start_flight", side_effect=RuntimeError("Bone pose unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Bone pose unavailable"):
                editor_session.dispatch(app, {"action": "game_ui", "value": 0}, self.bridge)
        self.assertFalse(self.controller._native_active)
        self.assertFalse(self.controller._native_manual)
        self.assertIsNone(self.controller._paused_pose)
        self.assertTrue(self.controller.status()["game_ui_visible"])
        self.assertEqual(self.bridge.owner, "game_ui")
        self.assertEqual(self.console.values["citadel_hud_visible"], 1)
        self.assertEqual(self.console.values["citadel_hide_replay_hud"], 0)
        self.assertEqual(self.console.values["hud_free_cursor"], 1)
        self.assertEqual(self.controller._game_ui_restore, originals)
        self.controller.toggle_game_ui(False)
        self.assertTrue(self.controller._native_manual)
        self.assertFalse(self.controller._game_ui_visible)
        self.assertEqual(self.bridge.owner, "flight")

    def test_failed_replay_ui_recovery_preserves_original_bone_error(self):
        self.controller.toggle_game_ui(True)
        self.console.fail_commands.add("hud_free_cursor 1")
        with patch.object(self.bridge, "start_flight", side_effect=RuntimeError("Bone pose unavailable")):
            with self.assertLogs("dolly", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "Bone pose unavailable"):
                    self.controller.toggle_game_ui(False)
        self.assertFalse(self.controller._native_manual)
        self.assertTrue(self.controller._game_ui_restore)

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
        self.controller.toggle_game_ui(True)
        self.controller.toggle_game_ui(False)
        self.controller.stop()
        self.assertEqual(self.console.values, self.original_values)

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
