import unittest

from dolly.editor_dof import edited_project, values_at
from dolly.editor_wire import DOF_CONFIG, DOF_OFFSET, EXTRA_ACTIONS, pack_dof
from dolly.path import CvarTrack, Keyframe, Project, TrackKey


class EditorDofTests(unittest.TestCase):
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
