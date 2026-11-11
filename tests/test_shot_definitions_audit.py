"""Shot snapshot, definitions publication and take-evidence owner contracts.

The GUI picker, network fetch and process observations are controlled. Reads,
cache writes, threads and validators are real. Video bytes test presence only.
The camera reader checks its actual default request and a small boundary fixture.
"""
from pathlib import Path
import contextlib
import copy
import io
import json
import threading
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
from dolly import definitions as d, gui, camera_bundle as cb, export_audit as audit
from dolly.path import Project, Keyframe
from dolly.shot_history import ShotHistory
from test_core_install import doc
from test_review_hardening import camera_document

def open_probe(root, *, bom=False, swap=False, legacy=False, invalid=False):
    root.mkdir(parents=True, exist_ok=True)
    file = root / 'shot.json'
    raw = Project(name='owned source', keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6, 90)]).to_dict()
    if legacy:
        raw = {k: v for k, v in raw.items() if k in ('format', 'version', 'name', 'interpolation', 'rotation_mode', 'start_tick', 'tick_rate', 'setup_values', 'keyframes', 'tracks')}
        raw['version'] = 1
        raw['keyframes'] = [{k: v for k, v in raw['keyframes'][0].items() if k in ('time', 'x', 'y', 'z', 'pitch', 'yaw', 'roll', 'fov')}]
    value = json.dumps(raw).encode()
    value = (b'\xef\xbb\xbf' if bom else b'') + value
    if invalid:
        value = b'{"version": 1,"version": 1}'
    file.write_bytes(value)
    original = Project(name='previous shot')
    errors = []
    messages = []
    history = ShotHistory(original)
    refresh = []
    app = NS(root=None, busy=False, playing=False, project=original, shot_history=history, _allow_discard=lambda: True, status_text=NS(set=messages.append), full_editor=NS(get=lambda: False), _refresh_project=lambda: refresh.append(True), _set_time=lambda time: None, _offer_replay_tick_rate=lambda: None, _error=lambda title, error: errors.append(error))
    app._guard = lambda title, fn: gui.DollyApp._guard(app, title, fn)
    realopen = Path.open
    opens = []
    replacement = Project(name='external replacement', keyframes=[Keyframe(0, 1, 2, 3, 4, 5, 6, 90)]).to_dict()
    if not legacy:
        replacement = {k: v for k, v in replacement.items() if k in ('format', 'version', 'name', 'interpolation', 'rotation_mode', 'start_tick', 'tick_rate', 'setup_values', 'keyframes', 'tracks')}
        replacement['version'] = 1
        replacement['keyframes'] = [{k: v for k, v in replacement['keyframes'][0].items() if k in ('time', 'x', 'y', 'z', 'pitch', 'yaw', 'roll', 'fov')}]
    replaced_bytes = json.dumps(replacement).encode()
    loader_name = 'load_versioned' if hasattr(Project, 'load_versioned') else 'load'
    original_loader = getattr(Project, loader_name)

    def opened(path, *args, **kwargs):
        if path == file:
            opens.append(args[0] if args else kwargs.get('mode', 'r'))
        return realopen(path, *args, **kwargs)

    def loaded(*args, **kwargs):
        result = original_loader(*args, **kwargs)
        if swap:
            file.write_bytes(replaced_bytes)
        return result
    with patch.object(gui.filedialog, 'askopenfilename', return_value=str(file)), patch.object(Path, 'open', opened), patch.object(Project, loader_name, side_effect=loaded):
        gui.DollyApp.open(app)
    if invalid:
        assert errors and app.project is original and (not refresh) and (file.read_bytes() == value)
        return
    assert not errors, 'validated shot rejected by second reader: ' + repr(errors)
    assert app.project.name == 'owned source' and app.file_path == file and (history._saved == app.project) and (refresh == [True])
    assert ('imported' in messages[-1].lower()) == legacy, 'migration notice describes a different read than the loaded shot'
    assert [mode for mode in opens if 'r' in mode] == ['rb'], 'shot decoded more than once: ' + repr(opens)
    assert file.read_bytes() == (replaced_bytes if swap else value)
    return

def definitions_race_probe(root, mode):
    root.mkdir(parents=True, exist_ok=True)
    cache = root / 'definitions.json'
    raw = json.dumps(doc(modules={'citadel/bin/win64/client.dll': {'accepted': ['a' * 64]}})).encode()
    fresh = json.dumps(doc(modules={'citadel/bin/win64/client.dll': {'accepted': ['b' * 64]}})).encode() if mode != 'withdraw' else json.dumps(doc()).encode()
    cache.write_bytes(raw)
    expected = d.decode(fresh)
    real_load = d.load
    entered = threading.Event()
    release = threading.Event()
    b_started = threading.Event()
    b_done = threading.Event()
    a_values = []
    b_values = []
    errors = []
    loads = []

    def paused(path):
        value = real_load(path)
        loads.append(True)
        entered.set()
        assert release.wait(3), 'owned reader release missing'
        return value

    def run_a():
        try:
            a_values.append(d.current())
        except BaseException as exc:
            errors.append(exc)

    def run_b():
        b_started.set()
        try:
            b_values.append(d.current() if mode == 'lookup' else d.refresh(cache=cache))
        except BaseException as exc:
            errors.append(exc)
        finally:
            b_done.set()
    saved = d._ACTIVE
    try:
        with patch.object(d, 'cache_path', return_value=cache), patch.object(d, 'load', paused), patch.object(d, 'fetch', return_value=fresh):
            d.reset()
            a = threading.Thread(target=run_a, name='owned-first-definition-read')
            b = threading.Thread(target=run_b, name='owned-second-definition-operation')
            a.start()
            assert entered.wait(3)
            b.start()
            assert b_started.wait(3)
            b_done.wait(0.15)
            release.set()
            a.join(3)
            b.join(3)
            assert not a.is_alive() and (not b.is_alive()) and (not errors), errors
            if mode == 'lookup':
                assert b_values and b_values[0].modules == d.decode(raw).modules, 'concurrent lookup observed provisional EMPTY'
                assert len(loads) == 1 and cache.read_bytes() == raw
            else:
                assert d.current().modules == expected.modules, 'first cached read overwrote completed refresh/withdrawal'
                assert b_values[0].modules == expected.modules and cache.read_bytes() == fresh
            d.adopt(None)
            assert d.current() is d.EMPTY
            d.reset()
            d.current()
            assert len(loads) == 2
            return
    finally:
        release.set()
        if 'a' in locals():
            a.join(3)
        if 'b' in locals():
            b.join(3)
        d._ACTIVE = saved

def write_sidecar(root, video='take.mkv'):
    root.mkdir(parents=True, exist_ok=True)
    value = dict(format='deadlock-dolly-shot', version=1, fixed_step=True, frames_written=1, first_frame=0, last_frame=0, video_file=video)
    (root / 'shot.json').write_text(json.dumps(value), encoding='utf-8')
    return value

def video_evidence_probe(root, kind):
    value = write_sidecar(root)
    video = root / 'take.mkv'
    if kind == 'missing-declaration':
        value.pop('video_file')
    elif kind == 'bad-declaration':
        value['video_file'] = []
    elif kind == 'blank-declaration':
        value['video_file'] = ''
    elif kind == 'missing-file':
        pass
    elif kind == 'empty-file':
        video.write_bytes(b'')
    else:
        video.write_bytes(b'owned nonempty video bytes; presence only, no decode claim')
    if kind == 'missing-declaration':
        video.write_bytes(b'undeclared video does not excuse malformed sidecar')
    sidecar = root / 'shot.json'
    sidecar.write_text(json.dumps(value), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = audit.audit_take(root)
    assert result['ok'] == (kind == 'good'), 'audit reported success without required nonempty video evidence: ' + kind
    assert not result['findings'] if kind == 'good' else any(('video' in s for s in result['findings']))
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    return

def recursive_camera_probe(root):
    write_sidecar(root)
    (root / 'take.mkv').write_bytes(b'owned nonempty video fixture')
    depth = root / 'depth'
    depth.mkdir(exist_ok=True)
    (depth / 'depth.mov').write_bytes(b'owned depth fixture')
    (depth / 'manifest.json').write_text(json.dumps(dict(complete=True, frames=1, master='depth.mov')), encoding='utf-8')
    path = depth / 'camera.json'
    raw = b'[' * 10000 + b']' * 10000
    path.write_bytes(raw)
    world = root / 'world'
    write_sidecar(world, 'world.mov')
    (world / 'world.mov').write_bytes(b'owned world fixture')
    try:
        result = audit.audit_take(root)
    except RecursionError as exc:
        raise AssertionError('recursive camera JSON aborted actual take audit') from exc
    assert not result['ok'] and any(('camera bundle is unreadable' in s for s in result['findings'])) and (result['takes']['world']['frames_written'] == 1)
    assert path.read_bytes() == raw
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = audit.main([str(root)])
    assert code == 1 and json.loads(out.getvalue())['takes']['world']['frames_written'] == 1
    return

def camera_budget_probe(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'camera.json'
    raw = json.dumps(camera_document()).encode()
    path.write_bytes(raw)
    actual_open = Path.open
    requests = []

    class Reader:

        def __init__(self, binary, total):
            self.binary, self.total = (binary, total)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, count=-1):
            requests.append(count)
            size = self.total if count < 0 else min(self.total, count)
            data = raw + b' ' * (size - len(raw))
            return data if self.binary else data.decode('utf-8')
    total = len(raw)

    def opening(selected, *args, **kwargs):
        if selected != path:
            return actual_open(selected, *args, **kwargs)
        mode = args[0] if args else kwargs.get('mode', 'r')
        return Reader('b' in mode, total)
    with patch.object(Path, 'open', opening):
        assert cb.load_bundle(path).width == 2
    assert requests == [128 * 1024 * 1024 + 1], requests
    for total, rejected in ((4096, False), (4097, True)):
        requests.clear()
        with patch.object(cb, '_MAX_FILE_BYTES', 4096), patch.object(Path, 'open', opening):
            if rejected:
                try:
                    cb.load_bundle(path)
                except cb.CameraBundleError:
                    pass
                else:
                    raise AssertionError('camera reader accepted one-byte overrun')
            else:
                assert cb.load_bundle(path).width == 2
        assert requests == [4097], requests
    assert path.read_bytes() == raw

class ShotDefinitionsAuditTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='dolly-shot-definitions-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_open_uses_one_strict_snapshot_for_project_and_version_notice(self):
        cases = [dict(bom=True), dict(legacy=True), dict(bom=True, legacy=True), dict(swap=True), dict(swap=True, legacy=True), dict(invalid=True)]
        for index, case in enumerate(cases):
            with self.subTest(**case):
                open_probe(self.root / str(index), **case)

    def test_definitions_first_read_cannot_expose_empty_or_overwrite_refresh(self):
        for mode in ('lookup', 'refresh', 'withdraw'):
            with self.subTest(mode=mode):
                definitions_race_probe(self.root / mode, mode)

    def test_camera_read_budget_and_typed_error_allow_audit_to_continue(self):
        camera_budget_probe(self.root / 'budget')
        recursive_camera_probe(self.root / 'recursive')

    def test_audit_requires_declared_existing_nonempty_video_evidence(self):
        for kind in ('missing-declaration', 'bad-declaration', 'blank-declaration', 'missing-file', 'empty-file', 'good'):
            with self.subTest(kind=kind):
                video_evidence_probe(self.root / kind, kind)
