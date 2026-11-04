"""Movie WAV capture parsing and conversion."""
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import Mock, patch

from dolly.movie_audio import MOVIE_START, MovieOutputContext, MovieWave


class MovieWaveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.game = Path(self.folder.name) / "Deadlock/game/bin/win64/deadlock.exe"
        self.game.parent.mkdir(parents=True)
        self.movie = MovieWave(Path(self.folder.name) / "shot.mp4", self.game)
        self.controller = Mock()
        self.controller.game_pid.return_value = 4321
        self.marker = Path(tempfile.gettempdir()) / "dolly_movie_audio_4321.ready"

    def tearDown(self):
        self.marker.unlink(missing_ok=True)

    def test_start_parses_the_engine_folder(self):
        self.marker.write_text("pcm-sample-map-v1", encoding="ascii")
        self.controller.replay_tick_rate.return_value = 64.0
        self.controller._request.side_effect = [
            "",
            "Audio device starved 3 samples \r\n"
            "Started recording movie: movie/2026_10_19_12_00_00/dolly_audio_x.*: "
            "framerate 60.000000",
            "Syntax: demo_goto <tick>\r\n  Currently playing 45 of 1000 ticks.",
        ]
        self.movie.start(self.controller, 60)
        self.assertTrue(self.movie.started)
        self.assertAlmostEqual(self.movie.movie_engine_seconds, 45 / 64.0)
        self.assertEqual(
            self.movie.folder,
            Path(self.folder.name) / "Deadlock/game/citadel/movie/2026_10_19_12_00_00")

    def test_start_refuses_without_the_native_marker(self):
        self.marker.unlink(missing_ok=True)
        with self.assertRaisesRegex(RuntimeError, "does not support fixed-step audio"):
            self.movie.start(self.controller, 60)

    def test_start_refuses_a_legacy_marker_without_sample_alignment(self):
        self.marker.write_text('', encoding='ascii')
        with self.assertRaisesRegex(RuntimeError, 'does not support fixed-step audio'):
            self.movie.start(self.controller, 60)
        self.controller._request.assert_not_called()

    def test_finish_converts_the_wav_to_48k(self):
        self.marker.write_text("pcm-sample-map-v1", encoding="ascii")
        self.controller.replay_tick_rate.return_value = 64.0
        self.controller._request.side_effect = [
            "",
            "Started recording movie: movie/2026_10_19_12_00_00/dolly_audio_x.*: "
            "framerate 60.000000",
            "Syntax: demo_goto <tick>\r\n  Currently playing 0 of 1000 ticks.",
        ]
        self.movie.start(self.controller, 60)
        assert self.movie.folder is not None
        self.movie.folder.mkdir(parents=True, exist_ok=True)
        source = self.movie.folder / f"{self.movie.name}.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"\0\0\0\0" * 44100)
        self.controller._request.side_effect = [
            "Finishing movie: movie/2026_10_19_12_00_00/dolly_audio_x.* "
            "[60 frames @ 60.000000 == 1.000000 seconds]"
        ]
        try:
            target = self.movie.finish(self.controller, 0.25, Path(self.folder.name),
                                       start_engine_seconds=0.25, speed=0.5)
        except RuntimeError as exc:
            self.skipTest(f"FFmpeg unavailable: {exc}")
        self.assertTrue(target.is_file())
        with wave.open(str(target), "rb") as audio:
            self.assertEqual(audio.getframerate(), 48000)
            self.assertEqual(audio.getnchannels(), 2)
            self.assertGreaterEqual(audio.getnframes(), 11000)
            self.assertLessEqual(audio.getnframes(), 13000)


class OwnedMovieWaveTests(unittest.TestCase):
    def test_clip_audio_collects_the_owned_mod_wav_and_keeps_the_legacy_sentinel(self):
        from dolly.clip_audio import ClipAudioCapture
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            game=folder/'Deadlock/game/bin/win64/deadlock.exe'
            own=folder/'Deadlock/game/citadel_dolly_test/cvar_unlocker/movie'
            controller=Mock()
            controller.game_pid.return_value=None
            controller._native_bridge.return_value.status.return_value={'engine_time':0}
            controller.movie_output_context.return_value=MovieOutputContext(own,Mock())
            controller._request.side_effect=lambda command,**kw: (
                'Started recording movie: movie/2026_10_20_18_01_38/dolly_audio_x.*: framerate 30'
                if command.startswith('startmovie ') else 'Finishing movie' if command=='endmovie' else '')
            with patch('dolly.clip_audio._ffmpeg',return_value=folder/'ffmpeg.exe'):
                audio=ClipAudioCapture(folder/'shot.mkv',game_audio=True,reconstructed=False,
                    bridge=Mock(),game=game,game_pid=123,controller=controller,fixed_step=True)
            audio.start_movie()
            name=audio.movie.name+'.wav'
            actual=own/'2026_10_20_18_01_38'/name
            wrong=folder/'Deadlock/game/citadel/movie/2026_10_20_18_01_38'/name
            actual.parent.mkdir(parents=True);actual.write_bytes(b'owned engine WAV')
            wrong.parent.mkdir(parents=True);wrong.write_bytes(b'unrelated legacy sentinel')
            def convert(command,**kwargs):
                self.assertEqual(Path(command[command.index('-i')+1]),actual)
                Path(command[-1]).write_bytes(b'converted audio')
                return SimpleNamespace(returncode=0,stderr='')
            with patch('dolly.clip_audio._ffmpeg',return_value=folder/'ffmpeg.exe'), \
                    patch('dolly.movie_audio.subprocess.run',side_effect=convert):
                result=audio.movie.finish(controller,.25,folder)
            self.assertTrue(result.is_file())
            self.assertEqual(wrong.read_bytes(),b'unrelated legacy sentinel')
            self.assertFalse(actual.exists())
            audio.temporary.cleanup()


class MovieAlignmentTests(unittest.TestCase):
    def setUp(self):
        import json
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.video = self.base / 'shot.mp4'
        self.name = 'dolly_audio_123456789a'
        self.status = dict(frames_written=120, fps=60, duration=2.0)
        # One second of preparation plus one unrecorded movie frame in the
        # middle. Neither belongs in the exported soundtrack.
        self.rows = [[int('123456789a', 16), 44100 + (i + (i >= 60))*735,
                      44100 + (i + (i >= 60) + 1)*735, 60+i+(i >= 60), 44100]
                     for i in range(120)]
        self.data = dict(format='deadlock-dolly-shot', version=1,
                         video_file=self.video.name, fps=60, fixed_step=True,
                         frames_written=120,
                         movie_audio=dict(version=1, complete=True, frames=self.rows))
        self.sidecar = self.video.parent / 'shot.json'
        self.save = lambda: self.sidecar.write_text(json.dumps(self.data), encoding='utf-8')
        self.save()

    def test_exact_intervals_exclude_preroll_and_a_skipped_movie_frame(self):
        from dolly.movie_audio import _movie_sample_windows, _align_movie_samples
        import numpy as np
        import soundfile as sf
        rate, windows = _movie_sample_windows(self.video, self.status, self.name)
        # Every source sample carries its source index as an independent oracle.
        raw = np.arange(windows[-1][1] + 1000, dtype='float32')[:, None]
        source = self.base / 'original.wav'
        target = self.base / 'aligned.wav'
        sf.write(source, np.repeat(raw, 2, axis=1), rate, subtype='FLOAT')
        _align_movie_samples(source, target, rate, windows)
        result, actual_rate = sf.read(target, always_2d=True, dtype='float32')
        expected = np.concatenate([np.arange(row[3]*800, (row[3]+1)*800, dtype='float32') for row in self.rows])
        np.testing.assert_array_equal(result[:, 0], expected)
        self.assertEqual(len(result), 96000)
        self.assertEqual(actual_rate, 48000)
        self.assertTrue(source.is_file())

    def test_120fps_skipped_odd_frames_do_not_accumulate_half_sample_drift(self):
        from dolly.movie_audio import _movie_sample_windows
        self.status = dict(frames_written=240, fps=120, duration=2.0)
        self.data.update(fps=120, frames_written=240)
        self.data['movie_audio']['frames'] = [
            [int('123456789a', 16), (2*i*44100+60)//120,
             ((2*i+1)*44100+60)//120, 2*i, 44100] for i in range(240)]
        self.save()
        rate, windows = _movie_sample_windows(self.video, self.status, self.name)
        self.assertEqual(rate, 48000)
        self.assertEqual(windows[:2], [(0, 400), (800, 1200)])
        self.assertEqual(sum(b-a for a,b in windows), 96000)

    def test_missing_foreign_incomplete_and_nonmonotonic_maps_are_refused(self):
        import copy
        from dolly.movie_audio import _movie_sample_windows
        original = copy.deepcopy(self.data)
        changes = [lambda d: d.update(video_file='different.mp4'),
                   lambda d: d['movie_audio'].update(complete=False),
                   lambda d: d['movie_audio']['frames'].pop(),
                   lambda d: d['movie_audio']['frames'][0].__setitem__(0, 999),
                   lambda d: d['movie_audio']['frames'][1].__setitem__(1, 0),
                   lambda d: d['movie_audio']['frames'][1].__setitem__(3, 0),
                   lambda d: d['movie_audio']['frames'][0].__setitem__(2, 999999),
                   lambda d: d['movie_audio']['frames'][0].__setitem__(4, 48000)]
        for mutate in changes:
            with self.subTest(mutate=mutate):
                self.data = copy.deepcopy(original)
                mutate(self.data)
                self.save()
                with self.assertRaisesRegex(RuntimeError, 'alignment is unavailable'):
                    _movie_sample_windows(self.video, self.status, self.name)
        self.sidecar.unlink()
        with self.assertRaisesRegex(RuntimeError, 'alignment is unavailable'):
            _movie_sample_windows(self.video, self.status, self.name)

    def test_short_source_is_preserved_instead_of_padding_missing_audio(self):
        from dolly.movie_audio import _align_movie_samples
        source = self.base / 'short.wav'
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0' * 400)
        original = source.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'does not cover'):
            _align_movie_samples(source, self.base/'output.wav', 44100, [(0, 735)])
        self.assertEqual(source.read_bytes(), original)

    def test_actual_clip_conversion_and_mux_keep_impulses_at_video_frame_times(self):
        from dolly.clip_audio import ClipAudioCapture
        from dolly.video_export import bundled_ffmpeg_path
        import numpy as np
        import soundfile as sf
        import subprocess
        ffmpeg = bundled_ffmpeg_path()
        if ffmpeg is None:
            self.skipTest('Bundled FFmpeg is unavailable')
        controller = Mock()
        controller.game_pid.return_value = None
        controller._native_bridge.return_value.status.return_value = {'engine_time': 100.0}
        controller.movie_output_context.return_value = MovieOutputContext(self.base/'owned/movie', Mock())
        controller._request.side_effect = lambda command, **kw: (
            'Started recording movie: movie/2026_11_04_00_00_00/dolly_audio_x.*: framerate 60'
            if command.startswith('startmovie ') else 'Finishing movie' if command == 'endmovie' else '')
        audio = ClipAudioCapture(self.video, game_audio=True, reconstructed=False, bridge=Mock(),
            game=self.base/'game/bin/win64/deadlock.exe', game_pid=1, controller=controller,
            fixed_step=True, movie_fps=60, start_engine_seconds=100.0)
        self.addCleanup(audio.temporary.cleanup)
        audio.start()
        audio.movie.name = self.name
        audio.start_movie()
        audio.movie.folder.mkdir(parents=True)
        source = audio.movie.folder / (self.name+'.wav')
        raw = np.zeros((self.rows[-1][2]+44100, 2), dtype='float32')
        for frame in (12, 90):
            first = self.rows[frame][1]
            pulse = .7 * np.sin(2*np.pi*1000*np.arange(400)/44100)
            raw[first:first+400] = pulse[:, None]
        sf.write(source, raw, 44100, subtype='PCM_16')
        subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'lavfi', '-i',
            'color=black:s=64x64:r=60:d=2', '-c:v', 'mpeg4', str(self.video)],
            check=True, capture_output=True)
        # Use the real video finalizer's native-sidecar relocation boundary.
        from dolly.video_export import VideoExport
        native_sidecar = Path(str(self.video) + '.shot.json')
        self.sidecar.rename(native_sidecar)
        exporter = VideoExport(controller)
        exporter.output_path = self.video
        exporter._layer_folder = self.video.parent
        exporter._finish_layered_sidecar()
        self.assertTrue(self.sidecar.is_file())
        audio.finish(self.status)
        decoded = self.base/'decoded.wav'
        subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(self.video), '-vn',
                        '-c:a', 'pcm_s16le', str(decoded)], check=True, capture_output=True)
        samples, rate = sf.read(decoded, always_2d=True)
        peaks = np.flatnonzero(np.max(np.abs(samples), axis=1) > .1)
        groups = np.split(peaks, np.flatnonzero(np.diff(peaks) > rate*.05)+1)
        self.assertEqual(len(groups), 2)
        self.assertAlmostEqual(groups[0][0]/rate, 12/60, delta=.002)
        self.assertAlmostEqual(groups[1][0]/rate, 90/60, delta=.002)
        self.assertFalse(source.exists())  # Only after successful aligned conversion.


class MovieLifetimeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.root=self.base/'owned/movie'
        self.date=self.root/'2026_10_20_18_01_38';self.date.mkdir(parents=True)
        self.controller=Mock();self.controller.game_pid.return_value=None
        self.controller._native_bridge.return_value.status.return_value={'engine_time':0}
        self.controller._request.side_effect=lambda command,**kw: (
            'Started recording movie: movie/2026_10_20_18_01_38/dolly_audio_x.*: framerate 30'
            if command.startswith('startmovie ') else 'Finishing movie' if command=='endmovie' else '')
        self.allowed=True
        def verify(controller):
            if not self.allowed or controller is not self.controller:raise RuntimeError('Original movie owner unavailable')
        self.movie=MovieWave(self.base/'video.mkv',self.base/'game/bin/win64/deadlock.exe',
            output_context=MovieOutputContext(self.root,verify))
        self.movie.start(self.controller,30)
        self.wav=self.date/(self.movie.name+'.wav');self.wav.write_bytes(b'owned WAV')
        self.controller._request.reset_mock()

    def test_cancel_deletes_only_exact_own_wav_and_preserves_unknown_siblings(self):
        sibling=self.date/'unrelated.wav';sibling.write_bytes(b'keep')
        self.movie.cancel(self.controller)
        self.assertFalse(self.wav.exists());self.assertTrue(sibling.is_file())
        self.assertEqual(sibling.read_bytes(),b'keep')
        self.assertTrue(self.root.exists())

    def test_clean_owned_take_removes_only_empty_date_and_movie_roots(self):
        self.movie.cancel(self.controller)
        self.assertFalse(self.date.exists());self.assertFalse(self.root.exists())
        self.assertTrue(self.root.parent.is_dir())

    def test_owner_loss_refuses_finish_cancel_and_cleanup_without_commands_or_deletion(self):
        self.allowed=False
        for action in (lambda:self.movie.finish(self.controller,.25,self.base),
                       lambda:self.movie.cancel(self.controller),self.movie.cleanup):
            with self.assertRaisesRegex(RuntimeError,'owner unavailable'):action()
        self.controller._request.assert_not_called();self.assertEqual(self.wav.read_bytes(),b'owned WAV')

    def test_wrong_controller_cannot_finish_or_cancel_the_original_movie(self):
        wrong=Mock()
        for action in (lambda:self.movie.finish(wrong,.25,self.base),lambda:self.movie.cancel(wrong)):
            with self.assertRaisesRegex(RuntimeError,'controller changed'):action()
        wrong._request.assert_not_called();self.controller._request.assert_not_called()
        self.assertEqual(self.wav.read_bytes(),b'owned WAV')

    def test_reassigned_root_or_capture_name_refuses_without_fallback(self):
        self.movie.movie_root=self.base/'legacy/movie'
        with self.assertRaisesRegex(RuntimeError,'root changed'):self.movie.cancel(self.controller)
        self.movie.movie_root=self.root;self.movie.name='other'
        with self.assertRaisesRegex(RuntimeError,'capture identity'):self.movie.cleanup()
        self.controller._request.assert_not_called();self.assertTrue(self.wav.is_file())

    def test_owner_change_after_hideconsole_prevents_startmovie_dispatch(self):
        fresh=MovieWave(self.base/'new.mkv',self.base/'game/bin/win64/deadlock.exe',
                        output_context=self.movie._output_context)
        def hide(command,**kwargs):
            self.allowed=False
            return ''
        self.controller._request.side_effect=hide
        with self.assertRaisesRegex(RuntimeError,'owner unavailable'):fresh.start(self.controller,30)
        self.assertEqual([call.args[0] for call in self.controller._request.call_args_list],['hideconsole'])
        self.assertEqual(self.wav.read_bytes(),b'owned WAV')

    def test_owner_change_during_endmovie_preserves_wav_before_conversion(self):
        def end(command,**kwargs):
            self.allowed=False
            return 'Finishing movie'
        self.controller._request.side_effect=end
        with patch('dolly.movie_audio.subprocess.run') as convert:
            with self.assertRaisesRegex(RuntimeError,'owner unavailable'):
                self.movie.finish(self.controller,.25,self.base)
        convert.assert_not_called();self.assertEqual(self.wav.read_bytes(),b'owned WAV')

    def test_replaced_or_linked_date_directory_is_preserved(self):
        retained=self.root/'retained';self.assertTrue(retained.resolve().is_relative_to(self.base.resolve()))
        self.date.rename(retained);self.date.mkdir()
        with self.assertRaisesRegex(RuntimeError,'directory changed'):self.movie.cleanup()
        self.date.rmdir()
        if __import__('sys').platform=='win32':
            import _winapi
            _winapi.CreateJunction(str(retained),str(self.date))
        else:self.date.symlink_to(retained,target_is_directory=True)
        try:
            with self.assertRaisesRegex(RuntimeError,'Linked movie'):self.movie.cancel(self.controller)
            self.assertEqual((retained/self.wav.name).read_bytes(),b'owned WAV')
            self.controller._request.assert_not_called()
        finally:
            if __import__('sys').platform=='win32':self.date.rmdir()
            else:self.date.unlink()

    def test_direct_legacy_constructor_keeps_stock_movie_root_and_unknown_files(self):
        legacy=MovieWave(self.base/'legacy.mkv',self.base/'game/bin/win64/deadlock.exe')
        legacy.start(self.controller,30)
        legacy.folder.mkdir(parents=True)
        own=legacy.folder/(legacy.name+'.wav');own.write_bytes(b'legacy own')
        sibling=legacy.folder/'other.wav';sibling.write_bytes(b'keep legacy')
        legacy.cancel(self.controller)
        self.assertFalse(own.exists());self.assertTrue(sibling.is_file())
        self.assertEqual(sibling.read_bytes(),b'keep legacy')
        self.assertTrue(legacy.movie_root.is_dir())


if __name__ == "__main__":
    unittest.main()
