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

Channel order is X, Y, Z, pitch, yaw, roll, aspect ratio. Captured lenses use
version 2: an eighth lens-scale channel, 176-byte header and 336-byte segments.
The old FOV field remains legacy metadata. native_effects wraps the camera blob with supported DOF
curves; their final timestamp extends the overall native shot duration.
"""

from __future__ import annotations

from bisect import bisect_right
import struct

from .path import Project


CHANNELS = ("x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
MAGIC = b"DLYPATH\0"
VERSION = 1
MAX_CAMERA_KEYS = 4096
HEADER = struct.Struct("<8sIIII17d")
LENS_HEADER = struct.Struct("<8sIIII19d")
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
    if project.interpolation == "track":
        return _compile_track(project)
    times = [float(key.time) for key in keys]
    channels: list[tuple[tuple[float, ...], int, int, tuple[float, ...], tuple[float, ...]]] = []
    names = CHANNELS + (("lens_scale",) if any(k.lens_scale is not None for k in keys) else ())
    for name in names:
        samples = project._camera_channel_samples(name)
        values, interpolation = samples.values, samples.interpolation
        # The native clamp bit follows the evaluator: the spline mode is
        # intentionally unclamped for every channel.
        requested_interpolation = (project.lens_interpolation
                                   if name in ("aspect_ratio", "lens_scale") else project.interpolation)
        flags = int(name not in ("x", "y", "z") and requested_interpolation != "spline")
        kind = 0 if interpolation == "step" else 1
        incoming = outgoing = (0.0,) * len(keys)
        if samples.tangents is not None:
            incoming = samples.incoming_tangents or samples.tangents
            outgoing = samples.outgoing_tangents or samples.tangents
            kind = 2
        channels.append((values, kind, flags, incoming, outgoing))
    result = bytearray((LENS_HEADER if len(names) == 8 else HEADER).pack(
        MAGIC, 2 if len(names) == 8 else VERSION, len(keys) - 1, len(names), 0,
        project.duration, times[0], times[-1],
        *(channel[0][0] for channel in channels),
        *(channel[0][-1] for channel in channels),
    ))
    for index in range(len(keys) - 1):
        result.extend(SEGMENT_TIMES.pack(times[index], times[index + 1]))
        for values, kind, flags, incoming, outgoing in channels:
            result.extend(CHANNEL_RECORD.pack(
                kind, flags, values[index], values[index + 1],
                outgoing[index], incoming[index + 1],
            ))
    return bytes(result)


def _piece(samples, begin: float, end: float) -> tuple[int, float, float, float, float]:
    """Restrict one channel to [begin, end] inside a single source segment.

    A cubic Hermite restricted to a sub-interval is again an exact cubic
    Hermite, so the native evaluator reproduces the editor's samples exactly.
    """
    times, values = samples.times, samples.values
    if len(times) == 1:
        return 1, values[0], values[0], 0.0, 0.0
    left = min(max(bisect_right(times, begin) - 1, 0), len(times) - 2)
    t0, t1 = times[left], times[left + 1]
    if samples.interpolation == "step":
        return 0, values[left], values[left], 0.0, 0.0
    if samples.tangents is None:
        return 1, samples.evaluate(begin), samples.evaluate(end), 0.0, 0.0
    outgoing = samples.outgoing_tangents or samples.tangents
    incoming = samples.incoming_tangents or samples.tangents
    span = t1 - t0

    def at(time: float) -> tuple[float, float]:
        u = min(max((time - t0) / span, 0.0), 1.0)
        u2, u3 = u * u, u * u * u
        v0, v1, d0, d1 = values[left], values[left + 1], outgoing[left], incoming[left + 1]
        value = ((2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * span * d0
                 + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * span * d1)
        rate = ((6 * u2 - 6 * u) * v0 + (3 * u2 - 4 * u + 1) * span * d0
                + (-6 * u2 + 6 * u) * v1 + (3 * u2 - 2 * u) * span * d1) / span
        return value, rate

    (a, da), (b, db) = at(begin), at(end)
    return 2, a, b, da, db


def _compile_track(project: Project) -> bytes:
    """Track paths: one segment per position sub-knot, all channels restricted."""
    names = CHANNELS + (("lens_scale",) if any(k.lens_scale is not None for k in project.keyframes) else ())
    samples = {name: project._camera_channel_samples(name) for name in names}
    knots = samples["x"].times
    if len(knots) - 1 > MAX_CAMERA_KEYS - 1:
        raise ValueError("This Track path needs too many native segments; use fewer cameras")
    records = []
    for name in names:
        requested = project.lens_interpolation if name in ("aspect_ratio", "lens_scale") else project.interpolation
        flags = int(name not in ("x", "y", "z") and requested != "spline")
        records.append((samples[name], flags))
    first = [s.evaluate(knots[0]) for s, _ in records]
    last = [s.evaluate(knots[-1]) for s, _ in records]
    result = bytearray((LENS_HEADER if len(names) == 8 else HEADER).pack(
        MAGIC, 2 if len(names) == 8 else VERSION, len(knots) - 1, len(names), 0,
        project.duration, float(project.keyframes[0].time), float(project.keyframes[-1].time),
        *first, *last,
    ))
    for index in range(len(knots) - 1):
        begin, end = knots[index], knots[index + 1]
        result.extend(SEGMENT_TIMES.pack(begin, end))
        for channel, flags in records:
            kind, left, right, d0, d1 = _piece(channel, begin, end)
            result.extend(CHANNEL_RECORD.pack(kind, flags, left, right, d0, d1))
    return bytes(result)
