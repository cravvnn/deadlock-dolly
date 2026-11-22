"""Export finishing: movie-audio ownership, ffmpeg text, timeouts and partial files.

FFmpeg is replaced by a controlled Python child or a controlled result; no
game, encoder or network is used.
"""
import inspect
import json
import shutil
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from dolly import clip_audio, gui, movie_audio, video_export
from dolly.console import ConsoleTimeout
from dolly.gui import DollyApp
from dolly.video_export import VideoExport, VideoOptions

REAL_RUN = subprocess.run
# Bytes no Windows ANSI code page or strict UTF-8 decoder accepts as-is
# (an FFmpeg error naming a non-ASCII take folder decoded the wrong way).
BAD_STDERR = "import sys; sys.stderr.buffer.write(b'\\xd0\\x90 \\x81\\x8d\\x9d\\xff error'); sys.exit(1)"


def ffmpeg_child(calls, *, partial=True):
    """Run the real subprocess.run with a controlled failing child instead of FFmpeg."""
    def run(command, **options):
        calls.append((list(command), options))
        if partial:
            Path(command[-1]).write_bytes(b'partial output')
        return REAL_RUN([sys.executable, '-c', BAD_STDERR], **options)
    return run


def timeout_child(calls, *, partial=True):
    def run(command, **options):
        calls.append((list(command), options))
        if partial:
            Path(command[-1]).write_bytes(b'partial output')
        raise subprocess.TimeoutExpired(command, options.get('timeout'))
    return run


class MovieController:
    def __init__(self, root, endmovie):
        self.root, self.endmovie, self.sent = root, list(endmovie), []

    def game_pid(self):
        return None

    def _native_bridge(self):
        return None

    def status(self):
        return {'time': 12.0}

    def _request(self, command, timeout=None, allow_error=False):
        self.sent.append(command)
        if command.startswith('startmovie'):
            (self.root / '2026_11_18_00_00_00').mkdir(exist_ok=True)
            return 'Started recording movie: movie/2026_11_18_00_00_00/'
        if command == 'endmovie':
            outcome = self.endmovie.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return ''


class ExportHardeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-export-hardening-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_stop_waits_at_least_as_long_as_native_finalization(self):
        header = (Path(__file__).resolve().parents[1] / 'native' / 'include'
                  / 'dolly_video_math.hpp').read_text(encoding='utf-8')
        match = re.search(r'kFinalizeHardCapMs = (\d+) \* (\d+) \* (\d+)', header)
        self.assertIsNotNone(match)
        native_ms = int(match[1]) * int(match[2]) * int(match[3])
        self.assertEqual(video_export.NATIVE_FINALIZE_LIMIT_S * 1000, native_ms)
        default = inspect.signature(VideoExport.stop).parameters['timeout'].default
        self.assertGreater(default, video_export.NATIVE_FINALIZE_LIMIT_S)

    # D1 ---------------------------------------------------------------
    def movie_take(self, endmovie):
        movie_root = self.root / 'movie'
        movie_root.mkdir()
        controller = MovieController(movie_root, endmovie)
        context = movie_audio.MovieOutputContext(movie_root, verify_owner=lambda owner: None)
        wave = movie_audio.MovieWave(self.root / 'take' / 'clip.mp4',
                                     self.root / 'game/bin/win64/deadlock.exe', output_context=context)
        wave.start(controller, 60)
        audio = NS(movie=wave,
                   finish=lambda status: wave.finish(controller, 1.0, self.root),
                   cancel=lambda: wave.cancel(controller))
        return controller, wave, audio

    def app_for(self, audio, state='completed'):
        exporter = NS(stop=lambda cancel=False: {'state': state, 'duration': 1.0})
        return NS(busy=False, video_export=exporter, clip_audio=audio, controller=NS(status=lambda: {}),
                  _video_operation_done=lambda status: None,
                  _submit=lambda label, operation, callback: operation())

    def test_finish_keeps_movie_audio_owner_until_endmovie_is_confirmed(self):
        controller, wave, audio = self.movie_take(
            [ConsoleTimeout('console busy'), 'Finishing movie take'])
        app = self.app_for(audio)
        with self.assertRaises(ConsoleTimeout):
            DollyApp._stop_video_recording(app)
        self.assertIs(app.clip_audio, audio)
        self.assertTrue(wave.started and not wave.finished)
        # A second Finish sends endmovie again; once the engine confirmed the
        # stop, a later conversion failure no longer keeps the owner.
        with self.assertRaisesRegex(RuntimeError, 'WAV was not written'):
            DollyApp._stop_video_recording(app)
        self.assertEqual(controller.sent.count('endmovie'), 2)
        self.assertTrue(wave.finished)
        self.assertIsNone(app.clip_audio)

    def test_discard_keeps_movie_audio_owner_until_endmovie_is_confirmed(self):
        controller, wave, audio = self.movie_take(
            [ConsoleTimeout('console busy'), 'Finishing movie take'])
        app = self.app_for(audio, state='cancelled')
        with self.assertRaisesRegex(RuntimeError, 'stop was not confirmed'):
            DollyApp._stop_video_recording(app, cancel=True)
        self.assertIs(app.clip_audio, audio)
        DollyApp._stop_video_recording(app, cancel=True)
        self.assertEqual(controller.sent.count('endmovie'), 2)
        self.assertIsNone(app.clip_audio)

    def polling_app(self, audio, state='completed'):
        class Widget:
            def __init__(self):
                self.options = {'state': 'disabled', 'text': ''}
            def cget(self, name):
                return self.options[name]
            def configure(self, **options):
                self.options.update(options)
        class Var:
            value = ''
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
        app = self.app_for(audio, state)
        app.video_export.status = lambda: {'state': state}
        app.video_export.finish_pending = False
        names = ('video_start_button', 'video_stop_button', 'video_cancel_button', 'video_path_entry',
                 'video_browse_button', 'video_fps_combo', 'video_bitrate_combo',
                 'video_game_audio_check', 'video_reconstructed_audio_check',
                 'reshade_configure_button', 'reshade_forget_button')
        for name in names:
            setattr(app, name, Widget())
        app.video_status_text, app.status_text = Var(), Var()
        app.controller, app.playing, app._layer_queue = NS(status=lambda: {}), False, []
        app._last_video_state = 'recording'
        app._refresh_reshade = lambda *args: None
        app._advance_layer_pipeline = lambda: None
        app.errors, app.stops = [], []
        app._error = lambda title, exc: app.errors.append(str(exc))
        app._log = lambda text: None
        real_stop = app._stop_video_recording = lambda cancel=False: (
            app.stops.append(cancel), DollyApp._stop_video_recording(app, cancel=cancel))
        return app, real_stop

    def test_unconfirmed_movie_stop_stops_retrying_after_three_tries(self):
        for state in ('completed', 'failed'):
            with self.subTest(state=state):
                for path in self.root.iterdir():
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                controller, wave, audio = self.movie_take(
                    [ConsoleTimeout('console busy')] * 3 + ['Finishing movie take'])
                app, _stop = self.polling_app(audio, state)
                status = {'playing': False, 'game_running': True}
                # Each poll retries until the third unconfirmed endmovie.
                for attempt in range(1, 4):
                    with self.assertRaises((ConsoleTimeout, RuntimeError)) as caught:
                        DollyApp._refresh_video(app, status)
                    self.assertEqual(len(app.stops), attempt)
                    self.assertIs(app.clip_audio, audio)
                    self.assertTrue(wave.started and not wave.finished)
                self.assertRegex(str(caught.exception), 'stopped retrying.*Finish recording')
                # The original engine failure stays attached for the log.
                self.assertIn(str(caught.exception.__cause__),
                              ('console busy', 'Movie audio stop was not confirmed; its WAV was kept.'))
                self.assertEqual(controller.sent.count('endmovie'), 3)
                # No more automatic attempts or repeated errors; ownership is kept and
                # Finish stays available for the user's own retry.
                for _ in range(5):
                    DollyApp._refresh_video(app, status)
                self.assertEqual((len(app.stops), app.errors), (3, []))
                self.assertIs(app.clip_audio, audio)
                self.assertEqual(app.video_stop_button.cget('state'), 'normal')
                # A user retry that the engine confirms releases the owner (this
                # fixture wrote no WAV, so a completed take then reports that).
                if state == 'completed':
                    with self.assertRaisesRegex(RuntimeError, 'WAV was not written'):
                        DollyApp._stop_video_recording(app)
                else:
                    DollyApp._stop_video_recording(app)
                self.assertTrue(wave.finished)
                self.assertIsNone(app.clip_audio)
                self.assertFalse(gui.movie_stop_held(app))
                self.assertEqual(controller.sent.count('endmovie'), 4)

    # D2 / D5: movie audio and mux --------------------------------------
    def finished_wave(self):
        movie_root = self.root / 'movie'
        movie_root.mkdir()
        controller = MovieController(movie_root, ['Finishing movie take'])
        context = movie_audio.MovieOutputContext(movie_root, verify_owner=lambda owner: None)
        wave = movie_audio.MovieWave(self.root / 'take' / 'clip.mp4',
                                     self.root / 'game/bin/win64/deadlock.exe', output_context=context)
        wave.start(controller, 60)
        sf.write(wave.folder / (wave.name + '.wav'), np.zeros((44100 * 2, 2)), 44100)
        return controller, wave

    def test_finished_shot_still_ends_the_take_after_a_later_status_message(self):
        # The paused camera can start right after the shot ends: Dolly is busy for
        # that moment and its status line replaces "Native shot finished".
        app, _stop = self.polling_app(None, 'recording')
        app._auto_finish_layered = True
        app._take_playing_seen = True
        finished = {'playing': False, 'game_running': True, 'shot_finished': True,
                    'message': 'Paused camera ready. Use the in-game movement controls.'}
        app.busy = True
        DollyApp._refresh_video(app, finished)
        self.assertEqual(app.stops, [])
        app.busy = False
        DollyApp._refresh_video(app, finished)
        self.assertEqual(app.stops, [False])
        self.assertFalse(app._auto_finish_layered)
        # A take whose shot has not ended stays open.
        app, _stop = self.polling_app(None, 'recording')
        app._auto_finish_layered = True
        app._take_playing_seen = True
        DollyApp._refresh_video(app, {**finished, 'shot_finished': False})
        self.assertEqual(app.stops, [])

    def test_pov_segment_plays_at_the_take_speed_not_the_live_export_speed(self):
        plays = []
        app = NS(busy=False, video_export=NS(output_path=None), _last_video_state='idle',
                 _pending_auto_play=True, _pov_project='pov project', _take_play_speed=0.5,
                 video_export_speed=NS(get=lambda: '2'), _base_capture=None,
                 controller=NS(play_pov=lambda project, speed: plays.append((project, speed))),
                 _advance_layer_pipeline=lambda: None)
        app.video_status_text = NS(value='', set=lambda text: setattr(app.video_status_text, 'value', text),
                                   get=lambda: app.video_status_text.value)
        app.status_text = NS(set=lambda text: None)
        app._submit = lambda label, function, callback: callback(function())
        DollyApp._video_operation_done(app, {'state': 'recording'})
        self.assertEqual(plays, [('pov project', 0.5)])
        self.assertTrue(app._take_playing_seen)
        self.assertFalse(app._pending_auto_play)

    def test_movie_conversion_failure_reports_utf8_ffmpeg_text(self):
        calls = []
        controller, wave = self.finished_wave()
        with patch.object(clip_audio, '_ffmpeg', return_value=Path('ffmpeg.exe')), \
                patch.object(movie_audio.subprocess, 'run', side_effect=ffmpeg_child(calls)):
            with self.assertRaisesRegex(RuntimeError, 'could not be converted: .*error'):
                wave.finish(controller, 1.0, self.root)
        self.assertEqual(calls[0][1].get('encoding'), 'utf-8')
        self.assertEqual(calls[0][1].get('errors'), 'replace')

    def test_movie_resample_failure_reports_utf8_ffmpeg_text(self):
        calls = []
        controller, wave = self.finished_wave()
        with patch.object(clip_audio, '_ffmpeg', return_value=Path('ffmpeg.exe')), \
                patch.object(movie_audio, '_movie_sample_windows', return_value=(48000, [(0, 48000)])), \
                patch.object(movie_audio.subprocess, 'run', side_effect=ffmpeg_child(calls)):
            with self.assertRaisesRegex(RuntimeError, 'resampling failed.*error'):
                wave.finish(controller, 1.0, self.root, video_status={'frames_written': 60})

    def test_movie_ffmpeg_runs_are_bounded_and_detached_from_stdin(self):
        for resample in (True, False):
            with self.subTest(resample=resample):
                calls = []
                for path in self.root.iterdir():
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
                controller, wave = self.finished_wave()
                status = {'frames_written': 60} if resample else None
                with patch.object(clip_audio, '_ffmpeg', return_value=Path('ffmpeg.exe')), \
                        patch.object(movie_audio, '_movie_sample_windows', return_value=(48000, [(0, 48000)])), \
                        patch.object(movie_audio.subprocess, 'run', side_effect=timeout_child(calls)):
                    with self.assertRaisesRegex(RuntimeError, 'timed out'):
                        wave.finish(controller, 1.0, self.root, video_status=status)
                options = calls[0][1]
                self.assertIs(options.get('stdin'), subprocess.DEVNULL)
                self.assertGreaterEqual(options.get('timeout') or 0, 60)

    def test_mux_failure_reports_utf8_text_and_timeout_is_clear(self):
        video = self.root / 'Иван' / 'clip.mp4'
        video.parent.mkdir()
        video.write_bytes(b'silent master')
        audio = self.root / 'game.wav'
        audio.write_bytes(b'wav')
        calls = []
        with patch.object(clip_audio.subprocess, 'run', side_effect=ffmpeg_child(calls)):
            with self.assertRaisesRegex(RuntimeError, 'could not add clip audio: .*error'):
                clip_audio._mux(video, [audio], Path('ffmpeg.exe'))
        self.assertEqual(calls[0][1].get('encoding'), 'utf-8')
        calls.clear()
        with patch.object(clip_audio.subprocess, 'run', side_effect=timeout_child(calls)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                clip_audio._mux(video, [audio], Path('ffmpeg.exe'), duration=900.0)
        self.assertIs(calls[0][1].get('stdin'), subprocess.DEVNULL)
        self.assertGreaterEqual(calls[0][1].get('timeout'), 1800.0)
        self.assertEqual(video.read_bytes(), b'silent master')

    # D2 / D4: depth preview --------------------------------------------
    def depth_take(self, name, codec):
        take = self.root / name
        (take / 'depth').mkdir(parents=True)
        (take / 'depth' / 'preview_64x64.raw').write_bytes(bytes(range(256)) * 16 * 30)
        exporter = VideoExport.__new__(VideoExport)
        exporter._lock = threading.RLock()
        exporter.output_path = take / 'clip.mp4'
        exporter._depth_options = VideoOptions(take / 'clip.mp4', codec=codec,
                                               ffmpeg_path=Path(sys.executable))
        return take, exporter

    def test_depth_preview_failure_is_best_effort_and_leaves_no_partial(self):
        for label, codec, child in (('cyrillic', 'hevc_amf', ffmpeg_child),
                                    ('timeout', 'lossless', timeout_child)):
            with self.subTest(label=label):
                take, exporter = self.depth_take('Иван_' + label, codec)
                calls = []
                with patch.object(video_export.subprocess, 'run', side_effect=child(calls)), \
                        self.assertLogs('dolly', level='WARNING'):
                    status = exporter._encode_depth_preview({'state': 'completed'})
                self.assertEqual(status, {'state': 'completed'})
                self.assertEqual(sorted(p.name for p in (take / 'depth').iterdir()),
                                 ['preview_64x64.raw'])

    def test_depth_preview_success_replaces_final_name(self):
        take, exporter = self.depth_take('ok', 'lossless')
        def run(command, **options):
            Path(command[-1]).write_bytes(b'preview')
            return NS(returncode=0, stderr='')
        with patch.object(video_export.subprocess, 'run', side_effect=run):
            status = exporter._encode_depth_preview({'state': 'completed'})
        self.assertEqual(status['depth_preview'], str(take / 'depth' / 'preview.mkv'))
        self.assertEqual(sorted(p.name for p in (take / 'depth').iterdir()), ['preview.mkv'])

    def test_depth_preview_pads_odd_sizes_for_yuv420p_encoders(self):
        for codec, padded in (('h264_mf', True), ('lossless', False)):
            with self.subTest(codec=codec):
                take, exporter = self.depth_take('odd_' + codec, codec)
                raw = take / 'depth' / 'preview_64x64.raw'
                raw.rename(raw.with_name('preview_743x418.raw'))
                commands = []
                def run(command, **options):
                    commands.append(command)
                    Path(command[-1]).write_bytes(b'preview')
                    return NS(returncode=0, stderr='')
                with patch.object(video_export.subprocess, 'run', side_effect=run):
                    exporter._encode_depth_preview({'state': 'completed'})
                self.assertIn('743x418', commands[0])
                self.assertEqual('pad=ceil(iw/2)*2:ceil(ih/2)*2' in commands[0], padded)

    # D2 / D3: layer alpha combine --------------------------------------
    def layer_app(self, frames=None):
        base = VideoOptions(self.root / 'Иван' / 'clip.mp4', codec='hevc_amf', fps=60,
                            ffmpeg_path=Path(sys.executable))
        layer_dir = self.root / 'Иван' / 'clip' / 'world'
        layer_dir.mkdir(parents=True)
        (layer_dir / 'world.mp4').write_bytes(b'black')
        (layer_dir / 'world_white.mp4').write_bytes(b'white')
        if frames is not None:
            (layer_dir / 'world.mp4.shot.json').write_text(
                json.dumps({'frames_written': frames}), encoding='utf-8')
        app = NS(_base_capture=base, controller=NS(end_matte_layer=lambda: None))
        return app, layer_dir

    def test_layer_combine_failure_reports_utf8_text_and_removes_partial_master(self):
        app, layer_dir = self.layer_app(frames=60)
        calls = []
        with patch.object(gui.subprocess, 'run', side_effect=ffmpeg_child(calls)):
            with self.assertRaisesRegex(RuntimeError, r'alpha combine failed: .*error \(matte passes kept\)'):
                DollyApp._combine_layer(app, 'world')
        self.assertEqual(calls[0][1].get('encoding'), 'utf-8')
        self.assertEqual(sorted(p.name for p in layer_dir.iterdir()),
                         ['world.mp4', 'world.mp4.shot.json', 'world_white.mp4'])

    def test_layer_combine_timeout_scales_with_frames_and_removes_partial_master(self):
        app, layer_dir = self.layer_app(frames=36000)
        calls = []
        with patch.object(gui.subprocess, 'run', side_effect=timeout_child(calls)):
            with self.assertRaisesRegex(RuntimeError, r'timed out.*\(matte passes kept\)'):
                DollyApp._combine_layer(app, 'world')
        self.assertGreaterEqual(calls[0][1].get('timeout'), 36000 * 0.5)
        self.assertNotIn('world.mov', [p.name for p in layer_dir.iterdir()])
        self.assertEqual(len(list(layer_dir.iterdir())), 3)

    def test_layer_combine_success_publishes_master(self):
        app, layer_dir = self.layer_app(frames=60)
        def run(command, **options):
            Path(command[-1]).write_bytes(b'master')
            return NS(returncode=0, stderr='')
        with patch.object(gui.subprocess, 'run', side_effect=run):
            result = DollyApp._combine_layer(app, 'world')
        self.assertEqual(result['master'], str(layer_dir / 'world.mov'))
        self.assertEqual((layer_dir / 'world.mov').read_bytes(), b'master')
        self.assertEqual(sorted(p.name for p in layer_dir.iterdir()), ['world.mov'])

    def test_layer_combine_keeps_its_master_for_a_mov_take(self):
        # A MOV take's black pass already has the master's name.
        base = VideoOptions(self.root / 'take' / 'clip.mov', codec='hevc_amf', fps=60,
                            ffmpeg_path=Path(sys.executable), container='mov')
        layer_dir = self.root / 'take' / 'clip' / 'world'
        layer_dir.mkdir(parents=True)
        (layer_dir / 'world.mov').write_bytes(b'black')
        (layer_dir / 'world_white.mov').write_bytes(b'white')
        (layer_dir / 'world.mov.shot.json').write_text(json.dumps({'frames_written': 60}), encoding='utf-8')
        app = NS(_base_capture=base, controller=NS(end_matte_layer=lambda: None))
        def run(command, **options):
            Path(command[-1]).write_bytes(b'master')
            return NS(returncode=0, stderr='')
        with patch.object(gui.subprocess, 'run', side_effect=run):
            result = DollyApp._combine_layer(app, 'world')
        self.assertEqual(result['master'], str(layer_dir / 'world.mov'))
        self.assertEqual(sorted(p.name for p in layer_dir.iterdir()), ['world.mov'])
        self.assertEqual((layer_dir / 'world.mov').read_bytes(), b'master')

    def test_still_frame_failure_reports_utf8_text_and_removes_partial_image(self):
        from dolly import screenshot
        folder = self.root / 'Иван'
        folder.mkdir()
        for child, expected in ((ffmpeg_child, 'FFmpeg failed: А .*error'), (timeout_child, 'timed out')):
            with self.subTest(child=child.__name__):
                calls = []
                with patch.object(subprocess, 'run', side_effect=child(calls)):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        screenshot.extract_plate(Path(sys.executable), folder / 'clip.mkv', 3, folder / 'plate.png')
                self.assertEqual(calls[0][1].get('encoding'), 'utf-8')
                self.assertEqual(list(folder.iterdir()), [])

    # D6 / D7: take folder admission ------------------------------------
    def test_existing_take_folder_is_refused_before_replay_for_bundled_audio(self):
        (self.root / 'clip').mkdir()
        with self.assertRaisesRegex(ValueError, 'take folder'):
            VideoOptions(self.root / 'clip.mp4', codec='builtin', bundle_audio=True).validated()

    # D8: take folder paths stay within Windows MAX_PATH ---------------
    def long_parent(self, length):
        parent = self.root / ('a' * (length - len(str(self.root)) - 1))
        parent.mkdir()
        self.assertEqual(len(str(parent)), length)
        return parent

    def test_take_folder_path_too_long_for_windows_is_refused_up_front(self):
        parent = self.long_parent(235)
        path = parent / 'clip.mp4'
        folder = path.with_suffix('')
        self.assertLess(len(str(path)) + len('.shot.json'), 260)
        # A single-file take at this path fits; its take folder's files would not.
        VideoOptions(path, codec='builtin').validated()
        with self.assertRaises(ValueError) as caught:
            VideoOptions(path, codec='builtin', bundle_audio=True).validated()
        message = str(caught.exception)
        self.assertIn('too long', message)
        self.assertIn(str(folder), message)
        self.assertIn('shorter', message)
        self.assertFalse(folder.exists())

    def test_take_folder_limit_counts_its_longest_entry(self):
        from dolly.video_export import take_folder_entries
        folder = Path('C:/take/clip')
        audio = take_folder_entries(folder, 'clip.mp4', VideoOptions(folder.with_suffix('.mp4'),
                                                                     bundle_audio=True))
        players = take_folder_entries(folder, 'clip.mp4', VideoOptions(folder.with_suffix('.mp4'),
                                                                       layers=('players',)))
        self.assertTrue(any('dolly-mux-' in str(entry) for entry, _directory in audio))
        self.assertGreater(max(len(str(entry)) for entry, _directory in players),
                           max(len(str(entry)) for entry, _directory in audio))
        # The shortest folder that fits is accepted.
        parent = self.long_parent(150)
        VideoOptions(parent / 'clip.mp4', codec='builtin', bundle_audio=True).validated()

    def test_unreadable_recorder_status_leaves_no_timing_or_take_folder(self):
        events = []
        controller = NS(_session=object(), _demo='owned.dem', _export_timing=None,
                        _export_resolution=None,
                        status=lambda: dict(game_running=True, connected=True, camera_backend='native',
                                            startup_stage='editing_ready', playing=False),
                        mark_recording_pending=lambda *args, **kwargs: None,
                        clear_export_resolution=lambda: None)
        controller.set_export_timing = lambda fps, speed: (events.append('set timing'),
                                                           setattr(controller, '_export_timing', {'fps': fps}))
        controller.clear_export_timing = lambda: (events.append('clear timing'),
                                                  setattr(controller, '_export_timing', None))
        def unreadable():
            raise OSError('media status unavailable')
        bridge = NS(video_status=unreadable)
        controller._native_bridge = lambda: bridge
        owner = VideoExport(controller)
        options = VideoOptions(self.root / 'take.mp4', codec='builtin', fixed_step=True, bundle_audio=True)
        with self.assertRaisesRegex(OSError, 'media status unavailable'):
            owner.start(options, before_record=lambda: events.append('movie started'))
        self.assertIsNone(controller._export_timing)
        self.assertFalse((self.root / 'take').exists())
        self.assertNotIn('movie started', events)


    # Shot names as file stems --------------------------------------
    def test_safe_stem_keeps_unicode_and_avoids_windows_device_names(self):
        for name, expected in (
                ('Cafe\u0301', 'Cafe\u0301'),
                ('\u0939\u093f\u0928\u094d\u0926\u0940', '\u0939\u093f\u0928\u094d\u0926\u0940'),
                ('\u540d\u524d \U0001f304', '\u540d\u524d \U0001f304'),
                ('Opening-shot_2', 'Opening-shot_2'),
                ('a<b>:c"d/e\\f|g?h*', 'a_b__c_d_e_f_g_h_'),
                ('tab\there\x00', 'tab_here_'),
                ('x\ud83c', 'x_'),
                ('  name. . ', 'name'),
                ('CON', '_CON'), ('con', '_con'), ('NUL ', '_NUL'), ('aux.backup', '_aux.backup'),
                ('COM1', '_COM1'), ('LPT9.take', '_LPT9.take'), ('COM\u00b9', '_COM\u00b9'),
                ('CONIN$', '_CONIN$'), ('PRN .x', '_PRN .x'),
                ('CONSOLE', 'CONSOLE'), ('COM10', 'COM10'), ('LPT', 'LPT'),
                ('', 'shot'), ('...', 'shot'), ('   ', 'shot')):
            with self.subTest(name=name):
                self.assertEqual(video_export.safe_stem(name), expected)
        self.assertEqual(video_export.safe_stem('', 'Dolly'), 'Dolly')

    def test_save_suggests_the_shot_name_as_a_safe_filename(self):
        for name, expected in (('Cafe\u0301 \U0001f304', 'Cafe\u0301 \U0001f304.json'),
                               ('COM1', '_COM1.json'), ('a/b', 'a_b.json')):
            with self.subTest(name=name):
                app = NS(project=gui.Project(name=name), file_path=None, busy=False, root=None,
                         _sync_options=lambda: None)
                with patch.object(gui.filedialog, 'asksaveasfilename', return_value='') as dialog:
                    self.assertFalse(DollyApp.save(app))
                self.assertEqual(dialog.call_args.kwargs['initialfile'], expected)

    def test_in_game_crav_path_avoids_windows_device_names(self):
        app = NS(project=gui.Project(name='COM1'), video_path=NS(get=lambda: ''),
                 app_settings=NS(export_folder=str(self.root)))
        self.assertEqual(DollyApp._renderlock_auto_path(app), self.root / '_COM1.crav')
        app.video_path = NS(get=lambda: str(self.root / 'LPT1.mp4'))
        self.assertEqual(DollyApp._renderlock_auto_path(app), self.root / '_LPT1.crav')


class FfmpegRunTests(unittest.TestCase):
    """The shared runner, with a real Python child standing in for FFmpeg."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-ffmpeg-run-')
        self.addCleanup(temporary.cleanup)
        self.partial = Path(temporary.name) / 'Иван' / 'out.partial.mov'
        self.partial.parent.mkdir()

    def child(self, body):
        return [sys.executable, '-c',
                'import sys, time; open(sys.argv[1], "wb").write(b"partial"); ' + body,
                str(self.partial)]

    def test_timeout_kills_child_and_removes_partial_output(self):
        from dolly import ffmpeg_run
        with self.assertRaises(subprocess.TimeoutExpired):
            ffmpeg_run.run_ffmpeg(self.child('time.sleep(30)'), timeout=2.0, partial=self.partial)
        self.assertFalse(self.partial.exists())
        with self.assertRaisesRegex(RuntimeError, 'took too long'):
            ffmpeg_run.run_ffmpeg(self.child('time.sleep(30)'), timeout=2.0, partial=self.partial,
                                  timeout_message='took too long')
        self.assertFalse(self.partial.exists())

    def test_failure_decodes_non_ascii_stderr_and_removes_partial_output(self):
        from dolly import ffmpeg_run
        result = ffmpeg_run.run_ffmpeg(self.child(
            "sys.stderr.buffer.write('Иван'.encode() + b' \\xff error'); sys.exit(1)"),
            timeout=30.0, partial=self.partial)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, 'Иван � error')
        self.assertEqual(ffmpeg_run.last_line(result), 'Иван � error')
        self.assertFalse(self.partial.exists())

    def test_success_keeps_output_and_runs_detached(self):
        from dolly import ffmpeg_run
        calls = []
        def run(command, **options):
            calls.append(options)
            return REAL_RUN(command, **options)
        with patch.object(subprocess, 'run', side_effect=run):
            result = ffmpeg_run.run_ffmpeg(self.child('sys.exit(0)'), timeout=30.0, partial=self.partial)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.partial.read_bytes(), b'partial')
        self.assertIs(calls[0]['stdin'], subprocess.DEVNULL)
        self.assertEqual((calls[0]['encoding'], calls[0]['errors']), ('utf-8', 'replace'))
        self.assertEqual(calls[0]['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(ffmpeg_run.scaled_timeout(120.0, 0.5, 36000), 18000.0)
        self.assertEqual(ffmpeg_run.scaled_timeout(120.0, 0.5, 10), 120.0)


class AutoEncoderTests(unittest.TestCase):
    """Auto's vendor encoder must pass one real test encode before it is used."""

    def setUp(self):
        from dolly import encoder_select
        temporary = tempfile.TemporaryDirectory(prefix='dolly-auto-encoder-')
        self.addCleanup(temporary.cleanup)
        self.ffmpeg = Path(temporary.name) / 'ffmpeg.exe'
        self.ffmpeg.write_bytes(b'fixture')
        for cache in (encoder_select._PROBE_CACHE, video_export._ENCODER_LIST_CACHE):
            patcher = patch.dict(cache, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []

    def run_ffmpeg(self, command, **options):
        self.calls.append(list(command))
        if '-encoders' in command:
            return NS(returncode=0, stdout=' V....D h264_nvenc\n V....D h264_amf\n V....D h264_mf\n',
                      stderr='')
        failed = command[command.index('-c:v') + 1] == 'h264_nvenc'
        return NS(returncode=1 if failed else 0, stdout='',
                  stderr='Cannot load nvcuda.dll\n' if failed else '')

    def test_failed_vendor_probe_falls_back_once_and_explicit_choice_is_kept(self):
        from dolly import encoder_select
        probes = lambda: [c for c in self.calls if 'lavfi' in c]
        with patch.object(encoder_select, 'gpu_adapters', return_value=(('nvidia', 'GeForce'),)), \
                patch.object(subprocess, 'run', side_effect=self.run_ffmpeg), \
                self.assertLogs('dolly.encoder_select', 'WARNING') as logs:
            auto = VideoOptions(self.ffmpeg.parent / 'take.mp4', ffmpeg_path=self.ffmpeg)
            self.assertEqual(video_export.resolve_backend(auto), (1, 3))  # h264_mf
            self.assertEqual(video_export.resolve_backend(auto), (1, 3))
            self.assertEqual(len(probes()), 1)
            self.assertIn('-frames:v', probes()[0])
            explicit = VideoOptions(self.ffmpeg.parent / 'take.mp4', codec='h264_nvenc',
                                    ffmpeg_path=self.ffmpeg)
            self.assertEqual(video_export.resolve_backend(explicit), (1, 1))
            self.assertEqual(len(probes()), 1)
        self.assertIn('nvcuda.dll', '\n'.join(logs.output))

    def test_working_vendor_probe_keeps_the_hardware_encoder(self):
        from dolly import encoder_select
        with patch.object(encoder_select, 'gpu_adapters', return_value=(('amd', 'Radeon'),)), \
                patch.object(subprocess, 'run', side_effect=self.run_ffmpeg):
            auto = VideoOptions(self.ffmpeg.parent / 'take.mp4', ffmpeg_path=self.ffmpeg)
            self.assertEqual(video_export.resolve_backend(auto), (1, 8))  # h264_amf

    def test_auto_resolves_once_per_start_and_reuses_the_validated_key(self):
        from dolly import encoder_select
        with patch.object(encoder_select, 'gpu_adapters', return_value=(('amd', 'Radeon'),)) as adapters, \
                patch.object(subprocess, 'run', side_effect=self.run_ffmpeg):
            options = VideoOptions(self.ffmpeg.parent / 'take.mp4', ffmpeg_path=self.ffmpeg).validated()
            adapters.return_value = (('nvidia', 'GeForce'),)  # must not change the take's encoder
            options = options.validated()
            self.assertEqual(options.auto_codec, 'h264_amf')
            self.assertEqual(video_export.resolve_backend(options), (1, 8))
            self.assertEqual(VideoExport._resolve_recording_backend(None, options),
                             (1, 8, 'Auto → AMD H.264 (AMF)'))
            self.assertEqual(adapters.call_count, 1)
            explicit = VideoOptions(self.ffmpeg.parent / 'take.mp4', codec='h264_mf',
                                    ffmpeg_path=self.ffmpeg, auto_codec='h264_amf').validated()
            self.assertEqual(explicit.auto_codec, '')


if __name__ == '__main__':
    unittest.main()
