"""Versioned native-editor messages, separate from the camera path payload.

The editor owns configuration and acknowledgements. The game owns its status
and bounded event ring. No console command strings cross this interface.
"""
from __future__ import annotations

import math
import struct

CONFIG_OFFSET = 576
STATUS_OFFSET = 2 * 1024 * 1024 + 2048
EDITOR_ABI = 2
CONFIG_MAGIC = b"DLYEDIT1"
STATUS_MAGIC = b"DLYEDS01"
CONFIG = struct.Struct("<8s10I2d52H96s128s2diIdI2H16s")
HEADER = struct.Struct("<8s8Id7d128sdiIQ")
EVENT = struct.Struct("<IId7diI")
EVENT_COUNT = 16
STATUS_BYTES = HEADER.size + EVENT.size * EVENT_COUNT
OWNERS = ("disabled", "flight", "panel", "game_ui", "console", "unfocused", "reshade")
EXTRA_ACTIONS = ("console", "set_speed", "select_view", "set_playback_speed", "set_playback_rate", "reshade", "start_video", "stop_video")


def _text(value, capacity):
    return str(value).replace("\0", "").encode("utf-8")[:capacity - 1].decode("utf-8", errors="ignore").encode("utf-8")


def _finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid native editor {name}")
    return float(value)


def _uint(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xffffffff:
        raise ValueError(f"Invalid native editor {name}")
    return value


def pack_config(sequence, owner_sequence, ack_event, values):
    from .editor_actions import ACTION_ORDER, default_action_bindings, validate_action_bindings
    from .settings import DEFAULT_RESHADE_BINDING, validate_reshade_binding
    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Editor enabled must be a boolean")
    owner = values.get("owner", "disabled")
    if owner not in OWNERS or owner == "unfocused":
        raise ValueError("Unknown native editor input owner")
    bindings = validate_action_bindings(values.get("bindings") or default_action_bindings())
    reshade = validate_reshade_binding(values.get("reshade_binding", DEFAULT_RESHADE_BINDING), bindings)
    wire = []
    for name in ACTION_ORDER:
        binding = bindings[name]
        wire.extend((0, 0) if binding is None else (binding.vk, int(binding.ctrl) | int(binding.alt) << 1 | int(binding.shift) << 2))
    if len(wire) != 52:
        raise ValueError("Editor action table does not match the native build")
    count = _uint(values.get("camera_count", 0), "camera count")
    selected = _uint(values.get("selected_camera", 0), "selected camera")
    if selected >= max(1, count):
        raise ValueError("Selected native camera is out of range")
    tick = values.get("replay_tick", 0)
    if isinstance(tick, bool) or not isinstance(tick, int) or not -0x80000000 <= tick <= 0x7fffffff:
        raise ValueError("Invalid replay tick")
    playback_rate = _uint(values.get("playback_rate", 60), "playback update rate")
    if playback_rate not in (30, 60, 120):
        raise ValueError("Native editor playback update rate must be 30, 60, or 120")
    return CONFIG.pack(
        CONFIG_MAGIC, _uint(sequence, "sequence"), EDITOR_ABI, int(enabled), OWNERS.index(owner),
        _uint(owner_sequence, "owner sequence"), selected, count, _uint(ack_event, "event acknowledgement"),
        int(bool(values.get("invert_y", False))), 0,
        _finite(values.get("speed", 320.0), 1, 10000, "movement speed"),
        _finite(values.get("sensitivity", .12), .001, 10, "mouse sensitivity"),
        *wire, _text(values.get("shot_name", "Untitled shot"), 96), _text(values.get("message", ""), 128),
        _finite(values.get("duration", 0), 0, 1e9, "duration"),
        _finite(values.get("playhead", 0), 0, 1e9, "playhead"), tick,
        int(bool(values.get("playing", False))) | int(bool(values.get("busy", False))) << 1,
        _finite(values.get("playback_speed", 1.0), .05, 4, "playback speed"),
        playback_rate, 0 if reshade is None else reshade.vk,
        0 if reshade is None else reshade.modifiers, b"\0" * 16)


def unpack_status(data, ack_event=0):
    from .editor_actions import ACTION_ORDER
    if len(data) != STATUS_BYTES:
        raise ValueError("Native editor returned the wrong status size")
    if data == b"\0" * STATUS_BYTES:
        return {"ready": False, "enabled": False, "input_mode": "disabled", "events": [], "last_event": 0}
    h = HEADER.unpack_from(data)
    magic, seq, abi, owner, flags, selected, count, latest, dropped = h[:9]
    if magic != STATUS_MAGIC or abi != EDITOR_ABI or seq & 1 or owner >= len(OWNERS) or flags & ~127:
        raise ValueError("Native editor protocol or input state does not match this build")
    speed, pose, message, phase, tick, reserved, frames = h[9], list(h[10:17]), h[17], *h[18:]
    if not all(math.isfinite(x) for x in [speed, phase, *pose]) or not 0 <= speed <= 10000:
        raise ValueError("Native editor returned invalid camera data")
    events = []
    action_names = ACTION_ORDER + EXTRA_ACTIONS
    for index in range(EVENT_COUNT):
        fields = EVENT.unpack_from(data, HEADER.size + EVENT.size * index)
        serial, action, value, *tail = fields
        if not serial or serial <= ack_event:
            continue
        event_pose, event_tick, paused = tail[:7], tail[7], tail[8]
        if serial > latest or action >= len(action_names) or paused not in (0, 1) or not all(math.isfinite(x) for x in [value, *event_pose]):
            raise ValueError("Native editor returned an invalid action")
        if action in (0, 1) and not .25 <= event_pose[6] <= 8:
            raise ValueError("Native capture returned an invalid framing value")
        events.append({"sequence": serial, "action": action_names[action], "action_id": action,
                       "value": value, "pose": event_pose, "tick": event_tick, "paused": bool(paused)})
    events.sort(key=lambda event: event["sequence"])
    serials = [e["sequence"] for e in events]
    if len(set(serials)) != len(serials) or (serials and serials != list(range(ack_event + 1, serials[-1] + 1))):
        raise ValueError("Native editor action queue lost synchronization; stop and reconnect the editor")
    return {"ready": bool(flags & 16), "enabled": bool(flags & 1), "focused": bool(flags & 2),
            "paused": bool(flags & 4), "flight_active": bool(flags & 8), "overlay_available": bool(flags & 32),
            "input_available": bool(flags & 64), "input_mode": OWNERS[owner],
            "console_open": owner == 4, "game_ui": owner == 3, "reshade_open": owner == 6,
            "selected_camera": selected, "camera_count": count, "last_event": latest, "dropped_events": dropped,
            "speed": speed, "applied_pose": pose, "message": message.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
            "visualization_state": ({0: "disconnected", 1: "waiting", 2: "ready", 3: "disabled",
                                     4: "invalid", 5: "updating", 6: "unavailable"}.get(reserved, "unknown")),
            "phase": phase, "tick": tick, "frame_count": frames, "events": events}
