"""The generated desktop UI art (assets/ui) is complete, current and shipped with Dolly."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import zlib

from dolly import ui_theme

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "ui"
# Figma [design reference]: the shard's polygon (Vector 7) and where it sits on the 1010 x 810 screen.
FIGMA_SHAPE = ((0, 783), (319, 671), (319, 589.5), (642.5, 640.5), (794, 0), (975, 907.5))
FIGMA_SHARD_AT = (381, 101)


def _load_tool():
    spec = importlib.util.spec_from_file_location("dolly_build_ui_art", ROOT / "tools" / "build_ui_art.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chunks(data):
    chunks, position = [], 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        chunks.append((data[position + 4:position + 8], data[position + 8:position + 8 + length]))
        position += 12 + length
    return chunks


class UiAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_tool()
        cls.manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))

    def test_build_tool_verifies_the_shipped_art(self):
        self.assertEqual(self.tool.verify(ART), [])

    def test_verification_needs_no_numpy(self):
        source = (ROOT / "tools" / "build_ui_art.py").read_text(encoding="utf-8")
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse([line for line in top_level if "numpy" in line], "numpy must be imported lazily")

    def test_manifest_files_match_their_hashes_and_png_headers(self):
        files = self.manifest["files"]
        self.assertEqual(len(files), 15)
        for name, entry in files.items():
            with self.subTest(file=name):
                data = (ART / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(data[12:16], b"IHDR")
                self.assertEqual(list(struct.unpack(">II", data[16:24])), entry["px"])
        on_disk = sorted(path.name for path in ART.iterdir())
        self.assertEqual(on_disk, sorted([*files, "manifest.json"]))

    def test_the_still_picture_comes_in_five_8bit_rgb_bands_with_filter0_rows(self):
        from dolly.ui import art
        levels = self.manifest["levels"]
        self.assertEqual(levels["size"], [1010, 162])
        self.assertEqual(levels["scale"], 1.0, "the design size: one picture px per design px")
        self.assertIs(levels["stacked"], True)
        self.assertEqual(levels["cycle"], [0], "the picture holds still")
        self.assertEqual(levels["files"], [f"backdrop_l{i}.png" for i in range(5)])
        for name in levels["files"]:
            with self.subTest(band=name):
                data = (ART / name).read_bytes()
                chunks = _chunks(data)
                width, height, depth, color_type, _c, _f, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
                self.assertEqual((width, height, depth, color_type, interlace), (1010, 162, 8, 2, 0))
                idat = [body for tag, body in chunks if tag == b"IDAT"]
                self.assertEqual(len(idat), 1)
                raw = zlib.decompress(idat[0])
                stride = 1 + 3 * width
                self.assertEqual(len(raw), stride * height)
                self.assertEqual(raw[::stride], bytes(height))
                # The runtime's stdlib reader takes the same file.
                self.assertEqual(art.read_png_rgb_filter0(ART / name)[:2], (1010, 162))
        self.assertEqual(5 * 162, self.manifest["design"]["screen"][1], "the bands stack to the whole screen")

    def test_eight_shard_poses_start_with_the_figma_design_and_move_only_its_visible_corner(self):
        from dolly.ui import backdrop
        shapes = self.manifest["shapes"]
        self.assertEqual(set(shapes), {"poses", "shard_fill"}, "the halftone rim is part of the still picture")
        self.assertIn(shapes["shard_fill"], ui_theme.TOKENS)
        poses = shapes["poses"]
        self.assertEqual(len(poses), 8)
        design = [[x + FIGMA_SHARD_AT[0], y + FIGMA_SHARD_AT[1]] for x, y in FIGMA_SHAPE]
        self.assertEqual(poses[0], {"shard": design})
        self.assertEqual(poses[0], {key: [list(p) for p in value] for key, value in backdrop.DESIGN_POSE.items()})
        for index, pose in enumerate(poses[1:], 1):
            with self.subTest(pose=index):
                shard = pose["shard"]
                self.assertEqual(set(pose), {"shard"})
                self.assertEqual(len(shard), 8, "one kink on each long visible edge")
                for x, y in shard:
                    self.assertEqual((x * 2) % 1, 0)   # half-pixel grid
                    self.assertEqual((y * 2) % 1, 0)
                # The spike and the bottom-right corner lie off screen at every window size: unchanged.
                self.assertEqual(shard[-2:], design[-2:])
                self.assertGreater(shard[5][0], 1010, "the diagonal still leaves the screen before its spike")
                moved = [point for point in shard[:6] if point not in design]
                self.assertTrue(moved)
                for x, y in moved:     # only the bottom right of the screen moves
                    self.assertGreater(y, 650)
                    self.assertGreater(x, 360)

    def test_sidebar_art_and_nav_icons_come_in_three_scale_buckets(self):
        self.assertEqual(self.manifest["sidebar_art"]["rect"], [0, 260, 190, 550])
        self.assertEqual(self.manifest["nav_icons"]["order"],
                         ["replay", "camera", "look", "export", "controls", "settings"])
        self.assertEqual(self.manifest["nav_icons"]["rows"], ["muted", "text"])
        sizes = {"sidebar_art": {"1": [190, 550], "1.5": [285, 825], "2": [380, 1100], "4": [760, 2200]},
                 "nav_icons": {"1": [96, 32], "1.5": [144, 48], "2": [192, 64]}}
        for group, expected in sizes.items():
            for bucket, name in self.manifest[group]["files"].items():
                with self.subTest(group=group, bucket=bucket):
                    self.assertEqual(self.manifest["files"][name]["px"], expected[bucket])

    def test_shot_playback_art_is_an_opaque_card_background_in_three_scale_buckets(self):
        from dolly.ui import art
        section = self.manifest["playback_art"]
        self.assertEqual((section["card"], section["size"], section["margin"]), ([930, 178], [930, 298], 60))
        self.assertEqual(section["surface"], ui_theme.TOKENS["panel"])
        sizes = {"1": (930, 298), "1.5": (1395, 447), "2": (1860, 596)}
        for bucket, name in section["files"].items():
            with self.subTest(bucket=bucket):
                self.assertEqual(name, f"playback_art@{bucket}x.png")
                self.assertEqual(tuple(self.manifest["files"][name]["px"]), sizes[bucket])
                width, height, rgb = art.read_png_rgb_filter0(ART / name)   # opaque 8-bit RGB, filter 0
                self.assertEqual((width, height), sizes[bucket])
                # The card colour with the faint dots and drawing: never far from the panel token.
                panel = ui_theme.rgb(ui_theme.TOKENS["panel"])
                for channel in range(3):
                    values = rgb[channel::3][::53]
                    self.assertLessEqual(max(abs(value - panel[channel]) for value in values), 40)
                    self.assertGreater(max(values) - min(values), 3, "the drawing shows")

    def test_runtime_loader_accepts_the_shipped_art(self):
        from dolly.ui import art
        assets = art.load(directory=ART)
        self.assertIsNotNone(assets)
        width, height, rgb = assets.level_rgb(0)
        self.assertEqual((width, height, len(rgb)), (1010, 162, 1010 * 162 * 3))

    def test_verify_reports_edits_strays_and_stale_icon_colours(self):
        folder = Path(tempfile.mkdtemp(prefix="dolly-ui-art-"))
        self.addCleanup(shutil.rmtree, folder, True)
        copy = folder / "ui"
        shutil.copytree(ART, copy)
        self.assertEqual(self.tool.verify(copy), [])
        with mock.patch.dict(ui_theme.TOKENS, {"muted": ui_theme.TOKENS["accent"]}):
            self.assertTrue(any("token" in problem for problem in self.tool.verify(copy)))
        (copy / "extra.png").write_bytes(b"")
        self.assertTrue(any(problem.startswith("extra.png") for problem in self.tool.verify(copy)))
        (copy / "extra.png").unlink()
        level = copy / "backdrop_l2.png"
        data = bytearray(level.read_bytes())
        data[-20] ^= 0xFF
        level.write_bytes(bytes(data))
        self.assertTrue(any(problem.startswith("backdrop_l2.png") for problem in self.tool.verify(copy)))
        (copy / "manifest.json").unlink()
        self.assertTrue(self.tool.verify(copy))

    def test_every_generated_file_and_module_is_a_listed_source_file(self):
        listed = set((ROOT / "SOURCE_FILES.txt").read_text(encoding="utf-8").split())
        names = [f"assets/ui/{path.name}" for path in sorted(ART.iterdir())]
        names += ["assets/README.md", "tools/build_ui_art.py", "dolly/ui/backdrop.py", "dolly/ui/canvas_controls.py",
                  "dolly/ui/stickers.py", "tests/test_ui_assets.py", "tests/test_ui_motion.py"]
        self.assertEqual([name for name in names if name not in listed], [])

    def test_packaging_ships_the_art_folder(self):
        spec = (ROOT / "packaging" / "dolly.spec").read_text(encoding="utf-8")
        self.assertIn('"assets" / "ui"', spec)

    def test_readme_records_the_art_provenance(self):
        text = (ROOT / "assets" / "README.md").read_text(encoding="utf-8")
        for needle in ("assets/ui", "design source", "[design reference]", "tools/build_ui_art.py"):
            self.assertIn(needle, text)


class UiFontAssetTests(unittest.TestCase):
    """Inter, the UI face (USER-ADDITIONS UPDATE 23), ships with Dolly: listed, packaged, licensed."""

    FONTS = ROOT / "assets" / "fonts" / "inter"

    def name_record(self, data, wanted):
        # The Windows (3, 1, 0x409) name record ``wanted`` of a TrueType file.
        tables = {}
        for index in range(struct.unpack_from(">H", data, 4)[0]):
            tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
            tables[tag] = offset
        base = tables[b"name"]
        _format, count, storage = struct.unpack_from(">HHH", data, base)
        for index in range(count):
            platform, encoding, language, name, length, offset = struct.unpack_from(">HHHHHH", data, base + 6 + 12 * index)
            if (platform, encoding, language, name) == (3, 1, 0x409, wanted):
                start = base + storage + offset
                return data[start:start + length].decode("utf-16-be")
        return None

    def test_every_face_dolly_registers_is_shipped_and_names_its_windows_family(self):
        from dolly.ui import fonts
        self.assertEqual(fonts.bundled_dir(), self.FONTS)
        for spec in fonts.BUNDLED_FACES:
            with self.subTest(face=spec["name"]):
                data = (self.FONTS / spec["name"]).read_bytes()
                fonts._check_tables(data)
                self.assertLessEqual(len(data), fonts._BUNDLED_LIMIT)
                self.assertEqual(self.name_record(data, 1), spec["family"])
                self.assertTrue(self.name_record(data, 5).startswith("Version 4."))
        self.assertEqual(self.name_record((self.FONTS / "Inter-Bold.ttf").read_bytes(), 2), "Bold")
        self.assertEqual({ui_theme.UI_FACE, ui_theme.UI_FACE_VALUE},
                         {spec["family"] for spec in fonts.BUNDLED_FACES} - {"Inter SemiBold"})

    def test_the_fonts_and_their_license_are_listed_packaged_and_documented(self):
        listed = set((ROOT / "SOURCE_FILES.txt").read_text(encoding="utf-8").split())
        on_disk = sorted(path.name for path in self.FONTS.iterdir())
        self.assertEqual(on_disk, ["Inter-Bold.ttf", "Inter-Medium.ttf", "Inter-Regular.ttf", "Inter-SemiBold.ttf",
                                   "LICENSE.txt"])
        self.assertEqual([name for name in on_disk if f"assets/fonts/inter/{name}" not in listed], [])
        self.assertIn('"assets" / "fonts" / "inter"', (ROOT / "packaging" / "dolly.spec").read_text(encoding="utf-8"))
        self.assertIn("SIL Open Font License", (self.FONTS / "LICENSE.txt").read_text(encoding="utf-8"))
        readme = (ROOT / "assets" / "README.md").read_text(encoding="utf-8")
        for name in on_disk[:4]:
            self.assertIn(hashlib.sha256((self.FONTS / name).read_bytes()).hexdigest(), readme, name)
        self.assertIn("rsms/inter", (ROOT / "CREDITS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
