"""Offline take-folder validation; no encoder, display or game process needed."""
import contextlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from dolly.export_audit import audit_take, main


def sidecar(folder: Path, video: str, frames: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / video).write_bytes(b"video")
    (folder / "shot.json").write_text(json.dumps({
        "format": "deadlock-dolly-shot", "version": 1, "video_file": video,
        "fps": 60, "fixed_step": True, "frames_written": frames,
        "first_frame": 0, "last_frame": frames - 1}), encoding="utf-8")


def depth_manifest(folder: Path, frames: int, complete: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "depth.mov").write_bytes(b"depth")
    (folder / "manifest.json").write_text(json.dumps({
        "version": 3, "frames": frames, "complete": complete, "master": "depth.mov"}), encoding="utf-8")


class ExportAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.take = Path(self.temp.name) / "paired"
        sidecar(self.take, "paired.mp4", 12)

    def test_complete_take_passes(self):
        sidecar(self.take / "world", "world.mp4", 12)
        players = self.take / "players"
        players.mkdir()
        (players / "players.mov").write_bytes(b"alpha")
        depth_manifest(self.take / "depth", 12)
        result = audit_take(self.take)
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["takes"]["color"]["frames_written"], 12)
        self.assertEqual(result["takes"]["depth"]["manifest_frames"], 12)

    def test_zero_frame_depth_manifest_is_flagged(self):
        depth_manifest(self.take / "depth", 0)
        result = audit_take(self.take)
        self.assertFalse(result["ok"])
        self.assertTrue(any("zero frames" in finding and "depth" in finding
                            for finding in result["findings"]), result["findings"])

    def test_incomplete_depth_manifest_is_flagged(self):
        depth_manifest(self.take / "depth", 12, complete=False)
        result = audit_take(self.take)
        self.assertTrue(any("not marked complete" in finding for finding in result["findings"]))

    def test_zero_frame_color_sidecar_is_flagged(self):
        sidecar(self.take, "paired.mp4", 0)
        result = audit_take(self.take)
        self.assertTrue(any("zero-frame take" in finding for finding in result["findings"]))

    def test_missing_referenced_video_is_flagged(self):
        (self.take / "paired.mp4").unlink()
        result = audit_take(self.take)
        self.assertTrue(any("referenced video is missing" in finding for finding in result["findings"]))

    def test_empty_layer_folder_is_flagged(self):
        (self.take / "world").mkdir()
        result = audit_take(self.take)
        self.assertTrue(any("world: folder has no" in finding for finding in result["findings"]))

    def test_decoded_audit_zero_depth_frames_is_flagged(self):
        (self.take.parent / "decoded-audit.json").write_text(json.dumps({
            "streams": {"color": {"decoded_frames": 12, "timestamps": ["1"]},
                        "depth": {"decoded_frames": 0, "timestamps": []}}}), encoding="utf-8")
        result = audit_take(self.take)
        self.assertTrue(any("zero decoded frames" in finding for finding in result["findings"]))

    def test_decoded_audit_timestamp_mismatch_is_flagged(self):
        (self.take.parent / "decoded-audit.json").write_text(json.dumps({
            "streams": {"color": {"decoded_frames": 12, "timestamps": ["1"]},
                        "depth": {"decoded_frames": 12, "timestamps": ["2"]}}}), encoding="utf-8")
        result = audit_take(self.take)
        self.assertTrue(any("timestamps do not match" in finding for finding in result["findings"]))

    def test_missing_folder_never_raises(self):
        result = audit_take(self.take / "missing")
        self.assertFalse(result["ok"])
        self.assertIn("take folder does not exist", result["findings"])

    def test_cli_exit_codes_follow_the_findings(self):
        with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
            self.assertEqual(main([str(self.take)]), 0)
            sidecar(self.take, "paired.mp4", 0)
            self.assertEqual(main([str(self.take)]), 1)


if __name__ == "__main__":
    unittest.main()
