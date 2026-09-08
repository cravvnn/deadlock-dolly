"""Session-only paused-camera controls, sampled by the camera worker.

GUI events supply only movement keys while their control pad owns focus.
Optional Windows input observes the launched game's foreground window. No
hooks are installed and no game input is consumed or synthesized.
"""
from __future__ import annotations

import threading
from typing import Callable

from .hotkey import _WindowsHotkeyAPI
from .navigation import CameraMotion


_KEYS = {
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44,
    "space": 0x20, "ctrl": 0x11, "shift": 0x10,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "escape": 0x1B, "alt": 0x12, "win_left": 0x5B, "win_right": 0x5C,
}
_BLOCKERS = frozenset(("alt", "win_left", "win_right"))
_ALIASES = {
    "control_l": "ctrl", "control_r": "ctrl", "control": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt",
    "super_l": "win_left", "super_r": "win_right",
    "win_l": "win_left", "win_r": "win_right",
}


def _key_name(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("A camera control must be a key name.")
    key = key.lower()
    key = _ALIASES.get(key, key)
    if key not in _KEYS:
        raise ValueError(f"Unknown camera control: {key}")
    return key


def _motion(keys: set[str]) -> CameraMotion:
    if keys & _BLOCKERS:
        return CameraMotion()
    return CameraMotion(
        forward=float(("w" in keys) - ("s" in keys)),
        right=float(("d" in keys) - ("a" in keys)),
        up=float(("space" in keys) - ("ctrl" in keys)),
        yaw=float(("left" in keys) - ("right" in keys)),
        pitch=float(("down" in keys) - ("up" in keys)),
        boost="shift" in keys,
        stop="escape" in keys,
    )


class CameraInput:
    """Thread-safe motion source shared by the GUI and flight worker.

    ``clear`` also re-arms game input: keys held on enabling, switching focus,
    or changing cameras must be released before they can move the camera.
    GUI-only controls work on any platform; Windows polling is opt-in.
    """

    def __init__(self, game_pid: Callable[[], int | None]) -> None:
        self._game_pid = game_pid
        self._lock = threading.Lock()
        self._gui_focus = False
        self._gui_keys: set[str] = set()
        self._game_enabled = False
        self._api: _WindowsHotkeyAPI | None = None
        self._focused_pid: int | None = None
        self._blocked: set[str] = set()
        self._closed = False

    def set_game_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("In-game camera input must be enabled or disabled.")
        with self._lock:
            if self._closed:
                return
            if enabled and self._api is None:
                try:
                    self._api = _WindowsHotkeyAPI()
                except Exception as exc:
                    raise OSError(
                        "In-game camera keys require Windows. "
                        "Use the movement pad in Dolly if they are unavailable."
                    ) from exc
            self._game_enabled = enabled
            self._focused_pid = None
            self._blocked.clear()

    def set_gui_focus(self, focused: bool) -> None:
        with self._lock:
            self._gui_focus = bool(focused) and not self._closed
            if not self._gui_focus:
                self._gui_keys.clear()

    def press(self, key: str) -> None:
        _key_name(key)
        with self._lock:
            if self._gui_focus and not self._closed:
                # Keep physical modifier identities until sampling so releasing
                # one Ctrl/Shift does not release its still-held counterpart.
                self._gui_keys.add(key.lower())

    def release(self, key: str) -> None:
        _key_name(key)
        with self._lock:
            self._gui_keys.discard(key.lower())

    def clear(self) -> None:
        with self._lock:
            self._gui_keys.clear()
            self._focused_pid = None
            self._blocked.clear()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._game_enabled = False
            self._gui_focus = False
            self._gui_keys.clear()
            self._focused_pid = None
            self._blocked.clear()
            self._api = None

    def sample(self) -> CameraMotion:
        with self._lock:
            if self._closed:
                return CameraMotion()
            if self._gui_focus:
                # Switching back to the game must establish a fresh baseline.
                self._focused_pid = None
                self._blocked.clear()
                return _motion({_key_name(key) for key in self._gui_keys})
            if not self._game_enabled or self._api is None:
                return CameraMotion()
            api = self._api
            try:
                pid = self._game_pid()
                if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                        or api.foreground_pid() != pid):
                    self._focused_pid = None
                    self._blocked.clear()
                    return CameraMotion()
                held = {key for key, vk in _KEYS.items() if api.key_down(vk)}
                # Recheck focus after the key queries, before issuing movement.
                if api.foreground_pid() != pid or self._game_pid() != pid:
                    self._focused_pid = None
                    self._blocked.clear()
                    return CameraMotion()
            except Exception as exc:
                self._game_enabled = False
                self._focused_pid = None
                self._blocked.clear()
                raise RuntimeError(
                    "Paused-camera input stopped because the game window or "
                    "keyboard state could not be read."
                ) from exc
            if self._focused_pid != pid:
                self._focused_pid = pid
                self._blocked = set(held)
                return CameraMotion()
            self._blocked.intersection_update(held)
            if held & _BLOCKERS:
                self._blocked.update(held)
                return CameraMotion()
            return _motion(held - self._blocked)
