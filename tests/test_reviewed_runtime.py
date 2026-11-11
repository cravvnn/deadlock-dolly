"""Encoder ownership, metadata admission and completed-result regressions.

Only owned temporary files/child processes; no FFmpeg, game or network.
"""
from pathlib import Path
import contextlib, copy, ctypes, io, json, os, struct, subprocess, sys, tempfile, threading, unittest
from types import SimpleNamespace
from unittest.mock import patch
from dolly import player_layer as pl, definitions
from dolly.video_export import VideoExport
from dolly.path import Project, Keyframe
from dolly.gui import DollyApp
REAL_POPEN = subprocess.Popen
REAL_THREAD = threading.Thread
REAL_TIMER = threading.Timer
if os.name == 'nt':
    K = ctypes.WinDLL('kernel32', use_last_error=True)
    K.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    K.CreateFileW.restype = ctypes.c_void_p
    K.CloseHandle.argtypes = [ctypes.c_void_p]
    K.CloseHandle.restype = ctypes.c_int
R = Path(__file__).resolve().parents[1]
def encoder_probe(C, label, *, refusal=False, locked=False, timer_refusal=False):
    d = C / 'owned' / label
    d.mkdir(parents=True, exist_ok=True)
    bundle = d / 'capture.bin'
    bundle.write_bytes((struct.pack('<8Q', pl.BUNDLE_MAGIC, 1, 1, 1, 1, 1, 0, 8) + struct.pack('<4e', 0.25, 0.25, 0.25, 2 if locked else 1)) * 2)
    output = d / 'players.mov'
    output.write_bytes(b'prior MOV retained')
    partial = d / 'owned.partial.mov'
    children = []
    handles = []
    generators = []
    real_iter = pl.iter_frames

    def frames(path):
        g = real_iter(path)
        generators.append(g)
        return g

    def popen(command, **kwargs):
        child = REAL_POPEN([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=kwargs['stdin'], stdout=kwargs['stdout'], stderr=kwargs['stderr'], creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(child)
        partial.write_bytes(b'partial output')
        if locked:
            h = K.CreateFileW(str(partial), 2147483648, 3, None, 3, 0, None)
            assert h not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
            handles.append(h)
        return child

    def refused_thread(*args, **kwargs):
        t = REAL_THREAD(*args, **kwargs)

        def fail():
            raise RuntimeError('owned cancellation-monitor admission refused')
        t.start = fail
        return t

    def refused_timer(*args, **kwargs):
        t = REAL_TIMER(*args, **kwargs)

        def fail():
            raise RuntimeError('owned watchdog admission refused')
        t.start = fail
        return t
    error = None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(pl.subprocess, 'Popen', side_effect=popen))
            stack.enter_context(patch.object(pl, 'iter_frames', side_effect=frames))
            if refusal:
                stack.enter_context(patch.object(pl.threading, 'Thread', side_effect=refused_thread))
            if timer_refusal:
                stack.enter_context(patch.object(pl.threading, 'Timer', side_effect=refused_timer))
            try:
                pl.encode_bundle(Path(sys.executable), bundle, output, cancelled=lambda: not locked, partial_path=partial)
            except Exception as exc:
                error = exc
        assert error is not None
        result = dict(error=type(error).__name__, message=str(error), child_retired=bool(children and children[0].poll() is not None), pipe_closed=bool(children and children[0].stdin.closed), frames_closed=bool(generators and generators[0].gi_frame is None), prior_output_preserved=output.read_bytes() == b'prior MOV retained', partial_retained=partial.exists())
        assert result['prior_output_preserved']
        return result
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if child.stdin and (not child.stdin.closed):
                child.stdin.close()
        for g in generators:
            g.close()
        for h in handles:
            assert K.CloseHandle(h)
        partial.unlink(missing_ok=True)

def audit_probe(C):
    d = C / 'owned/audit'
    d.mkdir(parents=True, exist_ok=True)
    owner = VideoExport(SimpleNamespace())
    owner._layer_folder = d
    status = dict(state='completed', frames_written=3000)
    original_is_dir = Path.is_dir

    def check(path):
        if path == d:
            raise PermissionError('owned take-folder access refused')
        return original_is_dir(path)
    with patch.object(Path, 'is_dir', check), patch('dolly.export_audit.audit_take') as audit:
        result = owner._finish_terminal(status, cancel=False)
        assert not audit.called and result == status and (owner._finished_status == status)
        assert owner._finish_terminal(status, cancel=False) == status
    owner._finished_status = None
    owner._layer_folder = d / 'absent'
    assert owner._finish_terminal(status, cancel=False) == status
    owner._finished_status = None
    owner._layer_folder = d
    with patch('dolly.export_audit.audit_take', return_value={'findings': [], 'ok': True}) as audit:
        result = owner._finish_terminal(status, cancel=False)
        assert result['audit']['ok'] and audit.call_count == 1
    return dict(completed_status_retained=True, completion_cached=True, missing_folder_allowed=True, normal_audit_attached=True)

def definitions_probe(C):
    d = C / 'owned/definitions'
    d.mkdir(parents=True, exist_ok=True)
    cache = d / 'definitions.json'
    nested = b'[' * 10000 + b']' * 10000
    assert len(nested) < definitions.MAX_BYTES
    cache.write_bytes(nested)
    saved = definitions._ACTIVE
    try:
        with patch.object(definitions, 'cache_path', return_value=cache):
            definitions.reset()
            from dolly import launcher
            assert launcher.game_pins() == launcher.merged_pins(launcher.NATIVE_GAME_SHA256, {})
            assert definitions.current() is definitions.EMPTY
            assert definitions.current() is definitions.EMPTY and cache.read_bytes() == nested
            document = definitions.decode((R / 'definitions.json').read_bytes())
            definitions.adopt(document)
            with patch.object(definitions, 'fetch', return_value=nested):
                try:
                    definitions.refresh()
                except definitions.DefinitionsError as exc:
                    assert isinstance(exc.__cause__, RecursionError)
                else:
                    raise AssertionError('recursive download accepted')
            assert definitions._ACTIVE is document and cache.read_bytes() == nested
        return dict(actual_launcher_pins_fall_back=True, first_cache_read_falls_back=True, retry_stable=True, failed_refresh_preserves_active_and_disk=True, valid_bundled_document=True)
    finally:
        definitions._ACTIVE = saved

class ReviewedRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='dolly-runtime-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_camera_commit_detaches_retained_and_new_keys(self):
        project = Project(keyframes=[Keyframe(0,0,0,0,0,0,0), Keyframe(1,10,0,0,0,0,0)])
        added = Keyframe(2,20,0,0,0,0,0)
        original = copy.deepcopy(project); published = []
        app = SimpleNamespace(project=project, _commit_project=lambda p,t: published.append(p))
        DollyApp._commit_camera(app, [*project.keyframes, added], 2)
        result = published[0]; result.keyframes[0].x = 99
        self.assertEqual(project, original)
        result.keyframes[2].x = 88; self.assertEqual(added.x, 20)
        project.keyframes[1].x = 77; self.assertEqual(result.keyframes[1].x, 10)
        from dolly.ui.shot_commands import commit_camera
        self.assertEqual(len(commit_camera(original, iter(original.keyframes)).keyframes), 2)

    @unittest.skipUnless(os.name == 'nt', 'Windows child/handle ownership')
    def test_refused_monitor_retires_encoder_and_preserves_primary(self):
        for key in ('refusal', 'timer_refusal'):
            value = encoder_probe(self.root, key, **{key: True})
            self.assertEqual(value['message'], 'owned cancellation-monitor admission refused' if key == 'refusal' else 'owned watchdog admission refused')
            self.assertTrue(all(value[k] for k in ('child_retired','pipe_closed','frames_closed','prior_output_preserved')))

    @unittest.skipUnless(os.name == 'nt', 'Windows delete-sharing lock')
    def test_locked_partial_keeps_primary_error_and_prior_mov(self):
        value = encoder_probe(self.root, 'locked', locked=True)
        self.assertEqual((value['error'], value['message']), ('ValueError', 'Invalid Players alpha.'))
        self.assertTrue(all(value[k] for k in ('child_retired','pipe_closed','frames_closed','prior_output_preserved','partial_retained')))

    def test_audit_folder_failure_keeps_cached_completed_status(self):
        with self.assertLogs('dolly', level='ERROR'):
            audit_probe(self.root)

    def test_recursive_cache_keeps_bundled_pins_and_rejected_refresh(self):
        definitions_probe(self.root)

    def test_players_admits_realistic_timing_rows_with_bounded_utf8_read(self):
        color = self.root/'color.mov'; color.write_bytes(b'owned inert video')
        frames = 6000
        data = dict(format='deadlock-dolly-shot', version=1, video_file=color.name, fps=60,
                    fixed_step=True, frames_written=frames, first_frame=0, last_frame=frames-1,
                    movie_audio=dict(version=1, complete=True, frames=[
                        [0x5a44, (i*44100+30)//60, ((i+1)*44100+30)//60, i, 44100] for i in range(frames)]))
        sidecar = self.root/'shot.json'; raw = json.dumps(data).encode('utf-8')
        self.assertGreater(len(raw), 128*1024)
        sidecar.write_bytes(raw); self.assertEqual(pl.reference_frame_count(color,60), frames)
        for changes in ({'video_file':'other.mov'}, {'fps':30}, {'frames_written':True}, {'fixed_step':False}, {'last_frame':2}):
            sidecar.write_text(json.dumps({**data, **changes}), encoding='utf-8')
            with self.assertRaises(RuntimeError): pl.reference_frame_count(color,60)
        for invalid in (json.dumps(data).encode('utf-16'), b'['*10000+b']'*10000):
            sidecar.write_bytes(invalid)
            with self.assertRaises(RuntimeError): pl.reference_frame_count(color,60)
        reads = []; real_open = Path.open
        class Growing(io.BytesIO):
            def read(self, n=-1):
                reads.append(n)
                return b'x'*n
        def opened(path, *args, **kwargs):
            return Growing() if path == sidecar else real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            with self.assertRaises(RuntimeError): pl.reference_frame_count(color,60)
        self.assertEqual(reads, [40*1024*1024+1])
