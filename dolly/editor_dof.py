"""In-game controls for the existing authored native range DOF channels."""
from copy import deepcopy
from dataclasses import replace
import math

from .path import CvarTrack, Keyframe, TrackKey, validate_cvar_value
from .native_effects import EFFECTS

# Appended action IDs 41..51; vector components remain one authored track.
CONTROLS = (
    ("r_depth_of_field", None, 1),
    ("r_dof_override", None, 0),
    *(("r_dof_override_ranges", component, 0) for component in range(4)),
    ("r_dof_override_near_blurry", None, -100),
    ("r_dof_override_near_crisp", None, 0),
    ("r_dof_override_far_crisp", None, 180),
    ("r_dof_override_far_blurry", None, 2000),
    ("r_dof_override_tilt_to_ground", None, .5),
)
ACTIONS = tuple(f"set_dof_{index}" for index in range(len(CONTROLS)))
RANGE_NAME = "r_dof_override_ranges"
DEFAULT_RANGES = (-100.0, 0.0, 180.0, 2000.0)


def _put_value(project, name, time, value):
    track = next((track for track in project.tracks if track.name == name), None)
    if track and track.keys:
        track.keys = [key for key in track.keys if key.time != time]
        track.keys.append(TrackKey(time, value))
        track.keys.sort(key=lambda key: key.time)
    else:
        project.setup_values[name] = value


def _enable_ranges(project, time, enabled):
    # An animated switch overrides setup_values, so edit its key too when present.
    for name in ("r_depth_of_field", "r_dof_override"):
        _put_value(project, name, time, float(enabled))
    if not enabled:
        return  # Turning off never destroys an authored focus pull.
    values = _cvars_at(project, time).get(RANGE_NAME, (0, 0, 0, 0))
    track = next((track for track in project.tracks if track.name == RANGE_NAME), None)
    if not track or not track.keys:
        # Match the desktop preset, retaining prior fixed edits or restore metadata.
        ranges = tuple(values) if any(values) else DEFAULT_RANGES
        end = max(project.duration, time)
        end = end if end > 0 else 5.0
        if track is None:
            track = CvarTrack(RANGE_NAME, interpolation="smooth")
            project.tracks.append(track)
        track.keys = [TrackKey(0.0, ranges), TrackKey(end, ranges)]
    elif not any(values):
        # Four zeros explicitly disables range override. Seed this playhead only;
        # preserve the rest of an existing animated track and its restore value.
        _put_value(project, RANGE_NAME, time, DEFAULT_RANGES)


def values_at(project, time):
    values = _cvars_at(project, time)
    return tuple(values.get(name, default) if component is None
                 else values.get(name, (0, 0, 0, 0))[component]
                 for name, component, default in CONTROLS)


def _cvars_at(project, time):
    view = project if project.keyframes else replace(project, keyframes=[Keyframe(0, 0, 0, 0, 0, 0, 0)])
    return view.evaluate(time)["cvars"]


def edited_project(project, time, control, value):
    if (type(control) is not int or not 0 <= control < len(CONTROLS)
            or not math.isfinite(time) or time < 0):
        raise ValueError("Invalid DOF control or shot time")
    name, component, _default = CONTROLS[control]
    _id, minimum, maximum, discrete = EFFECTS[name]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum
            or (discrete and value != int(value))):
        raise ValueError("DOF value is outside the native control's range")
    candidate = deepcopy(project)
    if control == 0:
        _enable_ranges(candidate, time, bool(value))
        candidate.validate()
        return candidate
    if component is not None:
        previous = _cvars_at(candidate, time).get(name, (0, 0, 0, 0))
        vector = list(previous)
        vector[component] = value
        value = vector
    value = validate_cvar_value(name, value)
    if control < 2 and value not in (0, 1):
        raise ValueError("DOF switches require zero or one")
    _put_value(candidate, name, time, value)
    candidate.validate()
    return candidate
