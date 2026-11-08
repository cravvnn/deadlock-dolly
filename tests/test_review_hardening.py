"""Compact failure-boundary and snapshot contracts from the reviewed ten-item set."""
import copy
import json
import os
from pathlib import Path
import queue
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dolly import camera_bundle, crash_dumps, depth_exr, export_audit, player_scratch
from dolly import replay_events, reshade_setup, shot_history
from dolly.ui.layer_pipeline import LayerPipeline
from dolly.ui.operations import OperationQueue


def camera_document():
    return dict(format='deadlock-dolly-camera', version=1, complete=True,
                frames=0, width=2, height=2, color_width=2, color_height=2, records=[])


def depth_frame(channel=None, kind=b'chlist'):
    def attr(name, kind, value):
        return name+b'\0'+kind+b'\0'+struct.pack('<i', len(value))+value
    if channel is None: channel = b'Z\0'+struct.pack('<4i', 2, 0, 1, 1)+b'\0'
    header = (struct.pack('<2i', 20000630, 2)+attr(b'channels', kind, channel)
        +attr(b'compression', b'compression', b'\0')
        +attr(b'dataWindow', b'box2i', struct.pack('<4i', 0, 0, 1, 0))
        +attr(b'lineOrder', b'lineOrder', b'\0')+b'\0')
    return header+struct.pack('<Q', len(header)+8)+struct.pack('<2i2f', 0, 8, 7, 14)


def minidump():
    data = bytearray(512)
    data[:4] = b'MDMP'; struct.pack_into('<3I', data, 4, 0, 2, 32)
    struct.pack_into('<3I', data, 32, 6, 56, 64)
    struct.pack_into('<3I', data, 44, 4, 112, 200)
    struct.pack_into('<2I', data, 64, 7, 0)
    struct.pack_into('<IIQQI', data, 72, 0xc0000005, 0, 0, 0x1005, 2)
    struct.pack_into('<2Q', data, 104, 0, 0xBEEF)
    struct.pack_into('<I', data, 200, 1)
    struct.pack_into('<QIIII', data, 204, 0x1000, 0x100, 0, 0, 400)
    name = 'C:/game/engine2.dll'.encode('utf-16-le')
    struct.pack_into('<I', data, 400, len(name)); data[404:404+len(name)] = name
    return data


class ReviewHardeningTests(unittest.TestCase):
    def test_layer_refusal_retains_intent_and_acceptance_preserves_worker_order(self):
        for kind in ('capture', 'combine', 'take', 'restore'):
            with self.subTest(kind=kind):
                pipeline = LayerPipeline(advance_pending=True)
                jobs = OperationQueue(); token = object(); blocked = [True]; calls = []
                field = dict(capture='pending_capture', combine='pending_combine',
                             take='queue', restore='base_capture')[kind]
                setattr(pipeline, field, [token] if kind == 'take' else token)
                def submit(label, function, callback):
                    return jobs.submit(label, function, callback, blocked=blocked[0],
                        started=lambda _: None, rejected=lambda _: None)
                args = dict(busy=False, recorder_active=lambda: False, submit=submit,
                    finish_capture=lambda t: calls.append(('capture', t)),
                    combine=lambda t: calls.append(('combine', t)),
                    start_next=lambda: calls.append(('take', token)), complete=lambda _: None,
                    audit=lambda _: None, restore=lambda: calls.append(('restore', token)))
                pipeline.advance(**args)
                self.assertTrue(pipeline.advance_pending)
                self.assertEqual(getattr(pipeline, field), [token] if kind == 'take' else token)
                self.assertTrue(jobs.jobs.empty())
                put = jobs.jobs.put
                def accepted(job):
                    self.assertFalse(pipeline.advance_pending)
                    if kind != 'take': self.assertIsNone(getattr(pipeline, field))
                    return put(job)
                blocked[0] = False
                with patch.object(jobs.jobs, 'put', side_effect=accepted): pipeline.advance(**args)
                _, function, _ = jobs.jobs.get_nowait(); function()
                self.assertEqual(calls, [(kind, token)])
                pipeline.advance(**args); self.assertTrue(jobs.jobs.empty())

    def test_replay_thread_refusal_reports_failure_and_new_identity_can_start(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = Path(directory)/'one.dem', Path(directory)/'two.dem'
            one.write_bytes(b'one'); two.write_bytes(b'two')
            reader = replay_events.ReplayEventReader()
            failed = Mock(); failed.start.side_effect = RuntimeError('thread limit')
            with patch.object(replay_events.threading, 'Thread', return_value=failed) as factory:
                first = reader.snapshot(one)
                self.assertEqual(first.status, 3)
                self.assertIn('thread limit', first.message)
                self.assertEqual(reader.snapshot(one), first)
                factory.assert_called_once()
            with patch.object(replay_events.threading, 'Thread') as factory:
                self.assertEqual(reader.snapshot(two).status, 1)
                factory.return_value.start.assert_called_once()

    def test_camera_bundle_rejects_noninteger_version_and_overflow_as_typed_errors(self):
        self.assertEqual(camera_bundle.parse_bundle(camera_document()).records, ())
        for version in (True, 1.0):
            with self.subTest(version=version):
                value = camera_document(); value['version'] = version
                with self.assertRaises(camera_bundle.CameraBundleError): camera_bundle.parse_bundle(value)
        self.assertEqual(camera_bundle._number(.125, 'near'), .125)
        with self.assertRaises(camera_bundle.CameraBundleError): camera_bundle._number(10**400, 'near')

    def test_depth_accepts_known_pixels_and_rejects_unsupported_channel_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'depth.exr'; path.write_bytes(depth_frame())
            self.assertEqual(depth_exr.read_distances(path).tolist(), [[7.0, 14.0]])
            valid = b'Z\0'+struct.pack('<4i', 2, 0, 1, 1)+b'\0'
            variants = [(b'Z\0'+struct.pack('<4i', 2, 0, 2, 1)+b'\0', b'chlist'),
                        (valid[:-1], b'chlist'), (valid+b'extra', b'chlist'), (valid, b'string')]
            for channel, kind in variants:
                with self.subTest(channel=channel, kind=kind):
                    path.write_bytes(depth_frame(channel, kind))
                    with self.assertRaises(depth_exr.DepthExrError): depth_exr.read_distances(path)

    def test_minidump_respects_stream_extents_and_preserves_valid_name_rva(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'owned.dmp'; path.write_bytes(minidump())
            report = crash_dumps.analyse(path)
            self.assertEqual((report['faulting_module'], report['module_offset'], report['access_address']),
                             ('engine2.dll', '0x5', '0xBEEF'))
            for offset, size in ((36, 4), (36, 40), (48, 4)):
                with self.subTest(offset=offset, size=size):
                    data = minidump(); struct.pack_into('<I', data, offset, size); path.write_bytes(data)
                    with self.assertRaises(ValueError): crash_dumps.analyse(path)
            data = minidump(); struct.pack_into('<3I', data, 32, 99, 500, 100)
            with self.assertRaises(ValueError): crash_dumps._streams(data)

    def test_audit_reports_actual_disappearance_and_keeps_programming_errors_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); depth = root/'depth'; depth.mkdir()
            movie = depth/'depth.mov'; movie.write_bytes(b'owned movie')
            is_file = Path.is_file
            def disappearing(path):
                found = is_file(path)
                if path == movie and found: path.unlink()
                return found
            with patch.object(Path, 'is_file', disappearing): report = export_audit.audit_take(root)
            self.assertFalse(report['ok'])
            self.assertTrue(any('could not read files' in text for text in report['findings']))
            with patch.object(export_audit, '_audit_layer', side_effect=TypeError('programming error')):
                with self.assertRaises(TypeError): export_audit.audit_take(root)

    def test_scratch_creation_preserves_error_attempts_all_cleanup_and_keeps_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); token = 'a'*32; folder = root/('dolly-players-'+token)
            write, unlink, rmdir = Path.write_text, Path.unlink, Path.rmdir
            primary = OSError('manifest write refused'); attempts = []
            def writing(path, text, *args, **kwargs):
                if path.name == player_scratch.MANIFEST:
                    path.write_bytes(b'partial'); (folder/'user-note').write_bytes(b'user'); raise primary
                return write(path, text, *args, **kwargs)
            def deleting(path, *args, **kwargs):
                if path.parent == folder:
                    attempts.append(path.name)
                    if path.name == player_scratch.OWNER: raise PermissionError('owner locked')
                return unlink(path, *args, **kwargs)
            def removing(path, *args, **kwargs):
                if path == folder: attempts.append('directory')
                return rmdir(path, *args, **kwargs)
            with patch.object(player_scratch.uuid, 'uuid4', return_value=SimpleNamespace(hex=token)), \
                 patch.object(Path, 'write_text', writing), patch.object(Path, 'unlink', deleting), \
                 patch.object(Path, 'rmdir', removing):
                with self.assertRaises(OSError) as caught: player_scratch.create(root, fps=60, frames=2)
            self.assertIs(caught.exception, primary)
            self.assertEqual(attempts, [player_scratch.OWNER, player_scratch.MANIFEST, 'directory'])
            self.assertFalse((folder/player_scratch.MANIFEST).exists())
            self.assertEqual((folder/'user-note').read_bytes(), b'user')

    def test_reshade_cleanup_keeps_original_error_user_bytes_and_narrow_exception_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'ReShade.ini'; path.write_bytes(b'user config')
            primary = PermissionError('replace denied')
            with patch.object(os, 'replace', side_effect=primary), \
                 patch.object(Path, 'unlink', side_effect=PermissionError('temp locked')):
                with self.assertRaises(PermissionError) as caught: reshade_setup._write_config(path, b'new config')
            self.assertIs(caught.exception, primary)
            self.assertEqual(path.read_bytes(), b'user config')
            reshade_setup._write_config(path, b'new config'); self.assertEqual(path.read_bytes(), b'new config')
            with patch.object(Path, 'unlink', side_effect=ValueError('programming error')):
                with self.assertRaises(ValueError): reshade_setup._write_config(path, b'last config')

    def test_history_reset_uses_one_private_copy_and_preserves_saved_content_after_eviction(self):
        project = {'items': [1, 2, 3]}
        # This counts a real allocation target; no source-text assertion or
        # saved-position substitution. Other history operations keep their copies.
        with patch.object(shot_history, 'deepcopy', wraps=copy.deepcopy) as copying:
            history = shot_history.ShotHistory(project, max_entries=2)
            self.assertEqual(copying.call_count, 1)
        project['items'].append(4); self.assertTrue(history.is_dirty(project))
        original = {'items': [1, 2, 3]}; self.assertFalse(history.is_dirty(original))
        with patch.object(shot_history, 'deepcopy', wraps=copy.deepcopy) as copying:
            history.mark_saved(original)
        self.assertEqual(copying.call_count, 0)
        original['items'].append(999)
        self.assertFalse(history.is_dirty({'items': [1, 2, 3]}))
        original = {'items': [1, 2, 3]}
        history.select(123); self.assertFalse(history.is_dirty(original))
        history.record({'items': [2]}); history.record({'items': [3]})
        self.assertFalse(history.is_dirty(original))
        history.record(original); moved = history.move(); moved.project['items'].append(999)
        self.assertFalse(history.is_dirty(original))
        history.mark_saved({'items': [99]}); self.assertFalse(history.is_dirty({'items': [99]}))

    def test_update_thread_failure_releases_busy_and_keeps_explicit_retry_event_protocol(self):
        from dolly import update_ui
        for mode in ('check', 'download'):
            with self.subTest(mode=mode):
                ui = update_ui.UpdateUI.__new__(update_ui.UpdateUI)
                ui.app = SimpleNamespace(closed=False, update_status=Mock(), root=Mock())
                ui.restarting = ui.running = False; ui.enabled = True; ui.request_id = 0
                ui.ready = ui.ready_release = ui.approved = None; ui.dismissed = False
                ui.available = None if mode == 'check' else dict(version='9.0.0', url='fixture', sha256='a'*64, size=1)
                ui.events = queue.Queue(); ui._render = Mock(); ui._can_update = lambda: True
                method = ui.check if mode == 'check' else ui.update_now
                refused = Mock(); refused.start.side_effect = RuntimeError('thread limit')
                with patch.object(update_ui.threading, 'Thread', return_value=refused): method()
                self.assertFalse(ui.running); self.assertTrue(ui.error); self.assertEqual(ui.request_id, 1)
                self.assertTrue(ui.events.empty())
                with patch.object(update_ui.threading, 'Thread') as factory:
                    method(); target = factory.call_args.kwargs['target']
                self.assertTrue(ui.running); self.assertEqual(ui.request_id, 2)
                ui.events.put((1, 'available', {'old': 'stale'}))
                if mode == 'check':
                    with patch.object(update_ui.updater, 'check_latest', return_value=None): target()
                else:
                    with patch.object(update_ui.updater, 'download_update', side_effect=OSError('offline fixture')): target()
                ui.poll(); self.assertFalse(ui.running)


if __name__ == '__main__': unittest.main()
