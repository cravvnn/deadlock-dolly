"""Camera/capture owner contracts; simulated game/bridge and owned files only."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

from dolly.native_bridge import NativeBridge, NativeBridgeError
import test_core_native as native_cases


def check_file_ledger(root, data):
    sys.path.insert(0, str(root / 'tools'))
    from release_files import source_files
    exported = source_files(root)
    code_suffixes = {'.py', '.cpp', '.hpp', '.h', '.c', '.cc', '.cs', '.go', '.rs',
                     '.mjs', '.js', '.ts', '.spec', '.cmd', '.bat', '.ps1', '.sh',
                     '.fx', '.fxh', '.hlsl', '.vfx'}
    units = {p.relative_to(root).as_posix() for p in exported
             if p.suffix.lower() in code_suffixes
             and not any(part in ('tests', 'vendor', 'third_party')
                         for part in p.relative_to(root).parts)}
    rows = data['files']
    assert len(rows) == len(units) and {row['path'] for row in rows} == units, 'file ledger lost implementation accounting'
    assert data['unclassified'] == 0 and not data['full_program_formal_verified']
    census = data['export_census']
    assert len(census) == len(exported) and {r['path'] for r in census} == {p.relative_to(root).as_posix() for p in exported}, 'export census lost a source file'
    for row in rows:
        assert row['disposition'] and row['reason'] and row['family']
        normalized = (root / row['path']).read_text(encoding='utf-8-sig').encode('utf-8')
        assert hashlib.sha256(normalized).hexdigest() == row['sha256'], 'reconcile changed owner disposition: ' + row['path']
        for scope in row['bounded_models']:
            assert (root / scope['spec']).is_file()
            assert all((root / config).is_file() for config in scope['configs'])
    by_path = {row['path']: row for row in rows}
    actual_models = {}
    for config in (p for p in exported if p.suffix == '.cfg'):
        names = sorted((p.stem for p in exported if p.suffix == '.tla'
                        and p.parent == config.parent), key=len, reverse=True)
        name = next((n for n in names if config.stem == n or config.stem.startswith(n + '-')), None)
        if name:
            spec = (config.parent / (name + '.tla')).relative_to(root).as_posix()
            actual_models.setdefault(spec, set()).add(config.relative_to(root).as_posix())
    assert actual_models == {s: set(v['configs']) for s, v in data['models'].items()}, 'reconcile model owner/config accounting'
    for model in data['models'].values():
        assert model['owner_utf8_lf_sha256'] == by_path[model['path']]['sha256'], 'reconcile model/code owner fingerprint'


class CameraSessionContracts(unittest.TestCase):
    def test_file_ledger_accounts_for_current_implementation_inputs(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / 'docs/internal/FORMAL_COVERAGE.json').read_text(encoding='utf-8'))
        check_file_ledger(root, data)

    def test_prepared_take_requires_matching_identity_readiness_and_single_use(self):
        for mode in ('ready', 'starting-ready', 'failed', 'idle', 'cancelled', 'timeout',
                     'foreign-session', 'foreign-replay', 'foreign-tick', 'native-unpaused'):
            with self.subTest(mode=mode):
                case = native_cases.NativeWorkflows()
                case.setUp()
                try:
                    case.prepared_recording()
                    owner = case.controller
                    owner._recording_warmup = None
                    case.bridge.video_status.reset_mock()
                    if mode == 'starting-ready':
                        case.bridge.video_status.side_effect = [{'state': 'starting'}, {'state': 'recording'}]
                    elif mode in ('failed', 'idle'):
                        case.bridge.video_status.return_value = {'state': mode, 'error': 'owned fixture failure'}
                    elif mode in ('cancelled', 'timeout'):
                        case.bridge.video_status.return_value = {'state': 'starting'}
                        if mode == 'cancelled':
                            owner._stop_event.set()
                    elif mode == 'foreign-session':
                        owner._recording_replay = (object(), str(owner._demo), 100)
                    elif mode == 'foreign-replay':
                        owner._recording_replay = (case.session, 'other.dem', 100)
                    elif mode == 'foreign-tick':
                        owner._recording_replay = (case.session, str(owner._demo), 101)
                    elif mode == 'native-unpaused':
                        case.console.paused = False
                    before = case.bridge.events.count('native.play')
                    if mode in ('ready', 'starting-ready'):
                        owner._prepare_native_shot_replay(case.project, 0)
                        with self.assertRaisesRegex(RuntimeError, 'unused prepared shot'):
                            owner._prepare_native_shot_replay(case.project, 0)
                    else:
                        with self.assertRaises(RuntimeError):
                            owner._prepare_native_shot_replay(case.project, 0)
                    self.assertIsNone(owner._recording_replay)
                    self.assertEqual(case.bridge.events.count('native.play'), before)
                    if mode == 'starting-ready':
                        self.assertGreaterEqual(case.bridge.video_status.call_count, 2)
                finally:
                    case.doCleanups()

    def test_release_matches_ack_and_retains_lease_until_success(self):
        for label, packets, accepted in (
            ('old fault', [dict(ack_command=1, state='fault'), dict(ack_command=2, state='stopped')], True),
            ('old stopped', [dict(ack_command=1, state='stopped'), dict(ack_command=2, state='stopped')], True),
            ('current fault', [dict(ack_command=2, state='fault')], False),
            ('unsupported session', [dict(ack_command=1, state='unsupported')], False)):
            with self.subTest(label=label):
                clock, reads = [0.0], []
                queue = iter(packets)

                def read():
                    value = next(queue)
                    reads.append(value)
                    return value

                bridge = SimpleNamespace(status=read, _clock=lambda: clock[0],
                    _sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
                if accepted:
                    NativeBridge._wait(bridge, 2, {'stopped', 'probe'}, 1)
                else:
                    with self.assertRaises(NativeBridgeError):
                        NativeBridge._wait(bridge, 2, {'stopped', 'probe'}, 1)
                self.assertEqual(len(reads), len(packets))
        case = native_cases.NativeWorkflows()
        case.setUp()
        try:
            owner = case.controller
            owner._native_active = owner._native_manual = True
            with patch.object(case.bridge, 'release', side_effect=RuntimeError('owned release failure')):
                with self.assertRaisesRegex(RuntimeError, 'owned release failure'):
                    owner._release_native_camera()
            self.assertTrue(owner._native_active and owner._native_manual)
            owner._release_native_camera()
            self.assertFalse(owner._native_active or owner._native_manual)
        finally:
            case.doCleanups()

    def test_worker_join_and_release_refusal_retain_camera_for_retry(self):
        for mode in ('join-refusal', 'release-refusal', 'dead-process', 'ordinary'):
            with self.subTest(mode=mode):
                case = native_cases.NativeWorkflows()
                case.setUp()
                try:
                    owner = case.controller
                    owner._native_active = owner._native_manual = True
                    owner._thread = case.worker
                    case.worker.is_alive.return_value = mode == 'join-refusal'
                    before = len(case.bridge.events)
                    if mode == 'join-refusal':
                        with self.assertRaisesRegex(RuntimeError, 'still waiting'):
                            owner._halt(native_action='release')
                        self.assertIs(owner._thread, case.worker)
                        self.assertTrue(owner._native_active and owner._native_manual)
                        self.assertNotIn('native.release', case.bridge.events[before:])
                        case.worker.is_alive.return_value = False
                    elif mode == 'release-refusal':
                        with patch.object(case.bridge, 'release', side_effect=RuntimeError('owned refusal')):
                            with self.assertRaisesRegex(RuntimeError, 'owned refusal'):
                                owner._halt(native_action='release')
                        self.assertIsNone(owner._thread)
                        self.assertTrue(owner._native_active and owner._native_manual)
                    elif mode == 'dead-process':
                        case.process.poll = lambda: 0
                    owner._halt(native_action='release')
                    self.assertIsNone(owner._thread)
                    self.assertFalse(owner._native_active or owner._native_manual)
                    if mode == 'dead-process':
                        self.assertNotIn('native.release', case.bridge.events[before:])
                finally:
                    case.doCleanups()

    def test_completed_players_playback_defers_camera_restoration(self):
        case = native_cases.NativeWorkflows()
        case.setUp()
        try:
            case.start()
            owner = case.controller
            case.bridge.state = 'completed'
            owner._player_capture_pending = True
            commands, events = len(case.console.requests), len(case.bridge.events)
            self.assertIsNone(owner._finish_native_playback())
            self.assertTrue(owner._native_active)
            self.assertFalse(owner.status()['playing'])
            self.assertEqual(len(case.console.requests), commands)
            self.assertEqual(len(case.bridge.events), events)
            self.assertTrue(owner._native_handoff_details['pending'])
            owner._player_capture_pending = False
            case.bridge.fail_handoff = True
            owner._finish_native_playback()
            self.assertGreater(len(case.console.requests), commands)
        finally:
            case.doCleanups()

    def test_capture_ack_identity_and_console_readiness_precede_retirement(self):
        from dolly import player_layer
        from dolly.console import ConsoleTimeout
        from dolly.video_export import VideoExport, VideoOptions
        for mode in ('no-ack', 'post-ack-replacement', 'console-timeout'):
            with self.subTest(mode=mode):
                case = native_cases.NativeWorkflows()
                case.setUp()
                try:
                    owner = case.controller
                    owner._state['startup_stage'] = 'editing_ready'
                    case.console.values.update(host_framerate=90, r_wait_on_present=0, r_aspectratio=1.5)
                    directory = owner.deployment_directory()
                    directory.mkdir(parents=True)
                    export = VideoExport(owner)
                    export.start(VideoOptions(case.root / 'players.mp4', fixed_step=True),
                                 frozen=True, capture_only=True,
                                 before_record=lambda: player_layer.begin_capture(directory, 48, 302))
                    case.start()
                    case.bridge.state = 'completed'
                    owner._finish_native_playback()
                    original, reads = case.console.request, []

                    def request(command, **kwargs):
                        if mode == 'console-timeout' and command == 'demo_goto':
                            reads.append(command)
                            raise ConsoleTimeout('owned busy console')
                        return original(command, **kwargs)

                    def wait(*args, **kwargs):
                        if mode == 'post-ack-replacement':
                            owner._demo = case.root / 'other.dem'
                        return 'armed' if mode == 'no-ack' else 'complete'

                    with patch('dolly.video_export.player_layer.wait_for_capture', side_effect=wait), \
                         patch.object(case.console, 'request', side_effect=request):
                        with self.assertRaises(RuntimeError):
                            export.stop(cancel=True)
                    self.assertTrue(owner._player_capture_pending and export._capture_only)
                    self.assertTrue(owner._native_active)
                    self.assertEqual(case.console.values['host_framerate'], 60)
                    if mode == 'console-timeout':
                        self.assertEqual(len(reads), 3)
                finally:
                    case.doCleanups()
