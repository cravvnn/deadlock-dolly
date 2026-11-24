"""Lens rain settings: per-shot controls, the native block and the drawer actions."""
import re
import struct
import unittest
from dataclasses import replace
from pathlib import Path

from dolly import editor_wire, weather
from dolly.path import Keyframe, Project
from dolly.weather import LENS_RANGES, WeatherSettings, preset_settings

ROOT = Path(__file__).resolve().parents[1]
WIRE_HEADER = ROOT / "native" / "include" / "dolly_lens_rain_wire.hpp"


def _shot(**changes):
    return Project(name="Lens", keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                           Keyframe(2, 10, 0, 0, 0, 0, 0)], **changes)


class LensSettings(unittest.TestCase):
    def test_lens_rain_is_off_by_default_with_the_native_defaults(self):
        self.assertFalse(weather.WEATHER_OFF.lens_rain)
        for name, (_low, _high, default) in LENS_RANGES.items():
            self.assertEqual(getattr(weather.WEATHER_OFF, name), default)

    def test_round_trips_with_the_shot(self):
        settings = replace(preset_settings("rain_storm"), lens_rain=True, lens_amount=0.8,
                           lens_size=2.0, lens_trails=0.0)
        project = _shot(weather=settings)
        loaded = Project.from_dict(project.to_dict())
        self.assertEqual(loaded.weather, settings)

    def test_weather_saved_before_lens_rain_still_loads(self):
        data = preset_settings("blizzard").to_dict()
        for name in ("lens_rain", *LENS_RANGES):
            del data[name]
        self.assertEqual(WeatherSettings.from_dict(data), preset_settings("blizzard"))

    def test_a_new_preset_keeps_the_lens_choices(self):
        settings = replace(preset_settings("rain_storm"), lens_rain=True, lens_softness=0.9)
        changed = settings.with_preset("light_snow")
        self.assertTrue(changed.lens_rain)
        self.assertEqual(changed.lens_softness, 0.9)

    def test_out_of_range_and_wrong_types_are_refused(self):
        for name, (low, high, _default) in LENS_RANGES.items():
            for value in (low - 0.01, high + 0.01, float("nan")):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    replace(weather.WEATHER_OFF, **{name: value}).validate()
        with self.assertRaises(ValueError):
            replace(weather.WEATHER_OFF, lens_rain=1).validate()
        data = preset_settings("rain_storm").to_dict()
        data["lens_amount"] = True
        with self.assertRaises(ValueError):
            WeatherSettings.from_dict(data)


class NativeLensBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = WIRE_HEADER.read_text(encoding="utf-8")

    def test_layout_matches_native(self):
        magic = "".join(re.search(r"kWireMagic\[8\] = \{(.*?)\};", self.header).group(1)
                        .replace("'", "").replace(" ", "").split(","))
        self.assertEqual(magic.encode(), weather.LENS_WIRE_MAGIC)
        self.assertEqual(int(re.search(r"kWireAbi = (\d+);", self.header).group(1)),
                         weather.LENS_WIRE_ABI)
        self.assertEqual(int(re.search(r"kWireFlagEnabled = (\d+);", self.header).group(1)),
                         weather.LENS_WIRE_FLAG_ENABLED)
        offset = re.search(r"kWireOffset = 2 \* 1024 \* 1024 \+ (\d+);", self.header)
        self.assertEqual(2 * 1024 * 1024 + int(offset.group(1)), weather.LENS_WIRE_OFFSET)
        size = int(re.search(r"sizeof\(Wire\) == (\d+)", self.header).group(1))
        self.assertEqual(size, weather.LENS_WIRE_BYTES)
        self.assertEqual(int(re.search(r"offsetof\(Wire, amount\) == (\d+)", self.header).group(1)),
                         struct.calcsize("<8sIIIi"))

    def test_ranges_match_native(self):
        body = re.search(r"kRanges\[6\] = \{(.*?)\};", self.header, re.S).group(1)
        native = [(float(a), float(b)) for a, b in re.findall(r"\{([\d.]+)f, ([\d.]+)f\}", body)]
        self.assertEqual(native, [(low, high) for low, high, _ in LENS_RANGES.values()])

    def test_block_fits_the_free_gap_in_the_shared_mapping(self):
        events = (ROOT / "native" / "include" / "dolly_replay_events.hpp").read_text(encoding="utf-8")
        events_end = 2 * 1024 * 1024 + 24576 + 4096 + int(
            re.search(r"sizeof\(EditorReplayEvents\) == (\d+)", events).group(1))
        self.assertGreaterEqual(weather.LENS_WIRE_OFFSET, events_end)
        self.assertLessEqual(weather.LENS_WIRE_OFFSET + weather.LENS_WIRE_BYTES,
                             editor_wire.CINEMATIC_OFFSET)

    def test_packed_block_carries_every_control(self):
        settings = replace(preset_settings("rain_storm"), lens_rain=True, lens_amount=0.5,
                           lens_size=1.5, lens_slide=0.25, lens_trails=0.75,
                           lens_refraction=1.25, lens_softness=0.125)
        block = settings.pack_lens_wire(7, 3210)
        self.assertEqual(len(block), weather.LENS_WIRE_BYTES)
        magic, seq, abi, flags, tick, *values = struct.unpack(weather.LENS_WIRE_FORMAT, block)
        self.assertEqual((magic, seq, abi, flags, tick),
                         (weather.LENS_WIRE_MAGIC, 7, weather.LENS_WIRE_ABI,
                          weather.LENS_WIRE_FLAG_ENABLED, 3210))
        self.assertEqual(values, [0.5, 1.5, 0.25, 0.75, 1.25, 0.125])
        off = preset_settings("rain_storm").pack_lens_wire(8, 0)
        self.assertEqual(struct.unpack(weather.LENS_WIRE_FORMAT, off)[3], 0)


class LensDrawerActions(unittest.TestCase):
    def test_action_names_follow_the_native_ids(self):
        header = (ROOT / "native" / "include" / "dolly_editor.hpp").read_text(encoding="utf-8")
        body = header[header.index("SetLensRain,"):header.index("SetLensSoftness") + 15]
        native = re.findall(r"\b(Set\w+)", body)
        snake = tuple(re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower() for n in native)
        self.assertEqual(snake, editor_wire.LENS_ACTIONS)
        from dolly.editor_actions import ACTION_ORDER
        names = ACTION_ORDER + editor_wire.EXTRA_ACTIONS
        self.assertEqual(names.index("set_lens_rain"), 139)
        self.assertEqual(names.index("set_lens_softness"), 145)

    def test_every_action_sets_its_weather_field(self):
        from dolly.editor_dispatch import WEATHER_FIELDS, WEATHER_SWITCHES
        fields = [WEATHER_FIELDS[action] for action in editor_wire.LENS_ACTIONS]
        self.assertEqual(fields, ["lens_rain", *LENS_RANGES])
        self.assertIn("lens_rain", WEATHER_SWITCHES)

    def test_drawer_and_page_show_every_control(self):
        drawer = (ROOT / "native" / "src" / "dolly_rail_drawers.cpp").read_text(encoding="utf-8")
        for name in ("SetLensRain", "SetLensAmount", "SetLensSize", "SetLensSlide", "SetLensTrails",
                     "SetLensRefraction", "SetLensSoftness"):
            self.assertIn(f"EditorAction::{name}", drawer)
        from dolly.gui import WEATHER_SLIDERS
        self.assertTrue(set(LENS_RANGES) <= set(WEATHER_SLIDERS))
        layout = (ROOT / "dolly" / "gui_layout.py").read_text(encoding="utf-8")
        for name in LENS_RANGES:
            self.assertIn(f'Control("{name}"', layout)


if __name__ == "__main__":
    unittest.main()
