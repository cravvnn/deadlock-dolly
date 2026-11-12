"""Producer boundary tests; synthetic captures are not live capture proof."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from dolly.camera_export import Frame, Take
from dolly.crav_export import build_crav, export_crav
from dolly.path import Project


def inputs():
    replay = dict(path="example.dem", sha256="a" * 64, build=6851,
                  map="dl_midtown", tick_rate=64, total_ticks=640)
    project = Project(name="Test shot", start_tick=64)
    frames = [Frame(i, i/30, (i, 0, 0), (0, 0, 0), 90, i/30) for i in range(2)]
    take = Take(frames, 2, [], source="native-view-hook", size=(1920, 1080), fixed_step=True)
    return project, take, replay


class CravExportTests(unittest.TestCase):
    def test_recorded_pose_and_replay_timing_are_preserved_without_media(self):
        project, take, replay = inputs()
        before = copy.deepcopy((project, take, replay))
        result = build_crav(project, take, fps=30, replay_before=replay, replay_after=replay)
        self.assertEqual((project, take, replay), before)
        shot = result["shots"][0]
        self.assertNotIn("scene", result)
        self.assertNotIn("media", shot)
        sample = shot["camera"]["samples"][1]
        self.assertEqual(sample["position"], [1, 0, 0])
        self.assertEqual(sample["right"], [0, -1, 0])
        self.assertAlmostEqual(shot["range"]["end_tick_exact"], 64 + 64/30)

    def test_refuses_unbound_incomplete_authored_or_unsupported_takes(self):
        for case in ("identity", "authored", "partial", "warning", "realtime", "retime", "overrides", "particles", "projection", "dimensions", "video_time"):
            with self.subTest(case=case):
                project, take, replay = inputs()
                after = dict(replay)
                if case == "identity": after["sha256"] = "b" * 64
                elif case == "authored": take = take._replace(source="authored")
                elif case == "partial": take = take._replace(video_frames=3)
                elif case == "warning": take = take._replace(warnings=["incomplete"])
                elif case == "realtime": take = take._replace(fixed_step=False)
                elif case == "retime": take.frames[1] = take.frames[1]._replace(phase=0)
                elif case == "overrides": project.setup_values["r_dof"] = 1
                elif case == "particles": project.confetti_enabled = True
                elif case == "projection": take.frames[0] = take.frames[0]._replace(projection=(0,) * 16)
                elif case == "dimensions": take = take._replace(size=(1920, 0))
                elif case == "video_time": take.frames[0] = take.frames[0]._replace(video_time=5)
                with self.assertRaises(ValueError):
                    build_crav(project, take, fps=30, replay_before=replay, replay_after=after)

    def test_publication_is_small_unicode_safe_and_never_overwrites(self):
        project, take, replay = inputs()
        project.name = "Cinéma"
        result = build_crav(project, take, fps=30, replay_before=replay, replay_after=replay)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shot.crav"
            export_crav(result, path)
            original = path.read_bytes()
            self.assertLess(len(original), 2000)
            self.assertEqual(json.loads(original), result)
            with self.assertRaises(FileExistsError):
                export_crav(result, path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            result["shots"][0]["camera"]["samples"][0]["state"] = "projection-only"
            with self.assertRaises(ValueError):
                export_crav(result, Path(directory) / "bad.crav")
            self.assertFalse((Path(directory) / "bad.crav").exists())


if __name__ == "__main__":
    unittest.main()
