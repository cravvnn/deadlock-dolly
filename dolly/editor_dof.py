"""In-game controls for the existing authored native range DOF channels."""
from copy import deepcopy
from dataclasses import replace
import math

from .path import Keyframe, TrackKey, validate_cvar_value
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
    track = next((track for track in candidate.tracks if track.name == name), None)
    if component is not None:
        previous = _cvars_at(candidate, time).get(name, (0, 0, 0, 0))
        vector = list(previous)
        vector[component] = value
        value = vector
    value = validate_cvar_value(name, value)
    if control < 2 and value not in (0, 1):
        raise ValueError("DOF switches require zero or one")
    if track and track.keys:
        track.keys = [key for key in track.keys if key.time != time]
        track.keys.append(TrackKey(time, value))
        track.keys.sort(key=lambda key: key.time)
    else:
        candidate.setup_values[name] = value
    candidate.validate()
    return candidate
