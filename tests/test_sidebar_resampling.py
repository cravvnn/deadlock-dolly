"""Sidebar artwork resampling preserves alpha and avoids nearest-neighbour steps."""
from pathlib import Path
import sys
import tempfile
import unittest
from dolly.ui import art, image_resample

@unittest.skipUnless(sys.platform == "win32", "Windows GDI+ resampler")
class SmoothArtworkTests(unittest.TestCase):
    def test_upscale_interpolates_alpha_and_preserves_color(self):
        pixels = bytes([255, 255, 255, 0, 255, 255, 255, 255] * 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            path.write_bytes(art.png_rgba(2, 2, pixels))
            result = image_resample.scale_centered(path, 16, 16, 16)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 16 * 16 * 4)
        middle = [result[(8 * 16 + x) * 4 + 3] for x in range(16)]
        self.assertTrue(any(0 < a < 255 for a in middle), middle)
        self.assertTrue(any(a == 0 for a in middle), middle)
        self.assertTrue(any(a == 255 for a in middle), middle)

    def test_invalid_file_falls_back(self):
        self.assertIsNone(image_resample.scale_centered('does-not-exist.png', 10, 10, 10))

if __name__ == '__main__':
    unittest.main()
