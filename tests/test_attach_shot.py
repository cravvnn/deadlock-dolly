"""Attach shot format (version 4) and DLYSHOT3 attach compilation."""
import struct
import tempfile
import unittest
from pathlib import Path

from dolly.native_effects import (ATTACH_HEADER, ATTACH_SEGMENT, ATTACH_SEGMENT2, ATTACH_SEGMENT3, SHOT_HEADER3,
                                  compile_attach, compile_shot, model_token)
from dolly.path import AttachKey, Keyframe, Project

MODEL = "models/heroes_staging/astro/astro.vmdl"
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "native/tests/fixtures/attach-shot.bin"


def fixture_project():
    """Deterministic shot shared with native/tests/attach_tests.cpp.

    Both sides assert the same bytes after compiling, so the Python compiler
    and native loader cannot drift: the first interval is free, the second is
    an attached eyes key with hide-body and known offsets, and the third key
    ends the shot.
    """
    keys = [
        Keyframe(time=0.0, x=100.0, y=200.0, z=300.0, pitch=0.0, yaw=90.0, roll=0.0,
                 aspect_ratio=16.0 / 9.0),
        Keyframe(time=1.0, x=0.0, y=0.0, z=6.0, pitch=0.0, yaw=0.0, roll=0.0,
                 aspect_ratio=16.0 / 9.0, source="attach",
                 attach=AttachKey(handle=19464268, entity_id=76, model=MODEL, point="eyes",
                                  offset=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), smoothing=0.25,
                                  hide_body=True)),
        Keyframe(time=2.0, x=110.0, y=200.0, z=300.0, pitch=0.0, yaw=90.0, roll=0.0,
                 aspect_ratio=16.0 / 9.0),
    ]
    return Project(name="attach-fixture", keyframes=keys, interpolation="linear",
                   lens_interpolation="linear")


def attach_target(**overrides):
    values = {"handle": 19464268, "entity_id": 76, "model": MODEL, "point": "eyes",
              "offset": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0), "smoothing": 0.25, "hide_body": True}
    values.update(overrides)
    return AttachKey(**values)


def project_with(specs):
    """specs is a sequence of (time, source) pairs with optional attach overrides."""
    keys = []
    for index, spec in enumerate(specs):
        time, source = spec[0], spec[1]
        attach = attach_target(**spec[2]) if len(spec) > 2 else attach_target()
        keys.append(Keyframe(time=time, x=float(index), y=2.0, z=3.0, pitch=0.0, yaw=0.0,
                             roll=0.0, source=source, attach=attach if source == "attach" else None))
    return Project(name="attach", keyframes=keys)


class AttachProjectTests(unittest.TestCase):
    def test_blend_round_trip_and_version_gate(self):
        project = fixture_project()
        project.keyframes[1].source_blend = 0.5
        project.keyframes[2].source_blend = 0.25
        data = project.to_dict()
        self.assertEqual(data["version"], 6)
        self.assertEqual(Project.from_dict(data).to_dict(), data)
        self.assertNotIn("source_blend", data["keyframes"][0])
        data["version"] = 5
        with self.assertRaisesRegex(ValueError, "Unknown camera keyframe field"):
            Project.from_dict(data)

    def test_blend_rejects_invalid_duration(self):
        for value in (-0.1, 10.1, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                project = fixture_project()
                project.keyframes[1].source_blend = value
                with self.assertRaises(ValueError):
                    project.validate()

    def test_free_project_json_is_unchanged(self):
        project = Project(name="free", keyframes=[Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0)])
        data = project.to_dict()
        self.assertEqual(data["version"], 2)
        self.assertNotIn("source", data["keyframes"][0])
        self.assertNotIn("attach", data["keyframes"][0])

    def test_attach_project_round_trips_at_version_four(self):
        project = project_with([(0.0, "attach")])
        data = project.to_dict()
        self.assertEqual(data["version"], 4)
        entry = data["keyframes"][0]
        self.assertEqual(entry["source"], "attach")
        self.assertEqual(entry["attach"]["handle"], 19464268)
        self.assertEqual(entry["attach"]["offset"], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertTrue(entry["attach"]["hide_body"])
        self.assertEqual(Project.from_dict(data).to_dict(), data)

    def test_attach_file_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "attach.dolly"
            project = project_with([(0.0, "free"), (1.0, "attach"), (2.0, "free")])
            project.save(path)
            self.assertEqual(Project.load(path).to_dict(), project.to_dict())

    def test_free_key_with_attach_data_is_refused(self):
        key = Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, source="free", attach=attach_target())
        with self.assertRaisesRegex(ValueError, "free source"):
            Project(name="bad", keyframes=[key]).validate()

    def test_attach_key_needs_identity(self):
        for overrides in ({"handle": 0, "model": ""}, {"handle": 0, "model": " "}):
            with self.subTest(overrides=overrides):
                key = Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, source="attach",
                               attach=attach_target(**overrides))
                with self.assertRaises(ValueError):
                    Project(name="bad", keyframes=[key]).validate()

    def test_attach_field_bounds_are_enforced(self):
        cases = (
            {"point": "chest"},
            {"offset": (0.0, 0.0, 0.0, 0.0, 0.0)},
            {"offset": (20000.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
            {"smoothing": 9.0},
            {"smoothing": -1.0},
            {"hide_body": 1},
            {"entity_id": -1},
            {"handle": True},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                key = Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, source="attach",
                               attach=attach_target(**overrides))
                with self.assertRaises(ValueError):
                    Project(name="bad", keyframes=[key]).validate()

    def test_attach_members_are_version_gated(self):
        data = Project(name="free", keyframes=[Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0)]).to_dict()
        data["version"] = 3
        data["keyframes"][0]["source"] = "attach"
        with self.assertRaisesRegex(ValueError, "Unknown camera keyframe field"):
            Project.from_dict(data)

    def test_bone_project_round_trips_at_version_five(self):
        project = project_with([(0.0, "attach",
                                 {"point": "bone", "bone": "weapon_bone_R"})])
        data = project.to_dict()
        self.assertEqual(data["version"], 5)
        self.assertEqual(data["keyframes"][0]["attach"]["bone"], "weapon_bone_R")
        restored = Project.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertEqual(restored.keyframes[0].attach.bone, "weapon_bone_R")

    def test_bone_names_are_validated(self):
        cases = (
            {"point": "bone"},
            {"point": "bone", "bone": ""},
            {"point": "bone", "bone": "bad name"},
            {"point": "bone", "bone": "9starts_with_digit"},
            {"point": "bone", "bone": "x" * 65},
            {"point": "eyes", "bone": "weapon_bone_R"},
            {"point": "weapon", "bone": "head"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                key = Keyframe(0.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, source="attach",
                               attach=attach_target(**overrides))
                with self.assertRaises(ValueError):
                    Project(name="bad", keyframes=[key]).validate()


class AttachCompileTests(unittest.TestCase):
    def test_blend_includes_arrival_at_final_free_key(self):
        project = fixture_project()
        project.keyframes[1].source_blend = 0.5
        project.keyframes[2].source_blend = 0.25
        block = compile_attach(project)
        self.assertEqual(ATTACH_HEADER.unpack_from(block), (b"DLYATT01", 3, 0, 3))
        self.assertEqual(len(block), ATTACH_HEADER.size + 3 * ATTACH_SEGMENT3.size)
        entries = [ATTACH_SEGMENT3.unpack_from(block, ATTACH_HEADER.size + i * ATTACH_SEGMENT3.size)
                   for i in range(3)]
        self.assertEqual([entry[:3] for entry in entries],
                         [(0, 1, 0), (1, 2, 3), (2, 2, 0)])
        self.assertEqual([entry[-1] for entry in entries], [0, 0.5, 0.25])

    def test_blend_into_final_attached_key_is_not_discarded(self):
        project = project_with([(0, "free"), (1, "attach", {"point": "bone", "bone": "head"})])
        project.keyframes[1].source_blend = 0.5
        block = compile_attach(project)
        self.assertEqual(ATTACH_HEADER.unpack_from(block)[1:], (3, 0, 2))
        last = ATTACH_SEGMENT3.unpack_from(block, ATTACH_HEADER.size + ATTACH_SEGMENT3.size)
        self.assertEqual(last[:4], (1, 1, 3, 2))
        self.assertEqual(last[-2:], (model_token("head"), 0.5))

    def test_free_shot_stays_dlyshot2(self):
        project = project_with([(0.0, "free")])
        self.assertEqual(compile_attach(project), b"")
        self.assertEqual(compile_shot(project)[:8], b"DLYSHOT2")

    def test_attach_shot_emits_dlyshot3_with_matching_lengths(self):
        project = project_with([(0.0, "attach"), (1.0, "attach")])
        block = compile_attach(project)
        magic, version, reserved, count = ATTACH_HEADER.unpack_from(block, 0)
        self.assertEqual((magic, version, reserved, count), (b"DLYATT01", 1, 0, 1))
        self.assertEqual(len(block), ATTACH_HEADER.size + ATTACH_SEGMENT.size)
        shot = compile_shot(project)
        header = SHOT_HEADER3.unpack_from(shot, 0)
        self.assertEqual(header[0], b"DLYSHOT3")
        self.assertEqual(header[1], 1)
        camera_bytes, effect_bytes, attach_bytes, reserved = header[2:6]
        self.assertEqual(reserved, 0)
        self.assertEqual(camera_bytes + effect_bytes + attach_bytes, len(shot) - SHOT_HEADER3.size)
        self.assertEqual(attach_bytes, len(block))
        self.assertEqual(shot[-len(block):], block)

    def test_attach_segment_values_round_trip(self):
        project = project_with([(0.0, "attach"), (1.0, "attach")])
        block = compile_attach(project)
        begin, end, flags, point, handle, entity_id, token, *rest = \
            ATTACH_SEGMENT.unpack_from(block, ATTACH_HEADER.size)
        self.assertEqual((begin, end), (0.0, 1.0))
        self.assertEqual(flags, 3)
        self.assertEqual(point, 0)
        self.assertEqual(handle, 19464268)
        self.assertEqual(entity_id, 76)
        self.assertEqual(token, model_token(MODEL))
        self.assertEqual(rest[:6], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(rest[6], 0.25)

    def test_hard_cut_segments_follow_the_governing_key(self):
        project = project_with([(0.0, "free"), (1.0, "attach"), (2.0, "free")])
        block = compile_attach(project)
        count = ATTACH_HEADER.unpack_from(block, 0)[3]
        self.assertEqual(count, 2)
        first = ATTACH_SEGMENT.unpack_from(block, ATTACH_HEADER.size + ATTACH_SEGMENT.size)
        self.assertEqual((first[0], first[1], first[2]), (1.0, 2.0, 3))
        second = ATTACH_SEGMENT.unpack_from(block, ATTACH_HEADER.size)
        self.assertEqual((second[0], second[1], second[2]), (0.0, 1.0, 0))

    def test_weapon_point_and_visible_body_flags(self):
        project = project_with([(0.0, "attach", {"point": "weapon", "hide_body": False}),
                                (1.0, "attach")])
        entry = ATTACH_SEGMENT.unpack_from(compile_attach(project), ATTACH_HEADER.size)
        self.assertEqual(entry[2], 1)
        self.assertEqual(entry[3], 1)

    def test_bone_point_compiles_to_version_two_with_the_name_hash(self):
        project = project_with([(0.0, "attach", {"point": "bone", "bone": "weapon_bone_R"}),
                                (1.0, "attach")])
        block = compile_attach(project)
        magic, version, reserved, count = ATTACH_HEADER.unpack_from(block, 0)
        self.assertEqual((magic, version, reserved, count), (b"DLYATT01", 2, 0, 1))
        self.assertEqual(len(block), ATTACH_HEADER.size + ATTACH_SEGMENT2.size)
        entry = ATTACH_SEGMENT2.unpack_from(block, ATTACH_HEADER.size)
        self.assertEqual(entry[3], 2)
        self.assertEqual(entry[-1], model_token("weapon_bone_R"))

    def test_mixed_bone_and_weapon_segments_share_the_version_two_stride(self):
        project = project_with([(0.0, "attach", {"point": "bone", "bone": "head"}),
                                (1.0, "attach", {"point": "weapon"}),
                                (2.0, "attach")])
        block = compile_attach(project)
        version, count = ATTACH_HEADER.unpack_from(block, 0)[1], ATTACH_HEADER.unpack_from(block, 0)[3]
        self.assertEqual((version, count), (2, 2))
        entries = [ATTACH_SEGMENT2.unpack_from(block, ATTACH_HEADER.size + index * ATTACH_SEGMENT2.size)
                   for index in range(count)]
        self.assertEqual([entry[3] for entry in entries], [2, 1])
        self.assertEqual(entries[0][-1], model_token("head"))
        self.assertEqual(entries[1][-1], 0)
        self.assertEqual(entries[0][0:2], (0.0, 1.0))
        self.assertEqual(entries[1][0:2], (1.0, 2.0))

    def test_compiled_shot_matches_the_native_fixture(self):
        self.assertTrue(FIXTURE_PATH.is_file(), "native attach fixture is missing")
        self.assertEqual(compile_shot(fixture_project()), FIXTURE_PATH.read_bytes())

    def test_attach_identity_token_is_stable(self):
        self.assertEqual(model_token(MODEL), 0x4F5B17F42C61561D)
        self.assertEqual(model_token(""), 14695981039346656037)


if __name__ == "__main__":
    unittest.main()
