"""Placed-object records validate, round-trip, and reject malformed data."""
import copy
import unittest

from dolly.object_model import PlacedObject, validate_objects


class PlacedObjectTests(unittest.TestCase):
    def make(self):
        return PlacedObject(library_id="crate", model="",
                            position=[1.0, 2.0, 3.0], angles=[0.0, 90.0, 0.0], scale=2.0)

    def test_round_trip(self):
        obj = self.make()
        self.assertEqual(PlacedObject.from_dict(obj.to_dict()), obj)

    def test_defaults_are_valid(self):
        PlacedObject().validate()

    def test_unknown_library_id_rejected(self):
        obj = self.make()
        obj.library_id = "nope"
        with self.assertRaises(ValueError):
            obj.validate()

    def test_bad_vectors_rejected(self):
        for field, value in (("position", [1, 2]), ("angles", ["a", 0, 0]),
                             ("position", [1, 2, float("nan")]),
                             ("angles", [1, 2, float("inf")])):
            obj = self.make()
            setattr(obj, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                obj.validate()

    def test_scale_bounds(self):
        for scale in (0.0, -1.0, 100.0, float("nan"), True):
            obj = self.make()
            obj.scale = scale
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                obj.validate()

    def test_position_and_angle_limits(self):
        obj = self.make()
        obj.position = [1e9, 0, 0]
        with self.assertRaises(ValueError):
            obj.validate()
        obj = self.make()
        obj.angles = [0, 1e9, 0]
        with self.assertRaises(ValueError):
            obj.validate()

    def test_from_dict_rejects_extra_and_missing_fields(self):
        raw = self.make().to_dict()
        with self.assertRaises(ValueError):
            PlacedObject.from_dict({**raw, "extra": 1})
        missing = dict(raw)
        missing.pop("scale")
        with self.assertRaises(ValueError):
            PlacedObject.from_dict(missing)
        with self.assertRaises(ValueError):
            PlacedObject.from_dict("not a dict")

    def test_model_path_length(self):
        obj = self.make()
        obj.model = "x" * 257
        with self.assertRaises(ValueError):
            obj.validate()

    def test_validate_objects(self):
        validate_objects([self.make(), self.make()])
        with self.assertRaises(ValueError):
            validate_objects("nope")
        with self.assertRaises(ValueError):
            validate_objects([object()])


if __name__ == "__main__":
    unittest.main()
