"""Unified desktop gestures/history, frozen originals and real Tk controls."""
import copy
import math
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly.curve_workspace import (CHANNELS, GROUPS, CameraCurveWorkspace,
                                   CurveWorkspaceState, clipped_paths, display_values)
from dolly.curve import PlotBounds
from dolly.native_path import compile_project
from dolly.path import AttachKey, Keyframe, Project
from dolly.shot_history import ShotHistory
from dolly.gui import DollyApp


def shot():
    return Project(keyframes=[Keyframe(0, 100.1234567890123, 20, 30, 10, 359, 1),
                              Keyframe(3, 130, -20, 60, 20, 1, 3, lens_scale=.875)])


class WorkspaceModelTests(unittest.TestCase):
    def setUp(self):
        self.project = shot()
        self.history = ShotHistory(self.project)
        self.changed = []
        def commit(index, channel, value):
            candidate = copy.deepcopy(self.project)
            field = "curve_"+channel if channel in CHANNELS[1] else channel
            setattr(candidate.keyframes[index], field, value)
            candidate.validate()
            self.project = candidate
            self.history.record(candidate)
            self.changed.append((index, channel, value))
            self.refresh(index)
        self.state = CurveWorkspaceState(commit)
        self.refresh(0)

    def refresh(self, index=0):
        self.state.refresh(self.project, index, 0, (self.history.session_id,self.history.revision))

    def test_seven_channels_use_normal_authored_fields_without_lens_or_time_change(self):
        for channel in sum(CHANNELS, ()):
            with self.subTest(channel=channel):
                self.setUp()
                original = copy.deepcopy(self.project)
                self.state.selected = 1
                self.assertTrue(self.state.begin(1, channel))
                value = 2.39 if channel == "aspect_ratio" else 123.456789012345
                self.assertTrue(self.state.commit(value))
                expected = copy.deepcopy(original)
                setattr(expected.keyframes[1], "curve_"+channel if channel in CHANNELS[1] else channel, value)
                self.assertEqual(self.project, expected)
                self.assertEqual(self.project.keyframes[1].lens_scale,.875)
                self.assertTrue(compile_project(self.project))

    def test_exact_noop_preserves_override_absence_and_redo(self):
        self.state.begin(0,"x")
        self.state.commit(101)
        self.project = self.history.move().project
        self.refresh()
        revision = self.history.revision
        self.state.begin(0,"x")
        self.assertFalse(self.state.commit(100.1234567890123))
        self.assertEqual(self.history.revision,revision)
        self.assertTrue(self.history.can_redo)
        self.assertEqual(len(self.changed),1)
        self.state.begin(0,"yaw")
        self.assertFalse(self.state.commit(359))
        self.assertIsNone(self.project.keyframes[0].curve_yaw)

    def test_independent_commits_keep_separate_undo_steps(self):
        before = copy.deepcopy(self.project)
        self.state.begin(0,"yaw"); self.state.commit(721)
        first = copy.deepcopy(self.project)
        self.state.begin(0,"roll"); self.state.commit(-359)
        self.assertEqual(self.history.move().project,first)
        self.assertEqual(self.history.move().project,before)

    def test_malformed_selection_does_not_fall_back_to_current_key(self):
        for index in (None,True,False,"0",-1,99):
            self.assertFalse(self.state.begin(index,"x"))
            self.assertIsNone(self.state.gesture)

    def test_original_pin_stays_fixed_across_edits_undo_and_toggle_until_update(self):
        self.assertFalse(self.state.show_original)
        self.state.set_show_original(True)
        original = copy.deepcopy(self.state.original)
        self.state.begin(0,"x"); self.state.commit(160)
        self.assertEqual(self.state.original,original)
        self.project = self.history.move().project
        self.refresh()
        self.assertEqual(self.state.original,original)
        self.state.set_show_original(False); self.state.set_show_original(True)
        self.assertEqual(self.state.original,original)
        self.state.begin(0,"x"); self.state.commit(170)
        self.state.pin_original()
        self.assertEqual(self.state.original,self.project)
        self.assertIsNot(self.state.original,self.project)

    def test_new_open_session_clears_original_even_if_project_revision_and_values_match(self):
        self.state.pin_original()
        old_session = self.history.session_id
        self.history = ShotHistory(self.project)
        self.assertNotEqual(old_session,self.history.session_id)
        self.refresh()
        self.assertIsNone(self.state.original)
        self.assertFalse(self.state.show_original)

    def test_stale_content_identity_context_selection_or_timing_cancels(self):
        for reason in ("content","identity","revision","selection","time","disabled"):
            with self.subTest(reason=reason):
                self.setUp()
                self.state.begin(0,"x")
                if reason == "content": self.project.keyframes[1].y += 1
                elif reason == "identity": self.project = copy.deepcopy(self.project)
                elif reason == "revision": self.history.record(Project(keyframes=copy.deepcopy(self.project.keyframes),name="changed"))
                elif reason == "time": self.project.keyframes[0].time = .1
                elif reason == "disabled": self.state.enabled = False
                self.refresh(1 if reason == "selection" else 0)
                self.assertFalse(self.state.commit(999))
                self.assertEqual(self.changed,[])

    def test_attached_and_extreme_legacy_values_are_read_only(self):
        self.project.keyframes[0].source = "attach"
        self.project.keyframes[0].attach = AttachKey(handle=3,model="hero.vmdl",point="bone",bone="head")
        for channel in sum(CHANNELS,()):
            self.assertFalse(self.state.begin(0,channel))
        self.project.keyframes[0].source = "free"
        self.project.keyframes[0].attach = None
        self.project.keyframes[0].curve_yaw = 3.6e12
        self.assertFalse(self.state.begin(0,"yaw"))
        self.assertTrue(self.state.begin(0,"pitch"))

    def test_invalid_values_never_commit_and_aspect_keeps_supported_range(self):
        for channel,value in (("x",math.nan),("yaw",math.inf),("pitch",1e8+1),("x",True),
                              ("aspect_ratio",.49),("aspect_ratio",4.01)):
            with self.subTest(channel=channel,value=value):
                self.state.begin(0,channel)
                self.assertFalse(self.state.commit(value))
        self.assertEqual(self.changed,[])

    def test_frame_all_is_view_only_and_original_alignment_does_not_rewrite_angles(self):
        self.project.keyframes[0].yaw = 179
        self.state.pin_original()
        original = copy.deepcopy(self.state.original)
        self.state.view.zoom(.3,1,self.project.duration)
        self.state.bounds("x",640,160)
        self.state.frame_all()
        self.assertEqual((self.state.view.start,self.state.view.end),(0,0))
        self.assertEqual(self.state.scales,{})
        self.assertEqual(self.changed,[])
        self.state.begin(0,"yaw"); self.state.commit(181)
        self.assertEqual(self.state.original,original)
        self.assertEqual(self.state.original_offset("yaw"),-360)
        self.project.rotation_mode="unwrapped"
        self.assertEqual(self.state.original_offset("yaw"),0)

    def test_shared_clock_separate_scales_and_large_finite_legacy_coordinates(self):
        self.state.view.zoom(.5,1,self.project.duration)
        xyz = self.state.bounds("x",640,160)
        rotation = self.state.bounds("yaw",640,160)
        framing = self.state.bounds("aspect_ratio",640,160)
        self.assertEqual((xyz.start,xyz.end),(rotation.start,rotation.end))
        self.assertEqual((xyz.start,xyz.end),(framing.start,framing.end))
        self.assertNotEqual((xyz.low,xyz.high),(rotation.low,rotation.high))
        self.project.keyframes[0].x,self.project.keyframes[1].x=-1e308,1e308
        self.state.scales.clear()
        bounds=self.state.bounds("x",640,160)
        self.assertTrue(all(math.isfinite(value) for value in bounds.point(1,0)))

    def test_shortest_display_branch_keeps_authored_full_turns_separate(self):
        self.assertEqual(display_values(self.project,"yaw"),[-1,1])
        self.state.begin(0,"yaw");self.state.commit(721)
        self.assertEqual(self.project.keyframes[0].yaw,359)
        self.assertEqual(self.project.keyframes[0].curve_yaw,721)
        self.assertEqual(display_values(self.project,"yaw"),[1,1])

    def test_plot_clipping_keeps_curves_out_of_labels(self):
        bounds=PlotBounds(60,10,620,120,0,3,0,100)
        paths=clipped_paths(bounds,[(0,-100),(1,50),(2,200),(3,50)])
        self.assertTrue(paths)
        self.assertTrue(all(bounds.left-1e-8<=x<=bounds.right+1e-8 and bounds.top-1e-8<=y<=bounds.bottom+1e-8
                            for path in paths for x,y in path))

    def test_resize_reuses_samples_but_authored_policy_view_and_revision_changes_do_not(self):
        from dolly.curve import sample_channel
        with patch('dolly.curve_workspace.sample_channel',wraps=sample_channel) as sampler:
            first=self.state.samples(self.project,'x',0,3)
            self.state.bounds('x',640,160);self.state.bounds('x',1200,250)
            self.assertEqual(sampler.call_count,1)
            self.assertIs(self.state.samples(self.project,'x',0,3),first)
            self.project.keyframes[0].x+=20
            changed=self.state.samples(self.project,'x',0,3)
            self.assertNotEqual(changed,first)
            self.project.interpolation='linear'
            self.state.samples(self.project,'x',0,3)
            self.state.samples(self.project,'x',.5,2)
            self.state.samples(copy.deepcopy(self.project),'x',0,3)
            self.assertEqual(sampler.call_count,5)
            self.refresh()
            self.state.samples(self.project,'x',0,3)
            self.assertEqual(sampler.call_count,6)

    def test_cached_aspect_samples_retain_exact_step_sides_and_original_is_independent(self):
        self.project.lens_interpolation='step'
        self.project.keyframes[1].aspect_ratio=2.39
        self.state.pin_original()
        samples=dict(self.state.samples(self.project,'aspect_ratio',0,3))
        self.assertEqual(samples[math.nextafter(3,-math.inf)],self.project.keyframes[0].aspect_ratio)
        self.assertEqual(samples[3],2.39)
        self.project.keyframes[1].aspect_ratio=3
        self.assertEqual(self.state.samples(self.project,'aspect_ratio',0,3)[-1][1],3)
        self.assertEqual(self.state.samples(self.state.original,'aspect_ratio',0,3)[-1][1],2.39)

    def test_empty_shot_framing_cache_tracks_its_standard_aspect(self):
        project=Project()
        self.state.refresh(project,context=(42,0))
        first=self.state.samples(project,'aspect_ratio',0,1)
        project.standard_aspect=2.39
        self.assertNotEqual(self.state.samples(project,'aspect_ratio',0,1),first)
        self.assertEqual(self.state.samples(project,'aspect_ratio',0,1),[(0,2.39),(1,2.39)])

    def test_capture_lifecycle_fits_real_coordinates_and_keeps_valid_viewport(self):
        # A real editor paints an empty shot before Capture mutates the same
        # Project. That placeholder used to hide subsequent X and Pitch curves.
        state = CurveWorkspaceState(Mock())
        project = Project(interpolation="smooth")
        channels = sum(CHANNELS, ())
        state.refresh(project, context=(52, 0))
        for channel in channels:
            state.bounds(channel, 1240, 260)
        cameras = [Keyframe(0, -2190, -420, 1450, 8.25, 359, -3, aspect_ratio=1.6),
                   Keyframe(.578125, -2420.7906, -180, 1320, 5.28, 1, 7, aspect_ratio=1.9),
                   Keyframe(.859375, -2480, -460, 1640, 3.15, 16, 4, aspect_ratio=2.1)]
        for number, camera in enumerate(cameras, 1):
            project.keyframes.append(camera)
            state.refresh(project, number-1, context=(52, number))
            for channel in channels:
                with self.subTest(cameras=number, channel=channel):
                    bounds = state.bounds(channel, 1240, 260)
                    values = [value for _, value in state.samples(project, channel,
                                                                  bounds.start, bounds.end)]
                    values += display_values(project, channel)
                    self.assertLessEqual(bounds.low, min(values))
                    self.assertGreaterEqual(bounds.high, max(values))
            if number == 1:
                x_bounds = state.bounds("x", 1240, 260)
                self.assertLess(x_bounds.high, -2100)
                self.assertGreater(x_bounds.low, -2250)
        state.view.zoom(.5, .578125, project.duration)
        state.view.pan(.05, project.duration)
        view = state.view.start, state.view.end
        before = {channel:state.bounds(channel, 1240, 260) for channel in channels}
        # Recapture or accepted numeric edits can leave the previous Y range.
        project.keyframes[1].x = -4100
        project.keyframes[1].curve_pitch = 28.5
        state.refresh(project, 1, context=(52, 4))
        for channel in channels:
            bounds = state.bounds(channel, 1240, 260)
            if channel in ("x", "pitch"):
                value = -4100 if channel == "x" else 28.5
                self.assertLessEqual(bounds.low, value)
                self.assertGreaterEqual(bounds.high, value)
            else:
                self.assertEqual((bounds.low, bounds.high),
                                 (before[channel].low, before[channel].high))
        self.assertEqual((state.view.start, state.view.end), view)
        self.assertEqual([key.time for key in project.keyframes], [0, .578125, .859375])


class WorkspaceAppBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.app=DollyApp.__new__(DollyApp)
        self.app.project=shot()
        self.app.shot_history=ShotHistory(self.app.project)
        self.app.busy=self.app.playing=self.app.preview_attach=False
        self.app.status_text=Mock()
        self.app.controller=Mock()
        self.app.video_export=SimpleNamespace(status=lambda:{"state":"idle"})
        self.app.camera_tree=object()
        self.app._selection_index=lambda _:1
        self.app._title=Mock()
        self.app._refresh_keys=Mock()
        self.app._refresh_curve=Mock()
        self.app._guard=lambda label,operation:operation()

    def test_desktop_commit_adapter_preserves_all_camera_fields_and_history(self):
        original=copy.deepcopy(self.app.project)
        self.app._change_unified_curve(1,"yaw",721.1234567890123)
        expected=copy.deepcopy(original)
        expected.keyframes[1].curve_yaw=721.1234567890123
        self.assertEqual(self.app.project,expected)
        self.assertEqual(self.app.controller.mock_calls,[])
        self.assertEqual(self.app.shot_history.move().project,original)

    def test_each_group_uses_one_edit_and_noop_preserves_redo(self):
        for channel,value in (("z",65.123456789),("roll",-359),("aspect_ratio",2.39)):
            with self.subTest(channel=channel):
                self.setUp()
                before=copy.deepcopy(self.app.project)
                self.app._change_unified_curve(1,channel,value)
                self.app.project=self.app.shot_history.move().project
                revision=self.app.shot_history.revision
                from dolly.path import channel_value
                self.app._change_unified_curve(1,channel,channel_value(self.app.project.keyframes[1],channel))
                self.assertEqual(self.app.project,before)
                self.assertEqual(self.app.shot_history.revision,revision)
                self.assertTrue(self.app.shot_history.can_redo)

    def test_fresh_recording_playback_and_source_guards_block_edits_and_reset(self):
        original=copy.deepcopy(self.app.project)
        for state in ("starting","recording","finalizing"):
            self.app.video_export=SimpleNamespace(status=lambda state=state:{"state":state})
            for channel,value in (("x",123),("pitch",12),("aspect_ratio",2.39)):
                self.app._change_unified_curve(1,channel,value)
            self.app._reset_rotation_curve("pitch")
            self.assertEqual(self.app.project,original)
        self.app.video_export=SimpleNamespace(status=lambda:{"state":"idle"})
        self.app.playing=True
        self.app._change_unified_curve(1,"x",123)
        self.assertEqual(self.app.project,original)
        self.app.playing=False
        self.app.project.keyframes[1].source="attach"
        self.app.project.keyframes[1].attach=AttachKey(handle=3,model="hero.vmdl",point="bone",bone="head")
        with self.assertRaisesRegex(ValueError,"attachment offsets"):
            self.app._change_unified_curve(1,"yaw",180)


class WorkspaceTkTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root=tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.context=[17,1]
        self.changes=Mock()
        self.reset=Mock()
        self.workspace=CameraCurveWorkspace(self.root,on_change=self.changes,on_select=Mock(),
                                            on_reset=self.reset,context=lambda:tuple(self.context))
        self.workspace.pack(fill="both",expand=True)
        self.project=shot()
        self.workspace.set_project(self.project,0)
        self.root.update_idletasks()

    def test_three_tabs_share_container_clock_and_original_controls(self):
        self.assertEqual(tuple(button.cget("text") for button in self.workspace.tab_buttons),GROUPS)
        self.assertEqual(len({lane.master for lane in self.workspace.lanes}),1)
        self.assertTrue(all(lane.view is self.workspace.state.view for lane in self.workspace.lanes))
        self.workspace.stack_var.set(True);self.workspace._layout()
        self.assertEqual(self.workspace.visible_groups(),(0,1))
        self.workspace.lane_vars[2].set(True);self.workspace._lanes_changed(2)
        self.assertEqual(self.workspace.visible_groups(),(0,1,2))
        self.assertEqual(self.workspace.tabs.winfo_manager(),"")
        self.assertEqual(self.workspace.graphs_button.winfo_manager(),"grid")
        for value in self.workspace.lane_vars:value.set(False)
        self.workspace._lanes_changed(2)
        self.assertEqual(self.workspace.visible_groups(),(2,))

    def test_pending_measure_is_cancelled_when_workspace_is_destroyed(self):
        self.workspace._schedule_measure()
        self.workspace.schedule_redraw()
        pending=self.workspace._measure_after
        redraw=self.workspace._redraw_after
        self.assertIn(pending,self.root.tk.splitlist(self.root.tk.call("after","info")))
        self.workspace.destroy()
        self.assertNotIn(pending,self.root.tk.splitlist(self.root.tk.call("after","info")))
        self.assertNotIn(redraw,self.root.tk.splitlist(self.root.tk.call("after","info")))

    def test_precision_wheel_uses_fractional_notches_and_coalesces_one_paint(self):
        lane=self.workspace.lanes[0]
        lane._bounds=PlotBounds(60,10,620,120,0,3,0,200)
        self.workspace.frame_all()
        self.workspace.state.view.zoom(.5,1,3)
        start,end=self.workspace.state.view.window(3)
        before=copy.deepcopy(self.project)
        with patch.object(self.workspace,'redraw') as redraw:
            pending=None
            for _ in range(4):
                lane._wheel(SimpleNamespace(x=180,delta=30))
                if pending is None:pending=self.workspace._redraw_after
                self.assertEqual(self.workspace._redraw_after,pending)
            self.root.update_idletasks()
            redraw.assert_called_once()
        new_start,new_end=self.workspace.state.view.window(3)
        self.assertAlmostEqual(new_end-new_start,(end-start)*.85)
        self.assertEqual(self.project,before)
        self.changes.assert_not_called()

    def test_zoom_out_at_full_shot_is_an_exact_view_noop_without_paint(self):
        lane=self.workspace.lanes[0]
        lane._bounds=PlotBounds(60,10,620,120,0,3,0,200)
        self.workspace.frame_all()
        with patch.object(self.workspace,'schedule_redraw') as schedule:
            lane._wheel(SimpleNamespace(x=180,delta=-30))
            schedule.assert_not_called()
        self.assertEqual((lane.view.start,lane.view.end),(0,0))

    def test_numeric_enter_noop_and_frame_all_settles_exact_draft_once(self):
        lane=self.workspace.lanes[0]
        self.assertEqual(lane.value_text.get(),"100.12346")
        lane._begin_value()
        self.assertEqual(float(lane.value_text.get()),self.project.keyframes[0].x)
        lane._apply_value()
        self.changes.assert_not_called()
        lane._begin_value();lane._apply_value()
        self.changes.assert_not_called()
        lane._begin_value();lane.value_text.set("100.9876543210123")
        self.workspace.frame_all_button.invoke()
        self.changes.assert_called_once_with(0,"x",100.9876543210123)
        self.assertIsNone(self.workspace.state.gesture)
        self.assertEqual((self.workspace.state.view.start,self.workspace.state.view.end),(0,0))

    def test_nonfinite_aspect_drag_cannot_be_coerced_to_range_endpoint(self):
        lane=self.workspace.lanes[2]
        self.workspace.select_group(2)
        self.assertTrue(lane.state.begin(0,"aspect_ratio"))
        lane._drag_bounds=PlotBounds(60,10,620,120,0,3,.5,4)
        lane._drag_press_y=60
        lane._motion(SimpleNamespace(y=math.nan))
        lane._release()
        self.changes.assert_not_called()
        self.assertIsNone(lane._preview)

    def test_lane_drag_previews_privately_and_commits_one_press_relative_value(self):
        lane=self.workspace.lanes[0]
        before=copy.deepcopy(self.project)
        bounds=lane.state.bounds("x",640,180)
        lane._bounds=bounds
        lane._points=[bounds.point(key.time,key.x) for key in self.project.keyframes]
        x,y=lane._points[0]
        # An off-centre hit must not move the key until a real pointer delta.
        lane._press(SimpleNamespace(x=x+2,y=y+2))
        lane._motion(SimpleNamespace(y=y+2))
        self.assertEqual(lane._drag_value,before.keyframes[0].x)
        lane._release()
        self.changes.assert_not_called()
        lane._press(SimpleNamespace(x=x+2,y=y+2))
        lane._motion(SimpleNamespace(y=y-8))
        expected=before.keyframes[0].x+(bounds.value_at(y-8)-bounds.value_at(y+2))
        self.assertEqual(self.project,before)
        self.assertAlmostEqual(lane._preview.keyframes[0].x,expected)
        self.changes.assert_not_called()
        lane._release()
        self.changes.assert_called_once_with(0,"x",expected)
        lane._release()
        self.assertEqual(self.changes.call_count,1)

    def test_yaw_drag_across_wrap_keeps_preview_on_the_same_visible_branch(self):
        self.project.keyframes[0].yaw=179
        self.project.keyframes[1].yaw=-179
        self.workspace.set_project(self.project,0)
        self.workspace.select_group(1)
        lane=self.workspace.lanes[1]
        lane.set_channel("yaw")
        bounds=lane.state.bounds("yaw",640,180)
        lane._bounds=bounds
        lane._points=[bounds.point(key.time,value) for key,value in zip(self.project.keyframes,display_values(self.project,"yaw"))]
        x,y=lane._points[0]
        lane._press(SimpleNamespace(x=x,y=y))
        target_y=bounds.point(0,181)[1]
        lane._motion(SimpleNamespace(y=target_y))
        self.assertAlmostEqual(lane._preview.keyframes[0].curve_yaw,181)
        self.assertEqual(lane._preview.keyframes[0].yaw,179)
        self.assertEqual(self.project.keyframes[0].yaw,179)
        offset=lane.state.original_offset("yaw")
        self.assertEqual(offset,0)
        from dolly.curve_workspace import branch_offset
        self.assertEqual(branch_offset(self.project,lane._preview,"yaw"),360)
        lane._release()
        self.assertEqual(self.changes.call_args.args[:2],(0,"yaw"))
        self.assertAlmostEqual(self.changes.call_args.args[2],181)

    def test_numeric_escape_focus_loss_and_invalid_draft_preserve_project(self):
        lane=self.workspace.lanes[0]
        before=copy.deepcopy(self.project)
        lane._begin_value();lane.value_text.set("200");lane._cancel_value()
        lane._begin_value();lane.value_text.set("300");lane._cancel_value(SimpleNamespace())
        self.changes.assert_not_called()
        lane._begin_value();lane.value_text.set("nan")
        self.workspace.state.view.zoom(.5,1,3)
        prior=copy.deepcopy(self.workspace.state.view)
        self.workspace.frame_all()
        self.assertEqual(self.workspace.state.view,prior)
        self.assertTrue(lane._editing_value)
        self.assertEqual(self.project,before)

    def test_valid_focus_loss_commits_once_and_return_can_begin_another_exact_edit(self):
        lane=self.workspace.lanes[0]
        lane._begin_value();lane.value_text.set("125.1234567890123");lane._leave_value()
        self.changes.assert_called_once_with(0,"x",125.1234567890123)
        lane._leave_value()
        self.assertEqual(self.changes.call_count,1)
        lane._value_key();lane.value_text.set("126.1234567890123");lane._apply_value()
        self.assertEqual(self.changes.call_args.args,(0,"x",126.1234567890123))
        self.assertEqual(self.changes.call_count,2)

    def test_axis_reset_and_new_session_keep_original_and_captured_separate(self):
        self.workspace.original_var.set(True);self.workspace._show_original()
        original=copy.deepcopy(self.workspace.state.original)
        rotation=self.workspace.lanes[1]
        rotation.set_channel("roll");rotation.reset_button.invoke()
        self.reset.assert_called_once_with("roll")
        self.assertEqual(self.workspace.state.original,original)
        self.context[0]+=1
        self.workspace.set_project(copy.deepcopy(self.project),0)
        self.assertIsNone(self.workspace.state.original)
        self.assertFalse(self.workspace.original_var.get())


class WorkspaceAppGestureTests(unittest.TestCase):
    """Real canvas bindings, selection queues and GUI commits through playback input."""
    def setUp(self):
        from dolly.settings import AppSettings
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.app = None
        self.addCleanup(self.close_app)
        with patch.object(DollyApp, "_submit"), \
                patch.object(DollyApp, "_load_app_settings", return_value=AppSettings(auto_updates=False)), \
                patch("dolly.graphics_profiles.load_library", return_value={"format":1,"selected":"","profiles":[]}):
            self.app = DollyApp(self.root)
        # Map transparent widgets so event_generate follows Tk's real binding
        # precedence, without displaying a test window or running app polling.
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1400x900+0+0")
        self.app.notebook.select(self.app.camera_tab)
        self.root.deiconify()
        self.reset_shot()

    def close_app(self):
        if self.app is not None:
            self.app.closed = True
            self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.destroy()

    def reset_shot(self):
        self.app.project = Project(keyframes=[Keyframe(0,-2200,100,1450,8,359,-3),
                                              Keyframe(.578125,-2420.7906,180,1320,5.28,1,7),
                                              Keyframe(.859375,-2480,460,1640,3.15,16,4)])
        self.app.shot_history = ShotHistory(self.app.project)
        self.app._refresh_keys(.578125)
        self.root.update()

    def event(self, lane, sequence, x, y, time):
        lane.canvas.event_generate(sequence, x=round(x), y=round(y), time=time)
        self.root.update()

    def quick_second_press(self, lane, x, y, time):
        self.event(lane, "<ButtonPress-1>", x, y, time)
        self.event(lane, "<ButtonRelease-1>", x, y, time+10)
        self.event(lane, "<ButtonPress-1>", x, y, time+200)

    def test_click_then_drag_reaches_saved_values_history_and_playback_for_every_channel(self):
        from dolly.path import channel_value
        for number,channel in enumerate(sum(CHANNELS, ())):
            with self.subTest(channel=channel):
                self.reset_shot()
                group = next(index for index,channels in enumerate(CHANNELS) if channel in channels)
                workspace = self.app.camera_curve_workspace
                workspace.select_group(group)
                lane = workspace.lanes[group]
                lane.set_channel(channel)
                self.root.update()
                before = copy.deepcopy(self.app.project)
                before_blob = compile_project(before)
                x,y = lane._points[1]
                time = 2000*(number+1)
                self.quick_second_press(lane, x, y, time)
                self.assertIsNotNone(workspace.state.gesture)
                bounds = lane._drag_bounds
                expected = channel_value(before.keyframes[1],channel)+(bounds.value_at(round(y)-8)-bounds.value_at(round(y)))
                self.event(lane, "<B1-Motion>", x, round(y)-8, time+250)
                self.assertEqual(self.app.project,before, "Draft motion must not create saved intermediate edits")
                self.event(lane, "<ButtonRelease-1>", x, round(y)-8, time+300)
                self.assertAlmostEqual(channel_value(self.app.project.keyframes[1],channel),expected)
                snapshot = self.app._snapshot()
                self.assertAlmostEqual(channel_value(snapshot.keyframes[1],channel),expected)
                self.assertNotEqual(compile_project(snapshot),before_blob)
                from dolly.editor_dispatch import dispatch
                bridge = Mock()
                def submit(_label, operation, complete=None):
                    result = operation()
                    if complete is not None:
                        complete(result)
                    return True
                with patch.object(self.app.controller,"play") as play, \
                        patch.object(self.app.controller,"seek"), \
                        patch.object(self.app.controller,"stop"), \
                        patch.object(self.app,"_submit",side_effect=submit):
                    for cycle in range(2):
                        # Native Play cinematic prepares, then dispatches
                        # PlayPath. Exercise both routes after the real edit.
                        dispatch(self.app,dict(action="prepare_cinematic",value=cycle+1,
                            pose=(self.app.shot_history.revision,0,0,0,0,0,0)),bridge,publish=Mock())
                        dispatch(self.app,dict(action="play_path"),bridge,publish=Mock())
                        playback = play.call_args.args[0]
                        self.assertEqual(playback,snapshot)
                        self.assertNotEqual(compile_project(playback),before_blob)
                        self.assertEqual(play.call_args.kwargs["time"],0.0)
                        self.app.playing = True
                        dispatch(self.app,dict(action="stop"),bridge,publish=Mock())
                        self.app.playing = False
                self.assertEqual(play.call_count,2)
                self.assertEqual(self.app.shot_history.move().project,before)
                self.assertFalse(self.app.shot_history.can_undo)

    def test_quick_second_handle_press_still_authors_one_saved_slope_pair(self):
        workspace = self.app.camera_curve_workspace
        workspace.select_group(0)
        self.root.update()
        lane = workspace.lanes[0]
        # A small window or a scaled display can hide one of the two handles
        # behind a neighbouring key, so exercise whichever were drawn.
        self.assertTrue(lane._handle_points,
                        "no tangent handles were drawn: canvas %sx%s, scale %s" % (
                            lane.canvas.winfo_width(), lane.canvas.winfo_height(),
                            lane.tk.call("tk", "scaling")))
        for side in sorted(lane._handle_points):
            with self.subTest(side=side):
                self.reset_shot()
                workspace = self.app.camera_curve_workspace
                workspace.select_group(0)
                self.root.update()
                lane = workspace.lanes[0]
                before = copy.deepcopy(self.app.project)
                x,y,_delta = lane._handle_points[side]
                self.quick_second_press(lane,x,y,1000)
                self.assertTrue(workspace.state.tangent_current())
                self.event(lane,"<B1-Motion>",x,round(y)-8,1250)
                self.event(lane,"<ButtonRelease-1>",x,round(y)-8,1300)
                self.assertEqual(self.app.project.camera_tangent(1,"x").mode,"broken")
                self.assertNotEqual(compile_project(self.app._snapshot()),compile_project(before))
        self.assertEqual(self.app.shot_history.move().project,before)
        self.assertFalse(self.app.shot_history.can_undo)

    def test_key_drag_number_and_preview_are_private_and_cancel_restores_them(self):
        workspace = self.app.camera_curve_workspace
        workspace.on_preview = preview = Mock()
        workspace.select_group(0)
        self.root.update()
        lane = workspace.lanes[0]
        before = copy.deepcopy(self.app.project)
        x,y = lane._points[1]
        self.event(lane,"<ButtonPress-1>",x,y,1000)
        self.event(lane,"<B1-Motion>",x,round(y)-8,1100)
        draft,gesture = preview.call_args.args
        self.assertIs(gesture,workspace.state.gesture)
        self.assertNotEqual(draft.keyframes[1].x,before.keyframes[1].x)
        self.assertAlmostEqual(float(lane.value_text.get()),draft.keyframes[1].x, places=3)
        self.assertEqual(self.app.project,before)
        self.assertAlmostEqual(float(self.app.key_vars["x"].get()),before.keyframes[1].x, places=3)
        self.event(lane,"<ButtonPress-3>",x,round(y)-8,1150)
        preview.assert_called_with(None,None)
        self.event(lane,"<ButtonRelease-1>",x,round(y)-8,1200)
        self.assertAlmostEqual(float(lane.value_text.get()),before.keyframes[1].x, places=3)
        self.assertEqual(self.app.project,before)
        self.assertFalse(self.app.shot_history.can_undo)

    def test_double_clicking_empty_plot_still_frames_all_without_editing(self):
        workspace = self.app.camera_curve_workspace
        workspace.state.view.zoom(.5,.5,self.app.project.duration)
        workspace.redraw()
        self.root.update()
        lane = workspace.lanes[0]
        before = copy.deepcopy(self.app.project)
        x,y = lane._bounds.left+30,lane._bounds.top+12
        self.quick_second_press(lane,x,y,1000)
        self.event(lane,"<ButtonRelease-1>",x,y,1300)
        self.assertEqual((workspace.state.view.start,workspace.state.view.end),(0,0))
        self.assertEqual(self.app.project,before)
        self.assertFalse(self.app.shot_history.can_undo)


if __name__ == "__main__":
    unittest.main()
