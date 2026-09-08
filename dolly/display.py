"""Read the launched game's client aspect without changing its window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


def client_aspect_ratio(pid):
    """Return the largest visible client area's ratio, or None if unavailable.

    Window coordinates are used only as a fallback for r_aspectratio's automatic
    mode. This does not infer a custom internal render viewport or letterboxing.
    """
    if os.name != "nt" or not pid or int(pid) <= 0:
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        candidates = []

        @callback_type
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == int(pid) and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
                rect = wintypes.RECT()
                if user32.GetClientRect(hwnd, ctypes.byref(rect)):
                    width, height = rect.right - rect.left, rect.bottom - rect.top
                    if width > 0 and height > 0:
                        candidates.append((width * height, width / height))
            return True

        if not user32.EnumWindows(visit, 0) or not candidates:
            return None
        return max(candidates)[1]
    except (OSError, AttributeError, TypeError, ValueError):
        return None
