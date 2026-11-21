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


class CurveHelperTests(unittest.TestCase):
    """The curve workspace draws the graphs; dolly.curve keeps only its shared math."""

    def test_curve_keeps_only_the_helpers_the_workspace_uses(self):
        from dolly import curve
        for name in ("AspectCurve", "RotationCurve", "PositionCurve", "_GraphViewMixin",
                     "plot_bounds", "rotation_bounds", "position_bounds", "sample_curve",
                     "clamp_aspect", "CHANNEL_LABELS"):
            self.assertFalse(hasattr(curve, name), name)
        self.assertFalse(hasattr(curve.PlotBounds, "aspect_at"))

    def test_shared_curve_math(self):
        from dolly.curve import PositionBounds, TimelineView, hit_key, sample_channel
        view = TimelineView()
        view.zoom(0.5, 1.0, 4.0)
        self.assertEqual(view.window(4.0), (0.5, 2.5))
        bounds = PositionBounds(0.0, 0.0, 100.0, 100.0, 0.0, 2.0, -1e300, 1e300)
        self.assertAlmostEqual(bounds.value_at(bounds.point(1.0, 5e299)[1]) / 5e299, 1.0)
        self.assertEqual(hit_key([(0.0, 0.0), (10.0, 10.0)], 9.0, 9.0), 1)
        self.assertIsNone(hit_key([(0.0, 0.0)], 50.0, 50.0))
        project = Project(keyframes=[_key(0.0), _key(2.0, 10.0)])
        samples = sample_channel(project, "x", 0.0, 2.0, 3)
        self.assertEqual(samples[0], (0.0, 0.0))
        self.assertAlmostEqual(samples[-1][1], 10.0)


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


class EffectPublicationTests(unittest.TestCase):
    """A saved effect value the in-game controls cannot show must not stop the editor poll."""

    def _published(self, project, playhead):
        from dolly import editor_dof
        enabled, sensor, focus = editor_dof.published_citadel_values_at(project, playhead)
        editor_wire.pack_citadel_dof(2, True, enabled, sensor, focus)
        values = editor_dof.published_values_at(project, playhead)
        editor_wire.pack_dof(2, True, values)
        return values, sensor, focus

    def test_out_of_range_effect_values_still_publish(self):
        from dolly.path import CvarTrack, TrackKey
        keys = [_key(0.0), _key(5.0, 10)]
        far = Project(keyframes=keys, setup_values={
            "r_citadel_depthoffield_focus_distance": 20000.0,
            "r_citadel_depthoffield_sensor_size": 0.1})
        self.assertEqual(self._published(far, 0.0)[1:], (0.5, 10000.0))
        fade = Project(keyframes=keys, tracks=[
            CvarTrack("r_dof_override", [TrackKey(0.0, 0.0), TrackKey(5.0, 1.0)], "linear")])
        self.assertEqual(self._published(fade, 2.5)[0][1], 1.0)
        self.assertEqual(self._published(fade, 0.0)[0][1], 0.0)

    def test_in_range_effect_values_publish_unchanged(self):
        from dolly import editor_dof
        project = Project(keyframes=[_key(0.0), _key(5.0, 10)], setup_values={
            "r_depth_of_field": 1.0, "r_citadel_depthoffield_focus_distance": 350.0,
            "r_dof_override_near_blurry": -40.0})
        self.assertEqual(editor_dof.published_values_at(project, 1.0),
                         editor_dof.values_at(project, 1.0))
        self.assertEqual(editor_dof.published_citadel_values_at(project, 1.0),
                         editor_dof.citadel_values_at(project, 1.0))


class BankDragHistoryTests(unittest.TestCase):
    def test_bank_drag_on_an_unselected_camera_is_one_undo_step(self):
        from dolly.shot_history import ShotHistory
        project = Project(keyframes=[_key(0.0), _key(2.0, 100), _key(4.0, 200)])
        history = ShotHistory(project)
        selection = ["0"]
        app = SimpleNamespace(
            project=project, shot_history=history, busy=False, playing=False, dirty=False,
            status_text=_Var(), shot_time=_Var(0.0), preview_attach=False,
            camera_tree=SimpleNamespace(selection=lambda: tuple(selection)),
            _shot_edit_ready=lambda: True, _title=lambda: None)

        def refresh_keys(selected_time=None):
            # The edited camera becomes the selected one, as in the camera list.
            for index, key in enumerate(app.project.keyframes):
                if key.time == selected_time:
                    selection[0] = str(index)
                    app.shot_time.set(key.time)

        app._refresh_keys = refresh_keys
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        app._mark_dirty = lambda: gui.DollyApp._mark_dirty(app)
        app._commit_project = lambda candidate, time=None: gui.DollyApp._commit_project(app, candidate, time)
        app._commit_camera = lambda keys, time=None: gui.DollyApp._commit_camera(app, keys, time)
        for step in range(1, 6):
            event = {"action": "set_camera_roll", "value": 2.0,
                     "pose": [history.revision, step * .25, 0, 0, 0, 0, 0]}
            self.assertTrue(editor_dispatch.dispatch(app, event, None, publish=lambda: None))
        self.assertEqual(app.project.keyframes[2].roll, 1.25)
        self.assertEqual(history.move().project.keyframes[2].roll, 0.0)


class CurveEditHistoryGroupTests(unittest.TestCase):
    def test_a_graph_edit_is_its_own_undo_step_inside_a_running_drag(self):
        for channel, value in (("x", 50.0), ("pitch", 10.0)):
            for fails in (False, True):
                with self.subTest(channel=channel, fails=fails):
                    project = Project(keyframes=[_key(0.0), _key(2.0, 100)])
                    seen = []

                    def commit(*_args, **_kwargs):
                        seen.append(app._history_edit_group)
                        if fails:
                            raise RuntimeError("commit refused")

                    app = SimpleNamespace(project=project, busy=False, playing=False, status_text=_Var(),
                                          errors=[], _history_edit_group=("drag", 1),
                                          _shot_edit_ready=lambda: True, _refresh_curve=lambda: None,
                                          _commit_camera=commit, _commit_project=commit)
                    app._error = lambda title, exc: app.errors.append(str(exc))
                    app._guard = lambda title, function: gui.DollyApp._guard(app, title, function)
                    for name in ("_change_position_curve", "_change_rotation_curve", "_change_curve_key"):
                        setattr(app, name, getattr(gui.DollyApp, name).__get__(app))
                    gui.DollyApp._change_unified_curve(app, 1, channel, value)
                    self.assertEqual(seen, [None])
                    self.assertEqual(app._history_edit_group, ("drag", 1))
                    self.assertEqual(app.errors, ["commit refused"] if fails else [])


class ReplaceCaptureTests(unittest.TestCase):
    def test_replacing_a_view_keeps_its_blend_in(self):
        project = Project(keyframes=[
            _key(0.0, source="attach", attach=AttachKey(handle=1, model="m")),
            _key(2.0, 100, source_blend=0.5)])
        shot_commands.finish_capture(project, "replace", _key(2.0, 250), 128, selected=1)
        self.assertEqual(project.keyframes[1].x, 250)
        self.assertEqual(project.keyframes[1].source_blend, 0.5)

    def test_replacing_an_attached_view_keeps_it_attached(self):
        attach = AttachKey(handle=7, entity_id=3, model="m", point="bone", bone="head",
                           offset=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), smoothing=0.25)
        project = Project(keyframes=[_key(0.0), _key(2.0, 100, source="attach", attach=attach)])
        shot_commands.finish_capture(project, "replace", _key(2.0, 250), 128, selected=1)
        replaced = project.keyframes[1]
        self.assertEqual((replaced.x, replaced.source, replaced.attach), (250, "attach", attach))
        self.assertIsNot(replaced.attach, attach)

    def test_replacing_with_an_attached_view_uses_that_view_s_attachment(self):
        project = Project(keyframes=[
            _key(0.0), _key(2.0, 100, source="attach", attach=AttachKey(handle=7, model="m"))])
        other = AttachKey(handle=9, model="n")
        shot_commands.finish_capture(project, "replace", _key(2.0, 250, source="attach", attach=other),
                                     128, selected=1)
        self.assertEqual(project.keyframes[1].attach, other)

    def test_replacing_a_free_view_keeps_it_free(self):
        project = Project(keyframes=[_key(0.0), _key(2.0, 100)])
        shot_commands.finish_capture(project, "replace", _key(2.0, 250), 128, selected=1)
        self.assertEqual((project.keyframes[1].source, project.keyframes[1].attach), ("free", None))


class AttachedFlyHistoryTests(unittest.TestCase):
    def test_one_attached_fly_is_one_undo_step(self):
        from dolly.native_effects import model_token
        from dolly.shot_history import ShotHistory
        attach = AttachKey(handle=7, model="models/hero.vmdl")
        project = Project(keyframes=[_key(0.0), _key(2.0, 100, source="attach", attach=attach)])
        history = ShotHistory(project)
        bridge = SimpleNamespace(_editor_attach_sequence=3)
        app = SimpleNamespace(
            project=project, shot_history=history, preview_attach=True, dirty=False,
            camera_tree=SimpleNamespace(selection=lambda: ("1",)),
            controller=SimpleNamespace(_native_bridge=lambda: bridge),
            _title=lambda: None, _refresh_attach_card=lambda _key: None)
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        app._mark_dirty = lambda: gui.DollyApp._mark_dirty(app)
        # An earlier edit the user may still want to undo after flying.
        earlier = shot_commands.commit_camera(project, [_key(0.0, 5), project.keyframes[1]])
        app.project = earlier
        app._mark_dirty()
        for step in range(1, 151):
            gui.DollyApp._attach_result_changed(app, {
                "config_sequence": 3, "handle": 7, "entity_index": 0,
                "model": model_token(attach.model), "point": 0,
                "offset": (float(step), 0.0, 0.0, 0.0, 0.0, 0.0)})
        self.assertEqual(app.project.keyframes[1].attach.offset[0], 150.0)
        self.assertEqual(history.move().project, earlier)
        self.assertEqual(history.move().project, project)


class AttachedViewChangeTests(unittest.TestCase):
    def _app(self):
        from unittest.mock import Mock
        selection = ["0"]
        app = SimpleNamespace(
            project=Project(keyframes=[_key(0.0), _key(2.0, 100)]), busy=False, playing=False,
            preview_attach=True, _native_attach_cache=object(), _bone_picker_context=None,
            _native_cinematic_request=None, status_text=_Var(), _submit=Mock(),
            controller=SimpleNamespace(stop=Mock()), _select_key=lambda: None, _snapshot=lambda: None,
            camera_tree=SimpleNamespace(selection=lambda: tuple(selection),
                                        selection_set=lambda item: selection.__setitem__(0, item),
                                        see=lambda _item: None))
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        return app

    def test_changing_view_while_attached_is_refused(self):
        app = self._app()
        bridge = SimpleNamespace(status=lambda: {"state": "manual"})
        for action in ("next_view", "previous_view"):
            with self.assertRaisesRegex(ValueError, "Detach the camera"):
                editor_dispatch.dispatch(app, {"action": action, "value": 0}, bridge, publish=lambda: None)
        app._submit.assert_not_called()
        self.assertEqual(app.camera_tree.selection(), ("0",))
        self.assertTrue(app.preview_attach)

    def test_stop_clears_attach_preview(self):
        app = self._app()
        published = []
        editor_dispatch.dispatch(app, {"action": "stop", "value": 0}, None,
                                 publish=lambda: published.append(app.preview_attach))
        self.assertFalse(app.preview_attach)
        self.assertIsNone(app._native_attach_cache)
        self.assertEqual(published, [False])
        app._submit.assert_called_once()


class CurveGraphPressTests(unittest.TestCase):
    def test_clicking_the_graph_applies_a_typed_value(self):
        import tkinter as tk
        from dolly.curve_workspace import CameraCurveWorkspace
        root = tk.Tk()
        root.withdraw()
        calls = []
        try:
            workspace = CameraCurveWorkspace(root, on_change=lambda *edit: calls.append(edit),
                                             on_select=lambda _index: None, on_reset=lambda _channel: None,
                                             context=lambda: ("shot", 1))
            workspace.pack()
            workspace.set_project(Project(keyframes=[_key(0.0), _key(2.0, 100)]), 0)
            lane = workspace.lanes[0]
            for text in ("abc", "1234"):
                lane.value_entry.focus_force()
                root.update()
                lane._begin_value()
                lane.value_text.set(text)
                result = lane._press(SimpleNamespace(x=0, y=0))
                root.update()
                if text == "abc":
                    self.assertEqual(result, "break")
                    self.assertTrue(lane._value_error)
                    self.assertEqual(calls, [])
                    lane._cancel_value()
            self.assertEqual(calls, [(0, "x", 1234.0)])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
