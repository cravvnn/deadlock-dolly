"""Hero tracks read from the replay file in the background (dolly/replay_tracks.py)."""
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dolly import replay_tracks, weather

ROOT = Path(__file__).resolve().parents[1]


class ReplayTracksReaderTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name)
        self.replay = base / "123.dem"
        self.replay.write_bytes(b"PBDEMS2\0" + b"x" * 64)
        self.helper = base / "DollyReplayTracks.exe"
        self.helper.write_bytes(b"helper")
        patcher = mock.patch.object(weather, "hero_paths_directory", return_value=base / "paths")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []
        self.done = threading.Event()

    def fake_run(self, code=0, error=b""):
        def run(args, **kwargs):
            self.calls.append(args)
            if code == 0:
                Path(args[2]).write_bytes(b"DLYTRK02")
            self.done.set()
            return subprocess.CompletedProcess(args, code, b"", error)
        return run

    def wait(self, reader):
        self.assertTrue(self.done.wait(5))
        for _ in range(100):
            if reader.state(self.replay).state != "reading":
                return
            time.sleep(0.01)

    def test_reads_once_beside_the_store_and_never_touches_the_replay(self):
        before = self.replay.read_bytes()
        reader = replay_tracks.ReplayTracksReader(self.helper, self.fake_run())
        self.assertEqual(reader.request(self.replay).state, "reading")
        self.wait(reader)
        self.assertEqual(reader.state(self.replay).state, "ready")
        out = replay_tracks.replay_cells_file(self.replay)
        self.assertEqual(out, weather.hero_track_store(str(self.replay)).with_name(
            weather.hero_track_store(str(self.replay)).name + ".replay"))
        self.assertEqual(self.calls[0][1:], [str(self.replay), str(out)])
        self.assertEqual(self.replay.read_bytes(), before)
        # A new reader (a new Dolly session) finds the cells current and does not read again.
        again = replay_tracks.ReplayTracksReader(self.helper, self.fake_run())
        self.assertEqual(again.request(self.replay).state, "ready")
        self.assertEqual(len(self.calls), 1)
        # A changed reader (a newer s2replay) reads the replay again.
        self.helper.write_bytes(b"helper v2")
        self.done.clear()
        newer = replay_tracks.ReplayTracksReader(self.helper, self.fake_run())
        self.assertEqual(newer.request(self.replay).state, "reading")
        self.wait(newer)
        self.assertEqual(len(self.calls), 2)

    def test_a_failure_is_reported_and_not_retried(self):
        reader = replay_tracks.ReplayTracksReader(self.helper, self.fake_run(1, b"bad replay"))
        reader.request(self.replay)
        self.wait(reader)
        state = reader.state(self.replay)
        self.assertEqual((state.state, state.message), ("failed", "bad replay"))
        self.assertEqual(reader.request(self.replay).state, "failed")
        self.assertEqual(len(self.calls), 1)

    def test_missing_helper_or_replay_fails_quietly(self):
        reader = replay_tracks.ReplayTracksReader(Path(self.folder.name) / "none.exe",
                                                  self.fake_run())
        self.assertEqual(reader.request(self.replay).state, "failed")
        self.assertEqual(reader.request("").state, "idle")
        self.assertEqual(reader.request(Path(self.folder.name) / "gone.dem").state, "failed")
        self.assertEqual(self.calls, [])


class ReplayTracksHelperFormat(unittest.TestCase):
    def test_helper_writes_the_store_format_and_grid(self):
        source = (ROOT / "native/replay_tracks/main.go").read_text(encoding="utf-8")
        self.assertIn('"DLYTRK02"', source)
        self.assertIn("step           = 1.0 / 15.0", source)
        self.assertAlmostEqual(1.0 / 15.0, weather.TRACK_STEP)
        # The game adopts <store>.replay (dolly_weather_runtime_win.cpp).
        runtime = (ROOT / "native/src/dolly_weather_runtime_win.cpp").read_text(encoding="utf-8")
        self.assertIn('path + ".replay"', runtime)

    def test_package_ships_the_helper_with_its_notice(self):
        info = json.loads((ROOT / "native/build_info.json").read_text(encoding="utf-8"))
        import hashlib
        helper = ROOT / "native/bin/win64/DollyReplayTracks.exe"
        self.assertEqual(info.get("replay_tracks_sha256"),
                         hashlib.sha256(helper.read_bytes()).hexdigest())
        notice = (ROOT / "native/replay_tracks/THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
        self.assertIn("github.com/paralin/s2replay", notice)
        self.assertIn("Christian Stewart", notice)


if __name__ == "__main__":
    unittest.main()
