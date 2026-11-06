"""Core headless workflows. Console and process responses are simulated, not game proof."""
import hashlib
import json
import shutil
import sys
import types
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from core_helpers import FakeConsole, FakeClock, CountedEvent
from dolly.controller import Controller
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT
from dolly.playback import ReplayClock
from dolly.settings import AppSettings, load_settings, save_settings
from dolly import gameinfo_transaction as transaction, launcher, native_path, updater

try:
    import tkinter
except ImportError:
    # The update transaction is headless; isolate its optional UI import.
    tkinter = types.ModuleType("tkinter")
    tkinter.ttk = types.ModuleType("tkinter.ttk")
    with patch.dict(sys.modules, {"tkinter": tkinter, "tkinter.ttk": tkinter.ttk}):
        from dolly import update_worker
else:
    from dolly import update_worker
from dolly.camera_export import sample_camera, export_usda


def shot():
    return Project(name='Test', start_tick=100, tick_rate=10, interpolation='linear',
                   lens_interpolation='linear', keyframes=[
        Keyframe(0, 0, 0, 200, 0, 0, 0, 90, STANDARD_ASPECT),
        Keyframe(10, 100, 200, 300, 30, 90, 10, 90, 1.0)])


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def controller(self):
        controller = Controller()
        clock = FakeClock()
        controller._stop_event = CountedEvent(clock)
        patcher = patch('dolly.controller.time.perf_counter', clock.monotonic)
        patcher.start()
        self.addCleanup(patcher.stop)
        console = FakeConsole()
        controller._console = console
        controller._session = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), pid=1234,
            session_dir=Path('unused'), close=MagicMock(), restore_gameinfo=MagicMock(), owns_console_port=lambda: True)
        controller._demo = Path('/chosen/example.dem')
        controller._unlocker_pid = 1234
        controller._probe_result = {'capabilities': {'spec_goto': True, 'cl_citadel_forceangles': True, 'r_aspectratio': True}}
        return controller, console

    def test_project_file_roundtrip_and_replace(self):
        project = shot()
        project.tracks = [CvarTrack('r_dof_override', [TrackKey(0, 1)], 'step', 0)]
        path = self.root / 'shot.dolly.json'
        project.save(path)
        self.assertEqual(Project.load(path), project)
        project.name = 'Updated'
        project.save(path)
        self.assertEqual(Project.load(path), project)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_invalid_project_is_rejected_without_overwriting_file(self):
        path = self.root / 'shot.json'
        shot().save(path)
        original = path.read_bytes()
        bad = shot()
        bad.keyframes[1].time = 0
        with self.assertRaises(ValueError):
            bad.save(path)
        self.assertEqual(path.read_bytes(), original)
        path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            Project.load(path)

    def test_camera_interpolation_and_endpoint_clamping(self):
        project = shot()
        self.assertEqual(project.evaluate(-1)['x'], 0)
        self.assertEqual(project.evaluate(2.5)['x'], 25)
        self.assertEqual(project.evaluate(2.5)['y'], 50)
        self.assertEqual(project.evaluate(99)['x'], 100)

    def test_camera_angles_take_short_route_across_wrap(self):
        project = Project(interpolation='linear', keyframes=[Keyframe(0, 0, 0, 0, 0, 170, -170),
                    Keyframe(4, 0, 0, 0, 0, -170, 170)])
        self.assertEqual(project.evaluate(1)['yaw'], 175)
        self.assertEqual(project.evaluate(3)['roll'], -185)

    def test_replay_clock_interpolates_bounds_and_rewinds(self):
        clock = ReplayClock(64, .1)
        clock.observe(100, 0)
        self.assertAlmostEqual(clock.position(.1), 100.64)
        self.assertEqual(clock.position(10), 101)
        clock.observe(50, 10)
        self.assertEqual(clock.position(10), 50)
        self.assertAlmostEqual(clock.position(10.05), 50.32)

    def test_controller_apply_pause_stop_restores_game_values(self):
        controller, console = self.controller()
        project = shot()
        project.setup_values = {'r_citadel_depthoffield_enable': 1}
        project.tracks = [CvarTrack('r_citadel_depthoffield_focus_distance', [TrackKey(0, 900), TrackKey(10, 1200)])]
        controller.apply(project, 5)
        self.assertEqual(console.values['r_citadel_depthoffield_focus_distance'], 1050)
        controller.pause()
        self.assertEqual(console.values['r_citadel_depthoffield_enable'], 1)
        controller.stop()
        self.assertEqual(console.values['r_citadel_depthoffield_enable'], 0)
        self.assertEqual(console.values['r_citadel_depthoffield_focus_distance'], 600)
        self.assertEqual(console.values['r_aspectratio'], 0)

    def test_controller_seek_maps_shot_time_and_clamps(self):
        controller, console = self.controller()
        controller.seek(shot(), 2.5)
        self.assertIn('demo_gototick 125 0 1', console.requests)
        self.assertIn('spec_goto 25 50 225', console.camera_writes[-1])
        controller.seek(shot(), 99)
        self.assertIn('demo_gototick 200 0 1', console.requests)
        self.assertEqual(controller.status()['time'], 10)

    def test_native_path_compilation_has_expected_wire_values(self):
        project = shot()
        before = project.to_dict()
        data = native_path.compile_project(project)
        header = native_path.HEADER.unpack_from(data)
        self.assertEqual(header[:5], (b'DLYPATH\0', 1, 1, 7, 0))
        self.assertEqual(header[5:8], (10, 0, 10))
        self.assertEqual(len(data), native_path.HEADER_BYTES + native_path.SEGMENT_BYTES)
        segment = native_path.HEADER_BYTES
        self.assertEqual(native_path.SEGMENT_TIMES.unpack_from(data, segment), (0, 10))
        x = native_path.CHANNEL_RECORD.unpack_from(data, segment + native_path.SEGMENT_TIMES.size)
        y = native_path.CHANNEL_RECORD.unpack_from(data, segment + native_path.SEGMENT_TIMES.size + native_path.CHANNEL_RECORD.size)
        self.assertEqual(x[:4], (1, 0, 0, 100))
        self.assertEqual(y[:4], (1, 0, 0, 200))
        self.assertEqual(project.to_dict(), before)

    def test_settings_persist_and_backup_invalid_original(self):
        path = self.root / 'settings.json'
        path.write_bytes(b'{broken\xff')
        with self.assertRaises(ValueError):
            load_settings(path)
        settings = AppSettings(full_editor=True, auto_updates=False)
        save_settings(settings, path)
        self.assertEqual(load_settings(path), settings)
        backups = list(self.root.glob('settings.json.*.invalid'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'{broken\xff')

    def test_settings_failed_replace_preserves_original(self):
        path = self.root / 'settings.json'
        save_settings(AppSettings(), path)
        original = path.read_bytes()
        with patch('dolly.settings.os.replace', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                save_settings(AppSettings(full_editor=True), path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])

    def recovery_fixture(self):
        target = self.root / 'gameinfo.gi'
        target.write_bytes(b'mounted')
        session = self.root / 'session'
        session.mkdir()
        (session / 'original.gameinfo.gi').write_bytes(b'original\r\n')
        digest = lambda b: hashlib.sha256(b).hexdigest()
        record = {'original_gameinfo': str(target), 'backup_name': 'original.gameinfo.gi',
                  'original_sha256': digest(b'original\r\n'), 'patched_sha256': digest(b'mounted'),
                  'config_state': 'mounted'}
        transaction.save_record(session, record)
        return target, session

    def test_game_configuration_recovery_restores_exact_original(self):
        target, session = self.recovery_fixture()
        transaction.restore_record(session, load_record=lambda p: json.loads((p / 'session.json').read_text()))
        self.assertEqual(target.read_bytes(), b'original\r\n')
        self.assertEqual(json.loads((session / 'session.json').read_text())['config_state'], 'restored')

    def test_game_configuration_recovery_preserves_external_edits(self):
        target, session = self.recovery_fixture()
        target.write_bytes(b'user edit')
        with self.assertRaises(launcher.LaunchError):
            transaction.restore_record(session, load_record=lambda p: json.loads((p / 'session.json').read_text()))
        self.assertEqual(target.read_bytes(), b'user edit')
        self.assertEqual(json.loads((session / 'session.json').read_text())['config_state'], 'conflict')

    def test_export_timing_restores_original_values(self):
        controller, console = self.controller()
        console.values.update(host_framerate=90, r_wait_on_present=0)
        controller.set_export_timing(60, .5)
        self.assertEqual(console.values['host_framerate'], 120)
        self.assertEqual(console.values['r_wait_on_present'], 1)
        controller.clear_export_timing()
        self.assertEqual(console.values['host_framerate'], 90)
        self.assertEqual(console.values['r_wait_on_present'], 0)

    def test_camera_export_samples_and_writes_usd(self):
        project = Project(interpolation='linear', keyframes=[
            Keyframe(0, 10, 20, 30, 0, 0, 0),
            Keyframe(1, 14, 28, 42, 0, 0, 0)])
        take = sample_camera(project, 2)
        self.assertEqual([(f.frame, f.time, f.position) for f in take.frames], [
            (0, 0, (10, 20, 30)), (1, .5, (12, 24, 36)),
            (2, 1, (14, 28, 42))])
        path = self.root / 'camera.usda'
        export_usda(project, path, 2)
        output = path.read_text()
        self.assertIn('endTimeCode = 2', output)
        self.assertIn('1: (2, 4, 6)', output)
        self.assertIn('2: (4, 8, 12)', output)

    def test_update_install_rollback_and_unsafe_archive_rejection(self):
        required = ('Dolly.exe', 'DollyUpdater.exe', '_internal/native/bin/win64/DollyNative.dll',
                    '_internal/third_party/ffmpeg/bin/ffmpeg.exe')
        target, work = self.root / 'Dolly', self.root / '.dolly-update-test'
        for folder, version in ((target, '1.0.0'), (work / 'payload', '1.0.1')):
            for name in required:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(version)
            updater.write_manifest(folder, version)
        def fail(_):
            raise RuntimeError('health check failed')
        with self.assertRaisesRegex(RuntimeError, 'health check failed'):
            update_worker.install({'target': str(target), 'work': str(work)}, fail)
        for name in required:
            self.assertEqual((target / name).read_text(), '1.0.0')
        archive = self.root / 'malicious.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            for path in (work / 'payload').rglob('*'):
                if path.is_file():
                    package.write(path, 'DeadlockDolly/' + path.relative_to(work / 'payload').as_posix())
            package.writestr('DeadlockDolly/../../outside', 'bad')
        with self.assertRaises(ValueError):
            updater.extract_verified(archive, self.root / 'staged',
                                     {'version': '1.0.1', 'sha256': updater.digest_file(archive)})
        self.assertFalse((self.root / 'outside').exists())
        successful_work = self.root / '.dolly-update-success'
        shutil.copytree(work / 'payload', successful_work / 'payload')
        update_worker.install({'target': str(target), 'work': str(successful_work)})
        for name in required:
            self.assertEqual((target / name).read_text(), '1.0.1')
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        self.assertFalse((target / update_worker.PENDING).exists())


if __name__ == '__main__':
    unittest.main()
