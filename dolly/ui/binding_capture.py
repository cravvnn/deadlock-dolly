"""Shortcut recording without settings writes or global input hooks.

The recorder returns a validated settings proposal. Its caller owns explicit
confirmation and persistence; cancelling or hearing an invalid chord changes
nothing. Side-button observation is confined to the recorder's own window.
"""
from __future__ import annotations

from dataclasses import replace
import sys

from dolly.editor_actions import (
    BINDABLE_ORDER, EDITOR_KEY_CHOICES, MODIFIER_KEYS, MOVEMENT_ACTIONS,
    OBJECT_PICKER_ENABLED, EditorBinding,
)
from dolly.settings import AppSettings
from .widgets import safe_unbind


_UNSUPPORTED_MODIFIERS = frozenset(("Win_L", "Win_R", "Super_L", "Super_R", "Meta_L", "Meta_R"))
_KEYS_BY_VK = {EditorBinding(key).vk: key for key in EDITOR_KEY_CHOICES
               if key not in ("Mouse4", "Mouse5", "MiddleMouse")}


def binding_snapshot(settings: AppSettings) -> tuple:
    """A detached identity of controls, excluding unrelated preferences."""
    return tuple((name, settings.action_bindings[name]) for name in BINDABLE_ORDER) + (
        ("reshade", settings.reshade_binding),)


def _check_action(action: str) -> None:
    if action not in (*BINDABLE_ORDER, "reshade"):
        raise ValueError("Choose an existing control to change.")
    if action == "object_picker" and not OBJECT_PICKER_ENABLED:
        raise ValueError("The Object Picker control is not available.")


def binding_settings(settings: AppSettings, action: str, binding: EditorBinding | None,
                     *, expected_snapshot: tuple | None = None) -> AppSettings:
    """Validate one replacement/clear without moving any other shortcut."""
    _check_action(action)
    if expected_snapshot is not None and binding_snapshot(settings) != expected_snapshot:
        raise ValueError("Controls changed while this window was open. Record the shortcut again.")
    if binding is not None and not isinstance(binding, EditorBinding):
        raise ValueError("Choose a supported shortcut or clear the control.")
    if action == "reshade":
        return settings.with_reshade_binding(binding)
    bindings = dict(settings.action_bindings)
    bindings[action] = binding
    return settings.with_action_bindings(bindings)


def modifiers_from_event(event, *, platform: str | None = None) -> int:
    """Translate Tk's state into Dolly's stable Ctrl=1, Alt=2, Shift=4 bits."""
    state = getattr(event, "state", 0)
    if not isinstance(state, int):
        return 0
    # Windows Tk's Mod1 is NumLock, not Alt. The Windows port uses ALT_MASK
    # (AnyModifier << 2); X11 normally maps Alt to Mod1 instead.
    platform = sys.platform if platform is None else platform
    alt_mask = 0x20000 if platform == "win32" else 0x8
    return int(bool(state & 0x4)) | (int(bool(state & alt_mask)) << 1) | (int(bool(state & 0x1)) << 2)


def key_from_event(event, *, platform: str | None = None) -> str | None:
    """Translate supported keys; wheel/primary clicks cannot become binds.

    Windows Tk 8.6 does not consistently deliver XBUTTON events. The separate
    window-scoped observer handles side buttons there. X11 buttons 4/5 are
    scrolling, whereas 8/9 conventionally identify the side buttons.
    """
    platform = sys.platform if platform is None else platform
    number = getattr(event, "num", None)
    if isinstance(number, int):
        return {2: "MiddleMouse", **({8: "Mouse4", 9: "Mouse5"} if platform != "win32" else {})}.get(number)
    code = getattr(event, "keycode", None)
    if platform == "win32" and type(code) is int:
        # Native input observes these VKs too. Using the physical event code
        # preserves non-US layouts and shifted OEM punctuation without
        # guessing which key produced its printed character.
        return {0x76: "F7", 0x5B: "Win_L", 0x5C: "Win_R"}.get(code, _KEYS_BY_VK.get(code))
    key = str(getattr(event, "keysym", ""))
    aliases = {
        "space": "Space", "Return": "Enter", "KP_Enter": "Enter",
        "Prior": "PageUp", "Next": "PageDown",
        "Control_L": "Ctrl", "Control_R": "Ctrl", "Shift_L": "Shift", "Shift_R": "Shift",
        "Alt_L": "Alt", "Alt_R": "Alt", "comma": "Comma", "less": "Comma",
        "period": "Period", "greater": "Period", "minus": "Minus", "underscore": "Minus",
        "equal": "Equals", "plus": "Equals", "bracketleft": "LeftBracket", "braceleft": "LeftBracket",
        "bracketright": "RightBracket", "braceright": "RightBracket",
        **dict(zip(("parenright", "exclam", "at", "numbersign", "dollar", "percent", "asciicircum",
                    "ampersand", "asterisk", "parenleft"), "0123456789")),
    }
    key = aliases.get(key, key.upper() if len(key) == 1 else key)
    return key if key in EDITOR_KEY_CHOICES or key == "F7" or key in _UNSUPPORTED_MODIFIERS else None


class BindingCapture:
    """One listening -> review -> explicit-save interaction.

    Modifier-only movement binds settle on release. Releasing a modifier used
    in a chord never creates a second candidate, including after a conflict.
    """

    def __init__(self, settings: AppSettings, action: str):
        _check_action(action)
        self.action = action
        self._settings = replace(settings)
        self.snapshot = binding_snapshot(self._settings)
        self.cancelled = False
        self.retry()

    @property
    def ready(self) -> bool:
        return self.candidate is not None and not self.cancelled and not self.listening

    def retry(self) -> None:
        if self.cancelled:
            return
        self.candidate = None
        self.error = ""
        self.listening = True
        self._pending_modifier = None
        self._modifier_used = False
        self._blocked_modifiers = set()

    def cancel(self) -> None:
        self.cancelled = True
        self.listening = False
        self.candidate = None
        self._pending_modifier = None

    def _capture(self, key: str, modifiers: int) -> bool:
        try:
            if type(modifiers) is not int or modifiers & ~7 or modifiers < 0:
                raise ValueError("Choose Ctrl, Alt or Shift with a supported key.")
            binding = EditorBinding(key, bool(modifiers & 1), bool(modifiers & 2), bool(modifiers & 4))
            binding_settings(self._settings, self.action, binding)
        except ValueError as exc:
            self.error = str(exc)
            self.candidate = None
            return False
        self.candidate = binding
        self.error = ""
        self.listening = False
        return True

    def press(self, key: str | None, modifiers: int = 0) -> bool:
        if not self.listening or self.cancelled:
            return False
        if key == "Escape":
            self.cancel()
            return False
        if key in _UNSUPPORTED_MODIFIERS:
            self._blocked_modifiers.add(key)
            self._modifier_used = True
            self.error = "Windows and Super key combinations are not supported. Release them and try again."
            return False
        if self._blocked_modifiers:
            self._modifier_used = True
            self.error = "Windows and Super key combinations are not supported. Release them and try again."
            return False
        if key in MODIFIER_KEYS:
            own_bit = {"Ctrl": 1, "Alt": 2, "Shift": 4}[key]
            if self._pending_modifier is None:
                self._pending_modifier = key
                self._modifier_used = bool(modifiers & ~own_bit)
            elif key != self._pending_modifier:
                self._modifier_used = True
            return False
        self._modifier_used = True
        if key is None:
            self.error = "Choose a supported keyboard key, middle mouse or side mouse button."
            return False
        return self._capture(key, modifiers)

    def release(self, key: str | None) -> bool:
        self._blocked_modifiers.discard(key)
        if not self.listening or self.cancelled or key != self._pending_modifier:
            return False
        self._pending_modifier = None
        if self._modifier_used:
            return False
        if self.action not in MOVEMENT_ACTIONS:
            self.error = "This control needs a keyboard key or mouse button, not a modifier alone."
            return False
        return self._capture(key, 0)

    def focus_lost(self) -> None:
        """A modifier begun before switching away cannot settle on return."""
        self._pending_modifier = None
        self._modifier_used = True

    def bind_escape(self, modifiers: int = 0) -> bool:
        """Explicit route because pressing Escape itself always cancels."""
        if not self.listening or self.cancelled:
            return False
        if self._blocked_modifiers:
            self.error = "Release the Windows or Super key before recording a shortcut."
            return False
        self._modifier_used = True
        return self._capture("Escape", modifiers)

    def settings_for(self, current: AppSettings) -> AppSettings:
        if not self.ready:
            raise ValueError("Record a shortcut before saving.")
        return binding_settings(current, self.action, self.candidate, expected_snapshot=self.snapshot)


class _WindowsSideMouseAPI:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.user32.GetForegroundWindow.argtypes = ()
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.IsWindow.argtypes = (wintypes.HWND,)
        self.user32.IsWindow.restype = wintypes.BOOL

    def focused(self, dialog_hwnd: int) -> bool:
        if not self.user32.IsWindow(dialog_hwnd):
            return False
        dialog_root = self.user32.GetAncestor(dialog_hwnd, 2)  # GA_ROOT; Tk wrapper HWND
        foreground = self.user32.GetForegroundWindow()
        return bool(dialog_root and foreground and self.user32.GetAncestor(foreground, 2) == dialog_root)

    def down(self, vk: int) -> bool:
        return bool(self.user32.GetAsyncKeyState(vk) & 0x8000)


class SideMouseCapture:
    """Tk-thread side-button observer active only for the dialog HWND.

    It observes state without registering, consuming or synthesizing input.
    Every open/refocus requires release before a button can be recorded.
    Destroying/stopping the dialog cancels the scheduled callback.
    """

    def __init__(self, dialog, callback, *, api=None, interval_ms: int = 15):
        self.dialog = dialog
        self.callback = callback
        self.api = api
        self.interval_ms = interval_ms
        self.running = False
        self._after = None
        self._armed = {0x05: False, 0x06: False}
        self._destroy_binding = None

    def start(self) -> bool:
        if self.running:
            return True
        if self.api is None:
            if sys.platform != "win32":
                return False
            self.api = _WindowsSideMouseAPI()
        self.running = True
        self._armed = {0x05: False, 0x06: False}
        self._destroy_binding = self.dialog.bind("<Destroy>", self._destroyed, add="+")
        self._poll()
        return self.running

    def _destroyed(self, event) -> None:
        if getattr(event, "widget", None) is self.dialog:
            self.stop()

    def keyboard_blocked(self) -> bool:
        """Guard a Win key already held before the recorder opened.

        Called only from this dialog's key handler, without observing input
        while another window owns focus. Non-Windows Tk uses key events.
        """
        if self.api is None:
            return False
        if not self.dialog.winfo_exists() or not self.api.focused(int(self.dialog.winfo_id())):
            return True
        return bool(self.api.down(0x5B) or self.api.down(0x5C))

    def stop(self) -> None:
        self.running = False
        if self._after is not None:
            token, self._after = self._after, None
            try:
                self.dialog.after_cancel(token)
            except Exception:
                pass
        if self._destroy_binding is not None:
            token, self._destroy_binding = self._destroy_binding, None
            try:
                safe_unbind(self.dialog, "<Destroy>", token)
            except Exception:
                pass

    def _poll(self) -> None:
        self._after = None
        if not self.running:
            return
        try:
            if not self.dialog.winfo_exists():
                self.stop()
                return
            focused = self.api.focused(int(self.dialog.winfo_id()))
            blocked = focused and (self.api.down(0x5B) or self.api.down(0x5C))
            if not focused or blocked:
                self._armed = {0x05: False, 0x06: False}
            else:
                modifiers = (int(self.api.down(0x11)) | (int(self.api.down(0x12)) << 1)
                             | (int(self.api.down(0x10)) << 2))
                for vk, key in ((0x05, "Mouse4"), (0x06, "Mouse5")):
                    down = self.api.down(vk)
                    if not down:
                        self._armed[vk] = True
                    elif self._armed[vk]:
                        self._armed[vk] = False
                        # Repeat the ownership check at the callback boundary.
                        if self.running and self.api.focused(int(self.dialog.winfo_id())):
                            self.callback(key, modifiers)
                    if not self.running:
                        break
            if self.running:
                self._after = self.dialog.after(self.interval_ms, self._poll)
        except Exception:
            self.stop()
            raise
