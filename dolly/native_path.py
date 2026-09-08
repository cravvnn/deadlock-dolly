"""Compile authored camera channels once for the native render-view bridge.

The little-endian, version-1 format has a 160-byte header followed by N
296-byte segments (at most 4095; 4096 authored camera keys):

  header: 8s magic, 4I version/segment_count/channel_count/reserved,
          3d duration/first_camera_time/last_camera_time, 7d first, 7d last
  segment: 2d begin/end, followed by seven channel records
  channel: 2I kind/flags, 4d left/right/left_derivative/right_derivative

Magic is b"DLYPATH\\0". Kinds are step=0, linear=1, cubic Hermite=2.
Flag bit 0 clamps the result to its two endpoints (the editor's PCHIP rule).
Hermite derivatives use shot seconds; evaluation uses normalized segment
position and the segment duration. Unknown bits, versions and trailing bytes
are rejected by the native parser. No console command or cvar name is stored.

Channel order is X, Y, Z, pitch, yaw, roll, aspect ratio. FOV is legacy shot
metadata. Other cvar tracks stay with the existing console effects backend;
their final timestamp still extends the overall native shot duration.
"""

from __future__ import annotations

import math
import struct

from .path import Project, _monotone_tangents, _position_tangents, _unwrap


CHANNELS = ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
MAGIC = b"DLYPATH\0"
VERSION = 1
MAX_CAMERA_KEYS = 4096
HEADER = struct.Struct("<8sIIII17d")
SEGMENT_TIMES = struct.Struct("<2d")
CHANNEL_RECORD = struct.Struct("<II4d")
HEADER_BYTES = HEADER.size
SEGMENT_BYTES = SEGMENT_TIMES.size + len(CHANNELS) * CHANNEL_RECORD.size


def compile_project(project: Project) -> bytes:
    """Return one immutable path blob without changing the saved project.

The current Project evaluator remains the definition of camera motion:
nonuniform position Hermite, bounded angular/zoom PCHIP, shortest yaw/roll
unwrapping, two-key linear motion, and linear fallback for invalid tangents.
The callback consumes these coefficients rather than a sampled pose stream.
"""
    if not isinstance(project, Project):
        raise ValueError("Native camera compilation requires a Project")
    project.validate()
    keys = project.keyframes
    if not keys:
        raise ValueError("Add a camera keyframe before compiling the shot")
    if len(keys) > MAX_CAMERA_KEYS:
        raise ValueError(f"Native camera paths support at most {MAX_CAMERA_KEYS:,} camera keys")
    times = [float(key.time) for key in keys]
    channels: list[tuple[list[float], int, int, list[float]]] = []
    for name in CHANNELS:
        values = [float(getattr(key, name)) for key in keys]
        if name in ("yaw", "roll") and project.rotation_mode == "shortest":
            values = _unwrap(values)
        interpolation = project.lens_interpolation if name == "aspect_ratio" else project.interpolation
        flags = int(name not in ("x", "y", "z"))
        kind = 0 if interpolation == "step" else 1
        tangents = [0.0] * len(keys)
        if len(keys) > 2 and interpolation == "smooth":
            try:
                candidate = (_monotone_tangents if flags else _position_tangents)(times, values)
                if all(math.isfinite(value) for value in candidate):
                    tangents = candidate
                    kind = 2
            except ArithmeticError:
                # Exactly the same full-channel fallback as Project.evaluate.
                pass
        channels.append((values, kind, flags, tangents))
    result = bytearray(HEADER.pack(
        MAGIC, VERSION, len(keys) - 1, len(CHANNELS), 0,
        project.duration, times[0], times[-1],
        *(channel[0][0] for channel in channels),
        *(channel[0][-1] for channel in channels),
    ))
    for index in range(len(keys) - 1):
        result.extend(SEGMENT_TIMES.pack(times[index], times[index + 1]))
        for values, kind, flags, tangents in channels:
            result.extend(CHANNEL_RECORD.pack(
                kind, flags, values[index], values[index + 1],
                tangents[index], tangents[index + 1],
            ))
    return bytes(result)
