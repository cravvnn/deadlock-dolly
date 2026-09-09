"""Compile the verified DOF controls into the same immutable native shot.

No console text is shipped: IDs map to a fixed native allowlist. Camera path
format and coefficients remain unchanged inside the versioned shot envelope.
"""
import math
import struct
from .native_path import compile_project, CHANNEL_RECORD, SEGMENT_TIMES
from .path import Project, TrackKey, _monotone_tangents

# ID order is part of bridge ABI 2. Native binding also checks the game's type.
EFFECTS = {
    "r_citadel_depthoffield_enable": (0, 0, 1, True),
    "r_citadel_depthoffield_focus_distance": (1, 0, 10000, False),
    "r_citadel_depthoffield_aperture_diameter": (2, 0, 3, False),
    "r_citadel_depthoffield_sensor_size": (3, .5, 3, False),
    "r_citadel_depthoffield_mode": (4, 0, 2, True),
    "r_citadel_depthoffield_debug": (5, 0, 1, True),
    "r_depth_of_field": (6, 0, 1, True),
}
SHOT_HEADER = struct.Struct("<8s4I")
EFFECT_HEADER = struct.Struct("<8s2I")
TRACK_HEADER = struct.Struct("<4I5d")
MAX_EFFECT_KEYS = 4096
MAX_SHOT_BYTES = 2 * 1024 * 1024 - 1024


def compile_effects(project):
    if not isinstance(project, Project):
        raise ValueError("Native effect compilation requires a Project")
    project.validate()
    names = set(project.setup_values) | {t.name for t in project.tracks if t.keys}
    unsupported = names - EFFECTS.keys()
    if unsupported:
        raise ValueError("Not supported for native frame synchronization: " + ", ".join(sorted(unsupported))
                         + ". Remove these shot variables or select Console camera mode before launch.")
    all_tracks = {track.name: track for track in project.tracks}
    tracks = {name:track for name,track in all_tracks.items() if track.keys}
    result = bytearray(EFFECT_HEADER.pack(b"DLYEFX01", len(names), 0))
    for name in sorted(names, key=lambda n: EFFECTS[n][0]):
        index, minimum, maximum, discrete = EFFECTS[name]
        track = tracks.get(name)
        keys = track.keys if track else [TrackKey(0, project.setup_values[name])]
        mode = track.interpolation if track else "step"
        if len(keys) > MAX_EFFECT_KEYS:
            raise ValueError(f"{name} supports at most {MAX_EFFECT_KEYS} native keys")
        if discrete and len(keys) > 1 and mode != "step":
            raise ValueError(f"{name} is a switch/mode: choose Step interpolation")
        times = [float(k.time) for k in keys]
        values = [float(k.value) for k in keys]
        restore_track = all_tracks.get(name)
        restore = restore_track.restore_value if restore_track else None
        for value in values + ([] if restore is None else [restore]):
            if not minimum <= value <= maximum or (discrete and value != int(value)):
                raise ValueError(f"{name} requires {'whole-number ' if discrete else ''}values from {minimum} to {maximum}")
        kind = 0 if mode == "step" else 1
        tangents = [0.0] * len(keys)
        if len(keys) > 2 and mode == "smooth":
            try:
                candidate = _monotone_tangents(times, values)
                if all(math.isfinite(v) for v in candidate):
                    kind, tangents = 2, candidate
            except ArithmeticError:
                pass
        result.extend(TRACK_HEADER.pack(index, len(keys)-1, int(restore is not None), 0,
                      times[0], times[-1], values[0], values[-1], restore if restore is not None else 0))
        for i in range(len(keys)-1):
            result.extend(SEGMENT_TIMES.pack(times[i], times[i+1]))
            result.extend(CHANNEL_RECORD.pack(kind, 1, values[i], values[i+1], tangents[i], tangents[i+1]))
    return bytes(result)


def compile_shot(project):
    camera, effects = compile_project(project), compile_effects(project)
    result = SHOT_HEADER.pack(b"DLYSHOT2", 1, len(camera), len(effects), 0) + camera + effects
    if len(result) > MAX_SHOT_BYTES:
        raise ValueError("Camera and effect curves exceed the native shot capacity; reduce the number of keys")
    return result
