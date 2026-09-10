"""Compile the verified DOF controls into the same immutable native shot.

No console text is shipped: IDs map to a fixed native allowlist. Camera path
format and coefficients remain unchanged inside the versioned shot envelope.
"""
import math
import struct
from .native_path import compile_project, CHANNEL_RECORD, SEGMENT_TIMES
from .path import Project, TrackKey, _monotone_tangents, CVAR_COMPONENTS, validate_cvar_value

# ID order is stable across bridge versions. Native binding also checks the game's type.
FLOAT32_MAX = 3.4028234663852886e38
EFFECTS = {
    "r_citadel_depthoffield_enable": (0, 0, 1, True),
    "r_citadel_depthoffield_focus_distance": (1, 0, 10000, False),
    "r_citadel_depthoffield_aperture_diameter": (2, 0, 3, False),
    "r_citadel_depthoffield_sensor_size": (3, .5, 3, False),
    "r_citadel_depthoffield_mode": (4, 0, 2, True),
    "r_citadel_depthoffield_debug": (5, 0, 1, True),
    "r_depth_of_field": (6, 0, 1, True),
    "r_dof_override": (7, 0, 1, True),
    "r_dof_override_ranges": (8, -FLOAT32_MAX, FLOAT32_MAX, False),
    "r_dof_override_near_blurry": (9, -FLOAT32_MAX, FLOAT32_MAX, False),
    "r_dof_override_near_crisp": (10, -FLOAT32_MAX, FLOAT32_MAX, False),
    "r_dof_override_far_crisp": (11, -FLOAT32_MAX, FLOAT32_MAX, False),
    "r_dof_override_far_blurry": (12, -FLOAT32_MAX, FLOAT32_MAX, False),
    "r_dof_override_tilt_to_ground": (13, -FLOAT32_MAX, FLOAT32_MAX, False),
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
    # Scalar-only envelopes remain byte-identical. Vector envelopes explicitly
    # version the track header's fourth word as the component index.
    vector_format = any(CVAR_COMPONENTS.get(name, 1) > 1 for name in names)
    count = sum(CVAR_COMPONENTS.get(name, 1) for name in names)
    result = bytearray(EFFECT_HEADER.pack(b"DLYEFX02" if vector_format else b"DLYEFX01", count, 0))
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
        authored = [validate_cvar_value(name, k.value) for k in keys]
        restore_track = all_tracks.get(name)
        restore = restore_track.restore_value if restore_track else None
        if restore is not None:
            restore = validate_cvar_value(name, restore)
        components = CVAR_COMPONENTS.get(name, 1)
        for component in range(components):
            values = [value[component] if components > 1 else value for value in authored]
            restore_component = restore[component] if components > 1 and restore is not None else restore
            for value in values + ([] if restore_component is None else [restore_component]):
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
            result.extend(TRACK_HEADER.pack(index, len(keys)-1, int(restore is not None), component,
                          times[0], times[-1], values[0], values[-1], restore_component if restore is not None else 0))
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


def supported_effects_catalog():
    """Portable list of authored controls supported by the native effect binder."""
    defaults = {"r_dof_override": 0, "r_dof_override_ranges": [0, 0, 0, 0],
                "r_dof_override_near_blurry": -100, "r_dof_override_near_crisp": 0,
                "r_dof_override_far_crisp": 180, "r_dof_override_far_blurry": 2000,
                "r_dof_override_tilt_to_ground": .5}
    controls = []
    for name, (effect_id, minimum, maximum, discrete) in EFFECTS.items():
        components = CVAR_COMPONENTS.get(name, 1)
        item = {"command": name, "components": components,
                "type": "vector4" if components == 4 else "integer" if name.endswith("_mode") else "boolean" if discrete else "float32",
                "interpolation": ["step"] if discrete else ["step", "linear", "smooth"],
                "frame_synchronized": True,
                "bounds": [minimum, maximum],
                "bounds_kind": "float32 representation" if minimum == -FLOAT32_MAX else "native range",
                "restore": "original full value unless a track restore value is entered"}
        if effect_id >= 7:
            item["verified_tier0_sha256"] = "b3192eac3cb8c54ac3f9c7aaf7c725ddfcc2dc46d99ba13d16177b6ebf736ebc"
            item["game_default"] = defaults[name]
        if components == 4:
            item["component_order"] = ["near blurry", "near crisp", "far crisp", "far blurry"]
            item["input_example"] = "-100 0 180 2000"
            item["note"] = "Four zeros disables the range override. All four components update together each rendered camera frame."
        controls.append(item)
    return {"format": "deadlock-dolly-supported-camera-cvars", "version": 1,
            "scope": "Native camera effects. Presence and exact native type are checked in the launched replay session; unsupported builds are rejected.",
            "framing": {"command": "r_aspectratio", "editor_range": [.5, 4],
                        "authored_in": "camera framing curve", "frame_synchronized": True},
            "controls": controls}
