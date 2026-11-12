"""Headless Follow ownership and authored bone-camera checks."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core_helpers import FakeConsole
from dolly.bone_picker import timed_attachment
from dolly.controller import Controller
from dolly.follow_camera import BOUNDS, ENABLED, PREFIX, FollowSettings
from dolly.path import AttachKey, Keyframe, Project


def exercise_camera_modes(case, root):
    console = FakeConsole()
    console.values.update({PREFIX + name: float((low + high) / 2)
                           for name, (low, high) in BOUNDS.items()})
    console.values.update({ENABLED: 0., 'citadel_camera_spectator_auto_target_view': 0.,
                           'citadel_spectator_mode': 0., PREFIX + 'fov': 90., PREFIX + 'ads_fov': 45.})
    console.values.update({PREFIX + 'pivot_z_offset': 86., PREFIX + 'pivot_z_offset_crouching': 55.})
    player = {'handle': 0x10002, 'entity_index': 2, 'model': 0x123456789abcdef,
              'model_path': 'models/heroes/frank/frank.vmdl'}

    class Bridge:
        def __init__(self):
            self.owner = 'game_ui'
            self.roster = [dict(player)]
        def status(self):
            return {'state': 'probe', 'frame_count': 1, 'tick': console.tick,
                    'paused': console.paused, 'demo_name': console.demo_name}
        def video_status(self):
            return {'state': 'idle'}
        def editor_roster(self):
            return {'available': True, 'players': list(self.roster)}
        def require_capability(self, capability):
            case.assertEqual(capability, 'follow')
        def configure_editor(self, **values):
            self.owner = values.get('owner', self.owner)
            console.events.append('input:' + self.owner)

    bridge = Bridge()
    controller = Controller()
    controller._console = console
    controller._session = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), pid=1234,
        session_dir=Path('unused'), owns_console_port=lambda: True, native=bridge)
    controller._demo = Path('/chosen/example.dem')
    controller._unlocker_pid = 1234
    controller._probe_result = {'capabilities': {'spec_goto': True, 'cl_citadel_forceangles': True,
                                                  'r_aspectratio': True}}
    controller._game_ui_visible = True
    original = dict(console.values)
    monitor = Mock()
    monitor.sample.side_effect = lambda: {name: {'value': value} for name, value in console.values.items()}
    target = Mock()
    target.sample_target.return_value = {'handle': player['handle'], 'mode': 2}
    target.sample_health_hud_context.return_value = {'handle': player['handle']}
    with patch('dolly.follow_capabilities.FollowCapabilityMonitor', return_value=monitor), \
         patch('dolly.follow_target.FollowTargetMonitor', return_value=target), \
         patch('dolly.replay_camera.ReplayCameraMonitor.sample', return_value={'ready': True}):
        # A stale serial with the same entity index must never target anyone.
        bridge.roster = [{**player, 'handle': 0x18002}]
        before = list(console.events)
        with case.assertRaisesRegex(RuntimeError, 'current roster'):
            controller.start_selected_game_follow(FollowSettings(distance=250), player)
        case.assertEqual(console.values, original)
        case.assertFalse(any(event.startswith('spec_target ') for event in console.events[len(before):]))
        target.sample_target.assert_not_called()
        bridge.roster = [dict(player)]
        controller.start_selected_game_follow(FollowSettings(distance=250), player)
        case.assertTrue(controller._follow_active)
        case.assertIn('spec_target 2', console.events)
        case.assertEqual(console.values['citadel_spectator_mode'], 2)
        case.assertEqual(console.values[ENABLED], 1)
        case.assertEqual(console.values[PREFIX + 'x_offset'], -250)
        case.assertEqual(console.values[PREFIX + 'pivot_z_offset_crouching'], 86.)
        # The engine applies weapon zoom itself; ADS starts from the normal lens.
        case.assertEqual(console.values[PREFIX + 'ads_fov'] - 40, 50)
        case.assertEqual(bridge.owner, 'panel')
        case.assertEqual(target.sample_target.call_count, 2)
        target.close.assert_called_once()
        # Return directly from an active rig, as F9 does. Stopping Follow
        # separately would hide a missing restore in the UI handoff itself.
        handoff_start = len(console.events)
        controller.toggle_game_ui(True)
        case.assertFalse(controller._follow_active)
        case.assertEqual(console.values['citadel_spectator_mode'], original['citadel_spectator_mode'])
        case.assertEqual(console.values[ENABLED], original[ENABLED])
        for name in BOUNDS:
            case.assertEqual(console.values[PREFIX + name], original[PREFIX + name])
        case.assertEqual(bridge.owner, 'game_ui')
        case.assertIsNone(controller._game_hero_restore)
        case.assertIn(ENABLED + ' 0', console.events[handoff_start:])
        # Switching to a second roster pawn near replay start must preserve the
        # selected paused tick and send that pawn's actual entity index.
        other = {**player, 'handle': 0x10003, 'entity_index': 3}
        console.tick = 1
        bridge.roster = [dict(other)]
        target.sample_target.return_value = {'handle': other['handle'], 'mode': 2}
        target.sample_health_hud_context.return_value = {'handle': other['handle']}
        switch_start = len(console.events)
        request = console.request
        resumed = False
        def advance_blend(command, *args, **kwargs):
            nonlocal resumed
            result = request(command, *args, **kwargs)
            if command == 'demo_resume':
                resumed = True
                console.tick = 10
            return result
        def camera_state(_monitor):
            return {'coherent': True, 'ready': resumed, 'blending': not resumed}
        console.request = advance_blend
        with patch('dolly.replay_camera.ReplayCameraMonitor.sample', side_effect=camera_state):
            controller.start_selected_game_follow(FollowSettings(), other)
        case.assertTrue(resumed)
        case.assertEqual(console.tick, 1)
        case.assertTrue(console.paused)
        events = console.events[switch_start:]
        case.assertIn('spec_target 3', events)
        case.assertLess(events.index('demo_resume'), events.index('demo_gototick 1 0 1'))
        case.assertLess(events.index('demo_gototick 1 0 1'), events.index(ENABLED + ' 1'))
        controller.toggle_game_ui(True)
        case.assertEqual(console.values[PREFIX + 'ads_fov'], 45)

    # A timed take duplicates a validated attached view without changing the
    # authored camera. Save/load checks its identity, offset and bone fields.
    attach = AttachKey(handle=0x10002, entity_id=2, model=player['model_path'], point='bone',
                       bone='head', offset=(1, 2, 3, 4, 5, 6), smoothing=.25,
                       hide_body=True, clearance_mode='auto')
    authored = Project(name='Bone view', start_tick=128, tick_rate=64,
                       keyframes=[Keyframe(0, 10, 20, 30, 1, 2, 3, source='attach', attach=attach)])
    before = deepcopy(authored)
    take = timed_attachment(authored, 2.5)
    case.assertEqual(authored, before)
    case.assertEqual([k.time for k in take.keyframes], [0, 2.5])
    case.assertEqual(take.duration, 2.5)
    case.assertIsNot(take.keyframes[0].attach, take.keyframes[1].attach)
    path = Path(root) / 'bone.dolly.json'
    take.save(path)
    loaded = Project.load(path)
    for key in loaded.keyframes:
        case.assertEqual((key.source, key.attach.handle, key.attach.entity_id, key.attach.model,
                          key.attach.point, key.attach.bone, key.attach.offset,
                          key.attach.smoothing, key.attach.hide_body, key.attach.clearance_mode),
                         ('attach', 0x10002, 2, player['model_path'], 'bone', 'head',
                          (1, 2, 3, 4, 5, 6), .25, True, 'auto'))
    for duration in (0, 121, float('nan')):
        with case.subTest(duration=duration), case.assertRaises(ValueError):
            timed_attachment(authored, duration)
    case.assertEqual(authored, before)
