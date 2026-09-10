"""Portable editor actions shared by the launcher and the native input bridge.

Action IDs are an ABI: append future actions; never reorder the existing tuple.
F7 always belongs to the game console and is deliberately not configurable here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .bindings import CaptureBinding, DEFAULT_BINDING, KEY_CHOICES


ACTION_ORDER = (
    "capture", "replace", "play_pause", "play_path", "stop", "previous_view",
    "next_view", "seek_back", "seek_forward", "panel", "game_ui", "flight",
    "move_forward", "move_back", "move_left", "move_right", "move_up", "move_down",
    "move_fast", "move_slow", "look_left", "look_right", "look_up", "look_down",
    "roll_left", "roll_right",
)
ACTION_IDS = {name: index for index, name in enumerate(ACTION_ORDER)}
ACTION_LABELS = dict(zip(ACTION_ORDER, (
    "Capture camera", "Replace selected camera", "Pause / resume replay", "Play camera path",
    "Stop / restore", "Previous camera", "Next camera", "Seek backward", "Seek forward",
    "Toggle Dolly panel", "Toggle game UI", "Enter paused flight", "Move forward",
    "Move backward", "Move left", "Move right", "Move up", "Move down", "Move faster",
    "Move slowly", "Look left", "Look right", "Look up", "Look down", "Roll left", "Roll right",
)))
MOVEMENT_ACTIONS = frozenset(ACTION_ORDER[12:])
MODIFIER_KEYS = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10}
_EXTRA_KEYS = {**MODIFIER_KEYS, "Comma": 0xBC, "Period": 0xBE,
               "Minus": 0xBD, "Equals": 0xBB, "LeftBracket": 0xDB, "RightBracket": 0xDD}
EDITOR_KEY_CHOICES = tuple(key for key in KEY_CHOICES if key != "F7") + tuple(_EXTRA_KEYS)
CONSOLE_KEY = "F7"
CONSOLE_VK = 0x76


@dataclass(frozen=True)
class EditorBinding:
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or self.key not in EDITOR_KEY_CHOICES:
            if self.key == CONSOLE_KEY:
                raise ValueError("F7 is reserved for the game console. Choose another editor key.")
            raise ValueError("Choose a supported keyboard key or Mouse4, Mouse5 or MiddleMouse.")
        for name in ("ctrl", "alt", "shift"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Editor modifier {name} must be true or false.")
        if self.key in MODIFIER_KEYS and self.modifiers:
            raise ValueError("A modifier used as a movement key cannot have extra modifiers.")

    @property
    def vk(self) -> int:
        if self.key in _EXTRA_KEYS:
            return _EXTRA_KEYS[self.key]
        return CaptureBinding(self.key, False, False, False).vk

    @property
    def modifiers(self) -> int:
        return int(self.ctrl) | (int(self.alt) << 1) | (int(self.shift) << 2)

    @property
    def label(self) -> str:
        return "+".join([name for name, active in (
            ("Ctrl", self.ctrl), ("Alt", self.alt), ("Shift", self.shift),
        ) if active] + [self.key])

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "ctrl": self.ctrl, "alt": self.alt, "shift": self.shift}

    @classmethod
    def from_dict(cls, value: Any) -> EditorBinding:
        if not isinstance(value, dict) or set(value) != {"key", "ctrl", "alt", "shift"}:
            raise ValueError("Editor binding must contain only key, ctrl, alt and shift.")
        return cls(**value)


def default_action_bindings(capture_binding: CaptureBinding = DEFAULT_BINDING) -> dict[str, EditorBinding | None]:
    if not isinstance(capture_binding, CaptureBinding):
        raise ValueError("capture_binding must be a CaptureBinding.")
    result = {name: EditorBinding(key) for name, key in zip(ACTION_ORDER, (
        "K", "R", "P", "F5", "F6", "PageUp", "PageDown", "Comma", "Period",
        "F8", "F9", "F10", "W", "S", "A", "D", "Space", "Ctrl", "Shift", "Alt",
        "Left", "Right", "Up", "Down", "Q", "E",
    ))}
    result["capture"] = EditorBinding.from_dict(capture_binding.to_dict())
    result["replace"] = EditorBinding("R", ctrl=True, alt=True)
    return result


def validate_action_bindings(bindings: Mapping[str, EditorBinding | CaptureBinding | None]) -> dict[str, EditorBinding | None]:
    """Return a detached, complete map; reject ambiguous simultaneous chords.

    Missing actions use defaults. ``None`` explicitly unbinds an action. Movement
    keys use their own flight-only input context, but exact duplicates remain
    invalid because editor shortcuts are also available during flight.
    """
    if not isinstance(bindings, Mapping):
        raise ValueError("Editor bindings must be an action-to-binding mapping.")
    unknown = set(bindings) - set(ACTION_ORDER)
    if unknown:
        raise ValueError("Unknown editor action: " + ", ".join(sorted(str(v) for v in unknown)))
    result = default_action_bindings()
    assigned: dict[tuple[int, int], str] = {}
    for name in ACTION_ORDER:
        value = bindings.get(name, result[name])
        if isinstance(value, CaptureBinding):
            value = EditorBinding.from_dict(value.to_dict())
        if value is not None and not isinstance(value, EditorBinding):
            raise ValueError(f"{ACTION_LABELS[name]} must be an EditorBinding or None.")
        if value is not None:
            value = EditorBinding.from_dict(value.to_dict())
            if value.key in MODIFIER_KEYS and name not in MOVEMENT_ACTIONS:
                raise ValueError(f"{ACTION_LABELS[name]} needs a keyboard key or mouse button, not a modifier alone.")
            if name == "capture":
                # The external compatibility listener still shares this value.
                CaptureBinding.from_dict(value.to_dict())
            identity = (value.vk, value.modifiers)
            if identity in assigned:
                previous = assigned[identity]
                raise ValueError(f"{value.label} is assigned to both {ACTION_LABELS[previous]} and {ACTION_LABELS[name]}. Choose a different binding or clear one.")
            assigned[identity] = name
        result[name] = value
    return result


def bindings_to_dict(bindings: Mapping[str, EditorBinding | CaptureBinding | None]) -> dict[str, dict[str, Any] | None]:
    return {name: value.to_dict() if value is not None else None
            for name, value in validate_action_bindings(bindings).items()}


def bindings_from_dict(value: Any) -> dict[str, EditorBinding | None]:
    if not isinstance(value, dict):
        raise ValueError("Editor bindings must be a JSON object.")
    return validate_action_bindings({name: EditorBinding.from_dict(raw) if raw is not None else None
                                     for name, raw in value.items()})
