"""Object Picker edits mutate only the placed list and reject bad input."""
import copy
import unittest

from dolly import object_picker as picks
from dolly.object_library import OBJECT_DEFAULT, OBJECT_IDS
from dolly.path import Project


class ObjectPickerEditTests(unittest.TestCase):
    def test_place_appends_and_does_not_mutate_original(self):
        project = Project()
        result = picks.place(project, "crate", [1, 2, 3], angles=[0, 90, 0], scale=2)
        self.assertEqual(len(result.objects), 1)
        self.assertEqual(project.objects, [])
        item = result.objects[0]
        self.assertEqual(item.library_id, "crate")
        self.assertEqual(item.position, [1.0, 2.0, 3.0])
        self.assertEqual(item.angles, [0.0, 90.0, 0.0])
        self.assertEqual(item.scale, 2.0)

    def test_place_rejects_unknown_and_bad_position(self):
        with self.assertRaises(ValueError):
            picks.place(Project(), "nope", [0, 0, 0])
        with self.assertRaises(ValueError):
            picks.place(Project(), "crate", [0, 0])

    def test_place_overflow_rejected(self):
        project = Project()
        for _ in range(picks.MAX_OBJECTS):
            project = picks.place(project, OBJECT_DEFAULT, [0, 0, 0])
        with self.assertRaises(ValueError):
            picks.place(project, OBJECT_DEFAULT, [0, 0, 0])

    def test_remove_and_select(self):
        project = picks.place(Project(), "crate", [0, 0, 0])
        project = picks.place(project, "barrel", [1, 1, 1])
        removed = picks.remove(project, 0)
        self.assertEqual([o.library_id for o in removed.objects], ["barrel"])
        self.assertEqual(len(project.objects), 2)
        with self.assertRaises(ValueError):
            picks.remove(project, 5)
        self.assertEqual(picks.select(project, 1), 1)
        self.assertEqual(picks.select(project, -1), -1)
        self.assertEqual(picks.select(project, 9), -1)

    def test_set_transform(self):
        project = picks.place(Project(), "crate", [0, 0, 0])
        moved = picks.set_transform(project, 0, position=[5, 6, 7], scale=3)
        self.assertEqual(moved.objects[0].position, [5.0, 6.0, 7.0])
        self.assertEqual(moved.objects[0].scale, 3.0)
        self.assertEqual(project.objects[0].position, [0.0, 0.0, 0.0])
        with self.assertRaises(ValueError):
            picks.set_transform(project, 4, position=[0, 0, 0])

    def test_clamp_distance(self):
        self.assertEqual(picks.clamp_distance(600), 600.0)
        for value in (0, 5, 20001, "x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                picks.clamp_distance(value)


class ProjectPersistenceTests(unittest.TestCase):
    def test_objects_round_trip(self):
        project = picks.place(Project(), "barrel", [1, 2, 3], angles=[0, 45, 0])
        project = picks.place(project, "crate", [4, 5, 6])
        data = project.to_dict()
        self.assertEqual(data["version"], 11)
        self.assertEqual(len(data["objects"]), 2)
        restored = Project.from_dict(data)
        self.assertEqual(len(restored.objects), 2)
        self.assertEqual(restored.objects[0].library_id, "barrel")
        self.assertEqual(restored.objects[1].position, [4.0, 5.0, 6.0])

    def test_old_project_without_objects_loads(self):
        base = Project().to_dict()
        base.pop("objects", None)
        restored = Project.from_dict(base)
        self.assertEqual(restored.objects, [])

    def test_invalid_objects_rejected_on_load(self):
        base = Project().to_dict()
        base["objects"] = [{"library_id": "nope", "model": "", "position": [0, 0, 0],
                            "angles": [0, 0, 0], "scale": 1.0}]
        with self.assertRaises(ValueError):
            Project.from_dict(base)

    def test_objects_field_rejected_before_v11(self):
        project = picks.place(Project(), "crate", [0, 0, 0])
        data = project.to_dict()
        data["version"] = 10
        with self.assertRaises(ValueError):
            Project.from_dict(data)


if __name__ == "__main__":
    unittest.main()
