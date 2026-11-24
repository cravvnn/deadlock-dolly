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

import math
import re
import struct
from dataclasses import dataclass, replace
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
        for name in ("hero_wakes", "include_in_effects"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"Weather {name.replace('_', ' ')} must be true or false")

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
                "include_in_effects": self.include_in_effects}

    @classmethod
    def from_dict(cls, value: Any) -> "WeatherSettings":
        if not isinstance(value, dict):
            raise ValueError("Weather must be an object")
        fields = ("preset", "intensity", "wind_direction", "wind_speed", "gusts", "coating",
                  "height_ceiling", "build_up", "flake_size", "density", "hero_wakes",
                  "include_in_effects")
        unknown = sorted(set(value) - set(fields))
        if unknown:
            raise ValueError(f"Unknown weather field: {unknown[0]}")
        missing = [name for name in fields if name not in value]
        if missing:
            raise ValueError(f"Weather is missing required field: {missing[0]}")
        numbers = {name: value[name] for name in fields[1:10]}
        for name, number in numbers.items():
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"Weather {name.replace('_', ' ')} must be a number")
        settings = cls(preset=value["preset"], hero_wakes=value["hero_wakes"],
                       include_in_effects=value["include_in_effects"],
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
