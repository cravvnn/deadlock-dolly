"""Dolly-styled modal confirmations for desktop workflows."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import fonts
from .chrome import style_title_bar
from .scale import display_scale, px


def ask_reconstructed_helper(parent, *, size_mb: float) -> bool:
    """Ask once before the automatic reconstructed-audio helper download.

    This replaces the native Tk message box so the prompt uses Dolly's card
    palette, typography (Inter, or the Segoe UI fallback) and teal primary
    action instead of system dialog colours.
    """
    tokens = ui_theme.TOKENS
    dialog = tk.Toplevel(parent)
    style_title_bar(dialog)
    dialog.title("Reconstructed audio helper")
    dialog.configure(bg=tokens["bg"])
    dialog.resizable(False, False)
    dialog.transient(parent)
    result = {"value": False}

    def finish(value: bool) -> None:
        if not dialog.winfo_exists():
            return
        result["value"] = bool(value)
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    scale = display_scale(parent.tk)
    dp = lambda value: px(scale, value)  # noqa: E731
    wrap = dp(426)
    body = ttk.Frame(dialog, padding=dp((24, 22, 24, 20)))
    body.pack(fill="both", expand=True)
    heading = tk.Label(body, text="Reconstructed audio helper", bg=tokens["bg"], fg=tokens["text"],
                       font=fonts.font(dialog, "page_title"), anchor="w")
    heading.pack(fill="x", pady=dp((0, 15)))
    card = ttk.Frame(body, style="Rounded.Card.TFrame", padding=dp(20))
    card.pack(fill="both", expand=True)
    card.columnconfigure(0, weight=1)
    ttk.Label(card, text="RECONSTRUCTED AUDIO", style="Section.TLabel").grid(
        row=0, column=0, sticky="w")
    ttk.Label(card, text="One-time helper download", style="CardTitle.TLabel").grid(
        row=1, column=0, sticky="w", pady=dp((6, 12)))
    ttk.Label(
        card,
        text=(
            f"Reconstructed audio needs a one-time download of about {size_mb:.0f} MB "
            "from ValveResourceFormat. Dolly verifies the archive and the helper "
            "with SHA-256 before using them."
        ),
        style="Card.TLabel", wraplength=wrap, justify="left").grid(
            row=2, column=0, sticky="w")
    ttk.Label(
        card,
        text=(
            "The helper is cached for later exports. No account or manual setup is "
            "needed, and game-only audio works without it."
        ),
        style="CardMuted.TLabel", wraplength=wrap, justify="left").grid(
        row=3, column=0, sticky="w", pady=dp((12, 0)))
    actions = ttk.Frame(body)
    actions.pack(fill="x", pady=dp((29, 0)))
    buttons = ttk.Frame(actions)
    buttons.pack(side="right")
    ttk.Button(buttons, text="Not now", style="TButton",
               command=lambda: finish(False)).pack(side="left")
    download = ttk.Button(buttons, text="Download helper", style="Primary.TButton",
                          command=lambda: finish(True))
    download.pack(side="left", padx=dp((10, 0)))
    dialog.bind("<Escape>", lambda _event: finish(False))
    dialog.bind("<Return>", lambda _event: finish(True))
    dialog.update_idletasks()
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    try:
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        download.focus_set()
    except tk.TclError:
        pass
    dialog.wait_window()
    return result["value"]
