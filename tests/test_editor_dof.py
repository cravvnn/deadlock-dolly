import unittest

from dolly.editor_dof import DEFAULT_RANGES, RANGE_NAME, edited_project, values_at
from dolly.editor_wire import DOF_CONFIG, DOF_OFFSET, EXTRA_ACTIONS, pack_dof
from dolly.path import CvarTrack, Keyframe, Project, TrackKey


class EditorDofTests(unittest.TestCase):
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
