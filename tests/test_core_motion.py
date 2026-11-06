"""Core authored motion, transport and export workflows.

The console, process and clock are simulations at the game boundary; these
checks do not establish renderer or in-game behavior.
"""
import json
import math
import re
import shutil
import subprocess
import warnings
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from core_helpers import FakeConsole, FakeClock, CountedEvent
from dolly import native_path
from dolly.camera_export import export_jsx, export_usda, sample_camera
from dolly.controller import Controller
from dolly.path import CvarTrack, Keyframe, Project, TrackKey, STANDARD_ASPECT, validate_cvar_name, parse_cvar_value
from dolly.playback import ReplayClock


def key(t, x=0, y=0, z=0, pitch=0, yaw=0, roll=0, fov=90, aspect=STANDARD_ASPECT):
    return Keyframe(t, x, y, z, pitch, yaw, roll, fov, aspect)


def shot():
    return Project(name='Test', start_tick=100, tick_rate=10, interpolation='linear',
                   lens_interpolation='linear', keyframes=[
        key(0, 0, 0, 200), key(10, 100, 200, 300, 30, 90, 10, aspect=1.0)])


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def game(self):
        messages = []
        controller = Controller(messages.append)
        clock = FakeClock()
        controller._stop_event = CountedEvent(clock)
        patcher = patch('dolly.controller.time.perf_counter', clock.monotonic)
        patcher.start()
        self.addCleanup(patcher.stop)
        console = FakeConsole()
        process = SimpleNamespace(poll=lambda: None)
        controller._console = console
        controller._session = SimpleNamespace(process=process, pid=1234,
            session_dir=Path('unused'), close=MagicMock(), restore_gameinfo=MagicMock(), owns_console_port=lambda: True)
        controller._demo = Path('/chosen/example.dem')
        controller._unlocker_pid = 1234
        controller._probe_result = {'capabilities': {'spec_goto': True, 'cl_citadel_forceangles': True, 'r_aspectratio': True}}
        return controller, console, process, clock, messages

    def start(self, controller, project, **kwargs):
        # Start real playback synchronously up to the worker-thread boundary.
        worker = MagicMock()
        worker.is_alive.return_value = False
        with patch('dolly.controller.threading.Thread', return_value=worker):
            controller.play(project, **kwargs)
        worker.start.assert_called_once()
        return worker

    def test_project_roundtrip_version_migration_and_atomic_failure(self):
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
        self.assertEqual(json.loads(original)['version'], 10)
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

    def test_project_rejects_malformed_schema_invalid_values_and_injection(self):
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

    def test_camera_curves_tracks_and_retime(self):
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
        # Curvature is continuous at the nontrivial spline key.
        h = .001
        f = lambda t: spline.evaluate(t)['x']
        curvature_left = (f(2)-2*f(2-h)+f(2-2*h))/(h*h)
        curvature_right = (f(2+2*h)-2*f(2+h)+f(2))/(h*h)
        self.assertAlmostEqual(curvature_left, curvature_right, places=2)
        angles = Project(interpolation='linear', keyframes=[key(0, yaw=170, roll=-170), key(4, yaw=-170, roll=170)])
        self.assertEqual((angles.evaluate(1)['yaw'], angles.evaluate(3)['roll']), (175, -185))
        angles.rotation_mode = 'unwrapped'
        angles.keyframes[1].yaw = 720
        angles.keyframes[0].yaw = 0
        self.assertEqual(angles.evaluate(2)['yaw'], 360)
        self.check_tracks_and_retime()

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

    def test_replay_clock_stall_jitter_and_rewind(self):
        clock = ReplayClock(64, .1)
        with self.assertRaises(RuntimeError): clock.position(0)
        clock.observe(100, 0)
        self.assertAlmostEqual(clock.position(.1), 100.64)
        clock.observe(100, .1)
        self.assertEqual(clock.position(10), 101)
        clock.observe(103, 10)
        self.assertEqual(clock.position(10), 101)  # delayed sample must not jump
        self.assertGreater(clock.position(10.1), 101)
        self.assertLessEqual(clock.position(10.1), 104)
        clock.observe(50, 11)
        self.assertEqual(clock.position(11), 50)
        self.assertAlmostEqual(clock.position(11.05), 50.32)

    def test_playback_orders_camera_before_resume_and_restores_on_stop(self):
        controller, console, _, _, _ = self.game()
        project = shot()
        project.setup_values = {'r_citadel_depthoffield_enable': 1}
        project.tracks = [CvarTrack('r_citadel_depthoffield_focus_distance', [TrackKey(0, 900), TrackKey(10, 1200)])]
        console.tick = 170
        console.goto_output = 100
        self.start(controller, project, speed=.5)
        ops = console.operations
        self.assertLess(ops.index('demo_gototick 100 0 1'), ops.index('spec_goto 0 0 200 0 0'))
        self.assertLess(ops.index('spec_goto 0 0 200 0 0'), ops.index('demo_resume'))
        snapshot = console.resume_snapshots[0]
        self.assertEqual(snapshot['tick'], 100)
        self.assertEqual(snapshot['pose'], [0, 0, 200, 0, 0])
        for name, expected in {'r_aspectratio': STANDARD_ASPECT, 'r_citadel_depthoffield_enable': 1,
                               'r_citadel_depthoffield_focus_distance': 900, 'citadel_hud_visible': 0,
                               'engine_no_focus_sleep': 0, 'demo_timescale': .5}.items():
            with self.subTest(name=name): self.assertAlmostEqual(snapshot['values'][name], expected)
        self.assertTrue(controller.status()['playing'])
        controller.pause()
        self.assertEqual(console.values['citadel_hud_visible'], 1)
        self.assertEqual(console.values['engine_no_focus_sleep'], 20)
        self.assertEqual(console.values['r_citadel_depthoffield_enable'], 1)
        controller.stop()
        self.assertEqual(console.values['r_citadel_depthoffield_enable'], 0)
        self.assertEqual(console.values['r_citadel_depthoffield_focus_distance'], 600)
        self.assertEqual(console.values['r_aspectratio'], 0)
        self.assertEqual(console.values['demo_timescale'], 1)
        # A worker that reaches the final replay tick holds the last pose and
        # restores temporary playback state; Stop restores the camera settings.
        controller, console, _, clock, _ = self.game()
        console.goto_output = 100
        self.start(controller, shot(), speed=.5)
        console.goto_outputs.extend([200, 200])
        controller._stop_event = CountedEvent(clock, limit=4)
        controller._run(shot(), 0, .5, 60, False)
        self.assertFalse(controller.status()['playing'])
        self.assertEqual(controller.status()['time'], 10)
        self.assertTrue(console.paused)
        self.assertEqual(console.values['citadel_hud_visible'], 1)
        self.assertEqual(console.values['engine_no_focus_sleep'], 20)
        self.assertEqual(console.values['r_aspectratio'], 1)
        controller.stop()
        self.assertEqual(console.values['r_aspectratio'], 0)

        from camera_modes import exercise_camera_modes
        exercise_camera_modes(self, self.root)

    def test_playback_guards_and_failed_restore_are_retryable(self):
        for reason in ('wrong demo', 'no demo', 'dead game', 'disconnected'):
            with self.subTest(reason=reason):
                controller, console, process, _, _ = self.game()
                if reason == 'wrong demo': console.demo_name = 'other.dem'
                elif reason == 'no demo': console.demo_output = 'Error - Not currently playing back a demo.'
                elif reason == 'dead game': process.poll = lambda: 0
                else: console.is_connected = False
                with patch('dolly.controller.threading.Thread') as worker:
                    with self.assertRaises((RuntimeError, ValueError)): controller.play(shot())
                worker.assert_not_called()
                self.assertFalse(any('spec_goto ' in c or 'demo_gototick ' in c or 'demo_resume' in c
                                     for c in console.requests + console.sent))
        controller, console, _, _, _ = self.game()
        console.fail_commands.add('demo_resume')
        with patch('dolly.controller.threading.Thread') as worker:
            with self.assertRaisesRegex(RuntimeError, 'Simulated console failure'): controller.play(shot(), speed=2)
        worker.assert_not_called()
        self.assertEqual(console.values['citadel_hud_visible'], 1)
        self.assertEqual(console.values['engine_no_focus_sleep'], 20)
        self.assertFalse(console.resume_snapshots)
        console.fail_commands.clear()
        self.start(controller, shot())
        console.fail_commands.add('citadel_hud_visible 1')
        with self.assertLogs('dolly', level='WARNING'): controller.stop()
        self.assertIn('citadel_hud_visible', controller._playback_restore)
        with self.assertLogs('dolly', level='WARNING'), patch('dolly.controller.threading.Thread') as worker:
            with self.assertRaisesRegex(RuntimeError, 'Previous settings still need restoration'): controller.play(shot())
        worker.assert_not_called()
        console.fail_commands.clear()
        controller.stop()
        self.assertEqual(console.values['citadel_hud_visible'], 1)
        self.assertEqual(controller._playback_restore, {})
        # Once a shot is running, losing the selected replay stops the loop
        # before a second camera update reaches the game.
        controller, console, _, clock, _ = self.game()
        console.demo_outputs.extend([100, 'Error - Not currently playing back a demo.'])
        controller._stop_event = CountedEvent(clock, limit=5)
        with self.assertLogs('dolly', level='ERROR'):
            controller._run(shot(), 0, 1, 60, False)
        self.assertEqual(len(console.camera_writes), 1)
        self.assertFalse(controller.status()['playing'])

    def test_seek_rewind_boundaries_and_bad_time(self):
        controller, console, _, _, _ = self.game()
        controller.seek(shot(), 2.5)
        self.assertIn('demo_gototick 125 0 1', console.requests)
        self.assertIn('spec_goto 25 50 225', console.camera_writes[-1])
        controller.seek(shot(), 99)
        self.assertEqual(controller.status()['time'], 10)
        controller.seek(shot(), -2)
        self.assertIn('demo_gototick 100 0 1', console.requests)
        self.assertEqual(controller.status()['time'], 0)
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(time=value):
                console.requests.clear()
                with self.assertRaises(ValueError): controller.seek(shot(), value)
                self.assertFalse(any('spec_goto ' in c or 'demo_gototick ' in c for c in console.requests))
        controller._stop_event.clear()
        console.goto_seek_output = ('Demo Skipping: skipping to demo tick 125 (game tick 1000) '
                                    'from full packet 12 (900).  Current playback is 22 (100)')
        console.goto_outputs.extend([124] * 12)
        result = controller._seek_tick(125)
        self.assertEqual(result['seek_boundary']['actual_tick'], 124)
        self.assertEqual(result['seek_boundary']['reason'], 'recorded_packet')
        self.assertTrue(controller._last_seek_details['verified'])
        # A forward skip undershoot is not a recorded packet floor. It must
        # fail rather than report a successful seek at the wrong tick.
        controller, console, _, _, _ = self.game()
        console.goto_seek_output = ('Demo Skipping: skipping forward to demo tick 125 (game tick 1000) '
                                    'from current 50 (925)')
        console.goto_outputs.extend([124] * 500)
        with self.assertRaisesRegex(RuntimeError, 'did not reach tick 125'):
            controller._seek_tick(125)
        self.assertFalse(controller._last_seek_details['verified'])

    def test_native_cubic_wire_coefficients_reproduce_motion(self):
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
        compiler = shutil.which('g++')
        if compiler is None:
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

    def test_camera_export_samples_speed_lens_and_output(self):
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
    def test_video_export_lifecycle_and_timing(self):
        from video_workflow import exercise_video_workflow
        exercise_video_workflow(self, self.root)
        # The recorder simulates its controller boundary; verify the real
        # engine timing controller separately through the console transport.
        controller, console, _, _, _ = self.game()
        console.values.update(host_framerate=90, r_wait_on_present=0)
        controller.set_export_timing(60, .5)
        self.assertEqual((console.values['host_framerate'], console.values['r_wait_on_present']), (120, 1))
        controller.clear_export_timing()
        self.assertEqual((console.values['host_framerate'], console.values['r_wait_on_present']), (90, 0))



if __name__ == '__main__':
    unittest.main()
