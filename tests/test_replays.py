import os
from pathlib import Path
import tempfile
import unittest

from dolly.replays import discover_replays, find_replay_folder, parse_launch_options


class ReplayBrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)

    def test_replays_are_case_insensitive_sorted_and_nonrecursive(self):
        older = self.folder / "old.dem"
        older.write_bytes(b"old")
        newer = self.folder / "new.DEM"
        newer.write_bytes(b"new replay")
        (self.folder / "not-a-replay.dem.info").write_text("skip")
        (self.folder / "directory.dem").mkdir()
        (self.folder / "directory.dem" / "nested.dem").write_bytes(b"nested")
        os.utime(older, ns=(100, 100))
        os.utime(newer, ns=(200, 200))
        result = discover_replays(self.folder)
        self.assertEqual([entry.name for entry in result], ["new.DEM", "old.dem"])
        self.assertEqual(result[0].size_bytes, 10)
        self.assertEqual(result[0].path, newer.resolve())
        self.assertEqual(result[0].modified_ns, 200)

    def test_missing_folder_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            discover_replays(self.folder / "missing")

    def test_installation_picker_forms_resolve_same_folder(self):
        base = self.folder / "Steam Library" / "Deadlock"
        expected = base / "game" / "citadel" / "replays"
        for selected in (base, base / "game", base / "game" / "citadel",
                         base / "game" / "bin" / "win64" / "deadlock.exe",
                         base / "game" / "bin" / "win64" / "citadel.exe"):
            with self.subTest(selected=selected):
                self.assertEqual(find_replay_folder(selected), expected)


class LaunchOptionsTests(unittest.TestCase):
    def test_window_resolution_and_refresh_are_normalized(self):
        self.assertEqual(parse_launch_options('-sw -width "1920" -height 1080 -refresh 144 -noborder'),
                         ["-windowed", "-w", "1920", "-h", "1080", "-refresh", "144", "-noborder"])

    def test_required_flags_remain_owned_by_launcher(self):
        self.assertEqual(parse_launch_options("-DEV -insecure -console -fullscreen"), ["-fullscreen"])
        self.assertEqual(parse_launch_options(""), [])

    def test_managed_game_flags_and_console_commands_are_rejected(self):
        for value in ("-secure", "-vulkan", "-dx12", "-game citadel", "-addon malicious",
                      "-netconport 9999", "-vconsole", "+playdemo test.dem", "+exec other.cfg",
                      "-windowed;quit", "-windowed\n+quit", "-w 1000 -insecure;quit",
                      "-w $(anything)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_launch_options(value)

    def test_conflicting_flags_and_invalid_values_are_actionable(self):
        for value in ("-windowed -fullscreen", "-fullscreen -noborder", "-w 1920 -width 1280",
                      "-w", "-w 0", "-h 99999", "-refresh nan", "-refresh 0", "-w １９２０",
                      '-w "1920', "-windowed -sw"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_launch_options(value)


if __name__ == "__main__":
    unittest.main()
