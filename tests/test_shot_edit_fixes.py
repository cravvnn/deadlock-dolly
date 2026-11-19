"""Shot edits keep their effect: bank with a roll curve, parked framing, rebase, retime."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dolly import editor_dispatch, editor_wire, gui
from dolly.path import AttachKey, Keyframe, Project
from dolly.ui import shot_commands


class _Var:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class _App:
    def __init__(self, project):
        self.project = project
        self.status_text = _Var()

    def _commit_camera(self, keys, select_time=None):
        self.project = shot_commands.commit_camera(self.project, keys)


def _key(time, x=0.0, pitch=0.0, yaw=0.0, roll=0.0, **extra):
    return Keyframe(time, x, 0.0, 0.0, pitch, yaw, roll, **extra)


class BankEditTests(unittest.TestCase):
    def test_native_bank_edit_applies_over_roll_curve(self):
        app = _App(Project(keyframes=[_key(0.0), _key(2.0, 100, curve_roll=15.0)]))
        editor_dispatch._edit_camera_value(app, "set_camera_roll", 1, -30.0)
        self.assertEqual(app.project.evaluate(2.0)["roll"], -30.0)

    def test_camera_list_publishes_effective_roll(self):
        project = Project(keyframes=[_key(0.0), _key(2.0, 100, roll=4.0, curve_roll=15.0)])
        data = editor_wire.pack_camera_list(1, project, 1)
        row = editor_wire.CAMERA_LIST_ROW.unpack_from(
            data, editor_wire.CAMERA_LIST_HEADER.size + editor_wire.CAMERA_LIST_ROW.size)
        self.assertEqual(row[2], 15.0)

    def test_desktop_bank_update_applies_over_roll_curve(self):
        app = SimpleNamespace(
            project=Project(keyframes=[_key(0.0), _key(2.0, 100, roll=4.0, curve_roll=15.0)]),
            key_vars={field: _Var() for field in gui.FIELDS},
            camera_tree=SimpleNamespace(selection=lambda: ("1",)),
            shot_history=None, status_text=_Var(),
            _guard=lambda _title, function: function(),
            _set_time=lambda _time: None, _refresh_curve=lambda: None,
            _refresh_attach_card=lambda _key: None)
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        app._read_key = lambda: gui.DollyApp._read_key(app)
        app._commit_camera = lambda keys, _time=None: setattr(
            app, "project", shot_commands.commit_camera(app.project, keys))
        gui.DollyApp._select_key(app)
        self.assertEqual(float(app.key_vars["roll"].get()), 15.0)
        gui.DollyApp._update_key(app)  # Unchanged form keeps the curve.
        self.assertEqual(app.project.keyframes[1].curve_roll, 15.0)
        self.assertEqual(app.project.keyframes[1].roll, 4.0)
        app.key_vars["roll"].set("-30")
        gui.DollyApp._update_key(app)
        self.assertEqual(app.project.evaluate(2.0)["roll"], -30.0)


class ParkedFramingTests(unittest.TestCase):
    def _parked(self, keys):
        app = SimpleNamespace(project=Project(keyframes=keys))
        results = []
        for index, key in enumerate(keys):
            e = app.project.evaluate(key.time)
            pose = [e[name] for name in ("x", "y", "z", "pitch", "yaw", "roll")] + [1.1]
            results.append(editor_dispatch._pose_matches_key(app, index, pose))
        return results

    def test_parked_on_curve_override(self):
        self.assertEqual(self._parked([_key(0.0, pitch=10, curve_pitch=25.0), _key(2.0, 100)]), [True, True])

    def test_parked_across_angle_wrap(self):
        self.assertEqual(self._parked([_key(0.0, yaw=170), _key(2.0, 100, yaw=-170)]), [True, True])
        app = SimpleNamespace(project=Project(keyframes=[_key(0.0, yaw=180.0)]))
        self.assertTrue(editor_dispatch._pose_matches_key(app, 0, [0, 0, 0, 0, -180.0, 0, 1.0]))
        self.assertFalse(editor_dispatch._pose_matches_key(app, 0, [0, 0, 0, 0, 90.0, 0, 1.0]))


class RebaseTests(unittest.TestCase):
    def test_deleting_first_camera_keeps_replay_ticks(self):
        p = Project(keyframes=[_key(0.0), _key(0.3, 1), _key(1.3, 2), _key(2.3, 3)],
                    start_tick=1000, tick_rate=64.0)

        def ticks(project):
            return [project.start_tick + k.time * project.tick_rate for k in project.keyframes]

        expected = ticks(p)
        for _ in range(3):
            p = shot_commands.delete_camera(p, 0)
            expected = expected[1:]
            for got, want in zip(ticks(p), expected):
                self.assertAlmostEqual(got, want, places=6)
            self.assertGreaterEqual(p.keyframes[0].time, 0)
            self.assertLess(p.keyframes[0].time, 1 / p.tick_rate)


class RetimeTests(unittest.TestCase):
    def test_retime_names_the_source_blend_that_overflows(self):
        p = Project(keyframes=[_key(0.0), _key(5.0, source="attach", attach=AttachKey(handle=1, model="m"),
                                               source_blend=6.0)], tick_rate=64.0)
        before = p.to_dict()
        with self.assertRaisesRegex(ValueError, r"(?i)retim.*camera 02.*source blend.*12\.0"):
            p.retime(32.0)
        self.assertEqual(p.to_dict(), before)


class RenameTests(unittest.TestCase):
    def test_rename_refuses_name_longer_than_the_project_limit(self):
        marks = []
        app = SimpleNamespace(busy=False, playing=False, root=None, status_text=_Var(),
                              project=Project(name="Opening", keyframes=[_key(0.0), _key(1.0, 10)]),
                              _mark_dirty=lambda: marks.append(True))
        with patch.object(gui.simpledialog, "askstring", return_value="x" * 300):
            gui.DollyApp.rename(app)
        app.project.validate()
        self.assertEqual(app.project.name, "Opening")
        self.assertEqual(marks, [])
        self.assertIn("256", app.status_text.get())


if __name__ == "__main__":
    unittest.main()
