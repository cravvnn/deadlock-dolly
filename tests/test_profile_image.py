"""Profile lookup must parse immutable PE exception metadata only once."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from generate_profile import Image


class ProfileImageTests(unittest.TestCase):
    def image(self, ranges):
        image = Image.__new__(Image)
        image._function_ranges = image._function_begins = None
        image.pe = SimpleNamespace(parse_data_directories=Mock(),
            DIRECTORY_ENTRY_EXCEPTION=[SimpleNamespace(struct=SimpleNamespace(
                BeginAddress=begin, EndAddress=end)) for begin,end in ranges])
        return image

    def test_repeated_lookups_parse_once_and_keep_boundaries(self):
        image = self.image([(30,40),(10,20),(0,5),(50,0)])
        with patch.dict(sys.modules, {'pefile':SimpleNamespace(DIRECTORY_ENTRY={
                'IMAGE_DIRECTORY_ENTRY_EXCEPTION':3})}):
            for _ in range(1000):
                self.assertEqual(image.function_at(10),(10,20))
                self.assertEqual(image.function_at(19),(10,20))
                self.assertEqual(image.function_at(39),(30,40))
                for address in (0,9,20,29,40,100):
                    self.assertIsNone(image.function_at(address))
            self.assertEqual(image.functions(),[(10,20),(30,40)])
        image.pe.parse_data_directories.assert_called_once_with(directories=[3])

    def test_returned_list_cannot_corrupt_lookup(self):
        image=self.image([(10,20)])
        with patch.dict(sys.modules, {'pefile':SimpleNamespace(DIRECTORY_ENTRY={
                'IMAGE_DIRECTORY_ENTRY_EXCEPTION':3})}):
            returned=image.functions()
            returned.clear()
            self.assertEqual(image.function_at(15),(10,20))
            self.assertEqual(image.functions(),[(10,20)])
        image.pe.parse_data_directories.assert_called_once()

    def test_empty_directory_is_cached(self):
        image=self.image([])
        del image.pe.DIRECTORY_ENTRY_EXCEPTION
        with patch.dict(sys.modules, {'pefile':SimpleNamespace(DIRECTORY_ENTRY={
                'IMAGE_DIRECTORY_ENTRY_EXCEPTION':3})}):
            for _ in range(5):
                self.assertIsNone(image.function_at(1))
                self.assertEqual(image.functions(),[])
        image.pe.parse_data_directories.assert_called_once()

    def test_failed_parse_is_not_cached(self):
        image=self.image([(10,20)])
        image.pe.parse_data_directories.side_effect=[ValueError('bad PE'),None]
        with patch.dict(sys.modules, {'pefile':SimpleNamespace(DIRECTORY_ENTRY={
                'IMAGE_DIRECTORY_ENTRY_EXCEPTION':3})}):
            with self.assertRaises(ValueError): image.function_at(12)
            self.assertEqual(image.function_at(12),(10,20))
        self.assertEqual(image.pe.parse_data_directories.call_count,2)


if __name__=='__main__':unittest.main()
