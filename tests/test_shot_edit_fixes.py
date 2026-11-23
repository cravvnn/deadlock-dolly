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

    def test_curve_editor_uses_the_project_limits(self):
        from dolly import curve_workspace, path
        self.assertIs(curve_workspace.TANGENT_MODES, path.TANGENT_MODES)
        self.assertTrue(curve_workspace.valid_value("aspect_ratio", path.ASPECT_MAX))
        self.assertTrue(curve_workspace.valid_value("aspect_ratio", path.ASPECT_MIN))
        self.assertFalse(curve_workspace.valid_value("aspect_ratio", path.ASPECT_MAX + 1e-9))
        self.assertFalse(curve_workspace.valid_value("aspect_ratio", path.ASPECT_MIN - 1e-9))

    def test_drawn_keys_sit_on_the_sampled_curve(self):
        from dolly.curve_workspace import display_values
        for mode in ("shortest", "unwrapped"):
            project = Project(rotation_mode=mode, keyframes=[
                _key(0.0, yaw=170, roll=-180), _key(1.0, 10, yaw=-170, roll=180, curve_yaw=-175.0),
                _key(2.0, 20, yaw=190, pitch=5)])
            for channel in ("yaw", "pitch", "roll"):
                self.assertEqual(display_values(project, channel),
                                 list(project.camera_channel_samples(channel).values), (mode, channel))
        self.assertEqual(display_values(Project(rotation_mode="shortest", keyframes=[
            _key(0.0, yaw=170), _key(1.0, yaw=-170), _key(2.0, yaw=190)]), "yaw"), [170.0, 190.0, 190.0])

    def test_project_versions_are_the_range_up_to_the_latest(self):
        from dolly import path
        versions = sorted(value for name, value in vars(path).items()
                          if name.endswith("FORMAT_VERSION") and name != "LATEST_FORMAT_VERSION")
        self.assertEqual(versions, list(range(2, path.LATEST_FORMAT_VERSION + 1)))
        for version in range(1, path.LATEST_FORMAT_VERSION + 1):
            try:
                Project.from_dict({"format": path.FORMAT_NAME, "version": version})
            except ValueError as error:
                self.assertNotIn("Unsupported project version", str(error))
        for version in (0, path.LATEST_FORMAT_VERSION + 1, True, 1.0):
            with self.assertRaisesRegex(ValueError, "Unsupported project version"):
                Project.from_dict({"format": path.FORMAT_NAME, "version": version})

    def test_close_keys_fall_back_to_linear_in_both_samplers(self):
        from dolly import path
        times, values = [0.0, 1e-300, 2e-300, 1.0], [0.0, 1e10, 0.0, 1e10]
        for interpolation in ("smooth", "spline"):
            for monotone in (True, False):
                self.assertIsNone(path._auto_tangents(times, values, interpolation, monotone))
                self.assertEqual(path._sample(times, values, 0.5, interpolation, monotone), 5e9)
        project = Project(keyframes=[_key(time, value) for time, value in zip(times, values)])
        self.assertEqual(project.camera_channel_samples("x").interpolation, "linear")
        normal = [0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 4.0, 9.0]
        self.assertEqual(path._auto_tangents(*normal, "smooth", True), path._monotone_tangents(*normal))
        self.assertEqual(path._auto_tangents(*normal, "smooth", False), path._position_tangents(*normal))
        self.assertEqual(path._auto_tangents(*normal, "spline", True), path._spline_tangents(*normal))


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


class AttachHereKeepsViewTests(unittest.TestCase):
    def test_attach_here_stores_the_free_view_then_attaches(self):
        from dolly.native_effects import model_token
        from dolly.shot_history import ShotHistory
        attach = AttachKey(handle=7, model="models/hero.vmdl", offset=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
        project = Project(keyframes=[_key(0.0, source="attach", attach=attach), _key(2.0, 100)])
        bridge = SimpleNamespace(_editor_attach_sequence=3, status=lambda: {"state": "manual"})
        published = []
        app = SimpleNamespace(
            project=project, shot_history=ShotHistory(project), preview_attach=False, dirty=False,
            busy=False, playing=False, _bone_picker_context=None, status_text=_Var(),
            camera_tree=SimpleNamespace(selection=lambda: ("0",)),
            controller=SimpleNamespace(_native_bridge=lambda: bridge, _follow_active=False),
            _title=lambda: None, _refresh_attach_card=lambda _key: None,
            _publish_editor=lambda: published.append(app.preview_attach))
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        app._mark_dirty = lambda: gui.DollyApp._mark_dirty(app)
        editor_dispatch.dispatch(app, {"action": "attach_preview", "value": 1}, bridge,
                                 publish=lambda: published.append(app.preview_attach))
        self.assertTrue(app._attach_snap_pending)
        self.assertEqual(app._attach_snap_request, 1)
        self.assertFalse(app.preview_attach, "The view is stored before attaching")
        # A second click while the view is on its way changes nothing.
        editor_dispatch.dispatch(app, {"action": "attach_preview", "value": 1}, bridge, publish=lambda: None)
        self.assertEqual(app._attach_snap_request, 1)
        gui.DollyApp._attach_result_changed(app, {
            "config_sequence": 3, "handle": 7, "entity_index": 0,
            "model": model_token(attach.model), "point": 0,
            "offset": (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)})
        key = app.project.keyframes[0].attach
        self.assertEqual(key.offset, (10.0, 20.0, 30.0, 40.0, 50.0, 60.0))
        self.assertEqual(key.orientation_mode, "rigid")
        self.assertTrue(app.preview_attach)
        self.assertFalse(app._attach_snap_pending)
        self.assertEqual(published[-1], True)

    def test_attach_here_says_why_native_could_not_keep_the_view(self):
        from unittest.mock import patch
        from dolly import editor_session, editor_wire
        raw = editor_wire.ATTACH_RESULT.pack(b"DLYATR01", 6, editor_wire.ATTACH_RESULT_ABI, 2, 20,
                                             7, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)
        result = editor_wire.unpack_attach_result(raw)
        self.assertEqual((result["valid"], result["snap_failure"], result["config_sequence"]), (False, 1, 20))
        bridge = SimpleNamespace(_editor_attach_sequence=20, editor_attach_result=lambda: result,
                                 editor_status=lambda: {"enabled": True, "events": []})
        app = SimpleNamespace(
            closed=False, busy=False, native_editor_active=True, _attach_roster_at=float("inf"),
            _bone_picker_context=None, status_text=_Var(), _attach_snap_pending=True,
            _attach_snap_started=10**9, _attach_snap_context={"index": 0},
            controller=SimpleNamespace(status=lambda: {"connected": True}, _native_bridge=lambda: bridge))
        with patch.object(editor_session, "configure"), patch.object(editor_session, "snap_is_current",
                                                                     return_value=True):
            editor_session.poll(app)
        self.assertFalse(app._attach_snap_pending)
        self.assertIn("too far from the player", app.status_text.get())

    def test_detach_cancels_a_pending_attach_here(self):
        from unittest.mock import Mock
        app = SimpleNamespace(
            project=Project(keyframes=[_key(0.0)]), preview_attach=False, busy=False, playing=False,
            _bone_picker_context=None, status_text=_Var(), _submit=Mock(),
            controller=SimpleNamespace(_follow_active=False, enter_native_flight=Mock()),
            _attach_snap_pending=True, _attach_snap_started=1.0, _attach_snap_context={"index": 0})
        editor_dispatch.dispatch(app, {"action": "attach_preview", "value": 0},
                                 SimpleNamespace(status=lambda: {"state": "manual"}), publish=lambda: None)
        self.assertEqual((app._attach_snap_pending, app._attach_snap_context, app.preview_attach),
                         (False, None, False))


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

    def test_attach_reset_clears_a_pending_snap(self):
        app = self._app()
        app.project = Project(keyframes=[_key(0.0, source="attach", attach=AttachKey(handle=1, model="m")),
                                         _key(2.0, 100)])
        app._commit_camera = lambda keys, _time=None: setattr(
            app, "project", shot_commands.commit_camera(app.project, keys))
        app._attach_snap_pending, app._attach_snap_started = True, 1.0
        app._attach_snap_context = {"index": 0}
        editor_dispatch.dispatch(app, {"action": "attach_reset", "value": 0}, None, publish=lambda: None)
        self.assertEqual((app.preview_attach, app._attach_snap_pending, app._attach_snap_started,
                          app._attach_snap_context, app.status_text.get()), (False, False, None, None, ""))
        self.assertEqual(app.project.keyframes[0].source, "free")

    def test_parked_framing_waits_for_an_editable_shot(self):
        for ready in (False, True):
            app = self._app()
            app.preview_attach = False
            app._shot_edit_ready = lambda: ready
            app._commit_camera = lambda keys, _time=None: setattr(
                app, "project", shot_commands.commit_camera(app.project, keys))
            original = app.project.keyframes[0].aspect_ratio
            published = []
            event = {"action": "set_framing", "value": 0, "pose": [0, 0, 0, 0, 0, 0, 1.1]}
            self.assertTrue(editor_dispatch.dispatch(app, event, None, publish=lambda: published.append(1)))
            self.assertEqual(app.project.keyframes[0].aspect_ratio,
                             round(original * 1.1, 4) if ready else original)
            self.assertEqual(published, [] if ready else [1])

    def test_position_edits_only_arrive_as_curve_values(self):
        app = self._app()
        app.preview_attach = False
        event = {"action": "set_camera_position", "value": 0, "pose": [1, 0, 5.0, 0.0, 0, 0, 0]}
        with self.assertRaisesRegex(ValueError, "unsupported UI action"):
            editor_dispatch.dispatch(app, event, None, publish=lambda: None)
        self.assertEqual(app.project.keyframes[0].x, 0.0)


class InGameCurveGateTests(unittest.TestCase):
    def _run(self, kind, *, input_mode="panel", selected="1"):
        project = Project(interpolation="smooth", keyframes=[_key(0.0), _key(2.0, 100), _key(4.0, 200)])
        app = SimpleNamespace(
            project=project, status_text=_Var(), _shot_edit_ready=lambda: True,
            shot_history=SimpleNamespace(revision=3, session_id=5),
            camera_tree=SimpleNamespace(selection=lambda: (selected,) if selected else ()))
        app._selection_index = lambda tree: gui.DollyApp._selection_index(app, tree)
        app._commit_camera = lambda keys, _time=None: setattr(
            app, "project", shot_commands.commit_camera(app.project, keys))
        app._commit_project = lambda candidate, _time=None: setattr(app, "project", candidate)
        bridge = SimpleNamespace(editor_status=lambda: dict(
            enabled=True, ready=True, focused=True, paused=True, input_mode=input_mode))
        if kind == "curve":
            event = {"value": 1, "pose": [3, 0, 150.0, 2.0, 5, 0, 0]}
            result = editor_dispatch._edit_camera_curve(app, event, bridge)
            changed = app.project.keyframes[1].x == 150.0
        else:
            event = {"value": 1 * 12 + 0 * 4 + 2, "pose": [3, 7.0, 7.0, 2.0, 5, 0, 0]}
            result = editor_dispatch._edit_camera_position_tangents(app, event, bridge)
            changed = "x" in app.project.keyframes[1].tangents
        return result, changed, app.status_text.get()

    def test_curve_and_slope_edits_share_the_panel_and_selection_gates(self):
        for kind, noun in (("curve", "Curve"), ("tangent", "Arrival/departure")):
            self.assertEqual(self._run(kind, input_mode="game_ui"), (False, False,
                             f"{noun} edit cancelled. Pause the replay and return to Dolly's controls."))
            self.assertEqual(self._run(kind, selected="2"), (False, False,
                             f"{noun} edit cancelled because the selected camera changed."))
            self.assertEqual(self._run(kind, selected=None)[:2], (False, False))
            result, changed, _status = self._run(kind)
            self.assertEqual((result, changed), (True, True), kind)


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


class EditorEventBusyTests(unittest.TestCase):
    def _follow_app(self, bridge):
        from unittest.mock import Mock
        app = SimpleNamespace(
            closed=False, busy=False, native_editor_active=True, _attach_roster_at=float("inf"),
            _bone_picker_context=None, _attach_snap_pending=False, preview_attach=False,
            status_text=_Var(), camera_tree=object(), _selection_index=lambda _: 0,
            _error=Mock(), _submit=Mock(),
            controller=SimpleNamespace(status=lambda: {"connected": True}, _native_bridge=lambda: bridge))
        return app

    def test_busy_roster_keeps_the_follow_event_queued(self):
        from dolly import editor_session
        from dolly.native_bridge import NativeStatusBusy
        event = {"action": "start_game_follow", "sequence": 1, "value": 135.0,
                 "pose": (34.0, 0.0, 0.0, 7, 3, 5, 0)}
        acks, reads = [], []

        def roster():
            reads.append(True)
            if len(reads) == 1:
                raise NativeStatusBusy("The native attach roster is busy; retry in a moment.")
            return {"players": [{"handle": 7, "entity_index": 3, "model": 5}]}

        bridge = SimpleNamespace(editor_status=lambda: {"enabled": True, "events": [event] if not acks else []},
                                 editor_roster=roster, acknowledge_editor_event=acks.append)
        app = self._follow_app(bridge)
        with patch.object(editor_session, "configure"):
            editor_session.poll(app)
            app._error.assert_not_called()
            app._submit.assert_not_called()
            self.assertEqual(acks, [])
            editor_session.poll(app)
        app._error.assert_not_called()
        self.assertEqual(app._submit.call_count, 1)
        self.assertEqual(acks, [1])

    def test_dropped_actions_and_errors_are_reported_per_bridge(self):
        from dolly import editor_session
        from dolly.native_bridge import NativeBridgeError
        dropped = "The editor was busy; an in-game action was ignored. Retry the action."

        def bridge(count):
            return SimpleNamespace(editor_status=lambda: {"enabled": True, "events": [],
                                                          "dropped_events": count})

        one, two = bridge(3), bridge(1)
        current = [one]
        app = self._follow_app(None)
        app.controller._native_bridge = lambda: current[0]
        with patch.object(editor_session, "configure"), self.assertLogs("dolly.editor_session", "ERROR"):
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), dropped)
            app.status_text.set("")
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), "")
            current[0] = two
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), dropped)
            app.status_text.set("")
            current[0] = bridge(1)  # Reconnected to the same game: no new drops.
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), "")
            current[0] = two

            failure = NativeBridgeError("The native editor stopped answering")
            two.editor_status = lambda: (_ for _ in ()).throw(failure)
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), str(failure))
            app.status_text.set("")
            editor_session.poll(app)
            self.assertEqual(app.status_text.get(), "")
            two.editor_status = lambda: {"enabled": True, "events": [], "dropped_events": 1}
            editor_session.poll(app)
            two.editor_status = lambda: (_ for _ in ()).throw(failure)
            editor_session.poll(app)
        self.assertEqual(app.status_text.get(), str(failure))

    def test_busy_status_skips_one_curve_preview_frame_only(self):
        from dolly import curve_preview
        from dolly.native_bridge import NativeStatusBusy
        reads, published = [], []

        def status():
            reads.append(True)
            if len(reads) == 1:
                raise NativeStatusBusy("Native editor status is being updated")
            return {"enabled": True, "ready": True, "paused": True, "flight_active": True,
                    "focused": True, "input_mode": "panel"}

        bridge = SimpleNamespace(editor_status=status,
                                 publish_visualization_preview=lambda data, **identity: published.append(identity))
        app = SimpleNamespace(project=Project(keyframes=[_key(0.0), _key(1.0, x=10.0)]),
                              shot_history=SimpleNamespace(session_id=5, revision=3),
                              camera_tree=object(), _selection_index=lambda _: 0)
        event = {"action": "preview_camera_curve_value", "value": 0, "pose": (3, 0, 4.0, 0.0, 5, 0, 9)}
        self.assertFalse(curve_preview.offer_native(app, event, bridge))
        self.assertFalse(app._curve_preview_state.native_terminal)
        self.assertTrue(curve_preview.offer_native(app, event, bridge))
        self.assertEqual(published[-1]["token"], 9)

    def test_busy_status_does_not_retire_a_paused_curve_preview(self):
        from dolly import curve_preview, visualization_preview_wire as wire
        from dolly.native_bridge import NativeStatusBusy

        def busy():
            raise NativeStatusBusy("Native editor status is being updated")

        state = curve_preview._State()
        state.native_session, state.native_token = 5, 9
        state.last_identity = dict(source=wire.NATIVE, token=9, revision=3, session_id=5,
                                   selected_camera=0, channel=0, key_time=0.0)
        bridge = SimpleNamespace(editor_status=busy, publish_visualization_preview=lambda *a, **k: None)
        state.bridge = bridge
        app = SimpleNamespace(_curve_preview_state=state)
        curve_preview.configure(app, bridge, active=True, selected=0)
        self.assertIsNotNone(state.last_identity)
        self.assertFalse(state.native_terminal)

    def test_closing_the_editor_retires_its_curve_preview(self):
        from dolly import curve_preview, editor_session, visualization_preview_wire as wire
        cleared, configured = [], []
        bridge = SimpleNamespace(publish_visualization_preview=lambda packet, **identity: cleared.append(
                                     (packet, identity)),
                                 configure_editor=lambda **kwargs: configured.append(kwargs))
        state = curve_preview._State()
        state.bridge, state.native_session, state.native_token = bridge, 5, 9
        state.last_identity = dict(source=wire.NATIVE, token=9, revision=3, session_id=5,
                                   selected_camera=0, channel=0, key_time=0.0)
        app = SimpleNamespace(_curve_preview_state=state,
                              controller=SimpleNamespace(status=lambda: {}, _native_bridge=lambda: bridge))
        editor_session.close(app)
        self.assertEqual([(packet, identity["token"]) for packet, identity in cleared], [(None, 9)])
        self.assertIsNone(state.last_identity)
        self.assertTrue(state.native_terminal)
        self.assertEqual(configured, [{"enabled": False, "owner": "disabled"}])


if __name__ == "__main__":
    unittest.main()
