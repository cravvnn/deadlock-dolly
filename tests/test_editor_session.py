"""Integration guards for Tk polling versus asynchronous native transitions."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dolly import editor_session as session
from dolly.native_bridge import NativeBridgeError
from dolly.path import Project, Keyframe
from dolly.settings import AppSettings


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class EditorSessionTests(unittest.TestCase):
    def test_pause_after_capture_preference_saves_before_publication(self):
        from unittest.mock import patch
        self.app.native_editor_active = True
        for enabled in (True, False):
            with self.subTest(enabled=enabled), patch('dolly.editor_dispatch.save_settings') as save:
                previous = self.app.app_settings
                session.dispatch(self.app, {'action': 'set_pause_after_capture', 'value': int(enabled)}, self.bridge)
                label, operation, complete = self.app._submit.call_args.args
                self.assertIs(self.app.app_settings, previous)
                result = operation()
                save.assert_called_once()
                self.assertEqual(save.call_args.args[0].pause_after_capture, enabled)
                complete(result)
                self.assertEqual(self.app.app_settings.pause_after_capture, enabled)
                self.assertEqual(self.bridge.configure_editor.call_args.kwargs['pause_after_capture'], enabled)

    def test_invalid_pause_after_capture_choices_do_not_save(self):
        from unittest.mock import patch
        for value in (-1, 2, .5, float('nan'), True, '1'):
            with self.subTest(value=value), patch('dolly.editor_dispatch.save_settings') as save:
                with self.assertRaisesRegex(ValueError, 'on or off'):
                    session.dispatch(self.app, {'action': 'set_pause_after_capture', 'value': value}, self.bridge)
                save.assert_not_called()

    def test_follow_capability_recovery_republishes_cached_controls(self):
        from dolly.replay_camera import CLIENT_SHA256
        self.controller.status.return_value['native_editor_active'] = True
        self.controller._startup_evidence = {'replay_camera_identity': {'client_sha256': CLIENT_SHA256}}
        self.controller._follow_transaction = None
        self.controller._follow_mode_original = None
        self.bridge.capability_available.return_value = False
        session.configure(self.app)
        self.assertFalse(self.bridge.configure_editor_follow.call_args.kwargs['available'])
        self.bridge.configure_editor_follow.reset_mock()
        self.bridge.capability_available.return_value = True
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor_follow.call_args.kwargs['available'])
        self.bridge.configure_editor_follow.assert_called_once()

    def test_follow_attach_preview_waits_for_successful_camera_handoff(self):
        from unittest.mock import patch
        self.controller._follow_active = True
        self.app.preview_attach = False
        with patch.object(session, 'configure') as configure:
            session.dispatch(self.app, {'action': 'attach_preview', 'value': 1}, self.bridge)
            work = self.app._submit.call_args.args
            self.assertFalse(self.app.preview_attach)
            configure.assert_not_called()
            result = work[1]()
            self.controller.enter_native_flight.assert_called_once_with(owner='panel')
            work[2](result)
            self.assertTrue(self.app.preview_attach)
            configure.assert_called_once_with(self.app)

    def test_failed_follow_handoff_does_not_enable_attach_preview(self):
        self.controller._follow_active = True
        self.controller.enter_native_flight.side_effect = RuntimeError('handoff failed')
        self.app.preview_attach = False
        session.dispatch(self.app, {'action': 'attach_preview', 'value': 1}, self.bridge)
        with self.assertRaisesRegex(RuntimeError, 'handoff failed'):
            self.app._submit.call_args.args[1]()
        self.assertFalse(self.app.preview_attach)

    def test_framing_grid_toggle_saves_and_publishes(self):
        from unittest.mock import patch
        self.app.app_settings = AppSettings()
        with patch('dolly.editor_dispatch.save_settings') as save:
            session.dispatch(self.app, {'action': 'framing_grid', 'value': 0}, self.bridge)
            label, operation, complete = self.app._submit.call_args.args
            self.assertIn("framing", label.lower())
            complete(operation())
            save.assert_called_once()
            self.assertTrue(self.app.app_settings.framing_grid_enabled)
            self.assertIn("on", self.app.status_text.set.call_args.args[0])
            self.app._submit.reset_mock()
            session.dispatch(self.app, {'action': 'framing_grid', 'value': 0}, self.bridge)
            _, operation, complete = self.app._submit.call_args.args
            complete(operation())
            self.assertFalse(self.app.app_settings.framing_grid_enabled)
            self.assertIn("off", self.app.status_text.set.call_args.args[0])

    def test_editing_stop_preserves_speed_through_the_native_panel(self):
        self.assertTrue(session.dispatch(self.app, {'action': 'stop', 'value': 0}, self.bridge))
        self.app._submit.call_args.args[1]()
        self.controller.stop.assert_called_once_with(preserve_speed=True)

    def camera_list_app(self):
        from dolly.shot_history import ShotHistory
        self.app.project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6),
                                               Keyframe(2, 7, 8, 9, 10, 11, 12)])
        self.app.shot_history = ShotHistory(self.app.project)
        self.app.playing = False
        self.app._set_time = Mock()
        self.app._play = Mock()
        self.app.camera_tree = Mock()
        self.app._select_key = Mock()
        self.app._snapshot = Mock(return_value=self.app.project)
        self.app._shot_edit_ready = Mock(return_value=True)
        self.app._delete_key = Mock()
        self.app.undo_shot, self.app.redo_shot = Mock(), Mock()

    def test_camera_click_selects_without_moving_replay_or_camera(self):
        self.camera_list_app()
        session.dispatch(self.app, {'action': 'select_camera', 'value': 1,
                                   'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        self.app.camera_tree.selection_set.assert_called_with('1')
        self.app._select_key.assert_called_once()
        self.app._submit.assert_not_called()

    def test_paused_recording_rejects_clear_without_an_error_dialog(self):
        from dolly.gui import DollyApp
        for state in ('starting', 'recording', 'finalizing'):
            with self.subTest(state=state):
                self.camera_list_app()
                self.app.native_editor_active = True
                self.app.video_export = Mock()
                self.app.video_export.status.return_value = {'state': state}
                self.app._shot_edit_ready = lambda: DollyApp._shot_edit_ready(self.app)
                self.app._commit_project = Mock()
                self.app._native_camera_page = 32
                self.app._error.reset_mock()
                self.bridge.acknowledge_editor_event.reset_mock()
                original = self.app.project.to_dict()
                revision = self.app.shot_history.revision
                self.bridge.editor_status.return_value = {'events': [
                    {'sequence': 1, 'action': 'clear_camera_path', 'value': 0,
                     'pose': (revision, 0, 0, 0, 0, 0, 0)}]}
                session.poll(self.app)
                self.app._error.assert_not_called()
                self.app._commit_project.assert_not_called()
                self.assertEqual(self.app.project.to_dict(), original)
                self.assertEqual(self.app.shot_history.revision, revision)
                self.assertIn('recording', self.app.status_text.set.call_args.args[0])
                self.assertNotIn('Stop playback', self.app.status_text.set.call_args.args[0])
                self.bridge.acknowledge_editor_event.assert_called_once_with(1)

    def test_clear_during_recording_ignores_stale_confirmation(self):
        self.camera_list_app()
        self.app._shot_edit_ready.return_value = False
        self.app._commit_project = Mock()
        self.assertTrue(session.dispatch(self.app, {
            'action': 'clear_camera_path', 'value': 0,
            'pose': (self.app.shot_history.revision - 1, 0, 0, 0, 0, 0, 0)}, self.bridge))
        self.app._commit_project.assert_not_called()

    def test_clear_during_playback_keeps_cameras_and_reports_status(self):
        self.camera_list_app()
        self.app.playing = True
        self.app._commit_project = Mock()
        self.assertTrue(session.dispatch(self.app, {
            'action': 'clear_camera_path', 'value': 0,
            'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}, self.bridge))
        self.app._commit_project.assert_not_called()
        self.assertIn('Stop path playback', self.app.status_text.set.call_args.args[0])

    def test_rejected_clear_keeps_finish_recording_available(self):
        from dolly.gui import DollyApp
        self.camera_list_app()
        self.app.native_editor_active = True
        self.app.video_export = Mock()
        self.app.video_export.status.return_value = {'state': 'recording'}
        self.app._shot_edit_ready = lambda: DollyApp._shot_edit_ready(self.app)
        self.app._commit_project = Mock()
        self.app._stop_video_recording = Mock()
        self.bridge.editor_status.return_value = {'events': [
            {'sequence': 1, 'action': 'clear_camera_path', 'value': 0,
             'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)},
            {'sequence': 2, 'action': 'stop_video', 'value': 0}]}
        session.poll(self.app)
        self.app._error.assert_not_called()
        self.app._commit_project.assert_not_called()
        self.app._stop_video_recording.assert_called_once_with(cancel=False)
        self.assertEqual([call.args[0] for call in self.bridge.acknowledge_editor_event.call_args_list], [1, 2])

    def test_clear_waits_for_queued_recording_and_then_can_be_retried(self):
        from dolly.gui import DollyApp
        self.camera_list_app()
        self.app.video_export = Mock()
        self.app.video_export.status.return_value = {'state': 'completed'}
        self.app._shot_edit_ready = lambda: DollyApp._shot_edit_ready(self.app)
        self.app._commit_project = Mock()
        event = {'action': 'clear_camera_path', 'value': 0,
                 'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}
        for name in ('_layer_queue', '_active_layer_take'):
            with self.subTest(name=name):
                setattr(self.app, name, ['players'])
                self.assertTrue(session.dispatch(self.app, event, self.bridge))
                self.app._commit_project.assert_not_called()
                self.assertEqual(self.app.status_text.set.call_args.args[0],
                                 'Finish recording before editing cameras.')
                setattr(self.app, name, None)
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        self.assertEqual(self.app._commit_project.call_args.args[0].keyframes, [])

    def test_cinematic_preparation_waits_for_seek_completion(self):
        self.camera_list_app()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'prepare_cinematic', 'value': 7,
                                   'pose': (revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        self.bridge.configure_editor_cinematic.assert_called_once_with(7, revision, 1)
        _, operation, complete = self.app._submit.call_args.args
        result = operation()
        self.controller.seek.assert_called_once_with(self.app.project, 0)
        self.bridge.configure_editor_cinematic.assert_called_once()
        complete(result)
        self.bridge.configure_editor_cinematic.assert_called_with(7, revision, 2)
        self.app._play.assert_not_called()

    def test_cinematic_preparation_failure_never_reports_ready(self):
        self.camera_list_app()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'prepare_cinematic', 'value': 7,
                                   'pose': (revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        self.controller.seek.side_effect = RuntimeError('seek failed')
        with self.assertRaisesRegex(RuntimeError, 'seek failed'):
            self.app._submit.call_args.args[1]()
        self.bridge.configure_editor_cinematic.assert_called_with(7, revision, 3)

    def test_cinematic_preparation_rejects_changed_path(self):
        self.camera_list_app()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'prepare_cinematic', 'value': 7,
                                   'pose': (revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        _, operation, complete = self.app._submit.call_args.args
        result = operation()
        self.app.project = Project()
        complete(result)
        self.bridge.configure_editor_cinematic.assert_called_with(7, revision, 3)

    def test_previous_and_next_camera_select_and_view_saved_pose(self):
        for action, initial, expected in [('previous_view', 1, 0), ('next_view', 0, 1)]:
            with self.subTest(action=action):
                self.camera_list_app()
                self.app._selection_index = Mock(return_value=initial)
                session.dispatch(self.app, {'action': action, 'value': 0}, self.bridge)
                self.app.camera_tree.selection_set.assert_called_with(str(expected))
                self.app._submit.call_args.args[1]()
                self.controller.select_paused_camera.assert_called_with(
                    self.app.project, self.app.project.keyframes[expected].time)

    def test_stop_invalidates_cinematic_completion(self):
        self.camera_list_app()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'prepare_cinematic', 'value': 7,
                                   'pose': (revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        _, operation, complete = self.app._submit.call_args.args
        result = operation()
        session.dispatch(self.app, {'action': 'stop', 'value': 0}, self.bridge)
        self.bridge.configure_editor_cinematic.assert_called_with(7, revision, 3)
        complete(result)
        self.bridge.configure_editor_cinematic.assert_called_with(7, revision, 3)

    def test_camera_publication_backfills_the_last_wire_block(self):
        from dolly.shot_history import ShotHistory
        self.camera_list_app()
        self.app.project = Project(keyframes=[Keyframe(i, 1, 2, 3, 4, 5, 6) for i in range(33)])
        self.app.shot_history = ShotHistory(self.app.project)
        self.app._selection_index = Mock(return_value=32)
        self.app._native_camera_selected = 31
        self.app.native_editor_active = True
        session.configure(self.app)
        self.assertEqual(self.bridge.configure_editor_cameras.call_args.args[2], 1)
        self.assertEqual(self.bridge.configure_lite_replay.call_args.args[2], 1)

    def test_view_camera_uses_paused_view_and_delete_never_moves_it(self):
        for action in ('view_camera', 'delete_camera'):
            with self.subTest(action=action):
                self.camera_list_app()
                self.app._submit.reset_mock()
                session.dispatch(self.app, {'action': action, 'value': 1,
                                           'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
                if action == 'view_camera':
                    self.app._submit.call_args.args[1]()
                    self.controller.select_paused_camera.assert_called_with(self.app.project, 2)
                else:
                    self.app._delete_key.assert_called_once()
                    self.app._submit.assert_not_called()

    def test_seek_camera_queues_the_selected_authored_endpoint_snapshot(self):
        from copy import deepcopy
        from dolly.shot_history import ShotHistory
        self.camera_list_app()
        self.app.project = Project(start_tick=31191, tick_rate=64, keyframes=[
            Keyframe(0, 1, 2, 3, 4, 5, 6),
            Keyframe(7.09375, 70, 80, 90, 10, 110, 12, aspect_ratio=1.8)])
        self.app.shot_history = ShotHistory(self.app.project)
        self.app._snapshot = lambda: deepcopy(self.app.project)
        self.app.shot_time = Value(2.35)
        saved = self.app.project.to_dict()
        self.assertTrue(session.dispatch(self.app, {
            'action': 'seek_camera', 'value': 1,
            'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}, self.bridge))
        self.assertEqual(self.app.project.to_dict(), saved)
        self.controller.seek.assert_not_called()
        operation = self.app._submit.call_args.args[1]
        # Editing or replacing the live shot after dispatch cannot retarget a
        # queued camera seek, including its authored time and full saved pose.
        self.app.project.start_tick = 1
        self.app.project.keyframes[1].time = 20
        self.app.project.keyframes[1].x = 999
        self.app.project = Project()
        operation()
        self.controller.seek.assert_called_once()
        project, time = self.controller.seek.call_args.args
        self.assertEqual(project.to_dict(), saved)
        self.assertEqual(time, 7.09375)
        self.assertEqual(round(project.start_tick + time * project.tick_rate), 31645)
        frame = project.evaluate(time)
        self.assertEqual(tuple(frame[name] for name in
            ('x', 'y', 'z', 'pitch', 'yaw', 'roll', 'aspect_ratio')),
            (70, 80, 90, 10, 110, 12, 1.8))
        self.controller.select_paused_camera.assert_not_called()
        self.controller.seek_replay_tick.assert_not_called()

    def test_seek_camera_rejects_invalid_indices_and_active_operations(self):
        from dolly.gui import DollyApp
        self.camera_list_app()
        event = {'action': 'seek_camera', 'value': 1,
                 'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}
        for value in (-1, 2, .5, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session.dispatch(self.app, dict(event, value=value), self.bridge)
        self.app._snapshot.assert_not_called()
        self.app._submit.assert_not_called()
        for state, playing, busy in (('starting', False, False),
                                    ('recording', False, False),
                                    ('finalizing', False, False),
                                    ('completed', True, False),
                                    ('completed', False, True)):
            with self.subTest(state=state, playing=playing, busy=busy):
                self.app.video_export = Mock()
                self.app.video_export.status.return_value = {'state': state}
                self.app.playing, self.app.busy = playing, busy
                self.app._shot_edit_ready = lambda: DollyApp._shot_edit_ready(self.app)
                self.assertEqual(session.dispatch(self.app, event, self.bridge), not busy)
                self.app._snapshot.assert_not_called()
                self.app._select_key.assert_not_called()
                self.app._submit.assert_not_called()
        self.app.playing = self.app.busy = False
        self.controller.status.return_value['playing'] = True
        with self.assertRaisesRegex(ValueError, 'Pause shot playback'):
            session.dispatch(self.app, event, self.bridge)
        self.app._snapshot.assert_not_called()
        self.app._submit.assert_not_called()
        self.controller.seek.assert_not_called()
        self.controller.select_paused_camera.assert_not_called()

    def test_stale_camera_action_cannot_delete_a_different_project_camera(self):
        self.camera_list_app()
        revision = self.app.shot_history.revision
        self.app.project.name = 'New edit'
        self.app.shot_history.record(self.app.project)
        for action in ('select_camera', 'view_camera', 'seek_camera', 'delete_camera', 'undo_shot', 'redo_shot', 'camera_page'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'camera list changed'):
                session.dispatch(self.app, {'action': action, 'value': 0,
                                           'pose': (revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        self.app._delete_key.assert_not_called()
        self.app._submit.assert_not_called()

    def test_native_history_actions_use_the_shared_editor_history(self):
        self.camera_list_app()
        for action in ('undo_shot', 'redo_shot'):
            session.dispatch(self.app, {'action': action, 'value': 0,
                                       'pose': (self.app.shot_history.revision, 0, 0, 0, 0, 0, 0)}, self.bridge)
        self.app.undo_shot.assert_called_once()
        self.app.redo_shot.assert_called_once()

    def test_inline_camera_time_edit_moves_and_selects_the_camera(self):
        self.camera_list_app()
        self.app._commit_camera = Mock()
        revision = self.app.shot_history.revision
        event = {'action': 'set_camera_time', 'value': 1,
                 'pose': (revision, 1.0, 0, 0, 0, 0, 0)}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        keys, selected_time = self.app._commit_camera.call_args.args
        self.assertEqual(keys[1].time, 1.0)
        self.assertEqual(selected_time, 1.0)
        self.assertEqual(self.app.project.keyframes[1].time, 2)
        self.app._delete_key.assert_not_called()
        self.app._submit.assert_not_called()

    def test_inline_camera_time_edit_nudges_off_an_exact_duplicate(self):
        self.camera_list_app()
        self.app._commit_camera = Mock()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'set_camera_time', 'value': 1,
                                    'pose': (revision, 0.0, 0, 0, 0, 0, 0)}, self.bridge)
        keys, selected_time = self.app._commit_camera.call_args.args
        self.assertAlmostEqual(keys[1].time, 0.001)
        self.assertAlmostEqual(selected_time, 0.001)

    def test_inline_camera_bank_edit_replaces_only_that_value(self):
        self.camera_list_app()
        self.app._commit_camera = Mock()
        revision = self.app.shot_history.revision
        session.dispatch(self.app, {'action': 'set_camera_roll', 'value': 0,
                                    'pose': (revision, 45.5, 0, 0, 0, 0, 0)}, self.bridge)
        keys, selected_time = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].roll, 45.5)
        self.assertEqual(keys[1].roll, 12)
        self.assertEqual(selected_time, 0)
        self.assertIn('bank', self.app.status_text.set.call_args.args[0].lower())

    def test_inline_camera_edits_reject_bad_values_and_stale_lists(self):
        self.camera_list_app()
        self.app._commit_camera = Mock()
        revision = self.app.shot_history.revision
        for action, value in (('set_camera_time', -1), ('set_camera_time', float('nan')),
                              ('set_camera_roll', float('inf'))):
            with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                session.dispatch(self.app, {'action': action, 'value': 1,
                                            'pose': (revision, value, 0, 0, 0, 0, 0)}, self.bridge)
        self.app._commit_camera.assert_not_called()
        self.app.project.name = 'New edit'
        self.app.shot_history.record(self.app.project)
        for action in ('set_camera_time', 'set_camera_roll'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'camera list changed'):
                session.dispatch(self.app, {'action': action, 'value': 0,
                                            'pose': (revision, 1.0, 0, 0, 0, 0, 0)}, self.bridge)

    def test_follow_event_preserves_full_model_identity(self):
        player = {'handle': 0x10002, 'entity_index': 2, 'model': 0xfedcba9876543210,
                  'model_path': 'models/heroes/frank/frank.vmdl'}
        self.bridge.editor_roster.return_value = {'players': [player]}
        self.app.preview_attach = False
        event = {'action': 'start_game_follow', 'value': 200,
                 'pose': (40, 20, 0, 0x10002, 2, 0x76543210, 0xfedcba98)}
        session.dispatch(self.app, event, self.bridge)
        label, operation = self.app._submit.call_args.args[:2]
        operation()
        self.controller.start_selected_game_follow.assert_called_once()
        settings, selected = self.controller.start_selected_game_follow.call_args.args
        self.assertEqual((settings.distance, settings.shoulder, settings.height), (200, 40, 20))
        self.assertEqual(selected, player)
        self.assertIn('Game Follow', label)

    def test_follow_event_rejects_changed_model_before_worker(self):
        self.app.preview_attach = False
        self.bridge.editor_roster.return_value = {'players': [
            {'handle': 0x10002, 'entity_index': 2, 'model': 0x87654321}]}
        with self.assertRaisesRegex(ValueError, 'player list changed'):
            session.dispatch(self.app, {'action': 'start_game_follow', 'value': 200,
                                       'pose': (40, 20, 0, 0x10002, 2, 0x76543210, 0)}, self.bridge)
        self.app._submit.assert_not_called()

    def test_return_after_attach_failure_disables_only_live_preview(self):
        from copy import deepcopy
        from dolly.path import AttachKey
        self.app.preview_attach = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
            attach=AttachKey(handle=11, model="models/hero.vmdl", point="bone", bone="head"))]
        before = deepcopy(self.app.project.keyframes)
        for state in ("fault", "stopped", "probe"):
            for action in ("flight", "panel"):
                with self.subTest(state=state, action=action):
                    self.app.preview_attach = True
                    self.bridge.status.return_value = {"state": state}
                    session.dispatch(self.app, {"action": action, "value": 1}, self.bridge)
                    self.assertFalse(self.app.preview_attach)
                    self.assertEqual(self.app.project.keyframes, before)

    def test_pov_panel_preserves_game_camera(self):
        self.app.video_source = Value("Player POV")
        session.dispatch(self.app, {"action": "panel", "value": 1}, self.bridge)
        operation = self.app._submit.call_args.args[1]
        operation()
        self.controller.open_pov_panel.assert_called_once()
        self.controller.toggle_game_ui.assert_not_called()

    def test_bone_picker_cannot_apply_a_stale_or_other_players_catalog(self):
        from dolly.path import AttachKey
        from dolly.native_effects import model_token
        model = "models/heroes_staging/astro/astro.vmdl"
        key = Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                       attach=AttachKey(handle=11, entity_id=4, model=model))
        self.app.project.keyframes = [key]
        self.app._selection_index = lambda _: 0
        self.app._commit_camera = Mock()
        catalog = {"sequence": 2, "handle": 11, "entity_id": 4,
                   "model": model_token(model), "names": ["head", "hand_R"]}
        self.bridge.editor_bones.return_value = catalog
        event = {"action": "set_attach_bone", "value": 1, "pose": (2, 0, 0, 0, 0, 0, 0)}
        session.dispatch(self.app, event, self.bridge)
        keys, _ = self.app._commit_camera.call_args.args
        self.assertEqual((keys[0].attach.point, keys[0].attach.bone), ("bone", "hand_R"))
        self.assertEqual(key.attach.point, "eyes")
        for changed in ({**catalog, "sequence": 4}, {**catalog, "handle": 12},
                        {**catalog, "model": 999}):
            self.bridge.editor_bones.return_value = changed
            self.app._commit_camera.reset_mock()
            with self.assertRaises(ValueError):
                session.dispatch(self.app, event, self.bridge)
            self.app._commit_camera.assert_not_called()

    def test_selected_bone_key_is_not_published_as_weapon(self):
        from dolly.path import AttachKey
        self.app.project.keyframes = [Keyframe(0, 0, 0, 0, 0, 0, 0, source="attach",
            attach=AttachKey(handle=11, model="astro.vmdl", point="bone", bone="head"))]
        state = session._attach_state(self.app, 0, 1)
        self.assertEqual((state["point"], state["bone"]), (2, "head"))

    def test_wheel_framing_updates_only_the_selected_camera_lens(self):
        from copy import deepcopy
        keys = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(2, 7, 8, 9, 10, 11, 12)]
        self.app.project.keyframes = keys
        self.app._commit_camera = Mock()
        event = {"action": "set_framing", "value": 1, "pose": [7, 8, 9, 10, 11, 12, .9]}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        changed, selected_time = self.app._commit_camera.call_args.args
        expected = deepcopy(keys)
        expected[1].aspect_ratio = max(.5, min(4.0, round(keys[1].aspect_ratio * .9, 4)))
        self.assertEqual(changed, expected)
        self.assertEqual(selected_time, 2)
        self.assertNotEqual(keys[1].aspect_ratio, expected[1].aspect_ratio)
        self.app._submit.assert_not_called()

    def test_wheel_away_from_the_saved_camera_stays_live(self):
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(2, 7, 8, 9, 10, 11, 12)]
        self.app._commit_camera = Mock()
        event = {"action": "set_framing", "value": 1, "pose": [7, 8, 9, 10, 999, 12, .9]}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        self.app._commit_camera.assert_not_called()
        self.assertIn("Live framing", self.app.status_text.set.call_args.args[0])

    def test_wheel_uses_the_live_selection_over_a_stale_native_index(self):
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(2, 7, 8, 9, 10, 11, 12)]
        self.app._commit_camera = Mock()
        self.app._selection_index = lambda _tree: 1
        event = {"action": "set_framing", "value": 0, "pose": [7, 8, 9, 10, 11, 12, .9]}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        changed, _ = self.app._commit_camera.call_args.args
        self.assertAlmostEqual(changed[1].aspect_ratio, round(Keyframe(0, 1, 2, 3, 4, 5, 6).aspect_ratio * .9, 4), places=4)

    def test_wheel_before_capture_keeps_live_framing_without_creating_a_key(self):
        self.assertTrue(session.dispatch(self.app, {"action": "set_framing", "value": -1,
                        "pose": [0, 0, 0, 0, 0, 0, 1.2]}, self.bridge))
        self.assertFalse(self.app.project.keyframes)
        self.assertIn("Capture", self.app.status_text.set.call_args.args[0])

    def test_invalid_wheel_edit_cannot_mutate_project(self):
        for index, factor in ((0, 0), (0, -1), (0, float("nan")),
                              (-2, .9), (.5, .9), (float("nan"), .9)):
            with self.subTest(index=index, factor=factor), self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "set_framing", "value": index,
                                 "pose": [0, 0, 0, 0, 0, 0, factor]}, self.bridge)

    def test_extreme_wheel_factors_clamp_to_the_curve_bounds(self):
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(2, 7, 8, 9, 10, 11, 12)]
        for factor, expected in ((1e-9, .5), (1e9, 4.0)):
            with self.subTest(factor=factor):
                self.app._commit_camera = Mock()
                session.dispatch(self.app, {"action": "set_framing", "value": 1,
                                 "pose": [7, 8, 9, 10, 11, 12, factor]}, self.bridge)
                changed, _ = self.app._commit_camera.call_args.args
                self.assertEqual(changed[1].aspect_ratio, expected)

    def test_dof_preview_finishes_before_the_project_is_committed(self):
        self.app.project.keyframes = [Keyframe(0, 0, 0, 0, 0, 0, 0)]
        self.app._mark_dirty = Mock()
        self.app._refresh_tracks = Mock()
        self.app._refresh_fixed = Mock()
        session.dispatch(self.app, {"action": "set_dof_0", "value": 1}, self.bridge)
        self.assertNotIn("r_dof_override", self.app.project.setup_values)
        work = self.app._submit.call_args.args
        result = work[1]()
        self.controller.preview_native_effects.assert_called_once()
        work[2](result)
        self.assertEqual(self.app.project.setup_values["r_dof_override"], 1)
        self.assertEqual(self.app.project.tracks[0].name, "r_dof_override_ranges")
        self.assertEqual(len(self.app.project.tracks[0].keys), 2)
        self.app._refresh_tracks.assert_called_once_with(0)
        self.app._mark_dirty.assert_called_once()

    def test_clear_ragdolls_dispatches_on_worker(self):
        self.assertTrue(session.dispatch(self.app, {"action": "destroy_ragdolls", "value": 0}, self.bridge))
        self.controller.destroy_ragdolls.assert_not_called()
        self.app._submit.call_args.args[1]()
        self.controller.destroy_ragdolls.assert_called_once_with()

    def test_citadel_buttons_dispatch_on_worker(self):
        for action, method in (("toggle_citadel_glow", "toggle_citadel_glow"),
                               ("toggle_healthbars", "toggle_healthbars"),
                               ("near_player_opacity_fix", "near_player_opacity_fix")):
            with self.subTest(action=action):
                getattr(self.controller, method).reset_mock()
                self.app._submit.reset_mock()
                self.assertTrue(session.dispatch(self.app, {"action": action, "value": 0}, self.bridge))
                getattr(self.controller, method).assert_not_called()
                self.app._submit.call_args.args[1]()
                getattr(self.controller, method).assert_called_once_with()

    def test_citadel_dof_preview_finishes_before_the_project_is_committed(self):
        self.app.project.keyframes = [Keyframe(0, 0, 0, 0, 0, 0, 0)]
        self.app._mark_dirty = Mock()
        self.app._refresh_tracks = Mock()
        self.app._refresh_fixed = Mock()
        session.dispatch(self.app, {"action": "set_citadel_dof_sensor_size", "value": 2.5}, self.bridge)
        self.assertNotIn("r_citadel_depthoffield_sensor_size", self.app.project.setup_values)
        work = self.app._submit.call_args.args
        result = work[1]()
        self.controller.preview_native_effects.assert_called_once()
        work[2](result)
        self.assertEqual(self.app.project.setup_values["r_citadel_depthoffield_sensor_size"], 2.5)
        self.app._mark_dirty.assert_called_once()
        self.app._refresh_fixed.assert_called_once_with()

    def setUp(self):
        self.bridge = Mock()
        self.bridge.editor_status.return_value = {"events": []}
        self.controller = Mock()
        self.controller._native_bridge.return_value = self.bridge
        self.controller.status.return_value = {"connected": True, "native_editor_active": False}
        self.app = SimpleNamespace(controller=self.controller, closed=False, busy=False,
            native_editor_active=False, app_settings=AppSettings(), project=Project(),
            camera_tree=object(), _selection_index=lambda _: None, shot_time=0,
            status_text=Mock(), _disable_external_input=Mock(), _capture_view=Mock(),
            _error=Mock(), _submit=Mock(return_value=True))
        self.app.status_text.get.return_value = "Ready"
        self.app.speed = Value("1")
        self.app.rate = Value("60")

    def test_tick_step_completion_updates_shot_cursor_without_seeking_the_path(self):
        self.app.project = Project(start_tick=100, tick_rate=64,
                                   keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0)])
        self.app._set_time = Mock()
        self.controller.step_replay_ticks.return_value = {"tick": 125}
        session.dispatch(self.app, {"action": "step_replay_ticks", "value": 25}, self.bridge)
        _, run, complete = self.app._submit.call_args.args
        complete(run())
        self.controller.step_replay_ticks.assert_called_once_with(25)
        self.app._set_time.assert_called_once_with(25 / 64)
        self.app.preview_attach = True
        with self.assertRaisesRegex(ValueError, "Detach"):
            session.dispatch(self.app, {"action": "step_replay_ticks", "value": 1}, self.bridge)
        self.app.preview_attach = False
        for value in (0, 3, 1.5, True, float("nan")):
            with self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "step_replay_ticks", "value": value}, self.bridge)

    def test_shot_seek_dispatches_the_desktop_seek_at_exact_selected_time(self):
        self.app.project.keyframes = [Keyframe(0, 0, 0, 0, 0, 0, 0),
                                     Keyframe(4, 10, 20, 30, 40, 50, 60)]
        self.app._set_time, self.app._seek = Mock(), Mock()
        event = {"action": "seek_shot", "value": 1.375}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        self.app._set_time.assert_called_once_with(1.375)
        self.app._seek.assert_called_once_with()
        self.app._set_time.reset_mock(); self.app._seek.reset_mock()
        for value in (-1, 4.01, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                session.dispatch(self.app, dict(event, value=value), self.bridge)
        self.controller.status.return_value["playing"] = True
        with self.assertRaisesRegex(ValueError, "Pause shot"):
            session.dispatch(self.app, event, self.bridge)
        self.app._set_time.assert_not_called()
        self.app._seek.assert_not_called()
        self.controller.status.return_value["playing"] = False
        self.app.busy = True
        self.assertFalse(session.dispatch(self.app, event, self.bridge))
        self.app._seek.assert_not_called()

    def test_poll_during_startup_does_not_disable_the_arming_native_camera(self):
        self.app.busy = True
        session.poll(self.app)
        self.bridge.configure_editor.assert_not_called()
        self.bridge.editor_status.assert_not_called()
        self.controller.status.return_value["native_editor_active"] = True
        session.poll(self.app)
        self.assertTrue(self.app.native_editor_active)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["enabled"])
        self.app._disable_external_input.assert_called_once()

    def test_path_playback_retains_editor_then_disconnect_disables_it(self):
        self.app.native_editor_active = True
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["enabled"])
        self.assertNotIn("owner", self.bridge.configure_editor.call_args.kwargs)
        self.controller.status.return_value["connected"] = False
        session.configure(self.app)
        self.assertFalse(self.app.native_editor_active)
        self.assertFalse(self.bridge.configure_editor.call_args.kwargs["enabled"])

    def test_failed_startup_keeps_live_panel_console_and_retry_actions_working(self):
        event = {"sequence": 1, "action": "console", "value": 1}
        self.bridge.editor_status.return_value = {"enabled": True, "events": [event]}
        session.poll(self.app)
        self.assertTrue(self.app.native_editor_active)
        self.app._disable_external_input.assert_called_once()
        self.app._submit.call_args.args[1]()
        self.controller.toggle_console.assert_called_once_with(enabled=True)
        self.bridge.acknowledge_editor_event.assert_called_once_with(1)

    def test_disconnected_bridge_cannot_reactivate_native_input(self):
        self.controller.status.return_value["connected"] = False
        self.bridge.editor_status.return_value = {"enabled": True, "events": []}
        session.poll(self.app)
        self.assertFalse(self.app.native_editor_active)
        self.bridge.editor_status.assert_not_called()

    def test_poll_refreshes_the_attach_roster_for_the_picker(self):
        self.app.native_editor_active = True
        self.app._attach_roster_changed = Mock()
        roster = {"available": True, "players": [{"handle": 5, "entity_index": 3, "model": 7,
                                                  "model_path": "models/heroes/x/x.vmdl"}]}
        self.bridge.editor_roster.return_value = roster
        session.poll(self.app)
        self.bridge.editor_roster.assert_called_once_with()
        self.app._attach_roster_changed.assert_called_once_with(roster)
        self.assertEqual(self.app.attach_roster, roster)
        session.poll(self.app)
        self.bridge.editor_roster.assert_called_once_with()

    def test_poll_tolerates_an_unavailable_roster(self):
        self.app.native_editor_active = True
        self.app._attach_roster_changed = Mock()
        self.bridge.editor_roster.side_effect = NativeBridgeError("busy")
        session.poll(self.app)
        self.app._attach_roster_changed.assert_called_once_with(None)

    def test_in_game_attach_target_edit_updates_the_selected_key(self):
        from dolly.path import AttachKey  # noqa: F401  (kept for the round-trip check)
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        self.app._selection_index = lambda _: 0
        self.app.attach_roster = {"available": True, "players": [
            {"handle": 11, "entity_index": 4, "model": 1,
             "model_path": "models/heroes_staging/astro/astro.vmdl"}]}
        self.app._commit_camera = Mock()
        self.assertTrue(session.dispatch(self.app, {"action": "set_attach_target", "value": 0},
                                         self.bridge))
        keys, selected_time = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].source, "attach")
        self.assertEqual(keys[0].attach.handle, 11)
        self.assertEqual(keys[0].attach.entity_id, 4)
        self.assertEqual(selected_time, 0)

    def test_in_game_attach_offsets_smoothing_hide_and_reset(self):
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        self.app._selection_index = lambda _: 0
        self.app.project.keyframes[0].source = "attach"
        from dolly.path import AttachKey
        self.app.project.keyframes[0].attach = AttachKey(handle=11, entity_id=4,
                                                         model="models/x/x.vmdl")
        self.app._commit_camera = Mock()
        pose = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 0.0]
        self.assertTrue(session.dispatch(self.app, {"action": "set_attach_offsets", "value": 0,
                                                    "pose": pose}, self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].attach.offset, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        self.app._commit_camera.reset_mock()
        self.assertTrue(session.dispatch(self.app, {"action": "set_attach_smoothing", "value": .4},
                                         self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].attach.smoothing, 0.4)
        self.app._commit_camera.reset_mock()
        self.assertTrue(session.dispatch(self.app, {"action": "set_attach_hide", "value": 0},
                                         self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertFalse(keys[0].attach.hide_body)
        self.assertEqual(keys[0].attach.clearance_mode, "exact")
        self.app._commit_camera.reset_mock()
        self.assertTrue(session.dispatch(self.app, {"action": "set_attach_hide", "value": 2},
                                         self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertFalse(keys[0].attach.hide_body)
        self.assertEqual(keys[0].attach.clearance_mode, "auto")
        self.app._commit_camera.reset_mock()
        self.app.preview_attach = True
        self.app._attach_snap_pending = True
        self.assertTrue(session.dispatch(self.app, {"action": "attach_reset", "value": 0},
                                         self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].source, "free")
        self.assertIsNone(keys[0].attach)
        self.assertFalse(self.app.preview_attach)
        self.assertFalse(self.app._attach_snap_pending)

    def test_vertical_aim_lock_edits_only_the_current_valid_camera(self):
        from dolly.path import AttachKey
        self.app.project.keyframes = [
            Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                     attach=AttachKey(handle=11, model="models/x/x.vmdl", point="bone", bone="head",
                                      offset=(5, 6, 7, 8, 9, 10))),
            Keyframe(1, 1, 2, 3, 4, 5, 6, source="attach",
                     attach=AttachKey(handle=11, model="models/x/x.vmdl", point="weapon"))]
        self.app._selection_index = lambda _: 0
        self.app.shot_history = SimpleNamespace(revision=17)
        self.app._shot_edit_ready = Mock(return_value=True)
        self.app._commit_camera = Mock()
        event = {"action": "set_attach_vertical_aim_lock", "value": 1,
                 "pose": (17, 0, 0, 0, 0, 0, 0)}
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertTrue(keys[0].attach.lock_vertical_aim)
        self.assertFalse(keys[1].attach.lock_vertical_aim)
        self.assertEqual(keys[0].attach.offset, (5, 6, 7, 8, 9, 10))
        self.app._commit_camera.reset_mock()
        for pose in ((16, 0, 0, 0, 0, 0, 0), (17, 1, 0, 0, 0, 0, 0)):
            with self.assertRaisesRegex(ValueError, "camera changed"):
                session.dispatch(self.app, dict(event, pose=pose), self.bridge)
        self.app._shot_edit_ready.return_value = False
        self.assertTrue(session.dispatch(self.app, event, self.bridge))
        self.app._commit_camera.assert_not_called()
        self.app._shot_edit_ready.return_value = True
        self.app._attach_snap_pending = True
        with self.assertRaisesRegex(ValueError, "Snap"):
            session.dispatch(self.app, event, self.bridge)
        self.app._commit_camera.assert_not_called()

    def test_source_blend_edits_free_key_without_enabling_attachment(self):
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        self.app._selection_index = lambda _: 0
        self.app._commit_camera = Mock()
        self.assertTrue(session.dispatch(self.app, {"action": "set_source_blend", "value": .5},
                                         self.bridge))
        keys, _ = self.app._commit_camera.call_args.args
        self.assertEqual(keys[0].source_blend, .5)
        self.assertEqual(keys[0].source, "free")
        self.assertIsNone(keys[0].attach)
        for value in (-1, 11, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "set_source_blend", "value": value}, self.bridge)

    def test_attach_preview_toggle_publishes_the_flag(self):
        fields = {"scene_node": 816, "owner": 48, "player_origin": 200, "player_angles": 212,
                  "eye_offset": 2184, "eye_angles": 4536, "scene_child": 64, "scene_sibling": 72}
        self.app.native_editor_active = True
        self.app.attach_fields = fields
        self.assertTrue(session.dispatch(self.app, {"action": "attach_preview", "value": 1},
                                         self.bridge))
        self.assertTrue(self.app.preview_attach)
        self.bridge.configure_editor_attach.assert_called_once()
        self.assertTrue(self.bridge.configure_editor_attach.call_args.kwargs["preview"])

    def test_detach_rearms_free_camera_without_changing_authored_keys(self):
        from copy import deepcopy
        from dolly.path import AttachKey
        self.app.preview_attach = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
            attach=AttachKey(handle=11, model="models/hero.vmdl", point="bone", bone="head"))]
        before = deepcopy(self.app.project.keyframes)
        session.dispatch(self.app, {"action": "attach_preview", "value": 0}, self.bridge)
        self.assertFalse(self.app.preview_attach)
        self.app._submit.call_args.args[1]()
        self.app.controller.enter_native_flight.assert_called_once_with(owner="panel")
        self.assertEqual(self.app.project.keyframes, before)

    def test_attach_snap_marks_a_pending_request(self):
        from dolly.path import AttachKey
        self.app.project.keyframes = [Keyframe(
            0, 1, 2, 3, 4, 5, 6, source="attach",
            attach=AttachKey(handle=11, model="models/hero.vmdl"))]
        self.app._selection_index = lambda _: 0
        self.app.preview_attach = False
        self.assertTrue(session.dispatch(self.app, {"action": "attach_snap", "value": 0},
                                         self.bridge))
        self.assertTrue(self.app._attach_snap_pending)
        self.assertEqual(self.app._attach_snap_request, 1)
        with self.assertRaisesRegex(ValueError, "Wait for Snap"):
            session.dispatch(self.app, {"action": "attach_preview", "value": 1}, self.bridge)
        self.app._attach_snap_pending = False
        self.app.preview_attach = True
        with self.assertRaisesRegex(ValueError, "Detach"):
            session.dispatch(self.app, {"action": "attach_snap", "value": 0}, self.bridge)
        self.assertEqual(self.app._attach_snap_request, 1)

    def test_unanswered_snap_times_out_without_overwriting_the_camera(self):
        from unittest.mock import patch
        from dolly.path import AttachKey
        self.app.project.keyframes = [Keyframe(
            0, 1, 2, 3, 4, 5, 6, source="attach",
            attach=AttachKey(handle=11, model="models/hero.vmdl"))]
        self.app._selection_index = lambda _: 0
        self.app.native_editor_active = True
        self.app.preview_attach = False
        with patch("dolly.editor_dispatch.time.monotonic", return_value=10):
            session.dispatch(self.app, {"action": "attach_snap", "value": 0}, self.bridge)
        self.bridge.editor_attach_result.return_value = None
        original = self.app.project.to_dict()
        with patch("dolly.editor_session.time.monotonic", return_value=16):
            session.poll(self.app)
        self.assertFalse(self.app._attach_snap_pending)
        self.assertIsNone(self.app._attach_snap_started)
        self.assertEqual(self.app.project.to_dict(), original)
        self.app.status_text.set.assert_any_call(
            "Snap did not finish. Pause, check the player, and try again.")

    def test_snap_request_is_cancelled_when_the_selected_camera_changes(self):
        from copy import deepcopy
        from dolly.path import AttachKey
        first = Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                         attach=AttachKey(handle=11, model="models/hero.vmdl"))
        second = deepcopy(first)
        second.time = 1
        self.app.project.keyframes = [first, second]
        selection = [0]
        self.app._selection_index = lambda _: selection[0]
        self.app.preview_attach = False
        self.app.native_editor_active = True
        self.bridge.editor_attach_result.return_value = None
        session.dispatch(self.app, {"action": "attach_snap", "value": 0}, self.bridge)
        original = deepcopy(self.app.project)
        selection[0] = 1
        session.poll(self.app)
        self.assertFalse(self.app._attach_snap_pending)
        self.assertIsNone(self.app._attach_snap_context)
        self.assertEqual(self.app.project, original)
        self.app.status_text.set.assert_any_call(
            "Snap cancelled because the selected camera changed.")

    def test_poll_forwards_native_attach_results(self):
        self.app.native_editor_active = True
        self.app._attach_result_changed = Mock()
        result = {"valid": True, "sequence": 7, "offset": (1, 2, 3, 4, 5, 6)}
        self.bridge.editor_attach_result.return_value = result
        session.poll(self.app)
        self.app._attach_result_changed.assert_called_once_with(result)

    def test_busy_retains_event_then_acknowledges_the_exact_capture_once(self):
        event = {"sequence": 1, "action": "capture", "value": 0,
                 "pose": [1, 2, 3, 4, 5, 6, 16/9], "tick": 64, "paused": True}
        self.app.native_editor_active = True
        self.bridge.editor_status.return_value = {"events": [event]}
        self.app.busy = True
        session.poll(self.app)
        self.bridge.acknowledge_editor_event.assert_not_called()
        self.app.busy = False
        session.poll(self.app)
        self.app._capture_view.assert_called_once_with("start", native_snapshot=event)
        self.bridge.acknowledge_editor_event.assert_called_once_with(1)
        self.bridge.editor_status.return_value = {"events": []}
        session.poll(self.app)
        self.app._capture_view.assert_called_once()

    def test_busy_editor_saves_the_visible_shot_but_retains_camera_mutations(self):
        self.app.native_editor_active = True
        self.app.busy = True
        self.app.save = Mock()
        self.bridge.editor_status.return_value = {'events': [
            {'sequence': 1, 'action': 'save_shot', 'value': 0},
            {'sequence': 2, 'action': 'capture', 'value': 0}]}
        session.poll(self.app)
        self.app.save.assert_called_once_with()
        self.bridge.acknowledge_editor_event.assert_called_once_with(1)
        self.app._capture_view.assert_not_called()

    def test_capture_and_replace_forward_the_input_frame_lens(self):
        for action in ('capture', 'replace'):
            with self.subTest(action=action):
                self.app._capture_view.reset_mock()
                event = {'action': action, 'value': 115.6,
                         'pose': [1, 2, 3, 4, 5, 6, 16/9], 'tick': 64, 'paused': True}
                session.dispatch(self.app, event, self.bridge)
                snapshot = self.app._capture_view.call_args.kwargs['native_snapshot']
                self.assertEqual(snapshot['horizontal_fov'], 115.6)
                self.assertEqual(snapshot['pose'], event['pose'])
                self.assertNotIn('horizontal_fov', event)

    def test_console_and_game_ui_use_desired_state_not_delayed_toggle(self):
        for action, method in (("console", self.controller.toggle_console),
                               ("game_ui", self.controller.toggle_game_ui)):
            for desired in (0, 1):
                session.dispatch(self.app, {"action": action, "value": desired}, self.bridge)
                self.app._submit.call_args.args[1]()
                method.assert_called_with(enabled=bool(desired))

    def test_panel_from_game_ui_returns_camera_before_opening_panel(self):
        events = []
        self.controller.toggle_game_ui.side_effect = lambda **_: events.append("camera")
        self.bridge.configure_editor.side_effect = lambda **_: events.append("panel")
        session.dispatch(self.app, {"action": "panel", "value": 1}, self.bridge)
        self.app._submit.call_args.args[1]()
        self.assertEqual(events, ["camera", "panel"])
        self.controller.toggle_game_ui.assert_called_once_with(enabled=False)

    def test_video_buttons_use_same_handlers_as_desktop_without_camera_stop(self):
        self.app._start_video_recording = Mock()
        self.app._stop_video_recording = Mock()
        session.dispatch(self.app, {"action": "start_video", "value": 0}, self.bridge)
        session.dispatch(self.app, {"action": "stop_video", "value": 0}, self.bridge)
        self.app._start_video_recording.assert_called_once_with()
        self.app._stop_video_recording.assert_called_once_with(cancel=False)
        self.controller.stop.assert_not_called()
        self.app._submit.assert_not_called()

    def test_reshade_binding_published_without_changing_input_owner(self):
        from dolly.editor_actions import EditorBinding
        self.app.native_editor_active = True
        self.app.app_settings = self.app.app_settings.with_reshade_binding(EditorBinding("Mouse5"))
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual(config["reshade_binding"], EditorBinding("Mouse5"))
        self.assertNotIn("owner", config)

    def test_seek_uses_project_tick_rate(self):
        self.app.project.tick_rate = 128
        session.dispatch(self.app, {"action": "seek_back", "value": 0}, self.bridge)
        self.app._submit.call_args.args[1]()
        self.controller.seek_relative.assert_called_once_with(-1, tick_rate=128)

    def test_playback_options_sync_both_directions_and_apply_speed_live(self):
        self.app.native_editor_active = True
        self.app.speed.set("0.1")
        self.app.rate.set("120")
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["playback_speed"], config["playback_rate"]), (.1, 120))
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_speed", "value": .25}, self.bridge))
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_rate", "value": 30}, self.bridge))
        self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("0.25", "30"))
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["playback_speed"], config["playback_rate"]), (.25, 30))
        # The speed applies live through the controller; the rate only updates
        # the next shot because it paces the monitoring thread.
        self.assertEqual(self.app._submit.call_count, 1)
        self.app._submit.call_args.args[1]()
        self.controller.set_playback_speed.assert_called_once_with(.25)

    def test_video_export_options_sync_both_directions(self):
        self.app.native_editor_active = True
        self.app.video_fps = Value("120")
        self.app.video_bitrate = Value("40 Mbps")
        self.app.video_codec = Value("NVIDIA HEVC (NVENC)")
        self.app.video_fixed_step = Value(True)
        self.app.video_depth = Value(True)
        self.app.video_depth_exr = Value(True)
        self.app.video_export_speed = Value("0.1")
        session.configure(self.app)
        config = self.bridge.configure_editor.call_args.kwargs
        self.assertEqual((config["video_fps"], config["video_bitrate_mbps"], config["video_encoder"],
                          config["video_fixed_step"], config["video_speed"]), (120, 40, 2, True, .1))
        self.assertTrue(config["video_depth"])
        self.assertTrue(config["video_depth_exr"])
        for action, value in (("set_video_fps", 300), ("set_video_bitrate", 40),
                              ("set_video_encoder", 1), ("set_video_fixed_step", 0),
                              ("set_video_depth_exr", 0),
                              ("set_video_depth", 0),
                              ("set_video_speed", .25)):
            self.assertTrue(session.dispatch(self.app, {"action": action, "value": value}, self.bridge))
        self.assertEqual((self.app.video_fps.get(), self.app.video_bitrate.get(),
                          self.app.video_codec.get(), self.app.video_fixed_step.get(),
                          self.app.video_export_speed.get()),
                         ("300", "40 Mbps", "NVIDIA H.264 (NVENC)", False, "0.25"))
        self.assertFalse(self.app.video_depth.get())
        self.assertFalse(self.app.video_depth_exr.get())

    def test_ae_camera_toggle_syncs_both_directions(self):
        self.app.native_editor_active = True
        self.app.video_ae_camera = Value(True)
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["video_ae_camera"])
        self.assertTrue(session.dispatch(self.app, {"action": "set_video_ae_camera", "value": 0}, self.bridge))
        self.assertFalse(self.app.video_ae_camera.get())
        self.assertFalse(self.bridge.configure_editor.call_args.kwargs["video_ae_camera"])
        for invalid in (-1, 2, .5):
            with self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "set_video_ae_camera", "value": invalid}, self.bridge)

    def test_usd_camera_toggle_syncs_both_directions(self):
        self.app.native_editor_active = True
        self.app.video_usd_camera = Value(False)
        session.configure(self.app)
        self.assertFalse(self.bridge.configure_editor.call_args.kwargs["video_usd_camera"])
        self.assertTrue(session.dispatch(self.app, {"action": "set_video_usd_camera", "value": 1}, self.bridge))
        self.assertTrue(self.app.video_usd_camera.get())
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["video_usd_camera"])
        for invalid in (-1, 2, .5):
            with self.assertRaises(ValueError):
                session.dispatch(self.app, {"action": "set_video_usd_camera", "value": invalid}, self.bridge)

    def test_layer_switches_sync_independently_and_arm_fixed_step(self):
        from dolly.gui import DollyApp
        self.app.native_editor_active = True
        self.app.video_fixed_step = Value(False)
        for layer in ("world", "players", "effects"):
            setattr(self.app, "video_layer_" + layer, Value(False))
        self.app._layer_toggled = lambda: DollyApp._layer_toggled(self.app)
        for layer in ("world", "players", "effects"):
            action = "set_video_layer_" + layer
            session.dispatch(self.app, {"action": action, "value": 1}, self.bridge)
            self.assertTrue(self.app.video_fixed_step.get())
            config = self.bridge.configure_editor.call_args.kwargs
            for other in ("world", "players", "effects"):
                self.assertEqual(config["video_layer_" + other], other == layer)
            for invalid in (-1, 2, .5, "on", float("nan")):
                with self.assertRaises(ValueError):
                    session.dispatch(self.app, {"action": action, "value": invalid}, self.bridge)
                self.assertTrue(getattr(self.app, "video_layer_" + layer).get())
            session.dispatch(self.app, {"action": action, "value": 0}, self.bridge)
            self.assertFalse(self.bridge.configure_editor.call_args.kwargs["video_layer_" + layer])
            self.assertTrue(self.app.video_fixed_step.get())
        self.app.video_layer_players.set(True)
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor.call_args.kwargs["video_layer_players"])
        self.app._submit.assert_not_called()

    def test_in_game_depth_arms_fixed_step_pairing(self):
        self.app.video_fixed_step = Value(False)
        self.app.video_depth = Value(False)
        self.app.video_depth_exr = Value(False)
        self.assertTrue(session.dispatch(self.app, {"action": "set_video_depth", "value": 1}, self.bridge))
        self.assertTrue(self.app.video_depth.get())
        self.assertTrue(self.app.video_fixed_step.get())
        self.assertTrue(session.dispatch(self.app, {"action": "set_video_depth_exr", "value": 1}, self.bridge))
        self.assertTrue(self.app.video_depth_exr.get())
        self.assertTrue(session.dispatch(self.app, {"action": "set_video_depth", "value": 0}, self.bridge))
        self.assertFalse(self.app.video_depth_exr.get())

    def test_invalid_in_game_video_options_do_not_change_desktop_state(self):
        self.app.video_fps = Value("60")
        self.app.video_bitrate = Value("20 Mbps")
        self.app.video_codec = Value("Auto (hardware when available)")
        self.app.video_fixed_step = Value(False)
        self.app.video_depth = Value(False)
        self.app.video_export_speed = Value("1")
        for action, invalid in (("set_video_fps", (0, 24, 200, 60.5, True)),
                                ("set_video_bitrate", (0, 15, 50)),
                                ("set_video_encoder", (-1, 11)),
                                ("set_video_depth", (2, -1, .5, "on")),
                                ("set_video_depth_exr", (2, -1, .5, "on")),
                                ("set_video_speed", (0, .01, 4.1, float("nan"), True))):
            for value in invalid:
                with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                    session.dispatch(self.app, {"action": action, "value": value}, self.bridge)
        self.bridge.configure_editor.assert_not_called()

    def test_unfinished_speed_edit_does_not_block_editor_configuration(self):
        self.app.native_editor_active = True
        self.app.speed.set(".5")
        session.configure(self.app)
        for text in ("", "-", "nan", "99"):
            self.app.speed.set(text)
            self.app.status_text.get.return_value = "Editing " + text
            session.configure(self.app)
            config = self.bridge.configure_editor.call_args.kwargs
            self.assertEqual(config["playback_speed"], .5)
            self.assertEqual(config["message"], "Editing " + text)
            self.assertEqual(self.app.speed.get(), text)

    def test_invalid_in_game_playback_options_do_not_change_desktop_state(self):
        for action, invalid in (("set_playback_speed", (0, .01, 4.1, float("nan"), True)),
                                ("set_playback_rate", (0, 60.1, 90, float("inf"), True))):
            for value in invalid:
                with self.subTest(action=action, value=value), self.assertRaises(ValueError):
                    session.dispatch(self.app, {"action": action, "value": value}, self.bridge)
                self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("1", "60"))
        self.bridge.configure_editor.assert_not_called()

    def test_busy_blocks_playback_events_and_playing_allows_live_speed(self):
        for action, value in (("set_playback_speed", .1), ("set_playback_rate", 120)):
            self.app.busy = True
            self.assertFalse(session.dispatch(self.app, {"action": action, "value": value}, self.bridge))
            self.app.busy = False
        self.controller.status.return_value["playing"] = True
        # Speed applies live while the replay plays; the rate stays fixed
        # because it paces the monitoring thread for the running shot.
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_speed", "value": .1}, self.bridge))
        self.assertTrue(session.dispatch(self.app, {"action": "set_playback_rate", "value": 120}, self.bridge))
        self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("0.1", "60"))
        self.controller.status.return_value["playing"] = False
        self.assertEqual(self.app._submit.call_count, 1)

    def test_in_game_options_are_acknowledged_once_after_application(self):
        self.app.native_editor_active = True
        self.bridge.editor_status.return_value = {"events": [
            {"sequence": 1, "action": "set_playback_speed", "value": .1},
            {"sequence": 2, "action": "set_playback_rate", "value": 120}]}
        session.poll(self.app)
        self.assertEqual((self.app.speed.get(), self.app.rate.get()), ("0.1", "120"))
        self.assertEqual([call.args[0] for call in self.bridge.acknowledge_editor_event.call_args_list], [1, 2])

    def test_path_guides_publish_only_when_camera_shape_or_selection_changes(self):
        from dolly.editor_publication import visualization_session
        self.app.native_editor_active = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(1, 7, 8, 9, 10, 11, 12)]
        session.configure(self.app)
        shot_session = visualization_session(self.app)
        self.assertGreater(shot_session, 0)
        self.bridge.publish_visualization.assert_called_once_with(self.app.project, enabled=True, selected_camera=0, revision=0, session_id=shot_session)
        self.app.shot_time = .5
        self.app.speed.set(".1")
        session.configure(self.app)
        self.bridge.publish_visualization.assert_called_once()
        self.assertEqual(visualization_session(self.app), shot_session)
        # Mutable camera objects must not mutate the cached comparison too.
        self.app.project.keyframes[0].z += 10
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 2)
        self.app._selection_index = lambda _: 1
        session.configure(self.app)
        self.bridge.publish_visualization.assert_called_with(self.app.project, enabled=True, selected_camera=1, revision=0, session_id=shot_session)
        self.app.project.rotation_mode = "unwrapped"
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 4)

    def test_optional_viewer_failure_does_not_interrupt_editor_or_retry_every_poll(self):
        self.app.native_editor_active = True
        self.bridge.publish_visualization.return_value = False
        self.bridge.visualization_diagnostics.return_value = {"error": "viewer unavailable"}
        with self.assertLogs(session.LOG, level="WARNING") as logs:
            session.configure(self.app)
            self.app.status_text.get.return_value = "Camera ready"
            session.configure(self.app)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("viewer unavailable", logs.output[0])
        self.assertEqual(self.bridge.configure_editor.call_args.kwargs["message"], "Camera ready")
        self.bridge.publish_visualization.assert_called_once()
        self.app._error.assert_not_called()

    def test_clear_and_new_bridge_refresh_guides_without_changing_input_owner(self):
        from dolly.editor_publication import visualization_session
        self.app.native_editor_active = True
        self.app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        session.configure(self.app)
        self.app.project.keyframes.clear()
        session.configure(self.app)
        self.assertEqual(self.bridge.publish_visualization.call_count, 2)
        replacement = Mock()
        self.controller._native_bridge.return_value = replacement
        session.configure(self.app)
        shot_session = visualization_session(self.app)
        replacement.publish_visualization.assert_called_once_with(self.app.project, enabled=True, selected_camera=0, revision=0, session_id=shot_session)
        self.controller.status.return_value["connected"] = False
        session.configure(self.app)
        replacement.publish_visualization.assert_called_with(self.app.project, enabled=False, selected_camera=0, revision=0, session_id=shot_session)
        self.assertNotIn("owner", replacement.configure_editor.call_args.kwargs)

    def test_attach_fields_are_published_once_per_bridge(self):
        self.app.native_editor_active = True
        self.app.attach_fields = {"scene_node": 816, "owner": 48, "player_origin": 200,
                                  "player_angles": 212, "eye_offset": 2184, "eye_angles": 4536,
                                  "scene_child": 64, "scene_sibling": 72}
        session.configure(self.app)
        self.bridge.configure_editor_attach.assert_called_once_with(
            self.app.attach_fields, None, preview=False, snap_request=0)
        session.configure(self.app)
        self.assertEqual(self.bridge.configure_editor_attach.call_count, 1)

    def test_attach_fields_are_not_published_before_the_schema_query(self):
        self.app.native_editor_active = True
        self.app.attach_fields = None
        session.configure(self.app)
        self.bridge.configure_editor_attach.assert_not_called()

    def test_picker_roster_refresh_does_not_replace_active_request(self):
        from copy import deepcopy
        from dolly.path import AttachKey
        self.app.native_editor_active = True
        self.app.attach_fields = {"scene_node": 816}
        key = Keyframe(0, 1, 2, 3, 4, 5, 6, source="attach",
                       attach=AttachKey(handle=11, entity_id=4, model="astro.vmdl"))
        self.app.project.keyframes = [key]
        self.app._selection_index = lambda _: 0
        self.app._bone_picker_context = dict(index=0, key=deepcopy(key), bridge=self.bridge)
        self.app.attach_roster = None
        session.configure(self.app)
        self.assertTrue(self.bridge.configure_editor_attach.call_args.kwargs["picker"])
        self.app.attach_roster = {"players": [{"handle": 11, "model_path": "astro.vmdl"}]}
        session.configure(self.app)
        self.assertEqual(self.bridge.configure_editor_attach.call_count, 1)
        self.app.project.keyframes[0].attach.handle = 12
        session.configure(self.app)
        self.assertIsNone(self.app._bone_picker_context)
        self.assertNotIn("picker", self.bridge.configure_editor_attach.call_args.kwargs)

    def test_bridge_failure_during_recovery_does_not_hide_original_error(self):
        self.controller.toggle_console.side_effect = RuntimeError("console command failed")
        self.bridge.editor_status.side_effect = RuntimeError("bridge disconnected")
        session.dispatch(self.app, {"action": "console", "value": 1}, self.bridge)
        with self.assertLogs(session.LOG, level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "console command failed"):
                self.app._submit.call_args.args[1]()


if __name__ == "__main__":
    unittest.main()
