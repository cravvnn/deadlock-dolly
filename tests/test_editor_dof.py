import unittest

from dolly.editor_dof import DEFAULT_RANGES, RANGE_NAME, edited_project, values_at
from dolly.editor_wire import DOF_CONFIG, DOF_OFFSET, EXTRA_ACTIONS, pack_dof
from dolly.path import CvarTrack, Keyframe, Project, TrackKey


class EditorDofTests(unittest.TestCase):
    def test_console_citadel_suppresses_runtime_ranges_without_mutating_authored_frame(self):
        from copy import deepcopy
        from dolly.controller import frame_commands
        from dolly.editor_dof import edited_citadel_project
        project = edited_project(Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6)]), 0, 0, 1)
        project = edited_citadel_project(project, 0, 0, 1)
        frame = project.evaluate(0)
        original = deepcopy(frame)
        self.assertIn('r_dof_override_ranges 0 0 0 0', frame_commands(frame))
        self.assertEqual(frame, original)

    def test_switching_modes_keeps_range_animation_and_seeds_usable_citadel_aperture(self):
        from copy import deepcopy
        from dolly.editor_dof import edited_citadel_project, citadel_values_at, CITADEL_APERTURE
        project = edited_project(Project(), 0, 0, 1)
        authored = deepcopy(project.tracks)
        citadel = edited_citadel_project(project, 2, 0, 1)
        self.assertEqual(citadel.setup_values[CITADEL_APERTURE], .5)
        self.assertEqual(citadel.setup_values['r_dof_override'], 0)
        self.assertTrue(citadel_values_at(citadel, 2)[0])
        self.assertEqual(citadel.tracks, authored)
        native = edited_project(citadel, 2, 0, 1)
        self.assertFalse(citadel_values_at(native, 2)[0])
        self.assertEqual(native.tracks, authored)
        self.assertEqual(values_at(native, 2)[:2], (1, 1))

    def test_citadel_preserves_explicit_aperture_and_other_mode_master_switch(self):
        from dolly.editor_dof import edited_citadel_project, CITADEL_APERTURE
        track = CvarTrack(CITADEL_APERTURE, [TrackKey(0, 0), TrackKey(3, .8)])
        project = Project(tracks=[track])
        citadel = edited_citadel_project(project, 0, 0, 1)
        self.assertEqual(citadel.tracks, project.tracks)
        self.assertNotIn(CITADEL_APERTURE, citadel.setup_values)
        native_off = edited_project(citadel, 0, 0, 0)
        self.assertEqual(native_off.setup_values['r_depth_of_field'], 1)
        native = edited_project(citadel, 0, 0, 1)
        citadel_off = edited_citadel_project(native, 0, 0, 0)
        self.assertEqual(citadel_off.setup_values['r_depth_of_field'], 1)

    def test_enable_initializes_the_same_track_as_desktop_range_dof(self):
        from copy import deepcopy
        from unittest.mock import Mock
        from dolly.gui import DollyApp
        from dolly.native_effects import compile_shot
        project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6), Keyframe(3, 7, 8, 9, 1, 2, 3)])
        app = DollyApp.__new__(DollyApp)
        app.project = deepcopy(project)
        app.root = Mock()
        app._guard = lambda _title, action: action()
        app._mark_dirty = app._refresh_tracks = app._refresh_fixed = app.status_text = Mock()
        app._range_dof_preset()
        enabled = edited_project(project, 1, 0, 1)
        self.assertEqual(enabled.to_dict(), app.project.to_dict())
        self.assertEqual(compile_shot(enabled), compile_shot(app.project))
        self.assertEqual(values_at(enabled, 1)[:6], (1, 1, *DEFAULT_RANGES))
        self.assertFalse(project.tracks or project.setup_values)

    def test_enable_repairs_empty_track_and_keeps_restore_metadata(self):
        project = Project(tracks=[CvarTrack(RANGE_NAME, interpolation="linear", restore_value=(1, 2, 3, 4))])
        enabled = edited_project(project, 0, 0, 1)
        self.assertEqual(enabled.tracks[0].keys, [TrackKey(0, DEFAULT_RANGES), TrackKey(5, DEFAULT_RANGES)])
        self.assertEqual(enabled.tracks[0].interpolation, "linear")
        self.assertEqual(enabled.tracks[0].restore_value, (1, 2, 3, 4))

    def test_enable_retains_prior_fixed_range_edits(self):
        ranges = (-160, 0, 180, 1490)
        enabled = edited_project(Project(setup_values={RANGE_NAME: ranges}), 0, 0, 1)
        self.assertEqual(enabled.tracks[0].keys[0].value, ranges)

    def test_off_on_preserves_focus_pull_and_changes_override_switch_tracks(self):
        project = Project(tracks=[
            CvarTrack(RANGE_NAME, [TrackKey(0, (-100, 0, 180, 1000)), TrackKey(4, (-200, 10, 600, 2000))], "smooth"),
            CvarTrack("r_depth_of_field", [TrackKey(0, 1)], "step"),
            CvarTrack("r_dof_override", [TrackKey(0, 1)], "step")])
        disabled = edited_project(project, 2, 0, 0)
        self.assertEqual(values_at(disabled, 2)[:2], (0, 0))
        self.assertEqual(disabled.tracks[0], project.tracks[0])
        enabled = edited_project(disabled, 2, 0, 1)
        self.assertEqual(values_at(enabled, 2)[:2], (1, 1))
        self.assertEqual(enabled.tracks[0], project.tracks[0])
        self.assertEqual(values_at(enabled, 2)[2:6], (-150, 5, 390, 1500))

    def test_enable_at_four_zero_key_seeds_only_that_playhead(self):
        project = Project(tracks=[CvarTrack(RANGE_NAME, [TrackKey(0, (0, 0, 0, 0)), TrackKey(4, (-50, 0, 250, 900))])])
        enabled = edited_project(project, 0, 0, 1)
        self.assertEqual(enabled.tracks[0].keys[0].value, DEFAULT_RANGES)
        self.assertEqual(enabled.tracks[0].keys[1], project.tracks[0].keys[1])

    def test_unkeyed_defaults_do_not_create_camera_or_effects(self):
        project = Project()
        self.assertEqual(values_at(project, 0), (1, 0, 0, 0, 0, 0, -100, 0, 180, 2000, .5))
        self.assertFalse(project.keyframes or project.setup_values)

    def test_vector_key_edit_preserves_other_components_curve_and_restore(self):
        track = CvarTrack("r_dof_override_ranges",
                          [TrackKey(0, (-100, 0, 100, 1000)), TrackKey(2, (-200, 20, 300, 2000))],
                          restore_value=(0, 0, 0, 0))
        project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6)], tracks=[track])
        original = project.to_dict()
        changed = edited_project(project, 1, 4, 750)
        self.assertEqual(changed.tracks[0].keys[1].value, (-150, 10, 750, 1500))
        self.assertEqual(changed.tracks[0].restore_value, track.restore_value)
        self.assertEqual(changed.tracks[0].interpolation, "linear")
        self.assertEqual(values_at(changed, 1)[2:6], (-150, 10, 750, 1500))
        self.assertEqual(project.to_dict(), original)
        replaced = edited_project(changed, 1, 4, 800)
        self.assertEqual(len(replaced.tracks[0].keys), 3)
        self.assertEqual(replaced.tracks[0].keys[1].value[2], 800)

    def test_fixed_edit_keeps_other_setup_values_and_empty_restore_track(self):
        project = Project(setup_values={"r_dof_override_ranges": (-1, 2, 3, 4)},
                          tracks=[CvarTrack("r_dof_override_ranges", restore_value=(0, 0, 0, 0))])
        changed = edited_project(project, 0, 2, -50)
        self.assertEqual(changed.setup_values["r_dof_override_ranges"], (-50, 2, 3, 4))
        self.assertEqual(changed.tracks, project.tracks)

    def test_invalid_values_and_switches_are_rejected(self):
        for control, value in ((0, .5), (1, 2), (4, float("nan")), (6, 1e39), (11, 0)):
            with self.subTest(control=control, value=value), self.assertRaises(ValueError):
                edited_project(Project(), 0, control, value)

    def test_optional_wire_block_and_stable_action_ids(self):
        values = values_at(Project(), 0)
        packet = pack_dof(2, True, values)
        self.assertEqual(len(packet), 112)
        self.assertLessEqual(DOF_OFFSET + len(packet), 2 * 1024 * 1024 + 4096)
        self.assertEqual(DOF_CONFIG.unpack(packet), (b"DLYDOF01", 2, 1, 1, 0, *values))
        self.assertEqual(EXTRA_ACTIONS.index("set_framing") + 26, 40)
        self.assertEqual(EXTRA_ACTIONS.index("set_dof_10") + 26, 51)

    def test_citadel_edits_author_both_switches_and_value_controls(self):
        from dolly.editor_dof import CITADEL_ACTIONS, citadel_values_at, edited_citadel_project
        project = Project(keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6)])
        self.assertEqual(citadel_values_at(project, 0), (False, 1.0, 200.0))
        enabled = edited_citadel_project(project, 0, 0, True)
        self.assertEqual(enabled.setup_values["r_citadel_depthoffield_enable"], 1.0)
        self.assertEqual(enabled.setup_values["r_depth_of_field"], 1.0)
        sensor = edited_citadel_project(enabled, 0, 1, 2.5)
        self.assertEqual(sensor.setup_values["r_citadel_depthoffield_sensor_size"], 2.5)
        focus = edited_citadel_project(sensor, 0, 2, 750)
        self.assertEqual(focus.setup_values["r_citadel_depthoffield_focus_distance"], 750.0)
        self.assertEqual(citadel_values_at(focus, 0), (True, 2.5, 750.0))
        disabled = edited_citadel_project(focus, 0, 0, False)
        self.assertEqual(citadel_values_at(disabled, 0), (False, 2.5, 750.0))
        self.assertFalse(project.setup_values)
        self.assertEqual(len(CITADEL_ACTIONS), 3)

    def test_citadel_edits_reject_out_of_range_values_and_keep_action_ids(self):
        from dolly.editor_dof import edited_citadel_project
        for control, value in ((0, .5), (1, .4), (1, 3.1), (2, -1), (2, 10001),
                               (2, float("nan")), (True, 1)):
            with self.subTest(control=control, value=value), self.assertRaises(ValueError):
                edited_citadel_project(Project(), 0, control, value)
        self.assertEqual(EXTRA_ACTIONS.index("toggle_citadel_glow") + 26, 57)
        self.assertEqual(EXTRA_ACTIONS.index("toggle_healthbars") + 26, 58)
        self.assertEqual(EXTRA_ACTIONS.index("near_player_opacity_fix") + 26, 59)
        self.assertEqual(EXTRA_ACTIONS.index("set_citadel_dof_enabled") + 26, 60)
        self.assertEqual(EXTRA_ACTIONS.index("set_citadel_dof_sensor_size") + 26, 61)
        self.assertEqual(EXTRA_ACTIONS.index("set_citadel_dof_focus_distance") + 26, 62)
        self.assertEqual(EXTRA_ACTIONS.index("set_attach_target") + 26, 63)
        self.assertEqual(EXTRA_ACTIONS.index("set_attach_point") + 26, 64)
        self.assertEqual(EXTRA_ACTIONS.index("set_attach_offsets") + 26, 65)
        self.assertEqual(EXTRA_ACTIONS.index("set_attach_smoothing") + 26, 66)
        self.assertEqual(EXTRA_ACTIONS.index("set_attach_hide") + 26, 67)
        self.assertEqual(EXTRA_ACTIONS.index("attach_cycle_target") + 26, 68)
        self.assertEqual(EXTRA_ACTIONS.index("attach_cycle_point") + 26, 69)
        self.assertEqual(EXTRA_ACTIONS.index("attach_reset") + 26, 70)
        self.assertEqual(EXTRA_ACTIONS.index("attach_preview") + 26, 71)
        self.assertEqual(EXTRA_ACTIONS.index("attach_snap") + 26, 72)

    def test_citadel_wire_block_follows_the_native_dof_block(self):
        from dolly.editor_wire import CITADEL_DOF, CITADEL_DOF_OFFSET, pack_citadel_dof
        packet = pack_citadel_dof(4, True, True, 2.5, 750.0)
        self.assertEqual(len(packet), 40)
        self.assertEqual(CITADEL_DOF_OFFSET, DOF_OFFSET + 112)
        self.assertLessEqual(CITADEL_DOF_OFFSET + len(packet), 2 * 1024 * 1024 + 4096)
        self.assertEqual(CITADEL_DOF.unpack(packet), (b"DLYCDOF1", 4, 1, 1, 1, 2.5, 750.0))
        for sensor, focus in ((.4, 750.0), (3.1, 750.0), (1.0, -1.0), (1.0, 10001.0)):
            with self.subTest(sensor=sensor, focus=focus), self.assertRaises(ValueError):
                pack_citadel_dof(4, True, True, sensor, focus)

    def test_desktop_citadel_focus_mapping_round_trips(self):
        from dolly.gui import DollyApp
        for value in (0.0, 1.0, 200.0, 2500.0, 10000.0):
            with self.subTest(value=value):
                slider = DollyApp._focus_to_slider(value)
                self.assertGreaterEqual(slider, 0.0)
                self.assertLessEqual(slider, 1.0)
                self.assertAlmostEqual(DollyApp._slider_to_focus(slider), value, places=6)
