"""One display-scale source for the desktop editor.

Layout numbers are written in design pixels (the Figma frame at 100 %) and pass
through :func:`px`. The scale is the same value ``DollyApp.ui_scale`` uses, so
the canvas shell and the ttk pages agree on every size. The app declares
system DPI awareness before its first window (:func:`enable_dpi_awareness`),
so on a 150 % display Tk reports the real 144 DPI, the scale reads 1.5, and
Windows draws the window sharp instead of stretching a 96 DPI bitmap.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_theme.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import os
import sys

BUCKETS = ("1", "1.5", "2")
DPI_AWARE_ENV = "DOLLY_UI_DPI_AWARE"
_awareness = None


def enable_dpi_awareness() -> bool:
    """Draw at the display's real resolution instead of being stretched by Windows.

    Call before the first Tk window of the process (Tk reads the display's DPI
    when that window opens); later calls return the first answer. System
    aware (1), not per monitor: Tk 8.6 does not re-layout when a window moves to
    a monitor with another scale, so Windows still stretches it there.
    ``DOLLY_UI_DPI_AWARE=0`` keeps the old, stretched behaviour. Returns whether
    the process is DPI aware afterwards.
    """
    global _awareness
    if _awareness is not None:
        return _awareness
    _awareness = False
    if sys.platform != "win32" or os.environ.get(DPI_AWARE_ENV, "1") == "0":
        return _awareness
    import ctypes
    try:
        shcore = ctypes.windll.shcore
        # E_ACCESSDENIED means a manifest or the host already chose a mode: report that one.
        shcore.SetProcessDpiAwareness(1)
        mode = ctypes.c_int(0)
        _awareness = shcore.GetProcessDpiAwareness(None, ctypes.byref(mode)) == 0 and mode.value > 0
    except (AttributeError, OSError):
        try:
            _awareness = bool(ctypes.windll.user32.SetProcessDPIAware())
        except (AttributeError, OSError):
            _awareness = False
    return _awareness


def display_scale(interpreter) -> float:
    """Display scale a Tk interpreter (``widget.tk``) reports, never below 1.

    ``tk scaling`` is pixels per point; times 72 it is the display DPI, which is
    rounded to a whole value so 150 % reads exactly 1.5 (Tk derives it from the
    screen's millimetre size and reports 143.86).
    """
    return max(1.0, round(float(interpreter.call("tk", "scaling")) * 72) / 96)


def ui_scale(widget) -> float:
    """:func:`display_scale` of ``widget``'s window, cached on its toplevel."""
    top = widget.winfo_toplevel()
    cached = getattr(top, "_dolly_ui_scale", None)
    if cached is None:
        cached = top._dolly_ui_scale = display_scale(top.tk)
    return cached


def px(scale: float, value):
    """Design pixels to screen pixels at ``scale``; a padding tuple converts item by item."""
    if isinstance(value, (tuple, list)):
        return tuple(px(scale, item) for item in value)
    return int(value * scale + .5)


def even_px(scale: float, value):
    """:func:`px` with exact halves rounded to even (a padding tuple converts item by item).

    For whole design numbers at 100 % it equals :func:`px`. At 150 % an odd number
    lands on a half (7 -> 10.5); rounding those alternately down and up keeps long
    pages of rows (25 px control + 7 px gap = 48 px at 150 %) from growing by a
    pixel per row, as rounding every half up would.
    """
    if isinstance(value, (tuple, list)):
        return tuple(even_px(scale, item) for item in value)
    return round(value * scale)


def scaler(widget):
    """:func:`even_px` bound to ``widget``'s window scale, for page builders without a scale at hand."""
    scale = ui_scale(widget)
    return lambda value: even_px(scale, value)


def chars(widget, count: int) -> int:
    """A ttk ``width`` of ``count`` characters that keeps its 100 % size in design px.

    Tk sizes ``width=`` in widths of the font's "0". Small text does not grow
    evenly with the DPI: Segoe UI 9 pt draws "0" 6 px wide at 96 DPI but 10 px
    (not 9) at 144, while words grow by the scale. Counted as design px, a
    20-character shortcut button stays 120 x 1.5 = 180 px at 150 % instead of 200.
    At 100 % it is ``count``.
    """
    top = widget.winfo_toplevel()
    ratio = getattr(top, "_dolly_char_ratio", None)
    if ratio is None:
        from .. import ui_theme
        scale = ui_scale(top)
        family, points = ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]
        ratio = 1.0
        if scale != 1.0:
            # The body face at its 100 % pixel size (9 pt at 96 DPI is 12 px) against the face as drawn now.
            design = float(top.tk.call("font", "measure", (family, -round(points * 96 / 72)), "0"))
            actual = float(top.tk.call("font", "measure", (family, points), "0"))
            if design > 0 and actual > 0:
                ratio = design * scale / actual
        top._dolly_char_ratio = ratio
    return max(1, int(count * ratio + .5)) if ratio != 1.0 else count


def hairline(scale: float) -> int:
    """Width of 1-pixel lines; whole pixels stay crisp at 125 % and 150 %."""
    return max(1, int(scale))


def bucket(scale: float) -> str:
    """The largest pre-rendered art scale (``"1"``, ``"1.5"``, ``"2"``) not above ``scale``."""
    chosen = BUCKETS[0]
    for name in BUCKETS:
        if float(name) <= scale + .01:
            chosen = name
    return chosen
