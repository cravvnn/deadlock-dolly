"""Single source of truth for Dolly's UI palette.

Shared by the desktop Tk surfaces (:mod:`dolly.gui`, :mod:`dolly.gui_theme`) and
the in-game ImGui panel (``native/src/dolly_overlay_win.cpp``). The native side
mirrors :data:`TOKENS` through ``native/src/dolly_ui_tokens_generated.hpp``
(written by ``tools/generate_ui_tokens.py``); ``tests/test_ui_theme.py`` guards
against drift. Write a Dolly UI colour here and nowhere else.

This is Dolly's own cool teal/graphite identity (not Deadlock's warm palette),
tuned to neutral, low-chroma tones.
"""
from __future__ import annotations

import os

# Semantic tokens. Keys are referenced verbatim by the desktop theme and the
# generated native header, so rename with both in mind.
TOKENS: dict[str, str] = {
    # Surfaces
    "bg": "#11171b",            # opaque graphite window background
    "panel": "#1b252b",         # cards and popovers
    "panel_alt": "#263139",     # nested rows, headers
    "field": "#0e1418",         # recessed fields and graph interiors
    "edge": "#2c373e",          # quiet outlines and separators
    # Text
    "text": "#c8ced1",          # primary text
    "muted": "#9ba7ad",         # secondary text
    "strong": "#e6ecee",        # titles
    # Accent (primary action, selection, focus): Dolly's neutral mint/teal.
    "accent": "#8bc1b8",
    "accent_hover": "#a6d6cd",
    "accent_active": "#74a89e",
    "accent_ink": "#122a28",    # text on an accent fill
    # Interactive states
    "button": "#253039",        # secondary button at rest
    "button_hover": "#34434b",
    "button_active": "#2c3a3e",
    "selected": "#304a48",      # selected row / axis
    "selected_ink": "#e0f0ec",
    "selected_edge": "#607f7b", # selected axis outline
    "selected_blue": "#357cbb",     # selected Lite playback speed
    "disabled_bg": "#1b232a",
    "disabled_fg": "#66747e",
    "track": "#27313a",         # slider / progress trough
    "scroll_hover": "#43525c",
    # In-game panel: dimmed (background) tabs
    "tab_dim": "#181d21",
    "tab_dim_selected": "#243033",
    "tab_dim_overline": "#3d5f5a",
    # Curve identities stay consistent in single and stacked views.
    "curve_x": "#d8aaa0",
    "curve_y": "#a9c3a4",
    "curve_z": "#96b9d6",
    "curve_rotation": "#d7c27f",
    "curve_before": "#b8c5cb",
    # Status
    "warn": "#d9bd76",
    "warn_bg": "#332f22",
    "danger": "#cf8c80",
    # Key-cap chips
    "keycap": "#a2adb4",
    "keycap_ink": "#12161a",
}


# Typography. Tk uses point sizes; the in-game ImGui panel loads its own font, so
# these apply to the desktop surfaces. A Wine/Proton prefix usually has neither
# Segoe UI nor Consolas, and Tk then draws whatever the prefix substitutes;
# DOLLY_FONT_FAMILY and DOLLY_FONT_MONO let a user point the desktop at fonts
# their prefix does have, without changing the Windows default.
FONT_FAMILY = os.environ.get("DOLLY_FONT_FAMILY", "").strip() or "Segoe UI"
FONT_MONO = os.environ.get("DOLLY_FONT_MONO", "").strip() or "Consolas"
FONT_SIZES = {"title": 18, "callout": 12, "card_title": 11, "section": 10, "body": 10,
              "small": 9, "tiny": 8}


def rgb(hex_color: str) -> tuple[int, int, int]:
    """``"#rrggbb"`` -> ``(r, g, b)``; raises on anything else."""
    value = hex_color.strip()
    if len(value) != 7 or value[0] != "#":
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}") from exc


def required_tokens() -> tuple[str, ...]:
    """Keys that both surfaces rely on; the native header must carry all of them."""
    return tuple(sorted(TOKENS))
