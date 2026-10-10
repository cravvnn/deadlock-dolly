"""The object catalog is stable, append-only, and self-consistent."""
import unittest

from dolly import object_library as lib


class ObjectLibraryTests(unittest.TestCase):
    def test_ids_are_unique_and_indexed_in_order(self):
        ids = lib.OBJECT_IDS
        self.assertEqual(len(ids), len(set(ids)))
        for index, name in enumerate(ids):
            self.assertEqual(lib.object_index(name), index)

    def test_every_entry_is_complete_and_bounded(self):
        for item in lib.OBJECTS:
            name, label, category, shape = item[0], item[1], item[2], item[3]
            self.assertTrue(name and label and category)
            self.assertIn(shape, lib.PROXY_SHAPES)
            self.assertIn(category, lib.CATEGORY_ORDER)
            for size in item[4:7]:
                self.assertGreaterEqual(size, lib.SIZE_MIN)
                self.assertLessEqual(size, lib.SIZE_MAX)
            self.assertIsInstance(item[7], str)

    def test_lookup_helpers(self):
        first = lib.OBJECT_DEFAULT
        self.assertEqual(lib.object_label(first), lib.OBJECT_LABELS[first])
        self.assertEqual(lib.object_shape(first), lib.OBJECT_SHAPES[first])
        self.assertEqual(lib.object_size(first), lib.OBJECT_DEFAULT_SIZE[first])
        self.assertEqual(lib.object_model(first), "")
        with self.assertRaises(ValueError):
            lib.object_index("does_not_exist")

    def test_catalog_rows_are_portable(self):
        rows = lib.catalog()
        self.assertEqual(len(rows), len(lib.OBJECTS))
        for row in rows:
            self.assertEqual(set(row), {"id", "label", "category", "shape", "size", "model"})
            self.assertEqual(len(row["size"]), 3)

    def test_append_only_shape(self):
        # Guard the protocol contract: existing ids must not be reordered.
        self.assertEqual(lib.OBJECT_DEFAULT, lib.OBJECT_IDS[0])
        self.assertIn("marker", lib.OBJECT_IDS)


if __name__ == "__main__":
    unittest.main()
