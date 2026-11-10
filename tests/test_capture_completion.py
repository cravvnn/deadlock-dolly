"""Owned completion/retry contracts; console, encoder and I/O faults controlled."""
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui, movie_audio, player_encode, player_layer, player_scratch, video_export
from dolly.gui import DollyApp
from dolly.ui.layer_pipeline import LayerPipeline
from dolly.ui.operations import OperationQueue


class CaptureCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-completion-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_sample_map_bounds_actual_read_and_keeps_exact_timing_identity(self):
        video = self.root / 'color.mov'
        sidecar = self.root / 'shot.json'
        data = dict(format='deadlock-dolly-shot', version=1, video_file=video.name,
                    fps=60, fixed_step=True, frames_written=2,
                    movie_audio=dict(version=1, complete=True,
                        frames=[[1, 0, 735, 0, 44100], [1, 735, 1470, 1, 44100]]))
        status = dict(frames_written=2, fps=60, duration=2/60)
        read = lambda: movie_audio._movie_sample_windows(video, status, 'dolly_audio_1')
        raw = json.dumps(data).encode('utf-8')
        sidecar.write_bytes(raw)
        self.assertEqual(read(), (48000, [(0, 800), (800, 1600)]))
        for changes in ({'video_file': 'foreign.mov'}, {'fixed_step': False}, {'frames_written': 3}):
            sidecar.write_text(json.dumps({**data, **changes}), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                read()
        nested = b'[' * 10000 + b']' * 10000
        sidecar.write_bytes(nested)
        with self.assertRaises(RuntimeError) as caught:
            read()
        self.assertIs(type(caught.exception), RuntimeError)
        self.assertIsInstance(caught.exception.__cause__, RecursionError)
        self.assertEqual(sidecar.read_bytes(), nested)
        sidecar.write_bytes(raw)
        requested = []
        class Growing(io.BytesIO):
            def read(self, n=-1):
                requested.append(n)
                return b'x' * n
        real_open = Path.open
        def opened(path, *args, **kwargs):
            return Growing() if path == sidecar else real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', opened), self.assertRaises(RuntimeError):
            read()
        self.assertEqual(requested, [40 * 1024 * 1024 + 1])
        self.assertEqual(sidecar.read_bytes(), raw)

    def test_movie_cancel_requires_stop_ack_then_retires_authority_once(self):
        root = self.root / 'movie'
        root.mkdir()
        owner = movie_audio.MovieWave(self.root / 'color.mp4',
            self.root / 'game/bin/win64/deadlock.exe',
            output_context=movie_audio.MovieOutputContext(root, lambda controller: None))
        folder = root / '2026_11_10_12_00_00'
        responses = [TimeoutError('owned acknowledgement lost'), '', 'Finishing movie']
        stops = []
        def request(command, **kwargs):
            if command.startswith('startmovie '):
                folder.mkdir()
                (folder / (owner.name + '.wav')).write_bytes(b'owned WAV')
                (folder / 'peer.wav').write_bytes(b'peer WAV')
                return 'Started recording movie: movie/2026_11_10_12_00_00/'
            if command == 'endmovie':
                stops.append(command)
                result = responses.pop(0) if responses else 'Finishing movie'
                if isinstance(result, Exception):
                    raise result
                return result
            return ''
        controller = SimpleNamespace(game_pid=lambda: None, _request=request,
            _native_bridge=lambda: None, status=lambda: dict(time=1))
        owner.start(controller, 60)
        wav = folder / (owner.name + '.wav')
        for source in (None, controller, controller):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                owner.cancel(source)
            self.assertEqual(wav.read_bytes(), b'owned WAV')
            self.assertEqual(owner.folder, folder)
            self.assertFalse(owner.finished)
        self.assertEqual(len(stops), 2)
        owner.cancel(controller)
        owner.cancel(controller)
        self.assertEqual(len(stops), 3)
        self.assertTrue(owner.finished)
        self.assertIsNone(owner.folder)
        self.assertFalse(wav.exists())
        self.assertEqual((folder / 'peer.wav').read_bytes(), b'peer WAV')

    def test_optional_depth_probe_keeps_completed_take_and_success_cleanup(self):
        depth = self.root / 'depth'
        depth.mkdir()
        output, master = self.root / 'color.mp4', depth / 'depth.mov'
        output.write_bytes(b'color master')
        master.write_bytes(b'depth master')
        raw, target = depth / 'preview_2x2.raw', depth / 'preview.mp4'
        raw.write_bytes(b'1234')
        status = dict(state='completed', frames_written=2)
        def owner():
            value = video_export.VideoExport(SimpleNamespace())
            value.output_path = output
            value._depth_options = video_export.VideoOptions(output, codec='builtin', depth=True)
            return value
        def encode(*args, **kwargs):
            target.write_bytes(b'controlled preview result')
            return SimpleNamespace(returncode=0, stderr='')
        real_is_file = Path.is_file
        def checked(path):
            if path == target:
                raise PermissionError('owned optional result lookup denied')
            return real_is_file(path)
        value = owner()
        with patch.object(video_export, 'resolve_ffmpeg', return_value=Path(sys.executable)), \
                patch.object(video_export.subprocess, 'run', side_effect=encode):
            with patch.object(Path, 'is_file', checked), self.assertLogs('dolly', level='WARNING'):
                self.assertEqual(value._finish_terminal(status, cancel=False), status)
            self.assertEqual(value._finished_status, status)
            self.assertEqual(raw.read_bytes(), b'1234')
            success = owner()._finish_terminal(status, cancel=False)
        self.assertEqual(success['depth_preview'], str(target))
        self.assertFalse(raw.exists())
        self.assertEqual(output.read_bytes(), b'color master')
        self.assertEqual(master.read_bytes(), b'depth master')

    def test_players_outcome_survives_status_write_error_but_admission_stays_strict(self):
        real_update = player_scratch.update
        for mode in ('failed', 'cancelled', 'admission'):
            with self.subTest(mode=mode):
                root = self.root / mode
                deployment, scratch = root / 'deploy', root / 'scratch'
                deployment.mkdir(parents=True)
                scratch.mkdir()
                player_layer.begin_capture(deployment, 8, 2, scratch_root=scratch,
                    output=root / 'take/players/players.mov')
                folder = player_scratch.from_marker(deployment)
                bundle = folder / player_layer.BUNDLE_NAME
                bundle.write_bytes(b'owned raw capture')
                app = SimpleNamespace(_still_run=None,
                    _base_capture=video_export.VideoOptions(root / 'take.mp4', ffmpeg_path=Path(sys.executable)),
                    controller=SimpleNamespace(deployment_directory=lambda: deployment))
                primary = InterruptedError('owned cancellation') if mode == 'cancelled' else ValueError('owned encode failure')
                denied = PermissionError('owned scratch status denied')
                def update(folder, state, **kwargs):
                    if state != 'captured' or mode == 'admission':
                        raise denied
                    return real_update(folder, state, **kwargs)
                with patch.object(player_layer, 'wait_for_capture', return_value='complete'), \
                        patch.object(gui, 'resolve_ffmpeg', return_value=Path(sys.executable)), \
                        patch.object(player_scratch, 'update', side_effect=update), \
                        patch.object(player_encode, 'encode_in_worker', side_effect=primary) as encode:
                    if mode == 'admission':
                        with self.assertRaises(PermissionError) as caught:
                            DollyApp._finish_player_capture(app, 'players')
                        self.assertIs(caught.exception, denied)
                        encode.assert_not_called()
                    else:
                        with self.assertLogs('dolly', level='WARNING'):
                            if mode == 'cancelled':
                                result = DollyApp._finish_player_capture(app, 'players')
                                self.assertEqual(result['state'], 'cancelled')
                            else:
                                with self.assertRaises(ValueError) as caught:
                                    DollyApp._finish_player_capture(app, 'players')
                                self.assertIs(caught.exception, primary)
                        self.assertIsNone(app._player_encode_cancel)
                self.assertEqual(bundle.read_bytes(), b'owned raw capture')

    def test_layer_queue_retries_only_confirmed_nonadmission(self):
        for kind in ('capture', 'combine', 'take', 'restore'):
            for outcome in ('raised', 'ambiguous'):
                with self.subTest(kind=kind, outcome=outcome):
                    jobs = OperationQueue()
                    token = object()
                    pipeline = LayerPipeline(advance_pending=True)
                    field = dict(capture='pending_capture', combine='pending_combine',
                                 take='queue', restore='base_capture')[kind]
                    original = [token] if kind == 'take' else token
                    setattr(pipeline, field, original)
                    primary = RuntimeError('owned admission acknowledgement lost')
                    refuse = [outcome == 'raised']
                    calls = []
                    def started(label):
                        if refuse[0]:
                            raise primary
                    if outcome == 'ambiguous':
                        class Published(queue.Queue):
                            def put(self, item, *args, **kwargs):
                                super().put(item, *args, **kwargs)
                                if item is not None:
                                    raise primary
                        jobs.jobs = Published()
                    app = SimpleNamespace(layer_pipeline=pipeline, operations=jobs,
                        _still_run=None, video_export=SimpleNamespace(status=lambda: dict(state='idle')),
                        _finish_player_capture=lambda value: calls.append(value),
                        _combine_layer=lambda value: calls.append(value),
                        _start_next_layer_take=lambda: calls.append(token),
                        _video_operation_done=lambda value: None, _audit_finished_layers=lambda value: None,
                        _restore_scene_state=lambda: calls.append(token))
                    app._submit = lambda label, function, callback: jobs.submit(label, function,
                        callback, started=started, rejected=lambda message: None)
                    def advance():
                        app.busy = jobs.busy
                        DollyApp._advance_layer_pipeline(app)
                    with self.assertRaises(RuntimeError) as caught:
                        advance()
                    self.assertIs(caught.exception, primary)
                    if outcome == 'raised':
                        self.assertFalse(jobs.busy)
                        self.assertTrue(jobs.jobs.empty())
                        self.assertTrue(pipeline.advance_pending)
                        self.assertIs(getattr(pipeline, field), original)
                        refuse[0] = False
                        advance()
                    self.assertTrue(jobs.busy)
                    self.assertFalse(pipeline.advance_pending)
                    if kind != 'take':
                        self.assertIsNone(getattr(pipeline, field))
                    self.assertEqual(jobs.jobs.qsize(), 1)
                    advance()
                    self.assertEqual(jobs.jobs.qsize(), 1)
                    worker = jobs.start(lambda: 'error')
                    try:
                        self.assertEqual(jobs.events.get(timeout=2)[0], 'done')
                        self.assertEqual(calls, [token])
                    finally:
                        jobs.shutdown()
                        worker.join(timeout=2)
                        self.assertFalse(worker.is_alive())
