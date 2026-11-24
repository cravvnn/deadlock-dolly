import json
import re
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from dolly import weather
from dolly.particles import PARTICLE_IDS
from dolly.path import (LATEST_FORMAT_VERSION, RIGID_ATTACH_FORMAT_VERSION,
                        WEATHER_FORMAT_VERSION, Keyframe, Project)
from dolly.screenshot import still_project
from dolly.weather import (WEATHER_IDS, WEATHER_OFF, WeatherSettings, legacy_particle_weather,
                           preset_settings)

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "native" / "include" / "dolly_weather_state.hpp"


def _shot(**changes):
    return Project(name="Weather", keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0),
                                              Keyframe(2, 10, 0, 0, 0, 0, 0)], **changes)


class WeatherModel(unittest.TestCase):
    def test_every_preset_round_trips_through_dict(self):
        for preset in WEATHER_IDS:
            with self.subTest(preset=preset):
                settings = replace(preset_settings(preset), intensity=2.5, wind_direction=200,
                                   build_up=45.0, hero_wakes=False)
                self.assertEqual(WeatherSettings.from_dict(settings.to_dict()), settings)

    def test_with_preset_keeps_user_choices_and_takes_preset_defaults(self):
        base = replace(WEATHER_OFF, intensity=1.8, wind_direction=123.0,
                       include_in_effects=False)
        blizzard = base.with_preset("blizzard")
        self.assertEqual((blizzard.intensity, blizzard.wind_direction, blizzard.include_in_effects),
                         (1.8, 123.0, False))
        self.assertEqual((blizzard.wind_speed, blizzard.gusts, blizzard.coating,
                          blizzard.height_ceiling), (420.0, 1.4, 0.7, 320.0))

    def test_invalid_values_are_refused(self):
        for change in ({"preset": "hail"}, {"intensity": 0.0}, {"intensity": float("nan")},
                       {"wind_direction": 361.0}, {"wind_speed": -1.0}, {"gusts": 3.0},
                       {"coating": 1.5}, {"height_ceiling": 10.0}, {"build_up": -1.0},
                       {"build_up": 601.0}, {"flake_size": 0.1}, {"density": 5.0},
                       {"hero_wakes": 1},
                       {"include_in_effects": "yes"}, {"intensity": True}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    replace(WEATHER_OFF, **change).validate()

    def test_from_dict_refuses_unknown_missing_and_boolean_numbers(self):
        good = preset_settings("rain_storm").to_dict()
        for bad in ({**good, "lightning": True},
                    {k: v for k, v in good.items() if k != "coating"},
                    {**good, "gusts": True}, [good]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    WeatherSettings.from_dict(bad)


class WeatherShotFile(unittest.TestCase):
    def test_shot_without_weather_keeps_the_older_format(self):
        data = _shot().to_dict()
        self.assertNotIn("weather", data)
        self.assertLess(data["version"], WEATHER_FORMAT_VERSION)

    def test_weather_is_saved_and_loaded_per_shot(self):
        settings = replace(preset_settings("sandstorm"), wind_direction=75.0, intensity=1.4)
        project = _shot(weather=settings)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shot.json"
            project.save(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], WEATHER_FORMAT_VERSION)
            self.assertEqual(raw["weather"], settings.to_dict())
            loaded, version = Project.load_versioned(path)
        self.assertEqual(version, WEATHER_FORMAT_VERSION)
        self.assertEqual(loaded.weather, settings)
        self.assertEqual(LATEST_FORMAT_VERSION, WEATHER_FORMAT_VERSION)

    def test_weather_needs_the_weather_format_version(self):
        data = _shot(weather=preset_settings("ash")).to_dict()
        data["version"] = RIGID_ATTACH_FORMAT_VERSION
        with self.assertRaises(ValueError):
            Project.from_dict(data)

    def test_a_still_keeps_the_shot_weather(self):
        settings = preset_settings("blizzard")
        project = _shot(weather=settings)
        still = still_project(project, project.keyframes[0], 100)
        self.assertEqual(still.weather, settings)


class LegacyParticles(unittest.TestCase):
    def test_retired_snow_and_rain_map_to_weather(self):
        expected = {"snow_flakes": "light_snow", "snow_long_fall": "light_snow",
                    "snow_heavy": "blizzard", "snow_machine": "light_snow",
                    "rain": "rain_storm"}
        for particle, preset in expected.items():
            with self.subTest(particle=particle):
                mapped = legacy_particle_weather(particle, 1.5)
                self.assertEqual((mapped.preset, mapped.intensity), (preset, 1.5))
        self.assertEqual(set(weather.LEGACY_PARTICLE_WEATHER), set(expected))
        self.assertTrue(set(expected) <= set(PARTICLE_IDS))

    def test_classic_particles_stay_particles(self):
        for particle in ("confetti", "frozen_flakes", "frozen_classic"):
            self.assertIsNone(legacy_particle_weather(particle, 1.0))

    def test_legacy_intensity_is_clamped_into_weather_range(self):
        self.assertEqual(legacy_particle_weather("rain", 0.01).intensity, weather.INTENSITY_MIN)


class NativeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = HEADER.read_text(encoding="utf-8")

    def test_preset_order_matches_native(self):
        body = re.search(r"enum class Preset : std::uint32_t \{(.*?)\};", self.header, re.S).group(1)
        names = re.findall(r"(\w+) = (\d+),", body)
        self.assertEqual([int(i) for _, i in names], list(range(len(names))))
        snake = [re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower() for n, _ in names]
        self.assertEqual(tuple(snake), WEATHER_IDS)
        count = int(re.search(r"kPresetCount = (\d+);", self.header).group(1))
        self.assertEqual(count, len(WEATHER_IDS))

    def test_preset_defaults_match_native(self):
        body = re.search(r"kPresetDefaults\[kPresetCount\] = \{(.*?)\n\};", self.header, re.S).group(1)
        rows = [tuple(float(v) for v in re.findall(r"([\d.]+)f", row))
                for row in re.findall(r"\{([^}]*)\}", body)]
        self.assertEqual(rows, [weather._PRESET_DEFAULTS[p] for p in WEATHER_IDS])

    def test_limits_match_native(self):
        def native(name):
            return float(re.search(name + r" = ([\d.]+)f", self.header).group(1))
        pairs = {"kIntensityMin": weather.INTENSITY_MIN, "kIntensityMax": weather.INTENSITY_MAX,
                 "kWindSpeedMin": weather.WIND_SPEED_MIN, "kWindSpeedMax": weather.WIND_SPEED_MAX,
                 "kGustsMin": weather.GUSTS_MIN, "kGustsMax": weather.GUSTS_MAX,
                 "kCoatingMin": weather.COATING_MIN, "kCoatingMax": weather.COATING_MAX,
                 "kHeightCeilingMin": weather.HEIGHT_CEILING_MIN,
                 "kHeightCeilingMax": weather.HEIGHT_CEILING_MAX,
                 "kWindDirectionDefault": weather.WIND_DIRECTION_DEFAULT,
                 "kBuildUpMin": weather.BUILD_UP_MIN, "kBuildUpMax": weather.BUILD_UP_MAX,
                 "kFlakeSizeMin": weather.FLAKE_SIZE_MIN, "kFlakeSizeMax": weather.FLAKE_SIZE_MAX,
                 "kDensityMin": weather.DENSITY_MIN, "kDensityMax": weather.DENSITY_MAX}
        for name, value in pairs.items():
            with self.subTest(name=name):
                self.assertEqual(native(name), value)

    def test_wire_layout_matches_native(self):
        magic = "".join(re.search(r"kWireMagic\[8\] = \{(.*?)\};", self.header).group(1)
                        .replace("'", "").replace(" ", "").split(","))
        self.assertEqual(magic.encode(), weather.WIRE_MAGIC)
        self.assertEqual(int(re.search(r"kWireAbi = (\d+);", self.header).group(1)), weather.WIRE_ABI)
        size = int(re.search(r"sizeof\(Wire\) == (\d+)", self.header).group(1))
        self.assertEqual(int(re.search(r"offsetof\(Wire, sequence\) == (\d+)", self.header)
                             .group(1)), 8)
        offset = re.search(r"kWireOffset = 2 \* 1024 \* 1024 \+ (\d+);", self.header)
        self.assertEqual(2 * 1024 * 1024 + int(offset.group(1)), weather.WIRE_OFFSET)
        self.assertEqual(size, weather.WIRE_BYTES)
        self.assertEqual(int(re.search(r"offsetof\(Wire, intensity\) == (\d+)", self.header).group(1)),
                         struct.calcsize("<8sIIII"))
        self.assertEqual(int(re.search(r"kWireFlagHeroWakes = (\d+);", self.header).group(1)),
                         weather.WIRE_FLAG_HERO_WAKES)
        self.assertEqual(int(re.search(r"kWireFlagIncludeInEffects = (\d+);", self.header).group(1)),
                         weather.WIRE_FLAG_INCLUDE_IN_EFFECTS)

    def test_packed_block_carries_every_setting(self):
        settings = replace(preset_settings("rain_storm"), intensity=2.0, wind_direction=45.0,
                           build_up=30.0, flake_size=1.5, density=2.0, hero_wakes=False)
        block = settings.pack_wire(9, "dl_midtown", 1234)
        self.assertEqual(len(block), weather.WIRE_BYTES)
        magic, seq, abi, preset, flags, *rest = struct.unpack(weather.WIRE_FORMAT, block)
        values, (name, tick, r0, r1) = rest[:7], rest[7:]
        self.assertEqual((name.rstrip(b"\0"), tick, r0, r1), (b"dl_midtown", 1234, 1.5, 2.0))
        self.assertEqual((magic, abi, seq, preset, flags),
                         (weather.WIRE_MAGIC, weather.WIRE_ABI, 9, WEATHER_IDS.index("rain_storm"),
                          weather.WIRE_FLAG_INCLUDE_IN_EFFECTS))
        expected = [2.0, 45.0, settings.wind_speed, settings.gusts, settings.coating,
                    settings.height_ceiling, 30.0]
        as_float32 = [struct.unpack("<f", struct.pack("<f", v))[0] for v in expected]
        self.assertEqual(values, as_float32)


if __name__ == "__main__":
    unittest.main()


class SceneDrawerActions(unittest.TestCase):
    def test_weather_action_names_follow_the_native_ids(self):
        from dolly import editor_wire
        header = (ROOT / "native" / "include" / "dolly_editor.hpp").read_text(encoding="utf-8")
        body = header[header.index("SetWeatherPreset,"):header.index("SetWeatherIncludeEffects") + 24]
        native = re.findall(r"\b(Set\w+)", body)
        snake = tuple(re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower() for n in native)
        self.assertEqual(snake, editor_wire.WEATHER_ACTIONS)
        from dolly.editor_actions import ACTION_ORDER
        names = ACTION_ORDER + editor_wire.EXTRA_ACTIONS
        self.assertEqual(names.index("set_weather_preset"), 125)
        self.assertEqual(names.index("set_weather_include_effects"), 136)


class PlateSignal(unittest.TestCase):
    def test_plate_order_matches_native_and_packs_into_bits_7_to_9(self):
        from dolly import media_wire
        header = (ROOT / "native" / "include" / "dolly_video.hpp").read_text(encoding="utf-8")
        body = re.search(r"enum class Plate : std::uint8_t \{(.*?)\};", header, re.S).group(1)
        names = [re.sub(r"(?<!^)([A-Z])", r"_\1", n).lower() for n, _ in re.findall(r"(\w+) = (\d+),", body)]
        self.assertEqual(tuple(names), media_wire.PLATES)
        for index, plate in enumerate(media_wire.PLATES):
            packed = media_wire.pack_command(2, "start_video", path="C:/x.mp4", plate=plate)
            fields = media_wire.COMMAND.unpack(packed)
            reserved = fields[6]
            self.assertEqual((reserved >> 7) & 7, index)
        with self.assertRaises(ValueError):
            media_wire.pack_command(2, "start_video", path="C:/x.mp4", plate="lens")


class DesktopScenePage(unittest.TestCase):
    def test_sliders_cover_every_numeric_drawer_setting(self):
        from dolly.editor_dispatch import WEATHER_FIELDS, WEATHER_SWITCHES
        from dolly.gui import WEATHER_SLIDERS
        numeric = set(WEATHER_FIELDS.values()) - {"preset"} - set(WEATHER_SWITCHES)
        self.assertEqual(set(WEATHER_SLIDERS), numeric)

    def test_both_switches_are_in_the_drawer_and_on_the_page(self):
        drawer = (ROOT / "native" / "src" / "dolly_rail_drawers.cpp").read_text(encoding="utf-8")
        self.assertIn("EditorAction::SetWeatherHeroWakes", drawer)
        self.assertIn("EditorAction::SetWeatherIncludeEffects", drawer)
        page = (ROOT / "dolly" / "ui" / "scene_page.py").read_text(encoding="utf-8")
        self.assertIn("state.hero_wakes", page)
        self.assertIn("state.include_in_effects", page)

    def test_page_edits_and_follows_the_shot_weather(self):
        import tkinter as tk
        from types import SimpleNamespace
        from dolly import gui
        from dolly.path import Project
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"no Tk display: {error}")
        root.withdraw()
        self.addCleanup(root.destroy)
        sent, dirty = [], []
        app = SimpleNamespace(
            project=Project(),
            weather_preset=tk.StringVar(root),
            weather_values={name: tk.DoubleVar(root) for name in gui.WEATHER_SLIDERS},
            weather_include_effects=tk.BooleanVar(root),
            weather_hero_wakes=tk.BooleanVar(root),
            weather_lens_rain=tk.BooleanVar(root),
            status_text=tk.StringVar(root),
            controller=SimpleNamespace(set_native_weather=lambda s, tick: sent.append((s, tick))),
            _mark_dirty=lambda: dirty.append(True),
            _guard=lambda _title, function: function(),
        )
        app._refresh_weather = lambda: gui.DollyApp._refresh_weather(app)
        changed = lambda: gui.DollyApp._weather_changed(app)
        app._refresh_weather()
        self.assertEqual(app.weather_preset.get(), "Off")

        # A preset fills its own defaults, as in game.
        app.weather_preset.set("Blizzard")
        changed()
        self.assertEqual(app.project.weather, weather.preset_settings("blizzard"))
        self.assertEqual(app.weather_values["wind_speed"].get(), 420.0)
        self.assertEqual(sent[-1], (app.project.weather, app.project.start_tick))

        # A slider commits its rounded value; untouched values keep full precision.
        app.project = replace(app.project, weather=replace(app.project.weather, gusts=1.23456))
        app._refresh_weather()
        app.weather_values["density"].set(2.5049)
        app.weather_include_effects.set(False)
        app.weather_hero_wakes.set(False)
        changed()
        self.assertEqual(app.project.weather.density, 2.5)
        self.assertEqual(app.project.weather.gusts, 1.23456)
        self.assertFalse(app.project.weather.include_in_effects)
        self.assertFalse(app.project.weather.hero_wakes)

        # Nothing moved: no new undo step and nothing sent.
        count = len(sent), len(dirty)
        changed()
        self.assertEqual((len(sent), len(dirty)), count)
