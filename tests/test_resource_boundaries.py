"""Resource limits and episode/handle cleanup gaps outside the core workflows."""
from contextlib import contextmanager
from itertools import count, cycle
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dolly import definitions, game_logging, memory_watch


class ResourceBoundaryTests(unittest.TestCase):
    def test_local_definitions_bounds_bytes_and_rejects_a_valid_oversized_prefix(self):
        payload = json.dumps({'format': 1, 'native_abi': 4}).encode()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'definitions.json'
            real_open = Path.open
            received = []

            class Reader:
                def __init__(self, stream): self.stream = stream
                def read(self, *args):
                    data = self.stream.read(*args)
                    received.append(len(data))
                    return data

            @contextmanager
            def opening(path, *args, **kwargs):
                with real_open(path, *args, **kwargs) as stream:
                    yield Reader(stream)

            for size in (definitions.MAX_BYTES, definitions.MAX_BYTES + 1, 4 * definitions.MAX_BYTES):
                with self.subTest(size=size):
                    cache.write_bytes(payload + b' ' * (size - len(payload)))
                    received.clear()
                    with patch.object(Path, 'open', opening):
                        if size > definitions.MAX_BYTES:
                            with self.assertRaisesRegex(definitions.DefinitionsError, 'too large'):
                                definitions.load(cache)
                        else:
                            self.assertTrue(definitions.load(cache).empty)
                    self.assertLessEqual(sum(received), definitions.MAX_BYTES + 1)

    def test_warned_memory_episode_stops_reads_and_rearms_on_each_reset(self):
        gib = 1024 ** 3
        expected = ('Paused replay memory grew 2.0 GiB in 6s '
                    '(358 MB/s; now 2.0 GiB committed). '
                    'Save the shot and restart the game session; the engine can run out of memory while paused.')
        for reset in ('resume', 'invalid_pid', 'pid', 'tick'):
            with self.subTest(reset=reset):
                sample = Mock(side_effect=cycle([0, gib, 2 * gib]))
                clock = Mock(side_effect=count(3, 3))
                watch = memory_watch.PausedMemoryWatch(sample=sample, clock=clock)
                def warn(pid, tick):
                    self.assertIsNone(watch.observe(pid, paused=True, tick=tick))
                    self.assertIsNone(watch.observe(pid, paused=True, tick=tick))
                    self.assertEqual(watch.observe(pid, paused=True, tick=tick), expected)
                warn(1, 10)
                for _ in range(4):
                    self.assertIsNone(watch.observe(1, paused=True, tick=10))
                self.assertEqual((sample.call_count, clock.call_count), (3, 3))
                if reset == 'resume': self.assertIsNone(watch.observe(1, paused=False, tick=10))
                if reset == 'invalid_pid': self.assertIsNone(watch.observe(0, paused=True, tick=10))
                pid, tick = (2 if reset == 'pid' else 1), (11 if reset == 'tick' else 10)
                warn(pid, tick)
                self.assertEqual((sample.call_count, clock.call_count), (6, 6))
                self.assertEqual([call.args[0] for call in sample.call_args_list], [1] * 3 + [pid] * 3)

    @unittest.skipUnless(os.name == 'nt', 'Windows handle inheritance contract')
    def test_stdio_restores_remaining_handle_after_error_and_preserves_saved_flags(self):
        import msvcrt
        get, set_flag = os.get_handle_inheritable, os.set_handle_inheritable
        for saved in (False, True):
            for fail_first in (False, True):
                with self.subTest(saved=saved, fail_first=fail_first), tempfile.TemporaryFile() as errors:
                    error_handle = msvcrt.get_osfhandle(errors.fileno())
                    prior = get(error_handle)
                    handles, attempted = [], []
                    restoring = False
                    def capture(handle):
                        handles.append(handle)
                        set_flag(handle, saved)
                        return get(handle)
                    def setting(handle, value):
                        if restoring:
                            attempted.append(handle)
                            if fail_first and handle == handles[0]:
                                raise OSError('first restore refused')
                        return set_flag(handle, value)
                    def exercise():
                        nonlocal restoring
                        with game_logging.gui_stdio(errors) as options:
                            self.assertEqual(options['startupinfo'].hStdOutput, 0)
                            self.assertEqual(options['startupinfo'].lpAttributeList['handle_list'], handles)
                            self.assertTrue(options['close_fds'])
                            self.assertTrue(all(get(h) for h in handles))
                            restoring = True
                            raise RuntimeError('spawn refused')
                    try:
                        with patch.object(os, 'get_handle_inheritable', side_effect=capture), \
                             patch.object(os, 'set_handle_inheritable', side_effect=setting):
                            expected_error = OSError if fail_first else RuntimeError
                            with self.assertRaisesRegex(expected_error, 'first restore refused' if fail_first else 'spawn refused'):
                                exercise()
                        self.assertEqual(attempted, handles)
                        self.assertEqual(get(error_handle), saved)
                    finally:
                        set_flag(error_handle, prior)


if __name__ == '__main__': unittest.main()
