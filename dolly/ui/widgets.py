"""Shared Tk widgets; independent of page builders and application state."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .scale import scaler

# Spacing is in design px (100 %); builders convert it with the window's display scale.
GAP = 14


WHEEL_PIXELS_PER_NOTCH = 60
WHEEL_UNIT_PIXELS = 1


def safe_unbind(widget, sequence, funcid, tag=None) -> None:
    """Remove only ``funcid`` from ``sequence`` on ``widget`` (or its bind ``tag``), keeping the others.

    Older tkinter's ``unbind`` with a funcid drops every handler of the sequence,
    and ``unbind_class`` takes no funcid.
    """
    try:
        target = widget._w if tag is None else tag
        script = str(widget.tk.call("bind", target, sequence))
        keep = "\n".join(line for line in script.split("\n") if funcid not in line)
        widget.tk.call("bind", target, sequence, keep if keep.strip() else "")
        widget.deletecommand(funcid)
    except tk.TclError:
        pass


def card(parent, title):
    dp = scaler(parent)
    frame = ttk.Frame(parent, style="Rounded.Card.TFrame", padding=dp(14))
    frame.pack(fill="x", pady=dp((0, GAP)))
    ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=dp((0, GAP)))
    return frame

