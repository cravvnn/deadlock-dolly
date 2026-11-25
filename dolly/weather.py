"""Weather settings: per-shot presets for the native wind-fluid weather pass.

Preset ids are protocol-stable: append only, never reorder. The native engine
mirrors the order in ``native/include/dolly_weather_state.hpp`` and tests
assert the two stay in step, including the wire block layout.

A preset is a recipe (atmosphere volume, precipitation, wind and surface
coating). Choosing a preset fills every setting from its defaults; the shot
then stores the resolved values, so a later change to a preset's defaults
never alters a saved shot.

Wind direction uses the game's yaw convention: the direction the wind blows
toward, in degrees, with 0 along world +X and 90 along world +Y.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

WEATHER_PRESETS = (
    ("off", "Off", "No weather."),
    ("light_snow", "Light snow", "Soft snowfall drifting on a light breeze."),
    ("blizzard", "Blizzard", "Dense snow, drifting ground haze and strong gusts."),
    ("rain_storm", "Rain storm", "Wind-driven rain with wet surfaces."),
    ("sandstorm", "Sandstorm", "Sand lifted and carried through the streets."),
    ("dust_haze", "Dust haze", "Thin, high dust in a steady wind."),
    ("ground_fog", "Ground fog", "A dense, floor-hugging fog blanket."),
    ("ash", "Ash", "Falling ash and embers."),
)
WEATHER_IDS = tuple(item[0] for item in WEATHER_PRESETS)
WEATHER_LABELS = {item[0]: item[1] for item in WEATHER_PRESETS}
WEATHER_LABEL_TO_ID = {item[1]: item[0] for item in WEATHER_PRESETS}
WEATHER_DEFAULT = "off"

INTENSITY_MIN, INTENSITY_MAX = 0.1, 3.0
WIND_SPEED_MIN, WIND_SPEED_MAX = 0.0, 900.0
GUSTS_MIN, GUSTS_MAX = 0.0, 2.5
COATING_MIN, COATING_MAX = 0.0, 1.0
HEIGHT_CEILING_MIN, HEIGHT_CEILING_MAX = 32.0, 800.0
# Seconds from the shot start for the surface coating to settle; 0 = already settled.
BUILD_UP_MIN, BUILD_UP_MAX = 0.0, 600.0
# Precipitation look: flake size and density multipliers.
FLAKE_SIZE_MIN, FLAKE_SIZE_MAX = 0.25, 3.0
DENSITY_MIN, DENSITY_MAX = 0.1, 4.0

# Preset defaults: wind speed (units/s), gusts, coating, height ceiling (units).
# Keep in step with kPresetDefaults in native/include/dolly_weather_state.hpp.
_PRESET_DEFAULTS = {
    "off": (180.0, 1.0, 0.0, 180.0),
    "light_snow": (90.0, 0.5, 0.35, 260.0),
    "blizzard": (420.0, 1.4, 0.7, 320.0),
    "rain_storm": (260.0, 1.1, 0.6, 140.0),
    "sandstorm": (380.0, 1.25, 0.35, 220.0),
    "dust_haze": (140.0, 0.6, 0.15, 320.0),
    "ground_fog": (45.0, 0.25, 0.1, 120.0),
    "ash": (120.0, 0.7, 0.3, 240.0),
}
WIND_DIRECTION_DEFAULT = 30.0

# Retired game-particle presets and the weather that replaces each. Particle
# ids are protocol-stable, so these entries stay readable forever; the switch
# that applies this mapping on load lands with the live weather pass.
LEGACY_PARTICLE_WEATHER = {
    "snow_flakes": "light_snow",
    "snow_long_fall": "light_snow",
    "snow_heavy": "blizzard",
    "snow_machine": "light_snow",
    "rain": "rain_storm",
}

# Wire block shared with the native helper (little-endian, packed), published
# seqlocked in the shared mapping: magic at 0, sequence at 8, ABI at 12.
WIRE_OFFSET = 2 * 1024 * 1024 + 24480
WIRE_MAGIC = b"DLYWTHR1"
WIRE_ABI = 1
WIRE_FORMAT = "<8sIIII7f32si2f"
WIRE_BYTES = struct.calcsize(WIRE_FORMAT)
WIRE_FLAG_HERO_WAKES = 1
WIRE_FLAG_INCLUDE_IN_EFFECTS = 2

# Lens rain: water on the camera lens while the shot's weather rains. Its controls
# ride with the weather (they only matter with rain) but use their own block, since
# the weather block fills its slot. Mirrors native/include/dolly_lens_rain_runtime.hpp.
LENS_AMOUNT_MIN, LENS_AMOUNT_MAX = 0.0, 1.0
LENS_SIZE_MIN, LENS_SIZE_MAX = 0.3, 3.0
LENS_SLIDE_MIN, LENS_SLIDE_MAX = 0.0, 2.0
LENS_TRAILS_MIN, LENS_TRAILS_MAX = 0.0, 1.0
LENS_REFRACTION_MIN, LENS_REFRACTION_MAX = 0.0, 2.0
LENS_SOFTNESS_MIN, LENS_SOFTNESS_MAX = 0.0, 1.0
# Field: (low, high, default).
LENS_RANGES = {
    "lens_amount": (LENS_AMOUNT_MIN, LENS_AMOUNT_MAX, 0.35),
    "lens_size": (LENS_SIZE_MIN, LENS_SIZE_MAX, 1.0),
    "lens_slide": (LENS_SLIDE_MIN, LENS_SLIDE_MAX, 1.0),
    "lens_trails": (LENS_TRAILS_MIN, LENS_TRAILS_MAX, 0.35),
    "lens_refraction": (LENS_REFRACTION_MIN, LENS_REFRACTION_MAX, 0.75),
    "lens_softness": (LENS_SOFTNESS_MIN, LENS_SOFTNESS_MAX, 0.5),
}
LENS_WIRE_OFFSET = 2 * 1024 * 1024 + 59392
LENS_WIRE_MAGIC = b"DLYLENS1"
LENS_WIRE_ABI = 1
LENS_WIRE_FORMAT = "<8sIIIi6f"
LENS_WIRE_BYTES = struct.calcsize(LENS_WIRE_FORMAT)
LENS_WIRE_FLAG_ENABLED = 1

# Hero trails: the editor asks the game to record the heroes' paths while it plays a shot
# through, and names the recorded file whose trails the weather draws.
TRAILS_WIRE_OFFSET = 2 * 1024 * 1024 + 59456
TRAILS_WIRE_MAGIC = b"DLYTRLS1"
TRAILS_WIRE_ABI = 1
TRAILS_PATH_BYTES = 240
TRAILS_WIRE_FORMAT = f"<8sIIII{TRAILS_PATH_BYTES}s{TRAILS_PATH_BYTES}s"
TRAILS_WIRE_BYTES = struct.calcsize(TRAILS_WIRE_FORMAT)
TRAILS_FLAG_RECORD = 1
TRAILS_FLAG_SHOW = 2
HERO_PATHS_MAGIC = b"DLYTRK01"
HERO_PATH_SAMPLE_FORMAT = "<dI3f"


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Weather {name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"Weather {name} must be between {low:g} and {high:g}")
    return number


@dataclass(frozen=True)
class WeatherSettings:
    preset: str = WEATHER_DEFAULT
    intensity: float = 1.0
    wind_direction: float = WIND_DIRECTION_DEFAULT
    wind_speed: float = _PRESET_DEFAULTS[WEATHER_DEFAULT][0]
    gusts: float = _PRESET_DEFAULTS[WEATHER_DEFAULT][1]
    coating: float = _PRESET_DEFAULTS[WEATHER_DEFAULT][2]
    height_ceiling: float = _PRESET_DEFAULTS[WEATHER_DEFAULT][3]
    build_up: float = 0.0
    flake_size: float = 1.0
    density: float = 1.0
    hero_wakes: bool = True
    include_in_effects: bool = True
    lens_rain: bool = False
    lens_amount: float = LENS_RANGES["lens_amount"][2]
    lens_size: float = LENS_RANGES["lens_size"][2]
    lens_slide: float = LENS_RANGES["lens_slide"][2]
    lens_trails: float = LENS_RANGES["lens_trails"][2]
    lens_refraction: float = LENS_RANGES["lens_refraction"][2]
    lens_softness: float = LENS_RANGES["lens_softness"][2]

    @property
    def enabled(self) -> bool:
        return self.preset != "off"

    def validate(self) -> None:
        if self.preset not in WEATHER_IDS:
            raise ValueError("Choose a weather preset from the library")
        _number(self.intensity, "intensity", INTENSITY_MIN, INTENSITY_MAX)
        _number(self.wind_direction, "wind direction", 0.0, 360.0)
        _number(self.wind_speed, "wind speed", WIND_SPEED_MIN, WIND_SPEED_MAX)
        _number(self.gusts, "gusts", GUSTS_MIN, GUSTS_MAX)
        _number(self.coating, "coating", COATING_MIN, COATING_MAX)
        _number(self.height_ceiling, "height ceiling", HEIGHT_CEILING_MIN, HEIGHT_CEILING_MAX)
        _number(self.build_up, "build-up", BUILD_UP_MIN, BUILD_UP_MAX)
        _number(self.flake_size, "flake size", FLAKE_SIZE_MIN, FLAKE_SIZE_MAX)
        _number(self.density, "density", DENSITY_MIN, DENSITY_MAX)
        for name in ("hero_wakes", "include_in_effects", "lens_rain"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"Weather {name.replace('_', ' ')} must be true or false")
        for name, (low, high, _default) in LENS_RANGES.items():
            _number(getattr(self, name), name.replace("_", " "), low, high)

    def with_preset(self, preset: str) -> "WeatherSettings":
        """The preset's defaults, keeping intensity, direction and the toggles."""
        if preset not in WEATHER_IDS:
            raise ValueError("Choose a weather preset from the library")
        speed, gusts, coating, ceiling = _PRESET_DEFAULTS[preset]
        return replace(self, preset=preset, wind_speed=speed, gusts=gusts, coating=coating,
                       height_ceiling=ceiling)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"preset": self.preset, "intensity": self.intensity,
                "wind_direction": self.wind_direction, "wind_speed": self.wind_speed,
                "gusts": self.gusts, "coating": self.coating,
                "height_ceiling": self.height_ceiling, "build_up": self.build_up,
                "flake_size": self.flake_size, "density": self.density,
                "hero_wakes": self.hero_wakes,
                "include_in_effects": self.include_in_effects,
                "lens_rain": self.lens_rain,
                **{name: getattr(self, name) for name in LENS_RANGES}}

    @classmethod
    def from_dict(cls, value: Any) -> "WeatherSettings":
        if not isinstance(value, dict):
            raise ValueError("Weather must be an object")
        fields = ("preset", "intensity", "wind_direction", "wind_speed", "gusts", "coating",
                  "height_ceiling", "build_up", "flake_size", "density", "hero_wakes",
                  "include_in_effects")
        # Lens rain came after the first weather shots, so its fields are optional.
        optional = ("lens_rain",) + tuple(LENS_RANGES)
        unknown = sorted(set(value) - set(fields) - set(optional))
        if unknown:
            raise ValueError(f"Unknown weather field: {unknown[0]}")
        missing = [name for name in fields if name not in value]
        if missing:
            raise ValueError(f"Weather is missing required field: {missing[0]}")
        numbers = {name: value[name] for name in fields[1:10]}
        numbers.update({name: value[name] for name in LENS_RANGES if name in value})
        for name, number in numbers.items():
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"Weather {name.replace('_', ' ')} must be a number")
        switches = {"lens_rain": value["lens_rain"]} if "lens_rain" in value else {}
        settings = cls(preset=value["preset"], hero_wakes=value["hero_wakes"],
                       include_in_effects=value["include_in_effects"], **switches,
                       **{name: float(number) for name, number in numbers.items()})
        settings.validate()
        return settings

    def pack_wire(self, sequence: int, map_name: str = "", start_tick: int = 0) -> bytes:
        """The native wire block; ``sequence`` lets the helper see a new value.

        ``map_name`` comes from the replay header (the helper bakes that map's
        collision grid) and ``start_tick`` is the shot start, where coating build-up
        begins.
        """
        self.validate()
        flags = ((WIRE_FLAG_HERO_WAKES if self.hero_wakes else 0) |
                 (WIRE_FLAG_INCLUDE_IN_EFFECTS if self.include_in_effects else 0))
        name = map_name.encode("ascii") if map_name else b""
        if len(name) > 31 or not re.fullmatch(rb"[a-z0-9_]*", name):
            name = b""  # the helper ignores a name it cannot use
        if isinstance(start_tick, bool) or not isinstance(start_tick, int) \
                or not 0 <= start_tick <= 0x7fffffff:
            raise ValueError("Weather start tick must be a nonnegative integer")
        return struct.pack(WIRE_FORMAT, WIRE_MAGIC, sequence & 0xffffffff, WIRE_ABI,
                           WEATHER_IDS.index(self.preset), flags, self.intensity,
                           self.wind_direction, self.wind_speed, self.gusts, self.coating,
                           self.height_ceiling, self.build_up, name, start_tick,
                           self.flake_size, self.density)

    def pack_lens_wire(self, sequence: int, start_tick: int = 0) -> bytes:
        """The native lens rain block. ``start_tick`` seeds the shot's drops."""
        self.validate()
        if isinstance(start_tick, bool) or not isinstance(start_tick, int) \
                or not 0 <= start_tick <= 0x7fffffff:
            raise ValueError("Weather start tick must be a nonnegative integer")
        flags = LENS_WIRE_FLAG_ENABLED if self.lens_rain else 0
        return struct.pack(LENS_WIRE_FORMAT, LENS_WIRE_MAGIC, sequence & 0xffffffff, LENS_WIRE_ABI,
                           flags, start_tick, *(getattr(self, name) for name in LENS_RANGES))


WEATHER_OFF = WeatherSettings()


def preset_settings(preset: str) -> WeatherSettings:
    """A fresh preset at its defaults."""
    return WEATHER_OFF.with_preset(preset)


def legacy_particle_weather(particle_preset: str, intensity: float) -> WeatherSettings | None:
    """The weather that replaces a retired particle preset, or None to keep it."""
    preset = LEGACY_PARTICLE_WEATHER.get(particle_preset)
    if preset is None:
        return None
    level = min(max(float(intensity), INTENSITY_MIN), INTENSITY_MAX)
    return replace(preset_settings(preset), intensity=level)


def preset_label(preset: str) -> str:
    return WEATHER_LABELS.get(preset, preset)


def _trails_path_bytes(path: str, name: str) -> bytes:
    data = path.encode("utf-8") if path else b""
    if len(data) >= TRAILS_PATH_BYTES or 0 in data:
        raise ValueError(f"The hero path {name} is too long for the game to open")
    return data


def pack_trails_wire(sequence: int, *, record_path: str = "", recording_id: int = 0,
                     show_path: str = "") -> bytes:
    """The native hero trails block.

    A ``record_path`` makes the game record the heroes' paths while the replay plays, saved
    to that file when the recording stops (the path is cleared or ``recording_id``
    changes). A ``show_path`` names the recorded file whose trails the weather draws.
    """
    if isinstance(recording_id, bool) or not isinstance(recording_id, int)             or not 0 <= recording_id <= 0xffffffff:
        raise ValueError("The hero path recording id must be a 32-bit count")
    record = _trails_path_bytes(record_path, "recording")
    show = _trails_path_bytes(show_path, "file")
    flags = (TRAILS_FLAG_RECORD if record else 0) | (TRAILS_FLAG_SHOW if show else 0)
    return struct.pack(TRAILS_WIRE_FORMAT, TRAILS_WIRE_MAGIC, sequence & 0xffffffff,
                       TRAILS_WIRE_ABI, flags, recording_id, record, show)


def hero_paths_directory() -> Path:
    """Where recorded hero paths are kept, beside the game's map grids."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "DeadlockDolly" / "hero-paths"


def hero_paths_file(replay: str, start_tick: int, end_tick: int) -> Path:
    """The recorded hero paths of one shot: its replay and its tick range."""
    digest = hashlib.sha256(str(replay).encode("utf-8")).hexdigest()[:16]
    return hero_paths_directory() / f"{digest}-{int(start_tick)}-{int(end_tick)}.trk"


def write_hero_paths(path: Path, samples) -> None:
    """Save hero path samples (time, hero slot, x, y, z) in the game's recorded format."""
    body = b"".join(struct.pack(HERO_PATH_SAMPLE_FORMAT, *row) for row in samples)
    target = Path(path)
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(HERO_PATHS_MAGIC + struct.pack("<II", len(samples), 0) + body)
    partial.replace(target)


def trim_hero_paths(path: Path, start: float, end: float) -> int:
    """Keep only the samples inside the shot's replay time range [start, end]; the game also
    sees the heroes while playback gets going. Returns how many samples were kept."""
    kept = [row for row in read_hero_paths(path) if start <= row[0] <= end]
    write_hero_paths(path, kept)
    return len(kept)


def read_hero_paths(path: Path) -> list[tuple[float, int, float, float, float]]:
    """The samples of a recorded hero path file: time, hero slot, x, y, z."""
    data = Path(path).read_bytes()
    if len(data) < 16 or data[:8] != HERO_PATHS_MAGIC:
        raise ValueError("Not a hero path file")
    count = struct.unpack_from("<I", data, 8)[0]
    size = struct.calcsize(HERO_PATH_SAMPLE_FORMAT)
    if len(data) != 16 + count * size:
        raise ValueError("The hero path file is truncated")
    return [struct.unpack_from(HERO_PATH_SAMPLE_FORMAT, data, 16 + i * size)
            for i in range(count)]
