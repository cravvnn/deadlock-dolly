import math
import os
import random
import struct
import subprocess
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace

from dolly import player_layer
from dolly.player_pixels import convert_rgba, CHUNK_PIXELS
from dolly.player_encode import encode_in_worker, resource_policy, ProcessJob


def scalar(pixels):
    result = bytearray()
    for r, g, b, a in struct.iter_unpack('<4e', pixels):
        result.extend([*(player_layer._preview_channel(v/a) for v in (r,g,b)), round(a*255)]
                      if a else [0,0,0,0])
    return result


class PixelTests(unittest.TestCase):
    def test_all_finite_half_colors_match_scalar_across_chunks(self):
        pixels = bytearray()
        for bits in range(65536):
            value = struct.unpack('<e', struct.pack('<H', bits))[0]
            if math.isfinite(value):
                for a in (1.0, .5, 2**-14):
                    pixels.extend(struct.pack('<4e', value, value, value, a))
        self.assertEqual(convert_rgba(pixels), scalar(pixels))

    def test_random_premultiplied_edges_and_transparency_match(self):
        rng = random.Random(810)
        data = bytearray(struct.pack('<4e', float('nan'), float('inf'), -1, 0))
        for _ in range(20000):
            a = rng.choice((0, 1, .5, rng.random(), 2**-24))
            data.extend(struct.pack('<4e', rng.uniform(-1, 16)*a, .0031308*a,
                                    rng.random()*a, a))
        self.assertEqual(convert_rgba(data), scalar(data))

    def test_invalid_pixels_and_cancellation(self):
        for a in (-1, 2, float('nan'), float('inf')):
            with self.assertRaisesRegex(ValueError, 'alpha'):
                convert_rgba(struct.pack('<4e', 1,1,1,a))
        with self.assertRaises(ValueError): convert_rgba(b'x')
        with self.assertRaises(InterruptedError):
            convert_rgba(b'\0'*8, cancelled=lambda: True)
        with self.assertRaisesRegex(ValueError, 'color'):
            convert_rgba(struct.pack('<4e', float('nan'),1,1,1))

    def test_source_fallback_matches_vector_path(self):
        import builtins
        original = builtins.__import__
        def imports(name, *args, **kwargs):
            if name == 'numpy': raise ImportError('optional source dependency')
            return original(name, *args, **kwargs)
        data = struct.pack('<4e', .1, .2, .3, .5) * 10
        expected = convert_rgba(data)
        with patch('builtins.__import__', side_effect=imports):
            self.assertEqual(convert_rgba(data), expected)


class ResourceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing contract')
    def test_progress_reader_cannot_abort_encoding(self):
        from dolly.player_encode import _write_json, _write_progress
        import json
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            progress = folder / 'progress.json'
            _write_json(progress, dict(frames=1))
            # A normal Windows Python reader denies deletion/replacement.
            with progress.open('rb'):
                with self.assertRaises(PermissionError):
                    _write_json(progress, dict(frames=2))
                _write_progress(folder, 2)
            self.assertEqual(json.loads(progress.read_text())['frames'], 1)
            _write_progress(folder, 3)
            self.assertEqual(json.loads(progress.read_text())['frames'], 3)

    def test_cancel_button_reaches_processing_while_editor_is_busy(self):
        from dolly.gui import DollyApp
        event = threading.Event()
        app = SimpleNamespace(_player_encode_cancel=event, busy=True,
                              status_text=Mock(), video_export=Mock())
        DollyApp._stop_video_recording(app, cancel=True)
        self.assertTrue(event.is_set())
        app.video_export.stop.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows process lifetime contract')
    def test_job_close_terminates_worker_and_its_child(self):
        import ctypes as c
        from ctypes import wintypes as w
        api=c.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];api.OpenProcess.restype=w.HANDLE
        api.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];api.WaitForSingleObject.restype=w.DWORD
        api.CloseHandle.argtypes=[w.HANDLE];api.CloseHandle.restype=w.BOOL
        with tempfile.TemporaryDirectory() as folder:
            child_file=Path(folder)/'child.txt'
            command='import sys,subprocess,time; from pathlib import Path; sys.stdin.readline(); p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)'
            process=subprocess.Popen([sys.executable,'-c',command,str(child_file)],stdin=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            job=None;child=None
            try:
                job=ProcessJob(process)
                process.stdin.write(b'go\n');process.stdin.close()
                deadline=time.monotonic()+5
                while not child_file.exists() and time.monotonic()<deadline: time.sleep(.05)
                self.assertTrue(child_file.exists())
                child=api.OpenProcess(0x100000,False,int(child_file.read_text()))
                self.assertTrue(child)
                job.close()
                self.assertEqual(api.WaitForSingleObject(child,3000),0)
                process.wait(timeout=3)
            finally:
                if job: job.close()
                if process.poll() is None: process.kill();process.wait(timeout=3)
                if child: api.CloseHandle(child)

    def test_modes_are_bounded_and_invalid_mode_fails(self):
        self.assertEqual([resource_policy(m)[0] for m in ('Background','Balanced','Fast')], [1,2,4])
        with self.assertRaises(ValueError): resource_policy('Unlimited')

    def test_cancel_unblocks_encoder_pipe_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)/'raw.bin'; output = Path(folder)/'players.mov'
            bundle.write_bytes(struct.pack('<8Q',player_layer.BUNDLE_MAGIC,1,1,1,0,0,0,8)+struct.pack('<4e',1,1,1,1))
            output.write_bytes(b'previous')
            cancel = threading.Event(); killed = threading.Event()
            process = Mock()
            def write(data):
                cancel.set()
                if not killed.wait(2): raise AssertionError('cancel did not unblock pipe')
                raise OSError('closed pipe')
            process.stdin.write.side_effect = write
            process.kill.side_effect = killed.set
            process.poll.side_effect = lambda: 1 if killed.is_set() else None
            process.wait.return_value = 1
            def spawn(command, **kwargs):
                self.assertIn('-filter_threads', command)
                self.assertEqual(command[command.index('-filter_threads')+1], '1')
                self.assertEqual(kwargs['creationflags'], getattr(subprocess,'CREATE_NO_WINDOW',0) | resource_policy('Background')[1])
                return process
            with patch.object(player_layer.subprocess,'Popen',side_effect=spawn):
                with self.assertRaises((RuntimeError, InterruptedError)):
                    player_layer.encode_bundle(Path('ffmpeg'),bundle,output,resource_mode='Background',cancelled=cancel.is_set)
            self.assertTrue(killed.is_set())
            self.assertEqual(output.read_bytes(),b'previous')
            self.assertEqual(list(Path(folder).glob('*.partial.mov')),[])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        from dolly.video_export import bundled_ffmpeg_path
        self.ffmpeg = bundled_ffmpeg_path()
        if self.ffmpeg is None:
            self.skipTest('Packaged FFmpeg is required for isolated worker integration')
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.bundle = self.root/'raw.bin'; self.output = self.root/'players.mov'
        self.output.write_bytes(b'previous')
        with self.bundle.open('wb') as stream:
            for i in range(4):
                stream.write(struct.pack('<8Q',player_layer.BUNDLE_MAGIC,32,32,1,0,0,0,8))
                stream.write(struct.pack('<4e',.5,.25,.125,1)*512 + b'\0'*8*512)

    def test_worker_software_roundtrip_preserves_alpha_and_raw(self):
        raw = self.bundle.read_bytes()
        encode_in_worker(self.ffmpeg,self.bundle,self.output,fps=30)
        result = subprocess.run([str(self.ffmpeg),'-v','error','-i',str(self.output),'-f','rawvideo',
                                 '-pix_fmt','rgba','pipe:1'],capture_output=True,check=True,timeout=20,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.stdout[3::4],(b'\xff'*512+b'\0'*512)*4)
        self.assertEqual(self.bundle.read_bytes(),raw)
        self.assertEqual(list(self.root.glob('*.partial.mov')),[])
        self.assertEqual(list(self.root.glob('*.worker.mov')),[])

    @unittest.skipUnless(os.name == 'nt', 'Windows priority contract')
    def test_background_worker_receives_lower_priority(self):
        import ctypes as c
        from ctypes import wintypes as w
        api=c.WinDLL('kernel32',use_last_error=True)
        api.GetPriorityClass.argtypes=[w.HANDLE];api.GetPriorityClass.restype=w.DWORD
        spawn=subprocess.Popen
        classes=[]
        def observed(*args,**kwargs):
            process=spawn(*args,**kwargs)
            classes.append(api.GetPriorityClass(int(process._handle)))
            return process
        with patch('dolly.player_encode.subprocess.Popen',side_effect=observed):
            encode_in_worker(self.ffmpeg,self.bundle,self.output,resource_mode='Background')
        self.assertEqual(classes,[subprocess.BELOW_NORMAL_PRIORITY_CLASS])

    def test_cancel_and_failure_keep_existing_mov(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):
            encode_in_worker(self.ffmpeg,self.bundle,self.output,cancel=cancel)
        self.bundle.write_bytes(b'truncated')
        with self.assertRaisesRegex(RuntimeError,'truncated'):
            encode_in_worker(self.ffmpeg,self.bundle,self.output)
        self.assertEqual(self.output.read_bytes(),b'previous')
        self.assertEqual(list(self.root.glob('*.partial.mov')),[])
        self.assertEqual(list(self.root.glob('*.worker.mov')),[])


if __name__ == '__main__': unittest.main()
