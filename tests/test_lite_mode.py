import math
import unittest
from types import SimpleNamespace
from dolly.lite_mode import launch_options_for_mode, pack_replay_info, REPLAY_INFO
from dolly.path import Project, Keyframe

class LiteModeTests(unittest.TestCase):
    def test_launch_mode_changes_only_the_owned_flag(self):
        self.assertEqual(launch_options_for_mode('-windowed -w 1920', True), '-windowed -w 1920 -dolly-lite')
        self.assertEqual(launch_options_for_mode('-windowed -dolly-lite -w 1920', False), '-windowed -w 1920')
        self.assertEqual(launch_options_for_mode('-dolly-lite -dolly-lite', True), '-dolly-lite')

    def test_live_camera_ticks_use_the_shots_replay_clock(self):
        p=Project(start_tick=3389, tick_rate=64, keyframes=[Keyframe(0,1,2,3,0,0,0),Keyframe(2.0,1,2,3,0,0,0)])
        values=REPLAY_INFO.unpack(pack_replay_info(2,p,7,0,146926))
        self.assertEqual(values[:7], (b'DLYLITE1',2,1,7,0,2,3))
        self.assertEqual(values[7:10],(3389,146926,64.0))
        self.assertEqual(values[10:12],(3389,3517))

    def test_unknown_replay_clock_is_not_replaced_with_sample_values(self):
        p=Project(keyframes=[Keyframe(0,1,2,3,0,0,0)])
        p.start_tick=None
        v=REPLAY_INFO.unpack(pack_replay_info(2,p,7,0,None))
        self.assertEqual(v[6],0)
        self.assertEqual(v[7:10],(-1,-1,0))
        self.assertEqual(v[10],-1)

    def test_extra_actions_are_appended_without_changing_original_ids(self):
        from dolly.editor_wire import EXTRA_ACTIONS
        self.assertEqual(EXTRA_ACTIONS[-18:-1],('save_shot','seek_replay_tick','clear_camera_path','focus_replay_player','prepare_cinematic','set_pause_after_capture','set_camera_position','set_camera_curve_value','select_curve_camera','set_camera_position_tangents','preview_camera_curve_value','preview_camera_position_tangents','clear_camera_curve_preview','level_view','seek_camera','set_video_ae_camera','set_video_usd_camera'))
        # Environment edition: appended last so every earlier action ID is unchanged.
        self.assertEqual(EXTRA_ACTIONS[-1],'capture_environment')
        self.assertEqual(EXTRA_ACTIONS.index('set_framing')+26,40)
        self.assertEqual(EXTRA_ACTIONS.index('save_shot')+26,105)
        self.assertEqual(EXTRA_ACTIONS.index('focus_replay_player')+26,108)
        self.assertEqual(EXTRA_ACTIONS.index('prepare_cinematic')+26,109)
        self.assertEqual(EXTRA_ACTIONS.index('set_pause_after_capture')+26,110)
        self.assertEqual(EXTRA_ACTIONS.index('set_camera_position')+26,111)
        self.assertEqual(EXTRA_ACTIONS.index('set_camera_curve_value')+26,112)
        self.assertEqual(EXTRA_ACTIONS.index('select_curve_camera')+26,113)
        self.assertEqual(EXTRA_ACTIONS.index('set_camera_position_tangents')+26,114)
        self.assertEqual(EXTRA_ACTIONS.index('preview_camera_curve_value')+26,115)
        self.assertEqual(EXTRA_ACTIONS.index('preview_camera_position_tangents')+26,116)
        self.assertEqual(EXTRA_ACTIONS.index('clear_camera_curve_preview')+26,117)
        self.assertEqual(EXTRA_ACTIONS.index('level_view')+26,118)
        self.assertEqual(EXTRA_ACTIONS.index('seek_camera')+26,119)
        self.assertEqual(EXTRA_ACTIONS.index('set_video_ae_camera')+26,120)
        self.assertEqual(EXTRA_ACTIONS.index('set_video_usd_camera')+26,121)

    def test_typed_tick_dispatch_uses_dolly_seek_and_rejects_invalid_input(self):
        from unittest.mock import patch
        from dolly.editor_dispatch import _dispatch as dispatch
        app=SimpleNamespace(busy=False,preview_attach=False,controller=SimpleNamespace(status=lambda:{'playing':False}, seek_replay_tick=lambda value:value))
        bridge=object()
        with patch('dolly.editor_dispatch._native_operation') as run:
            dispatch(app,{'action':'seek_replay_tick','value':3470},bridge,publish=lambda:None)
            self.assertEqual(run.call_args.args[2](),3470)
        for bad in (-1,float('nan'),2.5,2147483648):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dispatch(app,{'action':'seek_replay_tick','value':bad},bridge,publish=lambda:None)

class SeekLifecycleTests(unittest.TestCase):
    def test_absolute_seek_releases_before_seek_and_restores_camera_and_panel(self):
        from tests.test_native_flight_controller import configured_controller
        app, console, bridge = configured_controller()
        app.enter_native_flight()
        pose = list(bridge.pose)
        console.events.clear()
        result = app.seek_replay_tick(3470)
        self.assertLess(console.events.index('native.release'), console.events.index('demo_gototick 3470 0 1'))
        self.assertLess(console.events.index('demo_gototick 3470 0 1'), console.events.index('native.flight'))
        self.assertEqual(bridge.pose, pose)
        self.assertEqual(bridge.owner, 'panel')
        self.assertEqual(result['tick'], 3470)
        from unittest.mock import patch
        with patch.object(app, '_require_demo', return_value={'tick':3470, 'total_ticks':10000}), self.assertRaises(ValueError):
            app.seek_replay_tick(10000)

class ClearCameraTests(unittest.TestCase):
    def test_clear_removes_every_camera_and_preserves_other_tracks(self):
        from unittest.mock import Mock
        from dolly.editor_dispatch import _dispatch
        p=Project(keyframes=[Keyframe(0,1,2,3,0,0,0),Keyframe(2,4,5,6,0,0,0)])
        app=SimpleNamespace(busy=False,playing=False,project=p,shot_history=SimpleNamespace(revision=7),_shot_edit_ready=lambda:True,_commit_project=Mock())
        _dispatch(app,{'action':'clear_camera_path','value':0,'pose':[7,0,0,0,0,0,0]},object(),publish=lambda:None)
        candidate=app._commit_project.call_args.args[0]
        self.assertEqual(candidate.keyframes,[])
        self.assertEqual(len(p.keyframes),2)
        self.assertEqual(app._native_camera_page,0)
        with self.assertRaises(ValueError):
            _dispatch(app,{'action':'clear_camera_path','value':0,'pose':[6,0,0,0,0,0,0]},object(),publish=lambda:None)

if __name__=='__main__':unittest.main()
