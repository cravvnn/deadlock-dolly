"""Regressions for Windows icon loading and bitmap-compatible ICO packaging."""
from pathlib import Path
import struct
import tkinter as tk
import unittest
from unittest.mock import Mock, patch, call

from dolly import branding


class BrandingTests(unittest.TestCase):
    def test_windows_sets_explicit_and_default_ico_without_overwriting_with_photo(self):
        root = Mock()
        with patch.object(branding.os, "name", "nt"), patch.object(branding.tk, "PhotoImage") as photo:
            branding.apply_window_icon(root)
        path = str(branding.ASSETS / "dolly.ico")
        self.assertEqual(root.iconbitmap.call_args_list, [call(path), call(default=path)])
        root.iconphoto.assert_not_called()
        photo.assert_not_called()

    def test_default_failure_keeps_successful_window_icon(self):
        root = Mock()
        root.iconbitmap.side_effect = [None, tk.TclError("default failed")]
        with patch.object(branding.os, "name", "nt"), patch.object(branding.tk, "PhotoImage") as photo:
            with self.assertLogs("dolly.branding", level="WARNING"):
                branding.apply_window_icon(root)
        photo.assert_not_called()
        root.iconphoto.assert_not_called()

    def test_failed_windows_ico_uses_png_sizes_and_keeps_references(self):
        root = Mock()
        root.iconbitmap.side_effect = tk.TclError("bad bitmap")
        photo = Mock()
        images = (object(), object(), object())
        photo.subsample.side_effect = images
        with patch.object(branding.os, "name", "nt"), patch.object(branding.tk, "PhotoImage", return_value=photo):
            with self.assertLogs("dolly.branding", level="WARNING"):
                branding.apply_window_icon(root)
        root.iconphoto.assert_called_once_with(True, *images, photo)
        self.assertIs(root._dolly_icon_photo, photo)
        self.assertEqual(root._dolly_icon_photos, (*images, photo))

    def test_nonwindows_uses_png_without_ico(self):
        root = Mock()
        with patch.object(branding.os, "name", "posix"), patch.object(branding.tk, "PhotoImage"):
            branding.apply_window_icon(root)
        root.iconbitmap.assert_not_called()
        root.iconphoto.assert_called_once()

    def test_missing_icons_do_not_break_startup(self):
        root = Mock()
        root.iconbitmap.side_effect = tk.TclError("missing ICO")
        with patch.object(branding.os, "name", "nt"), patch.object(branding.tk, "PhotoImage", side_effect=tk.TclError("missing PNG")):
            with self.assertLogs("dolly.branding", level="WARNING") as messages:
                branding.apply_window_icon(root)
        self.assertEqual(len(messages.output), 2)

    def test_every_packaged_ico_frame_has_correct_bitmap_header_and_mask(self):
        data = (branding.ASSETS / "dolly.ico").read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, kind), (0, 1))
        sizes = set()
        for index in range(count):
            w, h, colors, unused, planes, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
            w, h = w or 256, h or 256
            sizes.add(w)
            frame = data[offset:offset + length]
            self.assertEqual(len(frame), length)
            self.assertEqual((colors, unused, planes, bits), (0, 0, 1, 32))
            # This is also the header interpretation used by older Tk 8.6.
            header, bw, bh, bp, bpp, compression = struct.unpack_from("<IiiHHI", frame)
            self.assertEqual((header, bw, bh, bp, bpp, compression), (40, w, h * 2, 1, 32, 0))
            mask_stride = ((w + 31) // 32) * 4
            self.assertEqual(length, 40 + w * h * 4 + mask_stride * h)
        self.assertTrue({16, 20, 24, 32, 40, 48, 64, 128, 256}.issubset(sizes))


if __name__ == "__main__":
    unittest.main()
