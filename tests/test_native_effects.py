import copy
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from dolly.native_effects import EFFECTS, SHOT_HEADER, compile_effects, compile_shot
from dolly.native_path import compile_project, CHANNELS
from dolly.path import Project, Keyframe, TrackKey, CvarTrack


def project():
    return Project(keyframes=[Keyframe(0, 0,0,10,0,350,0), Keyframe(2,30,20,20,20,20,0)],
        setup_values={"r_depth_of_field":1}, tracks=[
          CvarTrack("r_citadel_depthoffield_focus_distance", [TrackKey(.2,200),TrackKey(.7,900),TrackKey(3,400)], "smooth", 600),
          CvarTrack("r_citadel_depthoffield_mode", [TrackKey(0,0),TrackKey(.5,1),TrackKey(2,2)], "step")])

class EffectCompilerTests(unittest.TestCase):
    def test_camera_bytes_and_authored_shot_unchanged(self):
        p=project();saved=copy.deepcopy(p.to_dict());data=compile_shot(p)
        magic,version,cam,fx,reserved=SHOT_HEADER.unpack_from(data)
        self.assertEqual((magic,version,reserved),(b'DLYSHOT2',1,0))
        self.assertEqual(data[24:24+cam],compile_project(p))
        self.assertEqual(data[24+cam:],compile_effects(p))
        self.assertEqual(len(data),24+cam+fx);self.assertEqual(saved,p.to_dict())
    def test_unsupported_or_non_discrete_mode_rejected(self):
        p=project();p.setup_values['cam_idealdist']=100
        with self.assertRaisesRegex(ValueError,'Not supported'):compile_shot(p)
        p=project();p.tracks[1].interpolation='smooth'
        with self.assertRaisesRegex(ValueError,'Step'):compile_shot(p)
        p=project();p.tracks[0].restore_value=10001
        with self.assertRaises(ValueError):compile_shot(p)
    def test_fixed_value_and_track_precedence(self):
        p=project();p.setup_values[p.tracks[0].name]=800
        self.assertEqual(compile_effects(p),compile_effects(project()))
    def test_fixed_value_with_empty_track_retains_restore_override(self):
        p=project();p.tracks.append(CvarTrack("r_depth_of_field", [], restore_value=0))
        self.assertNotEqual(compile_effects(p),compile_effects(project()))

    def test_empty_tracks_do_not_lose_fixed_setting(self):
        p=project();p.tracks.append(CvarTrack('r_depth_of_field',[]))
        self.assertEqual(compile_effects(p),compile_effects(project()))

@unittest.skipUnless(shutil.which('g++'), 'Windows CMake runs the native effect gate')
class NativeEffectEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        root=Path(__file__).resolve().parents[1];cls.runner=Path(cls.temp.name)/'effects'
        subprocess.run([shutil.which('g++'),'-std=c++17','-O2','-ffp-contract=off','-I',str(root/'native/include'),
            str(root/'native/src/dolly_path.cpp'),str(root/'native/src/dolly_effects.cpp'),
            str(root/'native/tests/effect_tests.cpp'),'-o',str(cls.runner)],check=True,capture_output=True)
    def run_blob(self,data,*times):
        blob=Path(self.temp.name)/'shot';blob.write_bytes(data)
        return subprocess.run([str(self.runner),str(blob),*map(str,times)],capture_output=True,text=True)
    def test_cpp_camera_and_effect_curves_match_editor_at_subtick_times(self):
        p=project();times=[0,.2,.499999,.5,.500001,.7,2,3,4]+[i/128 for i in range(385)]
        result=self.run_blob(compile_shot(p),*times);self.assertEqual(result.returncode,0,result.stderr)
        names=sorted(p.evaluate(0)['cvars'],key=lambda n:EFFECTS[n][0])
        for time,row in zip(times,result.stdout.splitlines()):
            expected=p.evaluate(time);values=[expected[n] for n in CHANNELS]+[expected['cvars'][n] for n in names]
            for actual,wanted in zip(map(float,row.split()),values):self.assertAlmostEqual(actual,wanted,places=9)
        self.assertEqual(len(result.stdout.splitlines()),len(times))
    def test_rejects_corrupt_or_truncated_payload(self):
        data=compile_shot(project());offset=24+SHOT_HEADER.unpack_from(data)[2]
        cases=[data[:n] for n in [0,8,23,24,len(data)-1]]+[data+b'\0']
        for where,value in [(8,99),(16,0xffffffff),(offset+8,100),(offset+16,99),(offset+20,0xffffffff)]:
            bad=bytearray(data);struct.pack_into('<I',bad,where,value);cases.append(bad)
        for bad in cases:self.assertNotEqual(self.run_blob(bad).returncode,0)
    def test_all_supported_controls_and_no_effect_shots(self):
        p=project();p.tracks=[];p.setup_values={n:spec[1] for n,spec in EFFECTS.items()}
        self.assertEqual(self.run_blob(compile_shot(p),0,1,2).returncode,0)
        p.setup_values={};self.assertEqual(self.run_blob(compile_shot(p),0,1,2).returncode,0)
