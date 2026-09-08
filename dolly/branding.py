"""Optional desktop branding; a missing icon must never prevent startup."""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import tkinter as tk
from .runtime import resource_root

LOG = logging.getLogger(__name__)
ASSETS = resource_root(Path(__file__).resolve().parents[1]) / "assets"
APP_ID = "DeadlockDolly.Desktop"


def set_taskbar_identity() -> bool:
    """Give Dolly its own Windows taskbar group before creating any UI."""
    if os.name != "nt":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        function = shell.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        result = function(APP_ID)
        if result != 0:
            LOG.warning("Windows taskbar identity returned HRESULT %s", result)
            return False
        return True
    except (OSError, AttributeError, TypeError):
        LOG.warning("Could not apply the Dolly taskbar identity", exc_info=True)
        return False


def apply_window_icon(root: tk.Tk) -> None:
    """Set the main window and future dialogs' default icon."""
    if os.name == "nt":
        icon = str(ASSETS / "dolly.ico")
        try:
            # Use bitmap-backed ICO frames: older Tk 8.6 Windows readers treat
            # PNG frame headers as BITMAPINFOHEADER, corrupting their sizes.
            # Set this window explicitly as well as the future-window default.
            root.iconbitmap(icon)
        except (OSError, tk.TclError):
            LOG.warning("Could not load the Dolly Windows icon; trying PNG", exc_info=True)
        else:
            try:
                root.iconbitmap(default=icon)
            except (OSError, tk.TclError):
                LOG.warning("Could not set the default dialog icon", exc_info=True)
            LOG.info("Applied Dolly Windows icon: %s", icon)
            return  # iconphoto would replace the successfully loaded ICO.
    try:
        photo = tk.PhotoImage(master=root, file=str(ASSETS / "dolly.png"))
        photos = (photo.subsample(16), photo.subsample(8), photo.subsample(4), photo)
        root.iconphoto(True, *photos)
        root._dolly_icon_photo = photo
        root._dolly_icon_photos = photos  # Keep every Tk image alive.
        LOG.info("Applied Dolly PNG icon fallback")
    except (OSError, tk.TclError):
        LOG.warning("Could not load the Dolly PNG icon", exc_info=True)
