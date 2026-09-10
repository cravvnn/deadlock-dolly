"""Validated, deterministic camera paths for the Deadlock Dolly editor.

All timestamps are shot-relative seconds. This module has no game access and
never executes strings from a project. Position uses nonuniform cubic Hermite
interpolation; aspect-ratio zoom and smooth cvar tracks use shape-preserving
cubic Hermite interpolation so values cannot overshoot neighboring keys.
Legacy FOV values remain in saved shots solely for backwards compatibility.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


FORMAT_NAME = "deadlock-dolly"
FORMAT_VERSION = 2
VECTOR_FORMAT_VERSION = 3
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_KEYS = 100_000
STANDARD_ASPECT = 16.0 / 9.0
# Deliberate editor limits, not a claim about native r_aspectratio bounds.
# Zero is the game's automatic-aspect sentinel and is never a curve value.
ASPECT_MIN = 0.5
ASPECT_MAX = 4.0

# These are names, never commands. Prefixes intentionally limit the generic
# numeric track editor to camera parameters. An allowed name is not a promise
# that the currently installed game exposes that cvar: the adapter checks it.
CAMERA_CVAR_PREFIXES = (
    "r_dof_", "mat_dof_", "dof_", "citadel_camera_", "cam_",
    "r_camera_", "cl_camera_", "fov_", "r_citadel_depthoffield_",
)
CAMERA_CVAR_NAMES = frozenset({
    "r_dof", "r_depth_of_field", "mat_dof_enabled", "fov", "fov_cs_debug", "default_fov",
    "spec_fov", "r_aspectratio", "r_drawviewmodel", "cl_drawhud", "citadel_hud_visible",
    "mat_depth_blur_focal_distance", "mat_depth_blur_strength",
})
# Only verified multi-component camera controls accept numeric arrays.
CVAR_COMPONENTS = {"r_dof_override_ranges": 4}
CvarValue = float | tuple[float, ...]
_CVAR_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\Z", re.ASCII)
_CVAR_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,95}\Z", re.ASCII)


def validate_cvar_name(name: str) -> str:
    """Return an allowed camera cvar identifier or raise ``ValueError``.

    Deliberately rejects whitespace, separators, aliases, mixed case and
    command-like inputs. Numeric values are validated separately. This list
    does not unlock variables or establish whether a cvar exists in the game.
    """
    if not isinstance(name, str) or not _CVAR_IDENTIFIER.fullmatch(name):
        raise ValueError("Camera cvar names must be lowercase identifiers without spaces or punctuation")
    if name not in CAMERA_CVAR_NAMES and not name.startswith(CAMERA_CVAR_PREFIXES):
        raise ValueError(f"Not an allowed camera cvar: {name}")
    return name


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def validate_cvar_value(name: str, value: Any, label: str = "Camera value") -> CvarValue:
    """Validate a scalar or the exact component count for a known vector cvar."""
    validate_cvar_name(name)
    count = CVAR_COMPONENTS.get(name, 1)
    if count == 1:
        return _finite(value, label)
    if not isinstance(value, (tuple, list)) or len(value) != count:
        raise ValueError(f"{name} needs {count} numbers separated by spaces")
    return tuple(_finite(component, f"{label} component {i + 1}")
                 for i, component in enumerate(value))


def parse_cvar_value(name: str, text: str, label: str = "Camera value") -> CvarValue:
    """Parse editor text without accepting console syntax or arbitrary strings."""
    validate_cvar_name(name)
    if not isinstance(text, str):
        raise ValueError(f"{label} must be numeric text")
    parts = text.strip().split()
    count = CVAR_COMPONENTS.get(name, 1)
    if len(parts) != count or any(not _CVAR_NUMBER.fullmatch(part) for part in parts):
        if count > 1:
            raise ValueError(f"{name} needs {count} numbers separated by spaces")
        raise ValueError(f"{label} must be a finite number")
    numbers = tuple(float(part) for part in parts)
    return validate_cvar_value(name, numbers if count > 1 else numbers[0], label)


def format_cvar_value(value: CvarValue) -> str:
    """Format already validated scalar/vector data for display or console use."""
    values = value if isinstance(value, (tuple, list)) else (value,)
    return " ".join(format(_finite(component, "Camera value"), ".9g") for component in values)


def _json_cvar_value(value: CvarValue):
    return list(value) if isinstance(value, (tuple, list)) else value


def _choice(value: Any, options: tuple[str, ...], label: str) -> None:
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"{label} must be one of: {', '.join(options)}")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _members(value: dict[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(map(str, sorted(unknown, key=str)))}")


def _array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if len(value) > MAX_KEYS:
        raise ValueError(f"{label} exceeds the {MAX_KEYS:,} key limit")
    return value


@dataclass
class Keyframe:
    time: float
    x: float
    y: float
    z: float
    pitch: float
    yaw: float
    roll: float
    fov: float = 90.0
    aspect_ratio: float = STANDARD_ASPECT


@dataclass
class TrackKey:
    time: float
    value: CvarValue


@dataclass
class CvarTrack:
    name: str
    keys: list[TrackKey] = field(default_factory=list)
    interpolation: str = "linear"
    restore_value: CvarValue | None = None


CAMERA_FIELDS = ("x", "y", "z", "pitch", "yaw", "roll", "fov", "aspect_ratio")
_LEGACY_CAMERA_FIELDS = CAMERA_FIELDS[:-1]


def _validate_times(keys: list[Any], label: str) -> None:
    previous = -math.inf
    for index, key in enumerate(keys):
        timestamp = _finite(key.time, f"{label}[{index}].time")
        if timestamp < 0:
            raise ValueError(f"{label} timestamps must be nonnegative")
        if timestamp <= previous:
            raise ValueError(f"{label} timestamps must be strictly increasing; duplicates are not allowed")
        previous = timestamp


def _unwrap(values: list[float]) -> list[float]:
    if not values:
        return []
    # Reducing first also avoids overflow when a user enters very large but
    # finite angles. Unwrapped mode deliberately skips this operation.
    authored = values
    values = [_wrap(value) for value in authored]
    result = [values[0]]
    for index, value in enumerate(values[1:], start=1):
        delta = (value - result[-1] + 180.0) % 360.0 - 180.0
        # Exactly half a turn follows the sign explicitly chosen by the user.
        if delta == -180.0 and authored[index] > authored[index - 1]:
            delta = 180.0
        result.append(result[-1] + delta)
    return result


def _wrap(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _position_tangents(times: list[float], values: list[float]) -> list[float]:
    slopes = [(values[i + 1] - values[i]) / (times[i + 1] - times[i])
              for i in range(len(times) - 1)]
    if len(times) == 2:
        return [slopes[0], slopes[0]]
    # Central secants weight by the actual time intervals. In particular, a
    # tiny neighboring interval cannot arbitrarily amplify a long-segment
    # tangent, as happens with an unweighted average of interval velocities.
    return [slopes[0]] + [
        (values[i + 1] - values[i - 1]) / (times[i + 1] - times[i - 1])
        for i in range(1, len(times) - 1)
    ] + [slopes[-1]]


def _monotone_tangents(times: list[float], values: list[float]) -> list[float]:
    """Fritsch-Butland/PCHIP derivatives for nonuniform sample times."""
    spacing = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    slopes = [(values[i + 1] - values[i]) / spacing[i] for i in range(len(spacing))]
    if len(times) == 2:
        return [slopes[0], slopes[0]]
    derivatives = [0.0] * len(times)
    for i in range(1, len(times) - 1):
        left, right = slopes[i - 1], slopes[i]
        if left == 0 or right == 0 or (left > 0) != (right > 0):
            continue
        w1 = 2 * spacing[i] + spacing[i - 1]
        w2 = spacing[i] + 2 * spacing[i - 1]
        derivatives[i] = (w1 + w2) / (w1 / left + w2 / right)

    def endpoint(here_h: float, next_h: float, here_s: float, next_s: float) -> float:
        derivative = ((2 * here_h + next_h) * here_s - here_h * next_s) / (here_h + next_h)
        if here_s == 0 or (derivative > 0) != (here_s > 0):
            return 0.0
        if (here_s > 0) != (next_s > 0) and abs(derivative) > abs(3 * here_s):
            return 3 * here_s
        return derivative

    derivatives[0] = endpoint(spacing[0], spacing[1], slopes[0], slopes[1])
    derivatives[-1] = endpoint(spacing[-1], spacing[-2], slopes[-1], slopes[-2])
    return derivatives


def _sample(times: list[float], values: list[float], time: float,
            interpolation: str, monotone: bool = True) -> float:
    if time <= times[0] or len(times) == 1:
        return values[0]
    if time >= times[-1]:
        return values[-1]
    left = bisect_right(times, time) - 1
    if interpolation == "step":
        return values[left]
    span = times[left + 1] - times[left]
    fraction = (time - times[left]) / span
    if interpolation == "linear" or len(times) == 2:
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    try:
        tangents = (_monotone_tangents if monotone else _position_tangents)(times, values)
    except ArithmeticError:
        # Extremely close timestamps can exceed floating-point derivative
        # precision. A linear segment remains finite and honors both keys.
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    if not all(math.isfinite(value) for value in tangents):
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    u2, u3 = fraction * fraction, fraction * fraction * fraction
    value = ((2 * u3 - 3 * u2 + 1) * values[left]
             + (u3 - 2 * u2 + fraction) * span * tangents[left]
             + (-2 * u3 + 3 * u2) * values[left + 1]
             + (u3 - u2) * span * tangents[left + 1])
    if not math.isfinite(value):
        return (1 - fraction) * values[left] + fraction * values[left + 1]
    if monotone:
        # Also contain floating-point rounding at segment boundaries.
        low, high = sorted((values[left], values[left + 1]))
        value = max(low, min(high, value))
    return value


@dataclass
class Project:
    name: str = "Untitled"
    keyframes: list[Keyframe] = field(default_factory=list)
    tracks: list[CvarTrack] = field(default_factory=list)
    interpolation: str = "smooth"
    rotation_mode: str = "shortest"
    start_tick: int = 0
    tick_rate: float = 64.0
    setup_values: dict[str, CvarValue] = field(default_factory=dict)
    standard_aspect: float = STANDARD_ASPECT
    lens_interpolation: str = "smooth"

    def validate(self) -> None:
        if not isinstance(self.name, str) or len(self.name) > 256:
            raise ValueError("Project name must be text no longer than 256 characters")
        _choice(self.interpolation, ("linear", "smooth"), "Camera interpolation")
        _choice(self.rotation_mode, ("shortest", "unwrapped"), "Rotation mode")
        _choice(self.lens_interpolation, ("linear", "smooth", "step"), "Zoom interpolation")
        standard_aspect = _finite(self.standard_aspect, "Standard aspect ratio")
        if not ASPECT_MIN <= standard_aspect <= ASPECT_MAX:
            raise ValueError(f"Standard aspect ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}")
        if isinstance(self.start_tick, bool) or not isinstance(self.start_tick, int) or self.start_tick < 0:
            raise ValueError("Start tick must be a nonnegative integer")
        if _finite(self.tick_rate, "Tick rate") <= 0:
            raise ValueError("Tick rate must be positive")
        _array(self.keyframes, "Camera keyframes")
        for i, key in enumerate(self.keyframes):
            if not isinstance(key, Keyframe):
                raise ValueError(f"Camera keyframe {i} must be a Keyframe")
            for name in CAMERA_FIELDS:
                _finite(getattr(key, name), f"Camera keyframe {i}.{name}")
            if not 1.0 <= key.fov <= 179.0:
                raise ValueError(f"Camera keyframe {i}.fov must be between 1 and 179 degrees")
            if not ASPECT_MIN <= key.aspect_ratio <= ASPECT_MAX:
                raise ValueError(
                    f"Camera keyframe {i}.aspect_ratio must be between {ASPECT_MIN:g} and {ASPECT_MAX:g}; "
                    "use the shot's standard aspect ratio instead of automatic 0")
        _validate_times(self.keyframes, "Camera keyframes")

        _array(self.tracks, "Cvar tracks")
        seen: set[str] = set()
        for i, track in enumerate(self.tracks):
            if not isinstance(track, CvarTrack):
                raise ValueError(f"Cvar track {i} must be a CvarTrack")
            validate_cvar_name(track.name)
            if track.name in seen:
                raise ValueError(f"Duplicate cvar track: {track.name}")
            seen.add(track.name)
            _choice(track.interpolation, ("linear", "smooth", "step"), f"{track.name} interpolation")
            _array(track.keys, f"{track.name} keys")
            for index, key in enumerate(track.keys):
                if not isinstance(key, TrackKey):
                    raise ValueError(f"{track.name} key {index} must be a TrackKey")
                validate_cvar_value(track.name, key.value, f"{track.name} key {index}.value")
            _validate_times(track.keys, track.name)
            if track.restore_value is not None:
                validate_cvar_value(track.name, track.restore_value, f"{track.name} restore value")

        _object(self.setup_values, "Setup values")
        for name, value in self.setup_values.items():
            validate_cvar_name(name)
            validate_cvar_value(name, value, f"{name} setup value")

    @property
    def duration(self) -> float:
        """Last authored timestamp, including independent cvar tracks."""
        ends = [self.keyframes[-1].time] if self.keyframes else [0.0]
        ends.extend(track.keys[-1].time for track in self.tracks if track.keys)
        return float(max(ends))

    def evaluate(self, time: float) -> dict[str, Any]:
        """Evaluate without state: forward playback and rewinds are identical.

        Camera and cvar channels hold their endpoint values outside their
        individual ranges. Setup values form the cvar baseline; a nonempty
        animated track overrides that baseline, including before its first key.
        ``restore_value`` is metadata for the runtime and is never animated.
        Pitch is interpolated directly in both modes. Only yaw and roll wrap
        between authored keys in shortest mode; unwrapped mode preserves
        authored full turns. Evaluated yaw/roll remain continuously unwrapped
        so crossing 180 degrees does not introduce a 360-degree command jump.
        Those equivalent angles do not alter authored keys or serialization.
        """
        self.validate()
        time = _finite(time, "Evaluation time")
        if not self.keyframes:
            raise ValueError("Add a camera keyframe before evaluating the shot")
        times = [float(key.time) for key in self.keyframes]
        result: dict[str, Any] = {}
        for name in CAMERA_FIELDS:
            values = [float(getattr(key, name)) for key in self.keyframes]
            wrap = self.rotation_mode == "shortest" and name in ("yaw", "roll")
            if wrap:
                values = _unwrap(values)
            interpolation = self.lens_interpolation if name == "aspect_ratio" else self.interpolation
            value = _sample(times, values, time, interpolation, monotone=name not in ("x", "y", "z"))
            result[name] = value
        cvars = {name: validate_cvar_value(name, value) for name, value in self.setup_values.items()}
        for track in self.tracks:
            if track.keys:
                key_times = [float(key.time) for key in track.keys]
                values = [validate_cvar_value(track.name, key.value) for key in track.keys]
                count = CVAR_COMPONENTS.get(track.name, 1)
                if count == 1:
                    cvars[track.name] = _sample(key_times, values, time, track.interpolation)
                else:
                    cvars[track.name] = tuple(_sample(
                        key_times, [value[i] for value in values], time, track.interpolation,
                    ) for i in range(count))
        result["cvars"] = cvars
        return result

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "format": FORMAT_NAME,
            "version": VECTOR_FORMAT_VERSION if (any(name in CVAR_COMPONENTS for name in self.setup_values)
                         or any(track.name in CVAR_COMPONENTS for track in self.tracks)) else FORMAT_VERSION,
            "name": self.name,
            "interpolation": self.interpolation,
            "rotation_mode": self.rotation_mode,
            "standard_aspect": self.standard_aspect,
            "lens_interpolation": self.lens_interpolation,
            "start_tick": self.start_tick,
            "tick_rate": self.tick_rate,
            "setup_values": {name: _json_cvar_value(value) for name, value in self.setup_values.items()},
            "keyframes": [{"time": key.time, **{name: getattr(key, name) for name in CAMERA_FIELDS}}
                          for key in self.keyframes],
            "tracks": [{"name": track.name, "interpolation": track.interpolation,
                        "restore_value": _json_cvar_value(track.restore_value),
                        "keys": [{"time": key.time, "value": _json_cvar_value(key.value)} for key in track.keys]}
                       for track in self.tracks],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Project":
        obj = _object(value, "Project")
        if obj.get("format", FORMAT_NAME) != FORMAT_NAME:
            raise ValueError("This is not a Deadlock Dolly project")
        version = obj.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version not in (1, FORMAT_VERSION, VECTOR_FORMAT_VERSION):
            raise ValueError(f"Unsupported project version: {version!r}; expected 1, {FORMAT_VERSION}, or {VECTOR_FORMAT_VERSION}")
        allowed = ("format", "version", "name", "interpolation", "rotation_mode",
                   "start_tick", "tick_rate", "setup_values", "keyframes", "tracks")
        if version >= FORMAT_VERSION:
            allowed += ("standard_aspect", "lens_interpolation")
            for required in ("standard_aspect", "lens_interpolation"):
                if required not in obj:
                    raise ValueError(f"Project version {version} is missing required field: {required}")
        _members(obj, allowed, "project")
        keyframes: list[Keyframe] = []
        for i, raw in enumerate(_array(obj.get("keyframes", []), "Camera keyframes")):
            key = _object(raw, f"Camera keyframe {i}")
            fields = CAMERA_FIELDS if version >= FORMAT_VERSION else _LEGACY_CAMERA_FIELDS
            _members(key, ("time", *fields), "camera keyframe")
            # A corrupt v2 shot must never silently lose its authored zoom.
            if version >= FORMAT_VERSION and "aspect_ratio" not in key:
                raise ValueError(f"Camera keyframe {i} is missing required field: aspect_ratio")
            if version == 1 and "fov" not in key:
                raise ValueError(f"Camera keyframe {i} is missing required field: fov")
            try:
                # No FOV-to-aspect conversion is valid for the unreliable
                # legacy free-camera FOV controls. Keep the old value as
                # metadata and start migrated zoom keys at standard aspect.
                keyframes.append(Keyframe(**key))
            except TypeError as exc:
                raise ValueError(f"Camera keyframe {i} is missing required fields") from exc
        tracks: list[CvarTrack] = []
        for i, raw in enumerate(_array(obj.get("tracks", []), "Cvar tracks")):
            track = _object(raw, f"Cvar track {i}")
            _members(track, ("name", "keys", "interpolation", "restore_value"), "cvar track")
            keys: list[TrackKey] = []
            for j, raw_key in enumerate(_array(track.get("keys", []), f"Cvar track {i} keys")):
                key = _object(raw_key, f"Cvar track {i} key {j}")
                _members(key, ("time", "value"), "cvar key")
                try:
                    keys.append(TrackKey(**key))
                except TypeError as exc:
                    raise ValueError(f"Cvar track {i} key {j} is missing required fields") from exc
            if "name" not in track:
                raise ValueError(f"Cvar track {i} is missing its name")
            tracks.append(CvarTrack(name=track["name"], keys=keys,
                                    interpolation=track.get("interpolation", "linear"),
                                    restore_value=track.get("restore_value")))
        project = cls(name=obj.get("name", "Untitled"), keyframes=keyframes, tracks=tracks,
                      interpolation=obj.get("interpolation", "smooth"),
                      rotation_mode=obj.get("rotation_mode", "shortest"),
                      start_tick=obj.get("start_tick", 0), tick_rate=obj.get("tick_rate", 64.0),
                      setup_values=obj.get("setup_values", {}),
                      standard_aspect=obj.get("standard_aspect", STANDARD_ASPECT),
                      lens_interpolation=obj.get("lens_interpolation", "smooth"))
        project.validate()
        if version < VECTOR_FORMAT_VERSION and (
                any(name in CVAR_COMPONENTS for name in project.setup_values)
                or any(track.name in CVAR_COMPONENTS for track in project.tracks)):
            raise ValueError("Multi-component camera variables require project version 3")
        return project

    def save(self, path: str | Path) -> None:
        """Write validated JSON atomically beside the destination."""
        import os
        import tempfile

        destination = Path(path)
        encoded = (json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_PROJECT_BYTES:
            raise ValueError("Project exceeds the 8 MiB file limit")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.",
                                             suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        def reject_constant(value: str) -> None:
            raise ValueError(f"Non-finite JSON number is not permitted: {value}")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError(f"Duplicate JSON field: {name}")
                result[name] = value
            return result

        with Path(path).open("rb") as handle:
            data = handle.read(MAX_PROJECT_BYTES + 1)
        if len(data) > MAX_PROJECT_BYTES:
            raise ValueError("Project exceeds the 8 MiB file limit")
        try:
            obj = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant,
                             object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"Invalid project JSON: {exc}") from exc
        return cls.from_dict(obj)
