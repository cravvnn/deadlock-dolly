import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.gui import DollyApp, _number
from dolly.path import AttachKey, CvarTrack, Keyframe, Project, TrackKey
from dolly.shot_history import ShotHistory


def shot():
    return Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6),
        Keyframe(2, 7, 8, 9, 10, 11, 12, source='attach', lens_scale=.95,
                 attach=AttachKey(handle=3, model='hero.vmdl', point='bone', bone='head'))],
        tracks=[CvarTrack('r_dof_override', [TrackKey(0, 1)])], setup_values={'r_drawviewmodel': 1})


class ShotHistoryTests(unittest.TestCase):
    def test_native_slider_stream_undoes_once_and_each_update_invalidates_old_actions(self):
        original = shot()
        history = ShotHistory(original)
        changed = copy.deepcopy(original)
        revision = history.revision
        with patch('dolly.shot_history.monotonic', side_effect=[10, 10.1, 10.2]):
            for value in (10, 20, 30):
                changed.keyframes[0].roll = value
                history.record(changed, 0, merge_key=('roll', 0))
                self.assertGreater(history.revision, revision)
                revision = history.revision
        self.assertEqual(history.move().project, original)
        self.assertFalse(history.can_undo)
        self.assertEqual(history.move(True).project.keyframes[0].roll, 30)

    def test_slider_pause_another_field_and_save_each_start_a_new_undo_step(self):
        for boundary in ('pause', 'field', 'save', 'undo'):
            with self.subTest(boundary=boundary):
                history = ShotHistory(shot())
                changed = shot()
                with patch('dolly.shot_history.monotonic', side_effect=[10, 11 if boundary == 'pause' else 10.1]):
                    changed.keyframes[0].roll = 20
                    history.record(changed, 0, merge_key='roll')
                    previous = copy.deepcopy(changed)
                    if boundary == 'save':
                        history.mark_saved(changed)
                    elif boundary == 'undo':
                        history.move()
                        history.move(True)
                    changed.keyframes[0].roll = 40
                    history.record(changed, 0, merge_key='aspect' if boundary == 'field' else 'roll')
                self.assertEqual(history.move().project, previous)
                self.assertEqual(history.move().project, shot())

    def test_delete_undo_redo_restores_full_camera_and_keeps_effects(self):
        original = shot()
        history = ShotHistory(original)
        history.select(2)
        deleted = copy.deepcopy(original)
        deleted.keyframes.pop()
        history.record(deleted, 0)
        restored = history.move()
        self.assertEqual(restored.project, original)
        self.assertEqual(restored.selected_time, 2)
        restored.project.keyframes[1].attach.bone = 'changed'
        self.assertEqual(history.move(True).project, deleted)
        self.assertEqual(history.move().project, original)

    def test_noop_preserves_redo_new_edit_discards_it(self):
        original = shot()
        history = ShotHistory(original)
        changed = copy.deepcopy(original)
        changed.name = 'Renamed'
        history.record(changed)
        history.move()
        self.assertFalse(history.record(original))
        self.assertTrue(history.can_redo)
        different = copy.deepcopy(original)
        different.setup_values['r_drawviewmodel'] = 0
        history.record(different)
        self.assertFalse(history.can_redo)

    def test_save_checkpoint_does_not_clear_history_and_dirty_tracks_undo(self):
        original = shot()
        history = ShotHistory(original)
        changed = copy.deepcopy(original)
        changed.keyframes[0].roll = 45
        history.record(changed)
        history.mark_saved(changed)
        self.assertFalse(history.is_dirty(changed))
        self.assertTrue(history.is_dirty(history.move().project))
        self.assertFalse(history.is_dirty(history.move(True).project))

    def test_new_project_clears_both_directions_and_invalidates_old_revision(self):
        history = ShotHistory(shot())
        history.record(Project(keyframes=[]))
        revision = history.revision
        history.reset(Project(name='New', keyframes=[]))
        self.assertGreater(history.revision, revision)
        self.assertFalse(history.can_undo or history.can_redo)

    def test_entry_and_byte_budgets_evict_oldest_states(self):
        for options in ({'max_entries': 3}, {'max_bytes': 1}):
            history = ShotHistory(Project(keyframes=[]), **options)
            for index in range(10):
                history.record(Project(name=str(index), keyframes=[]))
            self.assertLessEqual(len(history._states), 3)
            self.assertEqual(history._states[history._index].project.name, '9')
            if options.get('max_bytes') == 1:
                self.assertFalse(history.can_undo)


class HistoryEditorTests(unittest.TestCase):
    def test_busy_save_opens_picker_from_desktop_and_native_action(self):
        from dolly import editor_dispatch
        with tempfile.TemporaryDirectory() as directory:
            for native in (False, True):
                with self.subTest(native=native):
                    app = self.app()
                    app.busy = True
                    app.file_path = None
                    app.root = Mock()
                    app._error = Mock()
                    path = Path(directory) / ('native.json' if native else 'desktop.json')
                    before = app.project.to_dict()
                    with patch('dolly.gui.filedialog.asksaveasfilename', return_value=str(path)) as picker:
                        if native:
                            editor_dispatch.dispatch(app, {'action': 'save_shot'}, Mock(), publish=Mock())
                        else:
                            self.assertTrue(app.save())
                    picker.assert_called_once()
                    app._error.assert_not_called()
                    self.assertEqual(Project.load(path).to_dict(), before)
                    self.assertEqual(app.file_path, path)
                    self.assertFalse(app.dirty)

    def test_save_picker_keeps_later_edits_dirty_and_blocks_reentrant_save(self):
        app = self.app()
        app.file_path = None
        app.root = Mock()
        app._error = Mock()
        before = app.project.to_dict()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shot.json'
            def choose(**_kwargs):
                self.assertFalse(app.save())
                self.assertFalse(app._allow_discard())
                app.project.name = 'Edited while choosing a file'
                app.shot_history.record(app.project)
                return str(path)
            with patch('dolly.gui.filedialog.asksaveasfilename', side_effect=choose) as picker:
                self.assertTrue(app.save())
            picker.assert_called_once()
            self.assertEqual(Project.load(path).to_dict(), before)
            self.assertTrue(app.dirty)
            self.assertFalse(app._saving_shot)
            # Saving again updates the existing file, with no new picker.
            with patch('dolly.gui.filedialog.asksaveasfilename') as picker:
                self.assertTrue(app.save())
            picker.assert_not_called()
            self.assertEqual(Project.load(path).to_dict(), app.project.to_dict())
            self.assertFalse(app.dirty)

    def test_cancel_and_failed_save_keep_unsaved_shot_and_allow_retry(self):
        app = self.app()
        app.file_path = None
        app.root = Mock()
        app._error = Mock()
        app.dirty = True
        with patch('dolly.gui.filedialog.asksaveasfilename', return_value=''):
            self.assertFalse(app.save())
        self.assertTrue(app.dirty)
        self.assertIsNone(app.file_path)
        self.assertFalse(app._saving_shot)
        with patch('dolly.gui.filedialog.asksaveasfilename', return_value='shot.json'), \
                patch.object(Project, 'save', side_effect=OSError('read only')):
            self.assertFalse(app.save())
        app._error.assert_called_once()
        self.assertTrue(app.dirty)
        self.assertIsNone(app.file_path)
        self.assertFalse(app._saving_shot)

    def test_delete_undo_save_reopen_preserves_lens_attachment_and_effects(self):
        app = self.app()
        # A valid mixed free/bone shot exercises data lost by pose-only undo.
        app.project.tracks[0].interpolation = 'step'
        app.shot_history.reset(app.project)
        original = copy.deepcopy(app.project)
        app._delete_key()
        app.undo_shot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mixed-shot.json'
            app.project.save(path)
            app.shot_history.mark_saved(app.project)
            restored = Project.load(path)
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertTrue(app.shot_history.can_redo)
        app.redo_shot()
        self.assertEqual(len(app.project.keyframes), 1)
        self.assertEqual(app.project.tracks, original.tracks)
        self.assertTrue(app.shot_history.is_dirty(app.project))
        app.undo_shot()
        self.assertEqual(app.project.to_dict(), restored.to_dict())
        self.assertFalse(app.shot_history.is_dirty(app.project))

    def test_desktop_stop_keeps_selected_playback_speed(self):
        app = self.app()
        app._submit = Mock(return_value=True)
        self.assertTrue(app._session_operation('Stopping', app.controller.stop))
        app._submit.call_args.args[1]()
        app.controller.stop.assert_called_once_with(preserve_speed=True)

    def app(self):
        app = DollyApp.__new__(DollyApp)
        app.project = shot()
        app.shot_history = ShotHistory(app.project)
        app.busy = app.playing = app.preview_attach = False
        app.dirty = False
        app.status_text = Mock()
        app.controller = Mock()
        app.controller._native_bridge.return_value = None
        app.video_export = Mock()
        app.video_export.status.return_value = {'state': 'idle'}
        app.camera_tree = Mock()
        app._selection_index = Mock(return_value=1)
        app._title = Mock()
        app._refresh_keys = Mock()
        app._refresh_project = Mock()
        app._set_time = Mock()
        app._sync_options = Mock()
        app._guard = lambda _label, operation: operation()
        return app

    def test_delete_uses_shared_history_and_selects_nearest_remaining_camera(self):
        app = self.app()
        original = copy.deepcopy(app.project)
        app.shot_history.select(2)
        app._delete_key()
        self.assertEqual(len(app.project.keyframes), 1)
        app._refresh_keys.assert_called_with(0)
        app.undo_shot()
        self.assertEqual(app.project, original)
        app._refresh_keys.assert_called_with(2)
        self.assertFalse(app.dirty)
        app.redo_shot()
        self.assertEqual(len(app.project.keyframes), 1)
        self.assertTrue(app.dirty)
        app._refresh_keys.assert_called_with(0)

    def test_last_camera_delete_undo_and_redo_keep_the_empty_shot_editable(self):
        app = self.app()
        app.project.keyframes = app.project.keyframes[:1]
        app.shot_history.reset(app.project)
        app.shot_history.select(0)
        app._selection_index.return_value = 0
        app._delete_key()
        self.assertEqual(app.project.keyframes, [])
        app._set_time.assert_called_with(0)
        app.undo_shot()
        self.assertEqual(len(app.project.keyframes), 1)
        app.redo_shot()
        self.assertEqual(app.project.keyframes, [])
        app._refresh_keys.assert_called_with(None)

    def test_failed_delete_does_not_report_success_or_create_history(self):
        app = self.app()
        original = copy.deepcopy(app.project)
        app._commit_project = Mock(side_effect=ValueError('invalid camera'))
        app._error = Mock()
        app._guard = DollyApp._guard.__get__(app)
        app._delete_key()
        self.assertEqual(app.project, original)
        self.assertFalse(app.shot_history.can_undo)
        app._error.assert_called_once()
        app.status_text.set.assert_not_called()

    def test_deleting_the_leading_camera_rebases_and_refreshes_the_anchor(self):
        app = self.app()
        app.start_tick = Mock()
        app._refresh_tracks = Mock()
        app._selection_index.return_value = 0
        original = copy.deepcopy(app.project)
        app.shot_history.reset(original)
        app._delete_key()
        self.assertEqual([k.time for k in app.project.keyframes], [0])
        self.assertEqual(app.project.start_tick, round(2 * original.tick_rate))
        self.assertEqual(app.project.tracks[0].keys[0].time, 0)
        app.start_tick.set.assert_called_once_with(_number(app.project.start_tick))
        app._refresh_tracks.assert_called_once()
        app.undo_shot()
        self.assertEqual(app.project, original)

    def test_history_cannot_mutate_active_playback_recording_or_preview(self):
        for attr in ('busy', 'playing', 'preview_attach', '_bone_picker_context', '_active_layer_take'):
            app = self.app()
            setattr(app, attr, True)
            original = copy.deepcopy(app.project)
            app._delete_key()
            app.undo_shot()
            self.assertEqual(app.project, original)
            self.assertFalse(app.shot_history.can_undo)
        app = self.app()
        app.video_export.status.return_value = {'state': 'recording'}
        self.assertFalse(app._shot_edit_ready())

    def test_text_input_shortcuts_do_not_undo_the_shot(self):
        app = self.app()
        app.undo_shot = Mock()
        widget = Mock()
        widget.winfo_class.return_value = 'TEntry'
        self.assertIsNone(app._history_shortcut(SimpleNamespace(widget=widget)))
        app.undo_shot.assert_not_called()
        widget.winfo_class.return_value = 'Treeview'
        self.assertEqual(app._history_shortcut(SimpleNamespace(widget=widget)), 'break')
        app.undo_shot.assert_called_once()

    def test_rounded_option_fields_do_not_turn_undo_into_a_new_edit(self):
        from dolly.gui import _number
        app = self.app()
        app.project.tick_rate = 60.123456789012
        app.project.standard_aspect = 1.723456789012
        app.project.confetti_spawn_height = 123.456789012
        app.project.particles_intensity = .723456789012
        original = copy.deepcopy(app.project)
        app.shot_history.reset(original)
        app.project.keyframes.pop()
        app.shot_history.record(app.project, 0)
        app._sync_options = DollyApp._sync_options.__get__(app)
        app._draw_path = app._refresh_curve = Mock()
        for field, value in (
                ('start_tick', _number(original.start_tick)),
                ('tick_rate', _number(original.tick_rate)),
                ('standard_aspect', _number(original.standard_aspect)),
                ('confetti_spawn_height', _number(original.confetti_spawn_height)),
                ('particles_intensity', _number(original.particles_intensity)),
                ('interpolation', original.interpolation),
                ('rotation', original.rotation_mode),
                ('lens_interpolation', original.lens_interpolation)):
            setattr(app, field, Mock(get=Mock(return_value=value)))
        app.undo_shot()
        self.assertEqual(app.project, original)
        self.assertTrue(app.shot_history.can_redo)
        app.redo_shot()
        self.assertEqual(len(app.project.keyframes), 1)
        self.assertEqual(app.project.tick_rate, original.tick_rate)
        self.assertEqual(app.project.standard_aspect, original.standard_aspect)
        self.assertEqual(app.project.confetti_spawn_height, original.confetti_spawn_height)
        self.assertEqual(app.project.particles_intensity, original.particles_intensity)

    def test_preset_label_keeps_nearly_matching_authored_aspect_exact(self):
        from dolly.gui import ASPECT_PRESETS, _number
        app = self.app()
        label, value = next(iter(ASPECT_PRESETS.items()))
        app.project.standard_aspect = value + 1e-9
        original = copy.deepcopy(app.project)
        app.shot_history.reset(original)
        app._draw_path = app._refresh_curve = Mock()
        for field, text in (('start_tick', _number(original.start_tick)),
                            ('tick_rate', _number(original.tick_rate)),
                            ('standard_aspect', label),
                            ('interpolation', original.interpolation),
                            ('rotation', original.rotation_mode),
                            ('lens_interpolation', original.lens_interpolation)):
            setattr(app, field, Mock(get=Mock(return_value=text)))
        DollyApp._sync_options(app)
        self.assertEqual(app.project, original)
        self.assertFalse(app.shot_history.can_undo)
