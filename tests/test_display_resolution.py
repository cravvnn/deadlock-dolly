from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dolly import display_resolution as dr

VIDEO = (
    '"video.cfg"\n'
    '{\n'
    '\t"Version"\t\t"20"\n'
    '\t"setting.defaultres"\t\t"2560"\n'
    '\t"setting.defaultresheight"\t\t"1440"\n'
    '\t"setting.nowindowborder"\t\t"1"\n'
    '}\n'
).encode("utf-8")


class WindowSizeTests(unittest.TestCase):
    def test_size_and_rejections(self):
        self.assertEqual(dr.window_size("-windowed -noborder -w 1024 -h 1024"), (1024, 1024))
        self.assertEqual(dr.window_size("-w 2048 -h 2048"), (2048, 2048))
        self.assertIsNone(dr.window_size(""))
        self.assertIsNone(dr.window_size("-windowed"))
        with self.assertRaises(Exception):
            dr.window_size("-w 1024")
        with self.assertRaises(Exception):
            dr.window_size("-w abc -h 1024")


class ResolutionEditTests(unittest.TestCase):
    def test_edit_changes_only_the_two_keys(self):
        updated = dr._with_resolution(VIDEO, {"setting.defaultres": "1024",
                                              "setting.defaultresheight": "1024"})
        self.assertEqual(dr._resolution(updated), {"setting.defaultres": "1024",
                                                   "setting.defaultresheight": "1024"})
        self.assertIn(b'"setting.nowindowborder"\t\t"1"', updated)
        # Everything except the two numbers is unchanged.
        self.assertEqual(updated.count(b'"1024"'), 2)

    def test_missing_resolution_is_rejected(self):
        with self.assertRaises(Exception):
            dr._resolution(b'"video.cfg"\n{\n\t"Version"\t\t"20"\n}\n')


class RestoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.session = Path(folder.name)
        self.video = self.session / "video.txt"; self.video.write_bytes(VIDEO)
        self.patches = [
            patch.object(dr.graphics_profiles, "active_account", return_value="123"),
            patch.object(dr.graphics_profiles, "steam_root", return_value=self.session),
            patch.object(dr.graphics_profiles, "video_path", return_value=self.video),
        ]
        for item in self.patches: item.start(); self.addCleanup(item.stop)

    def test_prepare_records_and_restore_puts_resolution_back(self):
        self.assertTrue(dr.prepare(self.session, "-windowed -noborder -w 1024 -h 1024"))
        self.assertTrue(dr.pending(self.session))
        # The game persisted the square size.
        self.video.write_bytes(dr._with_resolution(VIDEO, {"setting.defaultres": "1024",
                                                           "setting.defaultresheight": "1024"}))
        dr.restore(self.session)
        self.assertFalse(dr.pending(self.session))
        self.assertEqual(dr._resolution(self.video.read_bytes()),
                         {"setting.defaultres": "2560", "setting.defaultresheight": "1440"})

    def test_no_size_means_no_journal(self):
        self.assertFalse(dr.prepare(self.session, "-windowed -noborder"))
        self.assertFalse(dr.pending(self.session))

    def test_restore_without_persistence_is_a_noop(self):
        dr.prepare(self.session, "-w 1024 -h 1024")
        dr.restore(self.session)  # video.txt still holds the user's values
        self.assertEqual(dr._resolution(self.video.read_bytes()),
                         {"setting.defaultres": "2560", "setting.defaultresheight": "1440"})

    def test_user_change_is_a_conflict_and_left_untouched(self):
        dr.prepare(self.session, "-w 1024 -h 1024")
        changed = dr._with_resolution(VIDEO, {"setting.defaultres": "1920",
                                              "setting.defaultresheight": "1080"})
        self.video.write_bytes(changed)
        with self.assertRaisesRegex(Exception, "changed during"):
            dr.restore(self.session)
        self.assertEqual(dr._resolution(self.video.read_bytes()),
                         {"setting.defaultres": "1920", "setting.defaultresheight": "1080"})


if __name__ == "__main__":
    unittest.main()