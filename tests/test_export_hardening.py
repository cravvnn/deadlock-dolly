"""Export finishing: movie-audio ownership, ffmpeg text, timeouts and partial files.

FFmpeg is replaced by a controlled Python child or a controlled result; no
game, encoder or network is used.
"""
import json
import shutil
from pathlib import Path
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
        return NS(busy=False, video_export=exporter, clip_audio=audio,
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


if __name__ == '__main__':
    unittest.main()
