"""Shared Tk widgets; independent of page builders and application state."""
from __future__ import annotations

from tkinter import ttk

from .scale import scaler

# Spacing is in design px (100 %); builders convert it with the window's display scale.
GAP = 14


WHEEL_PIXELS_PER_NOTCH = 60
WHEEL_UNIT_PIXELS = 1


def card(parent, title):
    dp = scaler(parent)
    frame = ttk.Frame(parent, style="Rounded.Card.TFrame", padding=dp(14))
    frame.pack(fill="x", pady=dp((0, GAP)))
    ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=dp((0, GAP)))
    return frame

