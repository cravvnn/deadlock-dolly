"""Returning-operation completion and non-destructive parser boundaries."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from dolly import graphics_profiles, replay_events, runtime
from dolly.launch_errors import LaunchError
from dolly.media_transport import MediaTransport


class FinalizeOwner(MediaTransport):
    """Native boundary replies; exercise the real finalization owner."""
    def __init__(self, state, command, *, audio=False, errors=()):
        self.calls = []
        self._lock = threading.RLock()
        self._media_sequence = 2
        self._media_last_command = command
        self._source_audio_recording = audio
        self.state = dict(state=state, ack=0, reshade=dict(state=1))
        self._media_mapping = SimpleNamespace(close=lambda: self.calls.append('unmap'))
        self.errors = set(errors)
        self._clock = lambda: 0
        self._sleep = lambda _: None

    def media_status(self):
        return deepcopy(self.state)

    def stop_source_audio(self):
        self.calls.append('audio')
        self._media_last_command = 'stop_source_audio'
        self._media_sequence += 2
        if 'audio' in self.errors:
            raise OSError('audio refused')

    def stop_video(self):
        self.calls.append('video')
        if 'video' in self.errors:
            raise RuntimeError('video refused')
        self.state['state'] = 'completed'

    def disable_reshade(self):
        self.calls.append('reshade')
        if 'reshade' in self.errors:
            raise ValueError('ReShade refused')


class CompletionBoundaryTests(unittest.TestCase):
    def test_media_cleanup_attempts_independent_stops_and_keeps_pending_intent(self):
        for pending in (False, True):
            for errors in ((), ('audio',), ('video',), ('audio', 'video'), ('reshade',)):
                with self.subTest(pending=pending, errors=errors):
                    owner = FinalizeOwner('idle' if pending else 'recording', 'start_video',
                                          audio=True, errors=errors)
                    if errors:
                        with self.assertLogs('dolly.media_transport', level='ERROR'):
                            owner._finish_media_and_unmap()
                    else:
                        owner._finish_media_and_unmap()
                    self.assertEqual(owner.calls, ['audio', 'video', 'reshade', 'unmap'])
                    self.assertIsNone(owner._media_mapping)
                    owner._finish_media_and_unmap()
                    self.assertEqual(owner.calls, ['audio', 'video', 'reshade', 'unmap'])
        # Idle/acknowledged state must not gain a spurious video stop.
        owner = FinalizeOwner('idle', 'start_video')
        owner.state['ack'] = owner._media_sequence
        owner._finish_media_and_unmap()
        self.assertEqual(owner.calls, ['reshade', 'unmap'])
        # Preserve unacknowledged audio cleanup even before its flag is set.
        owner = FinalizeOwner('idle', 'start_source_audio')
        owner._finish_media_and_unmap()
        self.assertEqual(owner.calls, ['audio', 'reshade', 'unmap'])

    def test_reader_publishes_terminal_parse_results_without_replacing_new_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helper = root/'native/bin/win64/DollyReplayIndex.exe'
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b'inert fixture; subprocess is simulated')
            replay = root/'owned.dem'
            replay.write_bytes(b'owned replay fixture')
            info = replay.stat()
            identity = (str(replay), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            good = json.dumps(dict(abi=1, total_ticks=100,
                                   players=[dict(id=1, name='Player')],
                                   kills=[dict(tick=20, killer=1, victim=1)])).encode()
            deep = b'[' * 12000 + b'0' + b']' * 12000
            self.assertLess(len(deep), 512 * 1024)
            for raw, status in ((deep, 3), (b'{bad', 3), (good, 2)):
                for current in (True, False):
                    with self.subTest(status=status, current=current, depth=raw is deep):
                        owner = replay_events.ReplayEventReader()
                        owner._identity = identity if current else ('new replay',)
                        before = owner._result = replay_events.KillIndex(1)
                        process = SimpleNamespace(returncode=0, stdout=raw, stderr=b'')
                        with patch.object(runtime, 'resource_root', return_value=root), \
                                patch.object(replay_events.subprocess, 'run', return_value=process):
                            owner._read(replay, identity)
                        if current:
                            self.assertEqual(owner._result.status, status)
                            if status == 2:
                                self.assertEqual(owner._result.kills, ((20, 1, 1),))
                        else:
                            self.assertIs(owner._result, before)

    def test_graphics_library_error_is_typed_and_preserves_bytes_and_valid_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'graphics-profiles.json'
            with patch.object(graphics_profiles, 'library_path', return_value=target):
                self.assertEqual(graphics_profiles.load_library(),
                                 dict(format=1, selected='', profiles=[]))
                for raw in (b'{"profiles":' + b'[' * 12000 + b'0' + b']' * 12000 + b'}',
                            b'{bad'):
                    self.assertLess(len(raw), graphics_profiles.MAX_BYTES)
                    target.write_bytes(raw)
                    with self.assertRaises(LaunchError):
                        graphics_profiles.load_library()
                    self.assertEqual(target.read_bytes(), raw)
                identifier = 'a' * 32
                library = dict(format=1, selected=identifier, profiles=[
                    dict(id=identifier, name='Owned preset', video_version='20',
                         values={'setting.r_effects_bloom': '0'})])
                raw = json.dumps(library).encode()
                target.write_bytes(raw)
                self.assertEqual(graphics_profiles.load_library(), library)
                self.assertEqual(target.read_bytes(), raw)
