"""Exercise the native recorder and inspect real FFmpeg color output.

Set DOLLY_TEST_VIDEO_COLOR_SMOKE to the CMake-built WARP fixture and
DOLLY_TEST_FFMPEG to the FFmpeg executable to run this Windows check.
Set DOLLY_TEST_NVENC=1 as well to exercise available NVIDIA hardware.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave


class NativeVideoColorIntegrationTests(unittest.TestCase):
    def test_recorder_uses_rec709_and_keeps_lossless_rgb_exact(self):
        smoke = os.environ.get("DOLLY_TEST_VIDEO_COLOR_SMOKE")
        ffmpeg = os.environ.get("DOLLY_TEST_FFMPEG")
        if not smoke or not ffmpeg:
            self.skipTest("Native WARP fixture and FFmpeg paths were not supplied")
        if os.name != "nt":
            self.skipTest("The native WARP recorder fixture requires Windows")
        self.assertTrue(Path(smoke).is_file(), "Native WARP fixture is missing")
        self.assertTrue(Path(ffmpeg).is_file(), "FFmpeg is missing")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        def inspect_709(path):
            info = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path), "-vf", "showinfo",
                 "-frames:v", "1", "-f", "null", "-"], capture_output=True,
                check=True, timeout=30, creationflags=flags).stderr.decode(errors="replace")
            self.assertIn("color_range:tv color_space:bt709 color_primaries:bt709 color_trc:bt709", info)
            yuv = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                 "-frames:v", "1", "-pix_fmt", "yuv420p", "-f", "rawvideo", "pipe:1"],
                capture_output=True, check=True, timeout=30, creationflags=flags).stdout
            self.assertEqual(len(yuv), 320 * 240 * 3 // 2)
            # Limited-range Rec. 709: red=63, green=173, blue=32,
            # white=235. The Rec. 601 red/green values are 81/145.
            for x, y, expected in ((80, 60, 63), (240, 60, 173),
                                   (80, 180, 32), (240, 180, 235)):
                self.assertLessEqual(abs(yuv[y * 320 + x] - expected), 4)
            return yuv

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = [smoke, ffmpeg, str(root)]
            if os.environ.get("DOLLY_TEST_NVENC") == "1":
                command.append("--nvenc")
            recorded = subprocess.run(command, capture_output=True, timeout=60, creationflags=flags)
            self.assertEqual(recorded.returncode, 0,
                             (recorded.stdout + recorded.stderr).decode(errors="replace"))
            for order in ("rgba", "bgra"):
                with self.subTest(order=order):
                    compressed = root / ("h264-" + order + ".mp4")
                    yuv = inspect_709(compressed)
                    # Audio finalization remuxes the video. It must keep both
                    # the stream's color tags and the encoded pixel values.
                    from dolly.clip_audio import _mux
                    audio = root / ("audio-" + order + ".wav")
                    with wave.open(str(audio), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(48000)
                        wav.writeframes(bytes(4800 * 2))
                    _mux(compressed, [audio], Path(ffmpeg))
                    self.assertEqual(inspect_709(compressed), yuv)
                    lossless = root / ("lossless-" + order + ".mkv")
                    rgba = subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(lossless),
                         "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"], capture_output=True,
                        check=True, timeout=30, creationflags=flags).stdout
                    colors = ((255, 0, 0, 255), (0, 255, 0, 255),
                              (0, 0, 255, 255), (255, 255, 255, 255))
                    frame = bytes(c for y in range(240) for x in range(320)
                                  for c in colors[(y // 120) * 2 + x // 160])
                    self.assertEqual(rgba, frame * 2)
            if len(command) == 4:
                for name in ("h264-auto-rgba.mp4", "hevc-nvenc-rgba.mp4"):
                    with self.subTest(codec=name):
                        inspect_709(root / name)
            with self.subTest(codec="Media Foundation MOV"):
                mov = root / "h264-mf-rgba.mov"
                pixels = inspect_709(mov)
                from dolly.clip_audio import _mux
                _mux(mov, [root / "audio-rgba.wav"], Path(ffmpeg))
                self.assertEqual(inspect_709(mov), pixels)


if __name__ == "__main__":
    unittest.main()
