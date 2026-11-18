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


if __name__ == '__main__':
    unittest.main()
