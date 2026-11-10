"""Recorder uncertainty and updater recovery/publication contracts.

Owned file fixtures, controlled controller/native packets and primary fsync fault.
The temporary lock is a real Windows handle; no game, installed app or network.
"""
import contextlib
import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from dolly import video_export as m, updater as u, update_worker as w
from dolly import release_launcher as launcher, media_transport, media_wire as wire
from dolly.video_export import VideoExport, VideoOptions
from dolly.gui import DollyApp

def stop_probe(root, mode, *, pov=False):
    d = root
    d.mkdir(parents=True, exist_ok=True)
    events = []
    native = dict(state='idle', ack=0)
    failure = OSError('owned command acknowledgement lost')
    controller = NS(_session=object(), _demo='owned.dem', _export_timing=None, _export_resolution=None, _pov_active=False, status=lambda: dict(game_running=True, connected=True, camera_backend='native', startup_stage='editing_ready', playing=False), _require_demo=lambda **kwargs: None, mark_recording_pending=lambda *args, **kwargs: None)

    def timing(*args):
        controller._export_timing = dict(fps=60)
        events.append('set timing')

    def scale():
        controller._export_resolution = 0.75
        events.append('set scale')

    def clear_timing():
        events.append('restore timing')
        controller._export_timing = None

    def clear_scale():
        events.append('restore scale')
        controller._export_resolution = None

    def finish_pov():
        events.append('restore POV')
        controller._pov_active = False
    controller.set_export_timing = timing
    controller.set_export_resolution = scale
    controller.clear_export_timing = clear_timing
    controller.clear_export_resolution = clear_scale
    controller.finish_pov_recording = finish_pov
    controller.prepare_pov_recording = lambda project: setattr(controller, '_pov_active', True)

    def start(*args, **kwargs):
        native.update(state='recording', ack=2)

    def stop(*args, **kwargs):
        if mode == 'stop-error':
            raise failure
        native.update(state='finalizing')
    bridge = NS(video_status=lambda: dict(native), start_video=start, stop_video=stop)
    controller._native_bridge = lambda: bridge
    owner = VideoExport(controller)
    options = VideoOptions(d / 'take.mp4', codec='builtin', fixed_step=True, full_resolution=True)
    if mode == 'pre-admission':
        try:
            owner.start(options, pov=pov, before_record=lambda: (_ for _ in ()).throw(failure))
        except OSError as exc:
            assert exc is failure
        else:
            raise AssertionError('pre-admission failure hidden')
        assert controller._export_timing is None and controller._export_resolution is None and (not controller._pov_active)
        return dict(pre_admission_unwinds=True)
    owner.start(options, pov=pov)
    stop_owner = owner.stop
    app = NS(busy=False, video_export=owner, clip_audio=None, _video_operation_done=lambda status: None, _submit=lambda label, operation, callback: operation())
    with patch.object(owner, 'stop', side_effect=lambda **kwargs: stop_owner(timeout=0, **kwargs)):
        try:
            DollyApp._stop_video_recording(app)
        except (RuntimeError, OSError):
            pass
        else:
            raise AssertionError('active recorder was accepted as terminal')
    assert native['state'] in ('recording', 'finalizing')
    assert controller._export_timing is not None and controller._export_resolution == 0.75, 'runtime settings restored before terminal recorder status: ' + repr(events)
    assert not any((event.startswith('restore') for event in events))
    if pov:
        assert controller._pov_active
    assert owner.finish_pending
    try:
        owner.start(options)
    except RuntimeError:
        pass
    else:
        raise AssertionError('second recording admitted while first unconfirmed')
    native.update(state='cancelled', ack=4)
    events.clear()
    owner.stop(cancel=True, timeout=0)
    assert events == ['restore timing', 'restore scale'] + (['restore POV'] if pov else []), events
    assert controller._export_timing is None and controller._export_resolution is None and (not controller._pov_active)
    assert not owner.finish_pending
    return dict(mode=mode, pov=pov, unconfirmed_settings_retained=True, terminal_retry_restores_once=True, new_start_refused=True)

class Transport(media_transport.MediaTransport):

    def __init__(self):
        self._lock = threading.RLock()
        self._closed = False
        self._atomic32 = None
        self._init_media()
        self._media_mapping = bytearray(wire.MAPPING_BYTES)

    def packet(self, state='recording', bad=False):
        raw = wire.STATUS.pack(b'BADMAGIC' if bad else b'DLYMDS01', 2, wire.ABI, 2, 0, wire.VIDEO_STATES.index(state), 60, 1920, 1080, 0, 0, 10, 0, 10000000, 0, 0, b'', b'', b'', 123, 1000)
        self._media_mapping[wire.STATUS_OFFSET:wire.STATUS_OFFSET + len(raw)] = raw

    def stop_video(self, **kwargs):
        pass

def transport_probe(root, exited=False, controller_unreadable=False):
    marks = []
    writes = []
    transport = Transport()
    transport.packet()
    controller = NS(_session=object(), _demo='owned.dem', _export_timing=dict(fps=60), _export_resolution=0.75, status=lambda: dict(game_running=not exited), _require_demo=lambda **kwargs: None, mark_recording_pending=lambda active, **kwargs: marks.append(active))

    def clear_timing():
        writes.append('timing')
        controller._export_timing = None

    def clear_scale():
        writes.append('scale')
        controller._export_resolution = None
    controller.clear_export_timing = clear_timing
    controller.clear_export_resolution = clear_scale
    owner = VideoExport(controller)
    owner._bridge = transport
    owner._finish_pending = True
    assert owner.status()['state'] == 'recording' if not exited else True
    if exited:
        owner._last = dict(state='recording', ack=2)
    marks.clear()
    transport.packet(bad=True)
    if controller_unreadable:
        controller.status = lambda: (_ for _ in ()).throw(OSError('owned process status unavailable'))
    result = owner.status()
    if exited:
        assert result['state'] == 'failed' and marks == [False]
        return dict(confirmed_exit_terminal=True)
    assert result['state'] == 'recording', 'transport failure became native terminal state'
    assert not marks and controller._export_timing is not None
    assert 'unavailable' in m.format_video_status(result).lower()
    try:
        owner.start(VideoOptions(root / 'replacement.mp4', codec='builtin'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('replacement admitted during status uncertainty')
    try:
        owner.stop(timeout=0)
    except RuntimeError:
        pass
    else:
        raise AssertionError('stop incorrectly reported complete')
    assert not writes and (not marks)
    controller.status = lambda: dict(game_running=True)
    transport.packet(state='cancelled')
    terminal = owner.stop(cancel=True, timeout=0)
    assert terminal['state'] == 'cancelled' and (not terminal.get('status_unavailable'))
    assert writes == ['timing', 'scale'] and (not owner.finish_pending)
    return dict(real_media_transport_and_decoder=True, unknown_not_terminal=True, terminal_retry_restores=True, controller_unreadable=controller_unreadable)

def workspace_probe(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in ('.dolly-update-a', '.dolly-update-b'):
        payload = root / name / 'payload'
        payload.mkdir(parents=True, exist_ok=True)
        for filename in ('Dolly.exe', '_internal/DollyApp.exe', '_internal/native/bin/win64/DollyNative.dll', '_internal/third_party/ffmpeg/bin/ffmpeg.exe'):
            file = payload / filename
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'owned inert payload')
        u.write_manifest(payload, '0.7.26-beta')
        (root / name / 'verified.json').write_text(json.dumps(dict(version='0.7.26-beta', sha256='a' * 64)), encoding='utf-8')
    broken = root / '.dolly-update-a' / 'payload' / u.MANIFEST
    broken.write_bytes(b'[' * 10000 + b']' * 10000)
    try:
        result = u.staged_payload(root, '0.7.26-beta', 'a' * 64)
    except RecursionError as exc:
        raise AssertionError('recursive unrelated workspace blocks valid staged discovery') from exc
    assert result == root / '.dolly-update-b'
    assert broken.read_bytes() == b'[' * 10000 + b']' * 10000
    return dict(actual_workspace_discovery_skips_bad_manifest=True, valid_stage_reused=True, invalid_bytes_preserved=True)

def read_budget_probe(root):
    root.mkdir(parents=True, exist_ok=True)
    p = root / u.MANIFEST
    valid = dict(schema=2, version='0.7.26-beta', files={n: '0' * 64 for n in ('Dolly.exe', '_internal/DollyApp.exe', '_internal/native/bin/win64/DollyNative.dll', '_internal/third_party/ffmpeg/bin/ffmpeg.exe')})
    raw = json.dumps(valid).encode()
    p.write_bytes(raw)
    assert u.read_manifest(root) == valid
    requests = []
    realopen = Path.open

    class Growing:

        def __init__(self, binary):
            self.binary = binary

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n=-1):
            requests.append(n)
            data = raw + b' ' * (8 * 1024 * 1024 + 1 - len(raw))
            data = data if n < 0 else data[:n]
            return data if self.binary else data.decode('utf-8')

    def opened(path, *args, **kwargs):
        return Growing('b' in (args[0] if args else kwargs.get('mode', 'r'))) if path == p else realopen(path, *args, **kwargs)
    with patch.object(Path, 'open', opened):
        try:
            u.read_manifest(root)
        except ValueError:
            pass
        else:
            raise AssertionError('manifest actual read exceeded stated budget')
    assert requests == [8 * 1024 * 1024 + 1], requests
    return dict(actual_read_request=requests[0], normal_required_fields_kept=True)

def cleanup_probe(root, kind):
    target = root / 'installed'
    target.mkdir(parents=True, exist_ok=True)
    legacy = kind.startswith('legacy-')
    work = (root if legacy else target) / '.dolly-update-owned'
    backup = work / 'backup/prior.bin'
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(b'owned prior application bytes')
    state = kind.removeprefix('legacy-')
    values = {'missing': {}, 'unknown': {'state': 'future'}, 'list': [], 'unhashable': {'state': []}, 'applying': {'state': 'applying'}, 'complete': {'state': 'complete'}, 'rolled_back': {'state': 'rolled_back'}, 'kept': {'state': 'complete'}, 'pending': {'state': 'complete'}}
    if state not in ('recent', 'old'):
        (work / 'journal.json').write_text('[' * 10000 + ']' * 10000 if state == 'recursive' else json.dumps(values[state]), encoding='utf-8')
    if legacy:
        (work / 'plan.json').write_text('[' * 10000 + ']' * 10000 if state == 'recursive' else '[]', encoding='utf-8')
    if state == 'old':
        os.utime(work, (time.time() - 7200,) * 2)
    if state == 'pending':
        (target / w.PENDING).write_text('{}', encoding='utf-8')
    called = []
    if state in ('kept', 'pending'):
        with w.install_lock(target):
            w.cleanup_workspaces(target, keep=work if state == 'kept' else None)
    else:
        with patch.object(launcher, 'open_editor', lambda *a, **kw: called.append(True)):
            assert launcher.main([], target=target) == 0
        assert called == [True]
    should_remove = state in ('complete', 'rolled_back', 'old') and (not legacy)
    assert backup.exists() != should_remove, ('workspace wrongly classified', kind)
    if not should_remove:
        assert backup.read_bytes() == b'owned prior application bytes'
    return dict(kind=kind, retained=not should_remove, launcher_opened=bool(called), real_install_lock=True)

def temp_probe(root, helper, fail=True):
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'target'
    source = root / 'source'
    target.write_bytes(b'old target')
    source.write_bytes(b'new source')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    handles = []
    temporary = []
    primary = OSError('owned durable flush failed')
    observed = None

    def fsync(fd):
        paths = list(root.glob('.dolly-*'))
        assert len(paths) == 1
        temporary.extend(paths)
        handle = api.CreateFileW(str(paths[0]), 2147483648, 3, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        handles.append(handle)
        raise primary
    try:
        with patch.object(w.os, 'fsync', fsync) if fail else contextlib.nullcontext():
            try:
                if helper == 'atomic_json':
                    w.atomic_json(target, {'new': True})
                else:
                    w.replace_file(source, target)
            except OSError as exc:
                observed = exc
        if fail:
            assert handles and temporary, 'did not exercise the actual Windows sharing lock'
            assert target.read_bytes() == b'old target'
            assert observed is primary, 'temporary unlink masked primary durable-write failure: ' + repr(observed)
            assert temporary[0].exists()
        else:
            assert observed is None
            assert json.loads(target.read_text()) == {'new': True} if helper == 'atomic_json' else target.read_bytes() == b'new source'
            assert not list(root.glob('.dolly-*'))
        return dict(helper=helper, real_delete_sharing_lock=fail, primary_preserved=fail, normal_write=not fail)
    finally:
        for handle in handles:
            assert api.CloseHandle(handle)
        for path in temporary:
            path.unlink(missing_ok=True)

class RecorderUpdaterTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='dolly-recorder-updater-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_stop_retains_runtime_until_terminal_and_pre_admission_unwinds(self):
        for mode in ('stop-error', 'timeout', 'pre-admission'):
            for pov in (False, True):
                with self.subTest(mode=mode, pov=pov):
                    stop_probe(self.root / (mode + str(pov)), mode, pov=pov)

    def test_status_read_uncertainty_holds_owner_until_terminal_or_known_exit(self):
        transport_probe(self.root)
        transport_probe(self.root, controller_unreadable=True)
        transport_probe(self.root, exited=True)

    def test_manifest_bounds_actual_read_and_bad_stage_does_not_hide_good_stage(self):
        workspace_probe(self.root / 'stages')
        read_budget_probe(self.root / 'read-budget')

    def test_cleanup_requires_known_terminal_journal_and_respects_recovery_guards(self):
        for kind in ('missing', 'unknown', 'list', 'unhashable', 'recursive', 'applying', 'complete', 'rolled_back', 'kept', 'pending', 'recent', 'old', 'legacy-list', 'legacy-recursive'):
            with self.subTest(kind=kind):
                cleanup_probe(self.root / kind, kind)

    @unittest.skipUnless(os.name == 'nt', 'Windows temporary-file sharing contract')
    def test_updater_locked_temporary_preserves_primary_error_and_normal_write(self):
        for helper in ('atomic_json', 'replace_file'):
            for fail in (True, False):
                with self.subTest(helper=helper, fail=fail):
                    temp_probe(self.root / (helper + str(fail)), helper, fail)
