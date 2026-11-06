"""Seven headless product workflows; external game and encoder boundaries are simulated."""
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core_helpers import FakeConsole, FakeClock, CountedEvent
from dolly import native_path, updater
from dolly.camera_export import export_jsx, export_usda, sample_camera
from dolly.controller import Controller
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT, validate_cvar_name, parse_cvar_value
from dolly.settings import AppSettings, load_settings, save_settings
import dolly as _dolly
_missing = object()
try:
    import tkinter
except ImportError:
    _prior_worker = getattr(_dolly, 'update_worker', _missing)
    tk = types.ModuleType('tkinter')
    tk.ttk = types.ModuleType('tkinter.ttk')
    with patch.dict(sys.modules, {'tkinter': tk, 'tkinter.ttk': tk.ttk}):
        from dolly import update_worker as worker
    if _prior_worker is _missing:
        _dolly.__dict__.pop('update_worker', None)
    else:
        _dolly.update_worker = _prior_worker
else:
    from dolly import update_worker as worker

def key(t, x=0, y=0, z=0, pitch=0, yaw=0, roll=0, fov=90, aspect=STANDARD_ASPECT):
    return Keyframe(t, x, y, z, pitch, yaw, roll, fov, aspect)

def shot():
    return Project(name='Test', start_tick=100, tick_rate=10, interpolation='linear',
                   lens_interpolation='linear', keyframes=[key(0, 0, 0, 200),
                   key(10, 100, 200, 300, 30, 90, 10, aspect=1.0)])

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dolly-workflows-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_p07_recording_admission_and_lifecycle(self):
        from video_workflow import exercise_video_workflow
        exercise_video_workflow(self, self.root)
        from dolly.video_export import VideoOptions
        exe = self.root / 'ffmpeg.exe'
        exe.write_bytes(b'fixture')
        with patch('dolly.video_export.ffmpeg_encoder_names', return_value=frozenset({'h264_nvenc'})):
            with self.assertRaisesRegex(ValueError, 'libx264'):
                VideoOptions(self.root / 'unsupported.mp4', codec='libx264', ffmpeg_path=exe).validated()
        self.assertFalse((self.root / 'unsupported.mp4').exists())
        self.check_export_timing()

    def check_export_timing(self):
        controller = Controller()
        console = FakeConsole()
        controller._console = console
        controller._session = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), pid=1234,
            session_dir=Path('unused'), owns_console_port=lambda: True)
        controller._demo = Path('/chosen/example.dem')
        controller._unlocker_pid = 1234
        controller._probe_result = {'capabilities': {'spec_goto': True, 'cl_citadel_forceangles': True, 'r_aspectratio': True}}
        console.values.update(host_framerate=90, r_wait_on_present=0)
        controller.set_export_timing(60, .5)
        self.assertEqual((console.values['host_framerate'], console.values['r_wait_on_present']), (120, 1))
        controller.clear_export_timing()
        self.assertEqual((console.values['host_framerate'], console.values['r_wait_on_present']), (90, 0))

    def test_p10_authored_path_preview_and_native(self):
        linear = shot()
        for time, expected in ((-1, 0), (2.5, 25), (10, 100), (99, 100)):
            with self.subTest(mode='linear', time=time): self.assertAlmostEqual(linear.evaluate(time)['x'], expected)
        smooth = Project(keyframes=[key(0, x=0), key(.4, x=5), key(4, x=-10)])
        eps = 1e-5
        left = (smooth.evaluate(.4)['x'] - smooth.evaluate(.4-eps)['x']) / eps
        right = (smooth.evaluate(.4+eps)['x'] - smooth.evaluate(.4)['x']) / eps
        self.assertAlmostEqual(left, right, places=2)
        uniform = Project(keyframes=[key(t, x=3*t+1) for t in (0, .1, 2.3, 10)])
        for t in (.04, .8, 7.1): self.assertAlmostEqual(uniform.evaluate(t)['x'], 3*t+1)
        spline = Project(interpolation='spline', keyframes=[key(t, x=x, pitch=x)
                         for t, x in zip((0, 1, 2, 3, 4), (0, 0, 10, 0, 0))])
        self.assertLess(spline.evaluate(.5)['x'], 0)
        for t in (0, .5, 1, 1.5, 2, 3, 4):
            self.assertAlmostEqual(spline.evaluate(t)['x'], spline.evaluate(t)['pitch'])
        angles = Project(interpolation='linear', keyframes=[key(0, yaw=170, roll=-170), key(4, yaw=-170, roll=170)])
        self.assertEqual((angles.evaluate(1)['yaw'], angles.evaluate(3)['roll']), (175, -185))
        angles.rotation_mode = 'unwrapped'
        angles.keyframes[1].yaw = 720
        angles.keyframes[0].yaw = 0
        self.assertEqual(angles.evaluate(2)['yaw'], 360)
        self.check_tracks_and_retime()
        project = Project(interpolation='spline', keyframes=[key(0, x=0), key(1, x=4), key(3, x=-2), key(4, x=1)])
        before = project.to_dict()
        data = native_path.compile_project(project)
        header = native_path.HEADER.unpack_from(data)
        self.assertEqual(header[:5], (b'DLYPATH\0', 1, 3, 7, 0))
        self.assertEqual(header[5:8], (4, 0, 4))
        self.assertEqual(len(data), native_path.HEADER_BYTES + 3*native_path.SEGMENT_BYTES)
        for segment, (start, end, a, b) in enumerate(((0, 1, 0, 4), (1, 3, 4, -2), (3, 4, -2, 1))):
            offset = native_path.HEADER_BYTES + segment*native_path.SEGMENT_BYTES
            self.assertEqual(native_path.SEGMENT_TIMES.unpack_from(data, offset), (start, end))
            kind, flags, left, right, slope_left, slope_right = native_path.CHANNEL_RECORD.unpack_from(data, offset + 16)
            self.assertEqual((kind, flags, left, right), (2, 0, a, b))
            for u in (.25, .5, .75):
                # Independently decode the documented cubic Hermite wire format.
                actual = ((2*u**3-3*u**2+1)*left + (u**3-2*u**2+u)*(end-start)*slope_left
                          + (-2*u**3+3*u**2)*right + (u**3-u**2)*(end-start)*slope_right)
                self.assertAlmostEqual(actual, project.evaluate(start + u*(end-start))['x'], places=9)
        self.assertEqual(project.to_dict(), before)
        with self.assertRaises(ValueError): native_path.compile_project(Project())
        compiler = shutil.which('g++') if os.name != 'nt' else None
        if os.name != 'nt' and compiler is None:
            warnings.warn('C++ path parity not run: g++ unavailable', RuntimeWarning)
            return
        root = Path(__file__).resolve().parents[1]
        runner = self.root / 'native-path-runner'
        source = self.root / 'runner.cpp'
        # Tiny adapter: run the actual native parser/evaluator and expose its
        # optional lens output, which the existing native test CLI omits.
        source.write_text(r"""
#include "dolly_path.hpp"
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <vector>
int main(int argc, char **argv) {
    if (argc < 3) return 1;
    std::ifstream in(argv[1], std::ios::binary);
    std::vector<char> bytes((std::istreambuf_iterator<char>(in)), {});
    dolly::NativePath path; std::string error;
    if (!path.load(bytes.data(), bytes.size(), error)) { std::cerr << error; return 2; }
    std::cout << std::setprecision(17);
    for (int i = 2; i < argc; ++i) {
        dolly::CameraPose pose{}; double lens = 0;
        if (!path.evaluate(std::strtod(argv[i], nullptr), pose, &lens)) return 3;
        for (double value : pose) std::cout << value << ' ';
        std::cout << lens << '\n';
    }
}
""")
        if os.name == 'nt':
            # Match the release toolchain without requiring a Developer Prompt
            # or installing a second compiler just for this source test.
            cmake = shutil.which('cmake')
            if cmake is None:
                installed = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'CMake/bin/cmake.exe'
                cmake = str(installed) if installed.is_file() else None
            self.assertIsNotNone(cmake, 'Windows path parity requires CMake and Visual Studio 2022 C++ tools')
            build = self.root / 'native-path-build'
            runner = build / 'native-path-runner.exe'
            (self.root / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.21)
project(DollyPathParity LANGUAGES CXX)
add_executable(native-path-runner "{source.as_posix()}" "{(root / 'native/src/dolly_path.cpp').as_posix()}")
target_include_directories(native-path-runner PRIVATE "{(root / 'native/include').as_posix()}")
target_compile_features(native-path-runner PRIVATE cxx_std_17)
target_compile_options(native-path-runner PRIVATE /fp:strict)
set_target_properties(native-path-runner PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE "${{CMAKE_BINARY_DIR}}")
''', encoding='utf-8')
            subprocess.run([cmake, '-S', str(self.root), '-B', str(build),
                            '-G', 'Visual Studio 17 2022', '-A', 'x64'],
                           check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([cmake, '--build', str(build), '--config', 'Release', '--parallel', '2'],
                           check=True, capture_output=True, text=True, timeout=60)
        else:
            subprocess.run([compiler, '-std=c++17', '-O2', '-ffp-contract=off',
                            '-I', str(root / 'native/include'), str(root / 'native/src/dolly_path.cpp'),
                            str(source), '-o', str(runner)], check=True, capture_output=True, text=True, timeout=60)
        # Distinct captured lenses force wire version 2. Explicit broken
        # tangents exercise authored nonlinear motion on unequal segments.
        authored = Project(keyframes=[key(0, x=0), key(1, x=10), key(3, x=20)])
        authored.set_camera_tangent(0, 'x', 'flat')
        authored.set_camera_tangent(1, 'x', 'broken', incoming=0, outgoing=10)
        for k, lens in zip(authored.keyframes, (.5, 1, 1.5)): k.lens_scale = lens
        for case in (project, authored):
            with self.subTest(interpolation=case.interpolation, authored=case is authored):
                blob = self.root / 'path.bin'
                blob.write_bytes(native_path.compile_project(case))
                times = [-1, 0, .25, .5, 1, 1.5, 2.5, 3, 4, 5]
                result = subprocess.run([str(runner), str(blob), *map(str, times)],
                                        check=True, text=True, capture_output=True, timeout=10)
                rows = result.stdout.splitlines()
                self.assertEqual(len(rows), len(times))
                for t, row in zip(times, rows):
                    values = [float(v) for v in row.split()]
                    self.assertEqual(len(values), 8)
                    expected = case.evaluate(t)
                    for name, actual in zip(native_path.CHANNELS, values):
                        self.assertAlmostEqual(actual, expected[name], places=9, msg=f'{name} at {t}')
                    if case is authored:
                        self.assertAlmostEqual(values[7], expected['lens_scale'], places=9)
                    else:
                        self.assertEqual(values[7], 0)
                if case is authored:
                    # Flat/flat cubic from 0 to 10 is 1.5625 at a quarter.
                    self.assertAlmostEqual(float(rows[2].split()[0]), 1.5625)
                    self.assertAlmostEqual(float(rows[4].split()[7]), 1)
        # Independent linear equations for all seven channels, not derived
        # from the Python evaluator. Smooth's uneven keys lie on the same
        # constant-velocity trajectory and must reproduce that trajectory.
        channels = lambda t: (10+2*t, -20+3*t, 30-4*t, 5+2*t,
                              -40+10*t, 20-5*t, 1+.25*t)
        linear = Project(interpolation='linear', lens_interpolation='linear',
                         keyframes=[key(t, *channels(t)[:6], aspect=channels(t)[6]) for t in (0, 2)])
        smooth = Project(interpolation='smooth', lens_interpolation='linear',
                         keyframes=[key(t, *channels(t)[:6], aspect=channels(t)[6]) for t in (0, .5, 2)])
        for path in (linear, smooth):
            for k in path.keyframes: k.lens_scale = .5 + .25*k.time
            blob = self.root / 'analytic.bin'
            blob.write_bytes(native_path.compile_project(path))
            timestamps = (0, .25, 1, 2)
            result = subprocess.run([str(runner), str(blob), *map(str, timestamps)],
                                    check=True, text=True, capture_output=True, timeout=10)
            rows = result.stdout.splitlines()
            self.assertEqual(len(rows), len(timestamps))
            for t, row in zip(timestamps, rows):
                with self.subTest(mode=path.interpolation, time=t):
                    actual = [float(value) for value in row.split()]
                    self.assertEqual(len(actual), 8)
                    for got, want in zip(actual, (*channels(t), .5+.25*t)):
                        self.assertAlmostEqual(got, want, places=10)

    def check_tracks_and_retime(self):
        name = 'r_dof_override_ranges'
        project = Project(start_tick=640, tick_rate=64, interpolation='linear', keyframes=[key(0), key(2, x=20)],
            tracks=[CvarTrack('r_dof_override', [TrackKey(0, 0), TrackKey(1, 1), TrackKey(2, 0)], 'step'),
                    CvarTrack(name, [TrackKey(0, (-100, 0, 180, 2000)), TrackKey(2, (0, 100, 500, 3000))], 'linear')])
        for t, expected in ((.99, 0), (1, 1), (2, 0), (1, 1)):
            with self.subTest(time=t): self.assertEqual(project.evaluate(t)['cvars']['r_dof_override'], expected)
        self.assertEqual(project.evaluate(1)['cvars'][name], (-50, 50, 340, 2500))
        project.retime(32)
        self.assertEqual(project.start_tick, 640)
        self.assertEqual([k.time for k in project.keyframes], [0, 4])
        self.assertEqual([k.time for k in project.tracks[0].keys], [0, 2, 4])
        self.assertEqual(project.evaluate(2)['cvars'][name], (-50, 50, 340, 2500))
        before = project.to_dict()
        for rate in (0, -1, math.inf, math.nan, True):
            with self.subTest(rate=rate), self.assertRaises(ValueError): project.retime(rate)
        self.assertEqual(project.to_dict(), before)

    def test_p11_camera_export(self):
        project = Project(interpolation='linear', lens_interpolation='linear', keyframes=[
            key(0, 10, 20, 30), key(1, 14, 28, 42, yaw=90)])
        project.keyframes[0].lens_scale = 1
        project.keyframes[1].lens_scale = 1
        take = sample_camera(project, 2, speed=.5)
        self.assertEqual([(f.frame, f.time, f.phase, f.position) for f in take.frames], [
            (0, 0, 0, (10,20,30)), (1, .5, .25, (11,22,33)), (2, 1, .5, (12,24,36)),
            (3, 1.5, .75, (13,26,39)), (4, 2, 1, (14,28,42))])
        for frame in take.frames: self.assertAlmostEqual(frame.fov, 2*math.degrees(math.atan(STANDARD_ASPECT)))
        path = self.root / 'camera.usda'
        export_usda(project, path, 2, speed=.5, width=1920, height=1080)
        output = path.read_text()
        self.assertIn('endTimeCode = 4', output)
        self.assertIn('2: (2, 4, 6)', output)
        self.assertIn('4: (4, 8, 12)', output)
        # USD keeps Source XYZ, rotates its -Z-facing camera from yaw 0 to
        # yaw 90, and uses focal = 18 / tan(horizontal_fov / 2).
        self.assertIn('1: (90, 0, -67.5)', output)
        self.assertIn('2: (90, 0, -45)', output)
        self.assertIn('4: (90, 0, -0)', output)
        self.assertIn('float focalLength.timeSamples', output)
        self.assertEqual(len(re.findall(r'^\s*[0-4]: 10\.125,$', output, re.MULTILINE)), 5)
        self.assertIn('framesPerSecond = 2', output)
        with self.assertRaises(FileExistsError): export_usda(project, path, 2)
        self.assertEqual(path.read_text(), output)
        jsx = self.root / 'camera.jsx'
        export_jsx(project, jsx, 2, speed=.5, width=1920, height=1080)
        script = jsx.read_text()
        def array(name):
            match = re.search(r'^\s*var ' + name + r' = (\[.*\]);$', script, re.MULTILINE)
            self.assertIsNotNone(match, name)
            return json.loads(match.group(1))
        # AE axes are (source X, -source Z, source Y), and its forward
        # orientation rotates from Y=90 to Y=0 over the shot.
        self.assertEqual(array('ORIGIN'), [10, -30, 20])
        self.assertEqual(array('frameNumbers'), [0, 1, 2, 3, 4])
        self.assertEqual(array('positions'), [[0,0,0], [1,-3,2], [2,-6,4], [3,-9,6], [4,-12,8]])
        self.assertEqual(array('rotations'), [[0,90,0], [0,67.5,0], [0,45,0], [0,22.5,0], [0,0,0]])
        for fov in array('fovs'):
            self.assertAlmostEqual(fov, 2*math.degrees(math.atan(16/9)), places=5)
        self.assertIn('var FPS = 2;', script)
        self.assertIn('var FRAMES = 5;', script)
        self.assertEqual(array('dollyKeys'), [[1,0,0,0,0], [2,2,4,-12,8]])

    def test_p12_follow_and_bone(self):
        from camera_modes import exercise_camera_modes
        exercise_camera_modes(self, self.root)

    def test_p13_shot_persistence_and_validation(self):
        name = 'r_dof_override_ranges'
        project = Project(name='Camera ☆', start_tick=640, tick_rate=64, interpolation='spline',
            confetti_enabled=True, particles_preset='snow_heavy', particles_intensity=1.5,
            keyframes=[key(0, x=1), key(1, x=4), key(3, x=-2)],
            setup_values={name: (0, 0, 0, 0)}, tracks=[CvarTrack(name,
                [TrackKey(0, (-100, 0, 180, 2000)), TrackKey(2, (0, 100, 500, 3000))],
                'linear', (0, 0, 0, 0))])
        path = self.root / 'shot.dolly.json'
        project.save(path)
        original = path.read_bytes()
        self.assertEqual(Project.load(path).to_dict(), project.to_dict())
        self.assertEqual(Project.load(path).evaluate(1)['cvars'][name], (-50, 50, 340, 2500))
        changed = Project.from_dict(project.to_dict())
        changed.name = 'Changed'
        with patch('os.replace', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                changed.save(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [path])
        changed.save(path)
        self.assertEqual(Project.load(path).name, 'Changed')
        legacy = Project(keyframes=[key(0)], confetti_enabled=True).to_dict()
        legacy['version'] = 7
        del legacy['particles_preset'], legacy['particles_intensity'], legacy['interpolation']
        migrated = Project.from_dict(legacy)
        self.assertEqual((migrated.interpolation, migrated.particles_preset, migrated.particles_intensity),
                         ('smooth', 'confetti', 1.0))
        path = self.root / 'bad.json'
        valid = shot().to_dict()
        path.write_text(json.dumps(valid))
        self.assertEqual(Project.load(path).to_dict(), valid)
        # Change one property of a valid shot so unrelated missing fields
        # cannot accidentally satisfy the expected rejection.
        invalid = {
            'duplicate field': '{"version": ' + str(valid['version']) + ',' + json.dumps(valid)[1:],
            'nonfinite number': json.dumps({**valid, 'tick_rate': math.nan}),
            'wrong keyframe container': json.dumps({**valid, 'keyframes': {}}),
            'unknown command field': json.dumps({**valid, 'command': 'quit'}),
            'wrong root type': '[1,2]', 'malformed JSON': 'not json',
        }
        for label, text in invalid.items():
            with self.subTest(case=label):
                path.write_text(text)
                with self.assertRaises(ValueError): Project.load(path)
                self.assertEqual(path.read_text(), text)
        invalid = [Project(keyframes=[key(0), key(0)]), Project(keyframes=[key(-1)]),
                   Project(keyframes=[key(0, x=math.nan)]), Project(keyframes=[key(0, fov=180)]),
                   Project(tick_rate=0), Project(start_tick=True), Project(interpolation='exec'),
                   Project(setup_values={'r_dof_override': '1;quit'})]
        for project in invalid:
            with self.subTest(project=project), self.assertRaises(ValueError): project.validate()
        for name in ('exec', 'r_dof_override;quit', 'r_dof_override\nquit', 'r_dof_override 1'):
            with self.subTest(name=name), self.assertRaises(ValueError): validate_cvar_name(name)
        for value in ('1 2 3', '1 1 1 1; quit', 'nan 0 0 0'):
            with self.subTest(value=value), self.assertRaises(ValueError): parse_cvar_value('r_dof_override_ranges', value)

    def test_p14_settings_preservation(self):
        path = self.root / 'settings.json'
        self.assertEqual(load_settings(path), AppSettings())
        self.assertFalse(path.exists())
        settings = AppSettings(full_editor=True, auto_updates=False, export_folder='D:/shots', movement_speed=500)
        save_settings(settings, path)
        self.assertEqual(load_settings(path), settings)
        valid = path.read_bytes()
        version = json.loads(valid)['version']
        duplicate = b'{"version": ' + str(version).encode() + b',' + valid.lstrip()[1:]
        invalid = [b'{broken\xff', duplicate,
                   valid.replace(b'"movement_speed": 500.0', b'"movement_speed": NaN'),
                   json.dumps({**json.loads(valid), 'version': 999}).encode()]
        self.assertNotEqual(invalid[2], valid)
        for value in invalid:
            with self.subTest(value=value[:50]):
                path.write_bytes(value)
                with self.assertRaises(ValueError):
                    load_settings(path)
                self.assertEqual(path.read_bytes(), value)
                save_settings(settings, path)
                self.assertEqual(load_settings(path), settings)
        backups = list(self.root.glob('settings.json.*.invalid'))
        self.assertCountEqual([p.read_bytes() for p in backups], invalid)
        self.root = self.root / 'write-faults'
        self.root.mkdir()
        path = self.root / 'settings.json'
        path.write_bytes(b'{invalid')
        with patch('dolly.settings.shutil.copyfileobj', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                save_settings(AppSettings(), path)
        self.assertEqual(path.read_bytes(), b'{invalid')
        self.assertEqual(list(self.root.iterdir()), [path])
        # Publication collision/failure cannot destroy invalid settings.
        publish = 'rename' if os.name == 'nt' else 'link'
        with patch('dolly.settings.os.' + publish, side_effect=FileExistsError('collision')):
            with self.assertRaises(FileExistsError):
                save_settings(AppSettings(), path)
        self.assertEqual(path.read_bytes(), b'{invalid')
        self.assertEqual(list(self.root.iterdir()), [path])
        save_settings(AppSettings(), path)
        original = path.read_bytes()
        with patch('dolly.settings.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                save_settings(AppSettings(full_editor=True), path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list(self.root.glob('*.tmp'))), 0)
        self.assertEqual(len(list(self.root.glob('*.invalid'))), 1)

        # Cleanup access failures must not hide the primary save result.
        primary = OSError('primary replace failure')
        real_unlink = Path.unlink
        def locked_temporary(candidate, *args, **kwargs):
            if candidate.parent == path.parent and candidate.name.endswith('.tmp'):
                raise PermissionError('temporary cleanup denied')
            return real_unlink(candidate, *args, **kwargs)
        with patch('dolly.settings.os.replace', side_effect=primary), \
             patch.object(Path, 'unlink', locked_temporary):
            with self.assertRaises(OSError) as failed:
                save_settings(AppSettings(full_editor=True), path)
        self.assertIs(failed.exception, primary)
        self.assertEqual(path.read_bytes(), original)

        # Version-one capture binding becomes the matching editor action,
        # without modifying the user's old file on read.
        legacy = self.root / 'legacy.json'
        raw = {"version": 1, "capture_binding": {"key": "J", "ctrl": True, "alt": False, "shift": True}}
        legacy.write_text(json.dumps(raw), encoding='utf-8')
        original = legacy.read_bytes()
        migrated = load_settings(legacy)
        self.assertEqual(migrated.capture_binding.to_dict(), raw['capture_binding'])
        self.assertEqual(migrated.action_bindings['capture'].to_dict(), raw['capture_binding'])
        self.assertEqual(legacy.read_bytes(), original)

    def test_p15_verified_update_and_rollback(self):
        required = ('Dolly.exe', 'DollyUpdater.exe', '_internal/native/bin/win64/DollyNative.dll',
                    '_internal/third_party/ffmpeg/bin/ffmpeg.exe')
        target = self.root / 'installed'
        source = self.root / 'new'
        for folder, data in ((target, b'old'), (source, b'new')):
            for name in required:
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data + b':' + name.encode())
        obsolete = target / '_internal/obsolete.dll'
        obsolete.write_bytes(b'old obsolete')
        newfile = source / '_internal/new.dll'
        newfile.write_bytes(b'new file')
        updater.write_manifest(target, '1.0.0')
        updater.write_manifest(source, '1.0.1')
        user = target / 'my-shot.json'
        user.write_bytes(b'user work')
        archive = self.root / 'release.zip'
        def make_zip(extra=None):
            with zipfile.ZipFile(archive, 'w') as z:
                for path in source.rglob('*'):
                    if path.is_file(): z.write(path, 'DeadlockDolly/' + path.relative_to(source).as_posix())
                if extra: z.writestr(extra, 'bad')
        make_zip()
        release = {'version': '1.0.1', 'sha256': updater.digest_file(archive)}
        bad_checksum = dict(release, sha256='0' * 64)
        with self.assertRaises(ValueError):
            updater.extract_verified(archive, self.root / 'bad-checksum', bad_checksum)
        self.assertFalse((self.root / 'bad-checksum').exists())
        with self.assertRaises(ValueError):
            updater.extract_verified(archive, self.root / 'bad-version',
                                     dict(release, version='9.9.9'))
        self.assertEqual((target / 'Dolly.exe').read_bytes(), b'old:Dolly.exe')
        work = target / '.dolly-update-test'
        work.mkdir()
        payload = work / 'payload'
        self.assertEqual(updater.extract_verified(archive, payload, release)['version'], '1.0.1')
        plan = {'target': str(target), 'work': str(work)}
        old_manifest = (target / updater.MANIFEST).read_bytes()
        def old_is_intact():
            for name in required:
                self.assertEqual((target / name).read_bytes(), b'old:' + name.encode())
            self.assertEqual(obsolete.read_bytes(), b'old obsolete')
            self.assertFalse((target / '_internal/new.dll').exists())
            self.assertEqual((target / updater.MANIFEST).read_bytes(), old_manifest)
            self.assertEqual(user.read_bytes(), b'user work')
            self.assertFalse((target / worker.PENDING).exists())
        # A bad backup copy fails before publishing a pending transaction.
        real_copy = worker.shutil.copyfile
        def corrupt_copy(src, dst, *args, **kwargs):
            result = real_copy(src, dst, *args, **kwargs)
            if Path(dst).name == 'Dolly.exe':
                Path(dst).write_bytes(b'damaged during copy')
            return result
        with patch.object(worker.shutil, 'copyfile', side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'backup verification failed'):
                worker.install(plan)
        self.assertFalse((work / 'journal.json').exists())
        old_is_intact()

        # A locally modified owned binary is backed up as it exists, not as
        # its old manifest describes it, and restored after a health failure.
        custom = b'locally modified executable'
        (target / 'Dolly.exe').write_bytes(custom)
        def failed_custom_health(_): raise RuntimeError('custom health failed')
        with self.assertRaisesRegex(RuntimeError, 'custom health failed'):
            worker.install(plan, failed_custom_health)
        self.assertEqual((target / 'Dolly.exe').read_bytes(), custom)
        self.assertEqual(user.read_bytes(), b'user work')
        self.assertFalse((target / worker.PENDING).exists())
        self.assertIn('Dolly.exe', json.loads((work / 'journal.json').read_text())['modified'])
        (work / 'journal.json').unlink()
        (target / 'Dolly.exe').write_bytes(b'old:Dolly.exe')
        old_is_intact()
        # Refuse a pending recovery and same-version/downgrade before writes.
        pending = target / worker.PENDING
        pending.write_text('pending recovery')
        with self.assertRaises(ValueError): worker.install(plan)
        self.assertEqual(pending.read_text(), 'pending recovery')
        pending.unlink()
        old_is_intact()
        payload_manifest = payload / updater.MANIFEST
        original_manifest = payload_manifest.read_bytes()
        manifest = json.loads(original_manifest)
        for version in ('1.0.0', '0.9.0'):
            manifest['version'] = version
            payload_manifest.write_text(json.dumps(manifest))
            with self.subTest(version=version):
                with self.assertRaises(ValueError): worker.install(plan)
                old_is_intact()
        payload_manifest.write_bytes(original_manifest)
        (payload / 'Dolly.exe').write_bytes(b'tampered')
        with self.assertRaises(ValueError): worker.install(plan)
        old_is_intact()
        (payload / 'Dolly.exe').write_bytes(b'new:Dolly.exe')
        # An unowned file at a newly introduced managed path must survive.
        unowned = target / '_internal/new.dll'
        unowned.write_bytes(b'personal file')
        with self.assertRaises(ValueError): worker.install(plan)
        self.assertEqual(unowned.read_bytes(), b'personal file')
        unowned.unlink()
        old_is_intact()
        calls = []
        def fail_midway(src, dst):
            calls.append(dst)
            if len(calls) == 2: raise OSError('replace failed')
            worker.replace_file(src, dst)
        with self.assertRaises(OSError): worker.install(plan, replace=fail_midway)
        self.assertEqual(len(calls), 2)
        old_is_intact()
        worker.rollback(plan)
        old_is_intact()
        # A damaged recovery copy must not overwrite a changed installed file.
        backup_exe = work / 'backup/Dolly.exe'
        original_backup = backup_exe.read_bytes()
        backup_exe.write_bytes(b'corrupt backup')
        (target / 'Dolly.exe').write_bytes(b'interrupted replacement')
        with self.assertRaisesRegex(ValueError, 'backup is damaged'):
            worker.rollback(plan)
        self.assertEqual((target / 'Dolly.exe').read_bytes(), b'interrupted replacement')
        self.assertEqual(user.read_bytes(), b'user work')
        backup_exe.write_bytes(original_backup)
        worker.rollback(plan)
        old_is_intact()
        (work / 'journal.json').unlink()
        def bad_health(_): raise RuntimeError('health failed')
        with self.assertRaisesRegex(RuntimeError, 'health failed'): worker.install(plan, bad_health)
        old_is_intact()
        (work / 'journal.json').unlink()
        worker.install(plan)
        for name in required:
            self.assertEqual((target / name).read_bytes(), b'new:' + name.encode())
        self.assertFalse(obsolete.exists())
        self.assertEqual((target / '_internal/new.dll').read_bytes(), b'new file')
        self.assertEqual(user.read_bytes(), b'user work')
        self.assertFalse((target / worker.PENDING).exists())
        self.assertEqual(updater.read_manifest(target)['version'], '1.0.1')
        # Each archive includes a complete good payload, followed by an unsafe
        # entry. The target and user file must stay unchanged after rejection.
        absolute = self.root / 'absolute-escape'
        cases = [
            ('traversal', 'DeadlockDolly/../../outside'),
            ('absolute', str(absolute)),
            ('backslash', r'DeadlockDolly\..\outside'),
            ('case_alias', 'DeadlockDolly/dolly.EXE'),
            ('symlink', 'DeadlockDolly/_internal/link'),
        ]
        for label, name in cases:
            with self.subTest(archive=label):
                make_zip()
                with zipfile.ZipFile(archive, 'a') as package:
                    if label == 'symlink':
                        info = zipfile.ZipInfo(name)
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        package.writestr(info, '../../outside')
                    else:
                        package.writestr(name, b'unsafe')
                staging = self.root / ('unsafe-' + label) / 'staged'
                with self.assertRaises(ValueError):
                    updater.extract_verified(archive, staging,
                        {'version': '1.0.1', 'sha256': updater.digest_file(archive)})
                # ../../ from the extraction directory would reach root/outside.
                self.assertFalse((self.root / 'outside').exists())
                self.assertFalse((staging.parent / 'outside').exists())
                self.assertFalse(absolute.exists())
                self.assertEqual((target / 'Dolly.exe').read_bytes(), b'new:Dolly.exe')
                self.assertEqual(user.read_bytes(), b'user work')

        self.check_update_consent()

    def check_update_consent(self):
        # Import only the UI policy; the notice is a widget boundary and is
        # replaced before import, so no Tk display or Tk installation is needed.
        import importlib
        prior_ui = getattr(_dolly, 'update_ui', _missing)
        prior_module = sys.modules.get('dolly.update_ui', _missing)
        def restore_ui_import():
            if prior_ui is _missing:
                _dolly.__dict__.pop('update_ui', None)
            else:
                _dolly.update_ui = prior_ui
            if prior_module is _missing:
                sys.modules.pop('dolly.update_ui', None)
            else:
                sys.modules['dolly.update_ui'] = prior_module
        self.addCleanup(restore_ui_import)
        notice_module = types.ModuleType('dolly.ui.update_notice')
        notice_module.UpdateNotice = Mock(return_value=Mock())
        with patch.dict(sys.modules, {'dolly.ui.update_notice': notice_module, 'dolly.update_worker': worker}):
            ui = importlib.import_module('dolly.update_ui')
        # Bad cached manifests cannot hide a later verified matching update.
        scan = self.root / 'scan-install'
        scan.mkdir()
        (scan / 'Dolly.exe').write_bytes(b'installed original')
        (scan / 'user-shot.json').write_bytes(b'user shot')
        good = scan / '.dolly-update-999good'
        bad = scan / '.dolly-update-000bad'
        for workspace in (bad, good):
            shutil.copytree(self.root / 'new', workspace / 'payload')
            (workspace / 'verified.json').write_text(json.dumps({'version': '1.0.1', 'sha256': 'a' * 64}))
        good_manifest = updater.read_manifest(good / 'payload')
        for malformed in ([], {**good_manifest, 'schema': True},
                          {**good_manifest, 'files': {**good_manifest['files'], 'Dolly.exe': None}}):
            with self.subTest(malformed=malformed):
                manifest = bad / 'payload' / updater.MANIFEST
                manifest.write_text(json.dumps(malformed))
                original = manifest.read_bytes()
                release = {'version': '1.0.1', 'sha256': 'a' * 64,
                           'url': 'https://offline.invalid/never', 'size': 1}
                with patch.object(updater, 'open_url', side_effect=AssertionError('unexpected network')) as network:
                    self.assertEqual(updater.download_update(release, scan), good)
                    network.assert_not_called()
                self.assertEqual(manifest.read_bytes(), original)
                self.assertEqual((scan / 'Dolly.exe').read_bytes(), b'installed original')
                self.assertEqual((scan / 'user-shot.json').read_bytes(), b'user shot')
        app = SimpleNamespace(update_notice_parent=object(), app_settings=SimpleNamespace(auto_updates=True),
            root=Mock(), update_status=Mock(), update_check_button=Mock(), closed=False, busy=False,
            playing=False, dirty=False, startup_cancel=None, controller=Mock(), video_export=Mock(),
            _on_close=Mock(), notebook=None, sidebar=None)
        app.controller.status.return_value = {'game_running': False}
        app.video_export.status.return_value = {'state': 'idle'}
        app.root.grab_current.return_value = None
        jobs = []
        def thread(*, target, **kwargs):
            return SimpleNamespace(start=lambda: jobs.append(target))
        release = {'version': '9.0.1', 'url': 'https://example.invalid/release.zip',
                   'sha256': 'a'*64, 'size': 100}
        with patch.object(ui, 'UpdateNotice', return_value=Mock()) as factory, \
             patch.object(ui, 'application_root', return_value=self.root), \
             patch.object(ui, 'is_frozen', return_value=True), \
             patch.object(ui.threading, 'Thread', side_effect=thread), \
             patch.object(ui.updater, 'check_latest', return_value=release), \
             patch.object(ui.updater, 'download_update', return_value=self.root / 'staged') as download, \
             patch.object(ui, 'check_processes'), patch.object(ui, 'launch_worker') as launch:
            (self.root / updater.MANIFEST).write_text('fixture')
            manager = ui.UpdateUI(app)
            manager.check()
            self.assertEqual(len(jobs), 1)
            jobs.pop(0)()
            manager.poll()
            self.assertEqual(manager.available, release)
            download.assert_not_called()
            launch.assert_not_called()
            self.assertIsNone(manager.approved)
            factory.call_args.kwargs['update']()
            self.assertEqual(len(jobs), 1)
            download.assert_not_called()
            jobs.pop(0)()
            download.assert_called_once()
            self.assertEqual(download.call_args.args[:2], (release, self.root))
            manager.poll()
            launch.assert_called_once()
