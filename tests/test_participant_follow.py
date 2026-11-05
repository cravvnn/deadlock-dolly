"""Participant intent and exact identity boundaries without a game process."""
import struct
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dolly._runtime_generated import FollowTarget as profile
from dolly.replay_player_focus import resolve_participant, ReplayPlayerUnavailable
from tests import test_rework_player_focus as focus_fixtures
from tests import test_follow_controller as follow_fixtures


class ParticipantIdentityTests(unittest.TestCase):
    def fixture(self):
        monitor, fields, players, controller = focus_fixtures.ReworkPlayerFocusTests().memory_fixture()
        fields[controller + profile.CONTROLLER_PAWN, 4] = struct.pack('<I', 0xffffffff)
        player = dict(players[1], model=42)
        fields[0xc0000 + profile.PAWN_CONTROLLER, 4] = struct.pack('<I', 0x10001)
        return monitor, fields, controller, player

    def test_absent_controller_pawn_finds_unique_fresh_full_backlink(self):
        monitor, _, _, player = self.fixture()
        evidence = {}
        self.assertEqual(resolve_participant(monitor, 1, {'players': [player]}, evidence=evidence),
                         dict(handle=0x1004c, entity_index=76, controller_handle=0x10001))
        self.assertEqual(evidence['lookup'], 'pawn_controller_backlink')
        self.assertGreaterEqual(monitor._owned.call_count, 4)

    def test_observer_controller_pawn_uses_verified_hero_backlink(self):
        monitor, fields, controller, player = self.fixture()
        index, pawn = 80, 0xe0000
        identity = 0x80000 + profile.IDENTITY_STRIDE * index
        fields.update({(controller + profile.CONTROLLER_PAWN, 4): struct.pack('<I', 0x10050),
                       (identity, 8): struct.pack('<Q', pawn),
                       (identity + profile.IDENTITY_HANDLE, 4): struct.pack('<I', 0x10050),
                       (pawn + profile.ENTITY_IDENTITY, 8): struct.pack('<Q', identity),
                       (pawn, 8): struct.pack('<Q', monitor.base + profile.OBSERVER_PAWN_VTABLE)})
        self.assertEqual(resolve_participant(monitor, 1, {'players': [player]})['entity_index'], 76)

    def test_model_or_roster_position_cannot_identify_other_players_hero(self):
        monitor, fields, _, player = self.fixture()
        fields[0xc0000 + profile.PAWN_CONTROLLER, 4] = struct.pack('<I', 0x20001)
        with self.assertRaises(ReplayPlayerUnavailable):
            resolve_participant(monitor, 1, {'players': [dict(player, name='selected player')]})

    def test_stale_serial_wrong_backpointer_or_unreviewed_type_never_matches(self):
        for problem in ('serial', 'backpointer', 'vtable'):
            monitor, fields, _, player = self.fixture()
            address, size, value = {
                'serial': (0x80000 + profile.IDENTITY_STRIDE * 76 + profile.IDENTITY_HANDLE, 4, 0x2004c),
                'backpointer': (0xc0000 + profile.ENTITY_IDENTITY, 8, 0),
                'vtable': (0xc0000, 8, monitor.base + 1234),
            }[problem]
            fields[address, size] = struct.pack('<I' if size == 4 else '<Q', value)
            with self.subTest(problem=problem), self.assertRaises(ReplayPlayerUnavailable):
                resolve_participant(monitor, 1, {'players': [player]})

    def test_primary_structural_error_does_not_fall_back(self):
        monitor, fields, controller, player = self.fixture()
        fields[controller, 8] = struct.pack('<Q', 0)
        with self.assertRaisesRegex(RuntimeError, 'controller changed'):
            resolve_participant(monitor, 1, {'players': [player]})
        self.assertNotIn((0xc0000 + profile.PAWN_CONTROLLER, 4),
                         [call.args for call in monitor.memory.read.call_args_list])

    def test_changed_backlink_or_unreadable_field_fails_closed(self):
        monitor, fields, _, player = self.fixture()
        backlink = 0xc0000 + profile.PAWN_CONTROLLER
        seen = False
        def read(address, size):
            nonlocal seen
            if address == backlink:
                if seen:
                    return struct.pack('<I', 0x20001)
                seen = True
            return fields[address, size]
        monitor.memory.read.side_effect = read
        with self.assertRaisesRegex(RuntimeError, 'changed during'):
            resolve_participant(monitor, 1, {'players': [player]})
        monitor.memory.read.side_effect = lambda address, size: fields[address, size]
        del fields[backlink, 4]
        with self.assertRaises(KeyError):
            resolve_participant(monitor, 1, {'players': [player]})

    def test_multiple_unique_heroes_are_ambiguous_duplicates_are_not(self):
        monitor, fields, _, player = self.fixture()
        self.assertEqual(resolve_participant(monitor, 1, {'players': [player, player]})['handle'], player['handle'])
        index, pawn = 77, 0xe0000
        identity = 0x80000 + profile.IDENTITY_STRIDE * index
        fields.update({(identity, 8): struct.pack('<Q', pawn),
                       (identity + profile.IDENTITY_HANDLE, 4): struct.pack('<I', 0x1004d),
                       (pawn + profile.ENTITY_IDENTITY, 8): struct.pack('<Q', identity),
                       (pawn, 8): struct.pack('<Q', monitor.base + profile.FAMILIAR_CLONE_PAWN_VTABLE),
                       (pawn + profile.PAWN_CONTROLLER, 4): struct.pack('<I', 0x10001)})
        with self.assertRaisesRegex(ReplayPlayerUnavailable, 'more than one'):
            resolve_participant(monitor, 1, {'players': [player, dict(player, handle=0x1004d, entity_index=77)]})

    def test_valid_primary_does_not_need_roster_or_backlink(self):
        monitor, _, _, _ = focus_fixtures.ReworkPlayerFocusTests().memory_fixture()
        self.assertEqual(resolve_participant(monitor, 1, {'players': []})['handle'], 0x1004c)

    def test_malformed_or_oversized_fallback_roster_is_not_scanned(self):
        for rows in ({'wrong': 'shape'}, [None] * 17):
            monitor, _, _, _ = self.fixture()
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, 'roster is invalid'):
                resolve_participant(monitor, 1, {'players': rows})


class ParticipantFollowControllerTests(unittest.TestCase):
    def setUp(self):
        follow_fixtures.FollowControllerTests.setUp(self)
        self.players = {
            1: dict(handle=0x1004c, entity_index=76, model=42, model_path='models/heroes/ivy/ivy.vmdl'),
            2: dict(handle=0x1004d, entity_index=77, model=43, model_path='models/heroes/haze/haze.vmdl'),
        }
        self.selected = 1
        self.bridge.editor_roster = Mock(side_effect=lambda: {'players': list(self.players.values())})
        self.target = Mock()
        self.target.sample_target.side_effect = lambda **_: dict(handle=self.players[self.selected]['handle'], mode=2)
        self.target_factory = patch('dolly.follow_target.FollowTargetMonitor', return_value=self.target)
        self.target_factory.start()
        self.addCleanup(self.target_factory.stop)
        def resolve(_monitor, identifier, _roster, **_):
            self.selected = identifier
            return dict(handle=self.players[identifier]['handle'],
                        entity_index=self.players[identifier]['entity_index'], controller_handle=0x10000 + identifier)
        self.resolver_patch = patch('dolly.replay_player_focus.resolve_participant', side_effect=resolve)
        self.resolve = self.resolver_patch.start()
        self.addCleanup(self.resolver_patch.stop)

    def test_select_same_player_next_and_back_reacquire_stock_camera(self):
        for identifier in (1, 1, 2, 1):
            self.c.follow_replay_player(identifier)
            self.assertEqual(self.c._participant_follow['identifier'], identifier)
            self.assertEqual(self.c.status()['participant_follow_player_id'], identifier)
            self.assertTrue(self.c._follow_active)
            self.assertFalse(self.c._native_active)
            self.assertFalse(self.c._native_manual)
            self.assertEqual(self.bridge.owner, 'panel')
        targets = [event for event in self.console.events if event.startswith('spec_target ')]
        self.assertEqual(targets.count('spec_target 76'), 3)
        self.assertEqual(targets.count('spec_target 77'), 1)

    def test_stock_game_follow_survives_replay_seek(self):
        # F9/stock Game Follow has no participant intent. A timeline seek must
        # keep the selected hero's rig instead of re-entering native flight at
        # the pre-seek pose.
        self.c._follow_active = True
        self.c._participant_follow = None
        before = len(self.console.events)
        result = self.c.seek_replay_tick(200)
        self.assertEqual(result['tick'], 200)
        self.assertTrue(self.c._follow_active)
        self.assertFalse(self.c._native_manual)
        self.assertNotIn('native.flight', self.console.events[before:])
        self.assertIn('Following the selected player.', self.c.status()['message'])

    def test_real_backlink_resolution_reaches_stock_target_and_refreshes_serial_after_seek(self):
        self.resolver_patch.stop()
        monitor, fields, _, controller = focus_fixtures.ReworkPlayerFocusTests().memory_fixture()
        fields[controller + profile.CONTROLLER_PAWN, 4] = struct.pack('<I', 0xffffffff)
        fields[0xc0000 + profile.PAWN_CONTROLLER, 4] = struct.pack('<I', 0x10001)
        self.target.base, self.target.memory, self.target._owned = monitor.base, monitor.memory, monitor._owned
        self.bridge.editor_roster.side_effect = lambda: {'players': [self.players[1]]}
        self.c.follow_replay_player(1)
        self.assertTrue(self.c._follow_active)
        self.assertIn('spec_target 76', self.console.events)
        fields[0x80000 + profile.IDENTITY_STRIDE * 76 + profile.IDENTITY_HANDLE, 4] = struct.pack('<I', 0x2004c)
        self.players[1] = dict(self.players[1], handle=0x2004c)
        before = len(self.console.events)
        self.c.seek_replay_tick(200)
        self.assertTrue(self.c._follow_active)
        self.assertEqual(self.c._follow_selection_evidence['expected_handle'], 0x2004c)
        self.assertIn('spec_target 76', self.console.events[before:])
        self.assertNotIn('native.flight', self.console.events)

    def test_play_pause_keeps_stock_follow_and_never_adds_native_writer(self):
        self.c.follow_replay_player(1)
        intent = self.c._participant_follow
        self.c.toggle_replay()
        self.assertFalse(self.console.paused)
        self.assertTrue(self.c._follow_active)
        self.assertIs(self.c._participant_follow, intent)
        self.c.toggle_replay()
        self.assertTrue(self.console.paused)
        self.assertTrue(self.c._follow_active)
        self.assertNotIn('native.flight', self.console.events)

    def test_stock_mode_handoff_advances_then_returns_to_selected_paused_tick(self):
        requested = self.console.tick
        request = self.console.request

        def advancing_mode(command, *args, **kwargs):
            output = request(command, *args, **kwargs)
            if command == 'citadel_spectator_mode 2':
                self.console.tick += 9
                self.console.paused = False
            return output

        self.console.request = advancing_mode
        self.c.follow_replay_player(1)
        self.assertTrue(self.c._follow_active)
        self.assertTrue(self.console.paused)
        self.assertEqual(self.console.tick, requested)
        self.assertEqual(self.c._follow_selection_evidence['expected_handle'], self.players[1]['handle'])

    def test_cancel_at_mode_handoff_never_seeks_or_enables_rig(self):
        request = self.console.request
        boundary = []

        def cancel(command, *args, **kwargs):
            output = request(command, *args, **kwargs)
            if command == 'citadel_spectator_mode 2':
                self.c._stop_event.set()
                boundary.append(len(self.console.events))
            return output

        self.console.request = cancel
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.c.follow_replay_player(1)
        later = self.console.events[boundary[0]:]
        self.assertFalse(any(e.startswith('demo_gototick ') for e in later))
        self.assertNotIn('citadel_camera_overrides_enabled 1', later)

    def test_post_handoff_controller_identity_change_never_enables_rig(self):
        request = self.console.request
        def drift(command, *args, **kwargs):
            result = request(command, *args, **kwargs)
            if command == 'citadel_spectator_mode 2':
                self.console.tick += 9
            return result
        self.console.request = drift
        resolve = self.resolve.side_effect
        seek = self.c._seek_tick
        restored = False

        def restore_tick(*args, **kwargs):
            nonlocal restored
            result = seek(*args, **kwargs)
            restored = True
            return result

        def changed_identity(*args, **kwargs):
            player = resolve(*args, **kwargs)
            return dict(player, controller_handle=0x20001) if restored else player

        self.resolve.side_effect = changed_identity
        with patch.object(self.c, '_seek_tick', side_effect=restore_tick):
            with self.assertRaisesRegex(RuntimeError, 'player changed during Follow camera handoff'):
                self.c.follow_replay_player(1)
        self.assertFalse(self.c._follow_active)
        self.target.sample_target.assert_not_called()
        self.assertNotIn('citadel_camera_overrides_enabled 1', self.console.events)

    def test_forward_rewind_and_relative_seek_resolve_fresh_pawn_each_time(self):
        self.c.follow_replay_player(1)
        intent = self.c._participant_follow
        old_settings = intent['settings']
        self.players[1] = dict(self.players[1], handle=0x2004e, entity_index=78)
        self.c.seek_replay_tick(3470)
        self.assertEqual(self.console.tick, 3470)
        self.assertIs(self.c._participant_follow, intent)
        self.assertIs(intent['settings'], old_settings)
        self.assertTrue(self.c._follow_active)
        self.assertIn('spec_target 78', self.console.events)
        with patch('dolly.controller.packet_index', return_value=None):
            self.c.step_replay_ticks(-25)
        self.assertEqual(self.console.tick, 3445)
        self.c.seek_relative(-2, tick_rate=10)
        self.assertEqual(self.console.tick, 3425)
        self.assertIs(self.c._participant_follow, intent)
        self.assertTrue(self.c._follow_active)
        self.assertFalse(self.c._native_manual)
        self.assertNotIn('native.flight', self.console.events)

    def test_absent_after_seek_waits_without_stale_target_and_reacquires_later(self):
        self.c.follow_replay_player(1)
        intent = self.c._participant_follow
        saved_resolver = self.resolve.side_effect
        self.resolve.side_effect = ReplayPlayerUnavailable('This player has no hero at this tick. Camera unchanged.')
        before = len(self.console.events)
        self.c.seek_replay_tick(200)
        self.assertEqual(self.resolve.call_count, 9)  # 3 initial (including post-handoff) + 6 absent.
        self.assertIs(self.c._participant_follow, intent)
        self.assertFalse(self.c._follow_active)
        self.assertTrue(self.c._native_manual)
        self.assertEqual(self.bridge.owner, 'panel')
        self.assertFalse(any(event.startswith('spec_target ') for event in self.console.events[before:]))
        self.assertIn('Follow is waiting', self.c.status()['message'])
        self.assertEqual(self.c.status()['participant_follow_player_id'], 1)
        self.resolve.side_effect = saved_resolver
        self.c.seek_replay_tick(300)
        self.assertTrue(self.c._follow_active)
        self.assertFalse(self.c._native_manual)
        self.assertIs(self.c._participant_follow, intent)

    def test_transient_absence_retries_only_until_fresh_identity_resolves(self):
        real_resolve = self.resolve.side_effect
        attempts = 0
        def delayed(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ReplayPlayerUnavailable('not ready')
            return real_resolve(*args, **kwargs)
        self.resolve.side_effect = delayed
        self.c.follow_replay_player(1)
        self.assertEqual(attempts, 5)  # 3 lookups + write-boundary and post-handoff checks.
        self.assertTrue(self.c._follow_active)

    def test_absent_new_player_replaces_old_intent_and_later_seek_acquires_new_target(self):
        self.c.follow_replay_player(1)
        before = len(self.console.events)
        resolver = self.resolve.side_effect
        self.resolve.side_effect = ReplayPlayerUnavailable('no hero')
        message = self.c.follow_replay_player(2)
        intent = self.c._participant_follow
        self.assertEqual(intent['identifier'], 2)
        self.assertEqual(self.c.status()['participant_follow_player_id'], 2)
        self.assertFalse(self.c._follow_active)
        self.assertTrue(self.c._native_manual)
        self.assertIn('Follow is waiting', message)
        self.assertFalse(any(event.startswith('spec_target ') for event in self.console.events[before:]))
        self.resolve.side_effect = resolver
        self.c.seek_replay_tick(200)
        self.assertIs(self.c._participant_follow, intent)
        self.assertTrue(self.c._follow_active)
        self.assertEqual([e for e in self.console.events if e.startswith('spec_target ')][-1], 'spec_target 77')
        self.c.follow_replay_player(1)
        self.assertEqual(self.c._participant_follow['identifier'], 1)

    def test_structural_new_selection_error_explicitly_retains_previous_target(self):
        self.c.follow_replay_player(1)
        intent = self.c._participant_follow
        self.resolve.side_effect = RuntimeError('identity differs')
        with self.assertRaisesRegex(RuntimeError, 'previous player is still being followed'):
            self.c.follow_replay_player(2)
        self.assertIs(self.c._participant_follow, intent)
        self.assertTrue(self.c._follow_active)

    def test_structural_error_is_not_retried_and_does_not_write(self):
        self.resolve.side_effect = RuntimeError('identity differs')
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.c.follow_replay_player(1)
        self.assertEqual(self.resolve.call_count, 1)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(any(event.startswith('spec_target ') for event in self.console.events))

    def test_changed_write_boundary_identity_is_rejected(self):
        self.resolve.side_effect = [dict(handle=0x1004c, entity_index=76, controller_handle=0x10001),
                                    dict(handle=0x2004c, entity_index=76, controller_handle=0x10001)]
        with self.assertRaisesRegex(RuntimeError, 'changed before Follow'):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(any(event.startswith('spec_target ') for event in self.console.events))

    def test_playing_or_recording_rejects_before_identity_or_camera_writes(self):
        for recording in (False, True):
            self.console.paused = recording
            self.c._recorder_active.return_value = recording
            before = dict(self.console.values)
            with self.subTest(recording=recording), self.assertRaisesRegex(RuntimeError, 'Pause the replay'):
                self.c.follow_replay_player(1)
            self.assertIsNone(self.c._participant_follow)
            self.assertEqual(self.console.values, before)
        self.resolve.assert_not_called()

    def test_replay_moves_during_lookup_is_rejected_before_target_write(self):
        resolve = self.resolve.side_effect
        def move(*args, **kwargs):
            result = resolve(*args, **kwargs)
            self.console.tick += 1
            return result
        self.resolve.side_effect = move
        with self.assertRaisesRegex(RuntimeError, 'replay changed'):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(any(event.startswith('spec_target ') for event in self.console.events))

    def test_cancel_during_stock_confirmation_stops_before_rig_enable(self):
        def cancel(**_):
            self.c.stop_game_follow()
            return dict(handle=self.players[1]['handle'], mode=2)
        self.target.sample_target.side_effect = cancel
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(self.c._follow_active)
        from dolly.follow_camera import ENABLED
        self.assertFalse(any(event.startswith(ENABLED + ' 1') for event in self.console.events))

    def test_cancel_after_stock_rig_applies_restores_it_and_retires_intent(self):
        cancelled = False
        calls = 0
        def target(**_):
            nonlocal calls, cancelled
            calls += 1
            cancelled = calls == 2
            return dict(handle=self.players[1]['handle'], mode=2)
        self.target.sample_target.side_effect = target
        with self.c._paused_preparation(lambda: cancelled), self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(self.c._follow_active)
        self.assertIsNone(self.c._follow_transaction)
        from dolly.follow_camera import ENABLED
        self.assertEqual(self.console.values[ENABLED], self.originals[ENABLED])

    def test_invalid_native_pose_never_establishes_participant_intent(self):
        self.bridge.original[0] = float('nan')
        with self.assertRaises(RuntimeError):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.resolve.assert_not_called()

    def test_scoped_cancellation_during_absence_never_arms_hold(self):
        cancelled = False
        def absent(*_, **__):
            nonlocal cancelled
            cancelled = True
            raise ReplayPlayerUnavailable('no hero')
        self.resolve.side_effect = absent
        with self.c._paused_preparation(lambda: cancelled), self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.c.follow_replay_player(1)
        self.assertIsNone(self.c._participant_follow)
        self.assertNotIn('native.flight', self.console.events)
        self.assertIsNone(self.c._paused_cancelled)

    def test_fly_f9_stop_and_ordinary_halt_clear_participant(self):
        for takeover in (self.c.enter_native_flight, lambda: self.c.toggle_game_ui(True),
                         self.c.stop, self.c._halt):
            with self.subTest(takeover=takeover):
                self.c.follow_replay_player(1)
                takeover()
                self.assertIsNone(self.c._participant_follow)
                self.assertIsNone(self.c.status()['participant_follow_player_id'])
                self.assertFalse(self.c._follow_active)

    def test_status_does_not_report_participant_from_a_replaced_session(self):
        from types import SimpleNamespace
        self.c.follow_replay_player(1)
        self.assertEqual(self.c.status()['participant_follow_player_id'], 1)
        self.c._session = SimpleNamespace(**vars(self.c._session))
        self.assertIsNone(self.c.status()['participant_follow_player_id'])
        self.assertIsNone(self.c._participant_follow)

    def assert_seek_cancels(self, problem):
        self.c.follow_replay_player(1)
        def seek(target, **_):
            if problem == 'cancel':
                self.c._stop_event.set()
            elif problem == 'session':
                self.c._session = object()
            elif problem == 'demo':
                self.c._demo = Path('/chosen/other.dem')
            else:
                raise RuntimeError('seek failed')
            return {'tick': target}
        self.resolve.reset_mock()
        with patch.object(self.c, '_seek_tick', side_effect=seek), self.assertRaises(RuntimeError):
            self.c.seek_replay_tick(200)
        self.assertIsNone(self.c._participant_follow)
        self.resolve.assert_not_called()

    def test_cancel_during_seek_cannot_resurrect_target(self):
        self.assert_seek_cancels('cancel')

    def test_changed_session_during_seek_cannot_resurrect_target(self):
        self.assert_seek_cancels('session')

    def test_changed_demo_during_seek_cannot_resurrect_target(self):
        self.assert_seek_cancels('demo')

    def test_failed_seek_cannot_resurrect_target(self):
        self.assert_seek_cancels('seek_error')

    def test_cancel_while_absent_hold_is_arming_cannot_restore_intent(self):
        self.c.follow_replay_player(1)
        self.resolve.side_effect = ReplayPlayerUnavailable('no hero')
        arm = self.bridge.start_flight
        def cancel(*args, **kwargs):
            result = arm(*args, **kwargs)
            self.c.stop_game_follow()
            return result
        with patch.object(self.bridge, 'start_flight', side_effect=cancel), self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.c.seek_replay_tick(200)
        self.assertIsNone(self.c._participant_follow)
        self.assertFalse(self.c._follow_active)

    def test_capture_pause_preference_preserves_active_follow_and_visible_pose(self):
        self.c.follow_replay_player(1)
        intent = self.c._participant_follow
        pose = [11., 22., 333., 4., 55., 6., 1.6]
        self.bridge.original = list(pose)
        for pause in (False, True):
            self.console.paused = False
            frame, tick = self.c.capture_native_snapshot(dict(pose=pose, tick=self.console.tick, paused=False), pause=pause)
            self.assertEqual(tick, self.console.tick)
            self.assertEqual([getattr(frame, field) for field in ('x','y','z','pitch','yaw','roll','aspect_ratio')], pose)
            self.assertEqual(self.console.paused, pause)
            self.assertIs(self.c._participant_follow, intent)
            self.assertTrue(self.c._follow_active)
            self.assertFalse(self.c._native_active)
            self.assertFalse(self.c._native_manual)
        sampled = self.c._sample_paused_native_view()
        self.assertEqual(sampled['pose'], pose)
        self.assertIs(self.c._participant_follow, intent)

    def test_ordinary_free_camera_relative_seek_returns_pose(self):
        self.c.enter_native_flight()
        self.bridge.pose[2] = 777.
        result = self.c.seek_relative(2, tick_rate=10)
        self.assertEqual(result['z'], 777.)
        self.assertEqual(self.console.tick, 120)
        self.assertIsNone(self.c._participant_follow)


if __name__ == '__main__':
    unittest.main()
