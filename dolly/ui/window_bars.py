"""The window's own title bar and link bar (USER-ADDITIONS UPDATE 20, Figma Frame 79 [design reference]).

Kara: "It's a custom title bar: replace the Windows title bar, draggable, with
minimise/maximise/close on the right like normal" and "that's where File and Edit will be".

- :class:`TitleBar` ("Custom Bar" [design reference], 29 design px across the top, the bottom bar's height): File and Edit at
  the left open the app's menus; minimize, maximize/restore and close at the right, sized like
  Windows 11's caption buttons scaled to the bar. Pressing anywhere else on the bar starts
  Windows' own move loop through :class:`.window_chrome.WindowChrome` (snapping,
  drag-from-maximized); double-clicking it maximizes or restores; right-clicking it opens the
  window menu.
- :class:`LinkBar` ("Custom Bar" [design reference], 29 design px across the bottom): the YouTube and
  Discord icons (links that open the browser), a divider and the credit line.

Both are plain canvases: the background is one opaque image (the design's horizontal gradient
with its 1 px line that fades in and out) rebuilt only when the width changes; everything else
is a canvas item. Hover and press colours ramp on the shared animator (:mod:`.tween`). Neither
bar takes keyboard focus. Page modules never import this one.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_chrome.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import font as tkfont
import webbrowser

from .. import ui_theme
from . import art, fonts
from .boil import IdleUpdate
from .scale import hairline, px
from .tween import ColorRamp
from .window_chrome import window_tag

# Design px (Figma [design reference]).
TOP_HEIGHT = 29          # Kara: same height as the bottom bar (Figma drew 25.5); its 1 px line is the bottom row
BOTTOM_HEIGHT = 29       # its 1 px line is the top row
PAD_X = 15               # both bars' left padding: File's text and the YouTube icon start here
MENU_PAD = 8             # hover plate beside each side of a menu name (the plates touch)
BUTTON_WIDTH = 42        # Windows 11's 46 x 32 caption buttons scaled to the 29 px bar
GLYPH = 8                # caption glyph size (Windows 11: 10 px at 32)
ICON = 18
ICON_Y = 6
ICON_REACH = 5           # extra click room on each side of an icon
YOUTUBE_X, DISCORD_X = 15, 43
# Figma's divider is a 1 px stroke centred on x 71 (so 70.5 .. 71.5); the app draws one crisp
# column from the stroke's left edge: column 71 at 100 %, 106 at 150 % (Figma's fully covered one).
DIVIDER_X, DIVIDER_TOP, DIVIDER_BOTTOM = 71, 7.75, 22.25
# Figma's text box is y 8..22 (middle 15) in Inter, so Inter's line is centred on 15 (baseline 19).
# Segoe UI's (the fallback's) shorter line box puts the same glyphs a pixel lower: it is centred on 14.
CREDIT_X, CREDIT_Y, CREDIT_Y_FALLBACK = 81, 15, 14
LABEL_RAISE = 1          # File / Edit: their capitals centred on the bar, not their line box
CREDIT = "Dolly by Cravvnn · UX/UI by Kara"
# The credit line in pieces; a piece with a name is a link (see link_url).
CREDIT_PARTS = (("Dolly by ", None), ("Cravvnn", "cravvnn"), (" · UX/UI by ", None), ("Kara", "kara"))

# Links from Kara (2026-10-27). An empty URL keeps its link inert: it lights on hover, a click does nothing.
DISCORD_URL = "https://discord.gg/XTefeBUqX5"
YOUTUBE_URL = "https://www.youtube.com/@Cravvnn"
CRAVVNN_URL = "https://github.com/cravvnn"
KARA_URL = "https://www.youtube.com/@KaraMongus"

# Gradients as (position, token, alpha); colour and alpha interpolate independently, as in Figma.
TOP_FILL = ((0.0, "titlebar_left", 1.0), (1.0, "titlebar_right", 1.0))
TOP_LINE = ((0.0, "bar_line_clear", 0.0), (0.1538, "titlebar_line", 1.0), (0.9567, "titlebar_line", 0.0))
BOTTOM_FILL = ((0.0, "linkbar_left", 1.0), (1.0, "linkbar_right", 1.0))
BOTTOM_LINE = ((0.0, "bar_line_clear", 0.0), (0.7115, "linkbar_line", 1.0), (0.9567, "titlebar_line", 0.0))

MENUS = ("File", "Edit")
BUTTONS = ("minimize", "maximize", "close")
# Hover and press plates, as (colour or token, opacity) over the bar right beneath each plate
# (the bar runs from blue at the left to near black at the right, so one opaque colour would read
# as a grey box under File and Edit). Windows 11's subtle fills: white 6 % hovered, 4 % pressed;
# close: its red, 90 % pressed.
PLATE_HOVER = ("caption_tint", 0.06)
PLATE_PRESSED = ("caption_tint", 0.04)
CLOSE_HOVER = ("caption_close", 1.0)
CLOSE_PRESSED = ("caption_close", 0.9)
# After a maximize or restore the title bar posts, the glyph is checked again this much later (a
# maximize that leaves the window's rect as it was sends Tk no <Configure>).
GLYPH_RECHECK_MS = 50
# Segoe Fluent Icons / MDL2 caption glyphs (the ones Windows itself draws).
GLYPHS = {"minimize": "", "maximize": "", "restore": "", "close": ""}

# The icons' outlines (SVG path data in an 18 x 18 box, even-odd fill: the play triangle and the
# Discord eyes are holes), exactly Figma's vectors [design reference] and [design reference] placed in their frames.
ICON_PATHS = {
    "youtube": "M7.18959 11.3275V6.27687C8.98258 7.12056 10.3712 7.93558 12.0136 8.81427C10.659 9.56551 "
               "8.98258 10.4084 7.18959 11.3275ZM17.1819 3.76496C16.8726 3.35747 16.3455 3.04028 15.7843 "
               "2.93527C14.1347 2.62202 3.84389 2.62113 2.19524 2.93527C1.7452 3.01963 1.34445 3.22356 1.0002 "
               "3.5404C-0.450326 4.88671 0.00419956 12.1065 0.353832 13.276C0.500856 13.7823 0.690921 14.1473 "
               "0.930285 14.387C1.23868 14.7038 1.66092 14.922 2.14592 15.0198C3.50411 15.3007 10.5012 15.4578 "
               "15.7556 15.062C16.2397 14.9776 16.6682 14.7524 17.0062 14.4221C18.3473 13.0812 18.2559 5.45592 "
               "17.1819 3.76496Z",
    "discord": "M15.2477 3.17085C14.0825 2.6257 12.8367 2.2295 11.5342 2.00391C11.3742 2.29311 11.1873 2.68211 "
               "11.0585 2.99155C9.67387 2.78332 8.30201 2.78332 6.94287 2.99155C6.81404 2.68211 6.62292 2.29311 "
               "6.46152 2.00391C5.15761 2.2295 3.91032 2.62716 2.74514 3.17374C0.394982 6.72514 -0.242108 "
               "10.1883 0.0764375 13.6024C1.63519 14.7664 3.14581 15.4735 4.63093 15.9362C4.99762 15.4316 "
               "5.32465 14.8951 5.60638 14.3297C5.06981 14.1258 4.5559 13.8742 4.07031 13.5821C4.19913 13.4867 "
               "4.32514 13.3869 4.44689 13.2842C7.40865 14.6695 10.6267 14.6695 13.5531 13.2842C13.6762 13.3869 "
               "13.8022 13.4867 13.9296 13.5821C13.4426 13.8756 12.9273 14.1273 12.3907 14.3312C12.6724 14.8951 "
               "12.9981 15.433 13.3662 15.9377C14.8527 15.4749 16.3647 14.7678 17.9235 13.6024C18.2973 9.64464 "
               "17.285 6.21325 15.2477 3.17085ZM6.00988 11.5028C5.12079 11.5028 4.39166 10.6727 4.39166 9.66198C"
               "4.39166 8.65122 5.10522 7.81977 6.00988 7.81977C6.91457 7.81977 7.64367 8.64977 7.6281 9.66198C"
               "7.62951 10.6727 6.91457 11.5028 6.00988 11.5028ZM11.9901 11.5028C11.101 11.5028 10.3718 10.6727 "
               "10.3718 9.66198C10.3718 8.65122 11.0854 7.81977 11.9901 7.81977C12.8947 7.81977 13.6238 8.64977 "
               "13.6083 9.66198C13.6083 10.6727 12.8947 11.5028 11.9901 11.5028Z",
}


def bars_px(scale: float) -> int:
    """Screen px the two bars take from the window's height (each bar rounds on its own)."""
    return px(scale, TOP_HEIGHT) + px(scale, BOTTOM_HEIGHT)


def link_url(name: str) -> str:
    """The URL of the ``youtube`` or ``discord`` icon, or of a ``cravvnn``/``kara`` credit name ("" while unknown)."""
    return {"youtube": YOUTUBE_URL, "discord": DISCORD_URL, "cravvnn": CRAVVNN_URL, "kara": KARA_URL}[name]


# -- icon outlines ---------------------------------------------------------------------------

def _path_polygons(path, scale, steps=12):
    """SVG path data (M, L, H, V, C, Z; absolute or relative) -> closed polygons, scaled."""
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", path)
    polygons, points = [], []
    x = y = start_x = start_y = 0.0
    command, i = "", 0
    while i < len(tokens):
        if tokens[i].isalpha():
            command, i = tokens[i], i + 1
            if command in "Zz":
                if len(points) > 2:
                    polygons.append(points)
                points, x, y = [], start_x, start_y
            continue
        relative = command.islower()
        ox, oy = (x, y) if relative else (0.0, 0.0)
        kind = command.upper()
        count = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6}[kind]
        v = [float(t) for t in tokens[i:i + count]]
        i += count
        if kind == "M":
            if len(points) > 2:
                polygons.append(points)
            x, y = start_x, start_y = ox + v[0], oy + v[1]
            points = [(x, y)]
            command = "l" if relative else "L"  # further pairs after a move are lines
        elif kind == "L":
            x, y = ox + v[0], oy + v[1]
            points.append((x, y))
        elif kind == "H":
            x = ox + v[0]
            points.append((x, y))
        elif kind == "V":
            y = oy + v[0]
            points.append((x, y))
        else:
            (x1, y1), (x2, y2), (x3, y3) = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3]), (ox + v[4], oy + v[5])
            for k in range(1, steps + 1):
                t = k / steps
                a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                points.append((a * x + b * x1 + c * x2 + d * x3, a * y + b * y1 + c * y2 + d * y3))
            x, y = x3, y3
    if len(points) > 2:
        polygons.append(points)
    return [[(px_ * scale, py_ * scale) for px_, py_ in points] for points in polygons]


def _coverage(polygons, size, rows=5):
    """Anti-aliased even-odd coverage (0..1) of ``polygons`` on a size x size grid.

    Exact across each pixel row, ``rows`` samples down it."""
    edges = [(a, b) for points in polygons for a, b in zip(points, points[1:] + points[:1]) if a[1] != b[1]]
    cover = [[0.0] * size for _ in range(size)]
    for row in range(size):
        line = cover[row]
        for s in range(rows):
            yy = row + (s + 0.5) / rows
            xs = sorted(x0 + (yy - y0) * (x1 - x0) / (y1 - y0)
                        for (x0, y0), (x1, y1) in edges if (y0 <= yy) != (y1 <= yy))
            for left, right in zip(xs[::2], xs[1::2]):
                left, right = max(0.0, left), min(float(size), right)
                column = int(left)
                while column < right:
                    line[column] += (min(right, column + 1) - max(left, column)) / rows
                    column += 1
    return cover


_COVERAGE: dict = {}


def icon_coverage(name: str, size: int):
    """Coverage rows of icon ``name`` drawn ``size`` px square (cached)."""
    key = (name, size)
    cover = _COVERAGE.get(key)
    if cover is None:
        cover = _COVERAGE[key] = _coverage(_path_polygons(ICON_PATHS[name], size / ICON), size)
    return cover


def icon_image(widget, name: str, size: int, colour: str) -> tk.PhotoImage:
    """Icon ``name`` in ``colour`` over transparency, ``size`` px square."""
    r, g, b = ui_theme.rgb(colour)
    rows = b"".join(bytes((r, g, b, min(255, int(255 * c + .5)))) for line in icon_coverage(name, size) for c in line)
    return art.photo_rgba(widget, size, size, rows)


# -- bars ------------------------------------------------------------------------------------

def _stops(stops):
    return sorted((float(t), ui_theme.rgb(ui_theme.TOKENS[token]), float(alpha)) for t, token, alpha in stops)


def _hex(values) -> str:
    return "#" + "".join(f"{max(0, min(255, round(value))):02x}" for value in values)


def overlay(base: str, ink: str, alpha: float) -> str:
    """``ink`` (a colour or a token) at ``alpha`` over the opaque colour ``base``."""
    ink = ui_theme.TOKENS.get(ink, ink)
    return _hex([b + (i - b) * alpha for b, i in zip(ui_theme.rgb(base), ui_theme.rgb(ink))])


class _Bar(tk.Canvas):
    """A full-width bar: the gradient and its fading 1 px line as one opaque background image."""

    def __init__(self, master, *, scale: float, height: int, fill, line, line_at: str):
        self.scale = scale
        self.bar_height = px(scale, height)
        self.line = hairline(scale)
        self._fill = _stops(fill)
        self._stroke = _stops(line)
        self._line_at = line_at
        super().__init__(master, height=self.bar_height, width=1, background=_hex(self._fill[0][1]),
                         highlightthickness=0, borderwidth=0, takefocus=0)
        self.background = None
        self._background_width = 0
        self._background_item = self.create_image(0, 0, anchor="nw", tags="background")
        self._relayout = IdleUpdate(self, self._layout_now)
        self.bind("<Configure>", self._relayout, add="+")

    def fill_at(self, x: float) -> str:
        """The bar's own colour at canvas ``x`` (what a plate fades from)."""
        width = max(1, self.winfo_width())
        colour, _alpha = art._sample(self._fill, min(1.0, max(0.0, (x + .5) / width)))
        return _hex(colour)

    def content_middle(self) -> float:
        """y of the middle of the bar without its line."""
        top = self.line if self._line_at == "top" else 0
        return top + (self.bar_height - self.line) / 2.0

    def _paint_background(self, width: int) -> None:
        fill, stroke = [], []
        for column in range(width):
            t = (column + .5) / width
            base, _alpha = art._sample(self._fill, t)
            ink, alpha = art._sample(self._stroke, t)
            fill.append(_hex(base))
            stroke.append(_hex([b + (c - b) * alpha for b, c in zip(base, ink)]))
        height, line = self.bar_height, self.line
        image = tk.PhotoImage(master=self, width=width, height=height)
        body, edge = "{" + " ".join(fill) + "}", "{" + " ".join(stroke) + "}"
        # Tk tiles one row over each rectangle, in C.
        if self._line_at == "top":
            image.put(edge, to=(0, 0, width, line))
            image.put(body, to=(0, line, width, height))
        else:
            image.put(body, to=(0, 0, width, height - line))
            image.put(edge, to=(0, height - line, width, height))
        self.background = image
        self._background_width = width
        self.itemconfigure(self._background_item, image=image)
        self.tag_lower(self._background_item)

    def _layout_now(self) -> None:
        width = self.winfo_width()
        if width <= 1:
            return
        if width != self._background_width:
            self._paint_background(width)
        self.layout(width)

    def layout(self, width: int) -> None:
        """Place the bar's items for ``width``."""


class _Plate:
    """A hover plate behind a menu name or caption button: hidden at rest, its fill ramping in.

    ``hover`` and ``pressed`` are (colour or token, opacity) laid over the bar's own colour at the
    plate's centre (:data:`PLATE_HOVER`), so the plate tints the bar where it sits.
    """

    def __init__(self, bar: _Bar, hover, pressed=None):
        self.bar = bar
        self._hover, self._pressed = hover, pressed
        self.item = bar.create_rectangle(0, 0, 0, 0, width=0, state="hidden")
        rest, hover_colour, pressed_colour = self.colours(bar.fill_at(0))
        self.ramp = ColorRamp(bar, self._paint, rest, hover_colour, pressed=pressed_colour)

    def colours(self, rest: str):
        """(rest, hover, pressed) over the bar colour ``rest``."""
        return (rest, overlay(rest, *self._hover),
                overlay(rest, *self._pressed) if self._pressed is not None else None)

    def _paint(self, colour: str) -> None:
        self.bar.itemconfigure(self.item, fill=colour, state="hidden" if colour == self.ramp.rest else "normal")

    def place(self, left: int, top: int, right: int, bottom: int) -> None:
        self.bar.coords(self.item, left, top, right, bottom)
        rest = self.bar.fill_at((left + right) / 2.0)
        if rest != self.ramp.rest:
            ramp = self.ramp
            ramp.set_colours(*self.colours(rest))
            if not ramp.active:
                ramp.jump(ramp.colour_at(ramp.value))   # at rest, hovered or held: in the new colours now


class TitleBar(_Bar):
    """The top bar: File and Edit menus, the caption buttons, and the window drag area.

    ``menus``: (name, tk.Menu) pairs in order; ``chrome``: the window's
    :class:`.window_chrome.WindowChrome`; ``on_close``: the window's close handler (the one
    WM_DELETE_WINDOW runs, so the unsaved-changes and busy prompts stay in one place).
    """

    def __init__(self, master, *, scale: float, chrome, menus, on_close):
        super().__init__(master, scale=scale, height=TOP_HEIGHT, fill=TOP_FILL, line=TOP_LINE, line_at="bottom")
        tokens = ui_theme.TOKENS
        self.chrome = chrome
        self.on_close = on_close
        self.menus = dict(menus)
        self.button_width = px(scale, BUTTON_WIDTH)
        self._font = fonts.font(self, "body")
        family = fonts.family_available(self, "icon")
        self._glyph_font = tkfont.Font(root=self, family=family, size=-px(scale, GLYPH)) if family else None
        self.hovered = None      # ("menu"|"button", name) under the pointer
        self.pressed = None      # caption button held down
        self.opened = None       # menu whose popup is posted
        self.window_active = True
        self.caption_buttons = True   # False beside a native caption (the fallback frame)
        self.menu_boxes: dict[str, tuple[int, int]] = {}
        self.button_boxes: dict[str, tuple[int, int]] = {}
        self._labels, self._label_ramps, self._menu_plates = {}, {}, {}
        for name in self.menus:
            self._menu_plates[name] = _Plate(self, PLATE_HOVER)
            item = self._labels[name] = self.create_text(0, 0, text=name, anchor="w", font=self._font,
                                                         fill=tokens["muted"])
            self._label_ramps[name] = ColorRamp(self, lambda colour, item=item: self.itemconfigure(item, fill=colour),
                                                tokens["muted"], tokens["text"])
        self._button_plates, self._glyph_ramps, self.glyphs = {}, {}, {}
        for name in BUTTONS:
            close = name == "close"
            self._button_plates[name] = _Plate(self, CLOSE_HOVER if close else PLATE_HOVER,
                                               CLOSE_PRESSED if close else PLATE_PRESSED)
            tag = "glyph_" + name
            self._glyph_ramps[name] = ColorRamp(self, lambda colour, tag=tag: self.itemconfigure(tag, fill=colour),
                                                tokens["muted"], tokens["text"], pressed=tokens["text"])
            self.glyphs[name] = None
        # Kara: "add shot title in the top bar", with a separating line like the link bar's divider (only
        # the line: the title itself reads like the menu names).
        self.title_divider = self.create_rectangle(0, 0, 0, 0, fill=tokens["dim"], width=0)   # visible on the blue bar
        self.title_item = self.create_text(0, 0, text="", anchor="w", font=self._font, fill=tokens["muted"])
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self.drop_hover)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<ButtonRelease-3>", self._on_menu_click)
        # The window's own events only (a binding on the toplevel's path tag would run for every
        # child widget's <Configure>, <Activate> and <Deactivate> too).
        tag = window_tag(self.winfo_toplevel())
        self.bind_class(tag, "<Activate>", lambda event: self.set_window_active(True), add="+")
        self.bind_class(tag, "<Deactivate>", lambda event: self.set_window_active(False), add="+")
        self.bind_class(tag, "<Configure>", lambda event: self.update_glyphs(), add="+")
        self.bind_class(tag, "<Map>", lambda event: self.update_glyphs(), add="+")
        self._glyph_check = None

    # -- layout ----------------------------------------------------------------------------------

    def layout(self, width: int) -> None:
        s = self.scale
        middle = self.content_middle()
        bottom = self.bar_height - self.line
        pad = px(s, MENU_PAD)
        left = px(s, PAD_X) - pad
        for name, item in self._labels.items():
            text = self._font.measure(name)
            right = left + pad + text + pad
            self.coords(item, left + pad, middle - px(s, LABEL_RAISE))
            self._menu_plates[name].place(left, 0, right, bottom)
            self.menu_boxes[name] = (left, right)
            left = right
        # Same gaps as the link bar: 10 px to the divider, 10 px on to the text (Figma [design reference]).
        x = left - pad + px(s, CREDIT_X - DIVIDER_X)
        half = px(s, (DIVIDER_BOTTOM - DIVIDER_TOP) / 2)
        self.coords(self.title_divider, x, middle - half, x + self.line, middle + half)
        self.coords(self.title_item, x + px(s, CREDIT_X - DIVIDER_X), middle - px(s, LABEL_RAISE))
        self.button_boxes = {}
        if self.caption_buttons:
            for index, name in enumerate(BUTTONS):
                left = width - (len(BUTTONS) - index) * self.button_width
                self.button_boxes[name] = (left, left + self.button_width)
                self._button_plates[name].place(left, 0, left + self.button_width, bottom)
        self.glyphs = {}
        self.delete("glyph")
        self.update_glyphs()

    def set_title(self, text: str) -> None:
        """The shot title shown centred in the bar."""
        self.itemconfigure(self.title_item, text=text)

    def set_caption_buttons(self, shown: bool) -> None:
        """Show or drop minimize/maximize/close (dropped while a native caption has its own)."""
        shown = bool(shown)
        if shown == self.caption_buttons:
            return
        self.caption_buttons = shown
        if not shown:
            if self.hovered is not None and self.hovered[0] == "button":
                self.drop_hover()
            for plate in self._button_plates.values():
                plate.ramp.jump(plate.ramp.rest)
        if self.winfo_width() > 1:
            self.layout(self.winfo_width())

    def update_glyphs(self) -> None:
        """Draw the caption glyphs (maximize turns into restore while the window is maximized)."""
        if not self.button_boxes or not self.winfo_exists():
            return
        middle = self.content_middle()
        for name in BUTTONS:
            glyph = "restore" if name == "maximize" and self.chrome.zoomed() else name
            left, right = self.button_boxes[name]
            centre = (left + right) // 2
            if self.glyphs.get(name) == (glyph, centre):
                continue
            tag = "glyph_" + name
            self.delete(tag)
            colour = self._glyph_ramps[name].colour
            if self._glyph_font is not None:
                self.create_text(centre, middle, text=GLYPHS[glyph], font=self._glyph_font, fill=colour,
                                 tags=("glyph", tag))
            else:
                self._draw_glyph(glyph, centre, middle, colour, tag)
            self.glyphs[name] = (glyph, centre)

    def _draw_glyph(self, glyph, cx, cy, ink, tag):
        """Line-drawn caption glyph when no icon font is installed."""
        s, w, tags = px(self.scale, GLYPH) / 2.0, hairline(self.scale), ("glyph", tag)
        if glyph == "minimize":
            self.create_line(cx - s, cy, cx + s, cy, fill=ink, width=w, tags=tags)
        elif glyph == "close":
            self.create_line(cx - s, cy - s, cx + s, cy + s, fill=ink, width=w, tags=tags)
            self.create_line(cx - s, cy + s, cx + s, cy - s, fill=ink, width=w, tags=tags)
        else:
            inset = px(self.scale, 2) if glyph == "restore" else 0
            self.create_line(cx - s, cy - s + inset, cx + s - inset, cy - s + inset, cx + s - inset, cy + s,
                             cx - s, cy + s, cx - s, cy - s + inset, fill=ink, width=w, tags=tags)
            if inset:
                self.create_line(cx - s + inset, cy - s, cx + s, cy - s, cx + s, cy + s - inset,
                                 fill=ink, width=w, tags=tags)

    # -- state -----------------------------------------------------------------------------------

    def hit(self, x: float, y: float):
        """("menu"|"button", name) at canvas (x, y), or None for the drag area."""
        if not 0 <= y < self.bar_height:
            return None
        for name, (left, right) in self.button_boxes.items():
            if left <= x < right:
                return "button", name
        for name, (left, right) in self.menu_boxes.items():
            if left <= x < right:
                return "menu", name
        return None

    def set_window_active(self, active: bool) -> None:
        """Caption glyphs dim while another window is active (as Windows draws them)."""
        if active == self.window_active:
            return
        self.window_active = active
        tokens = ui_theme.TOKENS
        rest = tokens["muted"] if active else tokens["dim"]
        for name, ramp in self._glyph_ramps.items():
            at_rest = ramp.value == 0.0 and not ramp.active
            ramp.set_colours(rest, tokens["text"], tokens["text"])
            if at_rest:
                ramp.jump(rest)

    def _set_hover(self, hit) -> None:
        if hit == self.hovered:
            return
        old, self.hovered = self.hovered, hit
        for target, entering in ((old, False), (hit, True)):
            if target is None:
                continue
            kind, name = target
            if kind == "menu":
                if name == self.opened:
                    continue   # an open menu keeps its look until it closes
                ramps = (self._menu_plates[name].ramp, self._label_ramps[name])
            else:
                if not entering and self.pressed == name:
                    continue   # held down: the pressed look stays until release
                ramps = (self._button_plates[name].ramp, self._glyph_ramps[name])
            for ramp in ramps:
                ramp.enter() if entering else ramp.leave()

    def drop_hover(self, _event=None) -> None:
        """Forget hover looks (on <Leave>, and when the pointer enters a window-edge resize zone)."""
        if self.pressed is not None:
            name, self.pressed = self.pressed, None
            self._button_plates[name].ramp.release()
            self._glyph_ramps[name].release()
        self._set_hover(None)

    # -- events ----------------------------------------------------------------------------------

    def _on_motion(self, event):
        self._set_hover(self.hit(event.x, event.y))

    def _on_press(self, event):
        hit = self.hit(event.x, event.y)
        if hit is None:
            self.chrome.start_move()
            return "break"
        kind, name = hit
        if kind == "menu":
            return self.open_menu(name)
        self._set_hover(hit)
        self.pressed = name
        self._button_plates[name].ramp.press()
        self._glyph_ramps[name].press()
        return "break"

    def _on_release(self, event):
        name, self.pressed = self.pressed, None
        if name is None:
            return None
        self._button_plates[name].ramp.release()
        self._glyph_ramps[name].release()
        hit = self.hit(event.x, event.y)
        if hit != ("button", name):
            self._set_hover(hit)
            if self.hovered != ("button", name):
                self._button_plates[name].ramp.leave()
                self._glyph_ramps[name].leave()
            return "break"
        self.invoke(name)
        return "break"

    def _on_double(self, event):
        hit = self.hit(event.x, event.y)
        if hit is None:
            self._toggle_maximize()
            return "break"
        return self._on_press(event)   # the second click of a fast double click on a button

    def _on_menu_click(self, event):
        if self.hit(event.x, event.y) is None:
            self.chrome.system_menu(event.x_root, event.y_root)
        return "break"

    # -- actions ---------------------------------------------------------------------------------

    def invoke(self, name: str) -> None:
        """Run caption button ``name``: minimize, maximize (restore when maximized) or close."""
        if name == "minimize":
            self.chrome.minimize()
        elif name == "maximize":
            self._toggle_maximize()
        elif name == "close":
            self.on_close()

    def _toggle_maximize(self) -> None:
        self.chrome.toggle_maximize()
        # Windows runs the posted maximize or restore from the event loop. A window already
        # covering exactly the work area keeps its rect and Tk sends no <Configure>, so the glyph
        # is checked again once Windows is done.
        if self._glyph_check is not None:
            self.after_cancel(self._glyph_check)
        self._glyph_check = self.after(GLYPH_RECHECK_MS, self._recheck_glyphs)

    def _recheck_glyphs(self) -> None:
        self._glyph_check = None
        self.update_glyphs()

    def open_menu(self, name: str):
        """Post menu ``name`` under its name (a press on it, Alt+F / Alt+E). Returns "break"."""
        menu = self.menus.get(name)
        if menu is None or self.opened is not None:
            return "break"
        self.opened = name
        self._menu_plates[name].ramp.jump(self._menu_plates[name].ramp.hover)
        self._label_ramps[name].jump(self._label_ramps[name].hover)
        left, _right = self.menu_boxes.get(name, (0, 0))
        try:
            self.update_idletasks()   # the open look shows before the menu's own loop runs
            menu.tk_popup(self.winfo_rootx() + left, self.winfo_rooty() + self.bar_height)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
            self.opened = None
            self._menu_closed(name)
        return "break"

    def _menu_closed(self, name: str) -> None:
        try:
            x, y = self.winfo_pointerxy()
            hit = self.hit(x - self.winfo_rootx(), y - self.winfo_rooty())
        except tk.TclError:
            return
        self.hovered = hit
        if hit != ("menu", name):
            self._menu_plates[name].ramp.leave()
            self._label_ramps[name].leave()
            if hit is not None:
                self.hovered = None
                self._set_hover(hit)


class LinkBar(_Bar):
    """The bottom bar: YouTube and Discord icons (links), a divider and the credit line."""

    LINKS = (("youtube", YOUTUBE_X), ("discord", DISCORD_X))

    def __init__(self, master, *, scale: float):
        super().__init__(master, scale=scale, height=BOTTOM_HEIGHT, fill=BOTTOM_FILL, line=BOTTOM_LINE,
                         line_at="top")
        tokens = ui_theme.TOKENS
        s = scale
        size = px(s, ICON)
        reach = px(s, ICON_REACH)
        self.icons, self.ramps, self.boxes = {}, {}, {}
        self._faces: dict = {}    # (icon, colour) -> PhotoImage
        self.hovered = self.pressed = None
        for name, x in self.LINKS:
            left = px(s, x)
            item = self.icons[name] = self.create_image(left, px(s, ICON_Y), anchor="nw",
                                                        image=self._face(name, tokens["linkbar_ink"]))
            self.ramps[name] = ColorRamp(self, lambda _colour, item=item, name=name:
                                         self.itemconfigure(item, image=self.ramps[name].image()),
                                         tokens["linkbar_ink"], tokens["muted"], pressed=tokens["text"],
                                         images=lambda colour, name=name: self._face(name, colour))
            self.boxes[name] = (left - reach, left + size + reach)
        x = px(s, DIVIDER_X - .5)   # from the stroke's left edge (see DIVIDER_X)
        self.divider = self.create_rectangle(x, px(s, DIVIDER_TOP), x + self.line, px(s, DIVIDER_BOTTOM),
                                             fill=tokens["linkbar_ink"], width=0)
        # The credit line, its two names links (Kara: Cravvnn -> GitHub, Kara -> her YouTube).
        font = fonts.font(self, "small")
        y = px(s, CREDIT_Y if fonts.family_available(self, "small") == ui_theme.UI_FACE else CREDIT_Y_FALLBACK)
        x, self.credit_items = px(s, CREDIT_X), []
        for text, name in CREDIT_PARTS:
            item = self.create_text(x, y, text=text, anchor="w", font=font, fill=tokens["linkbar_ink"])
            self.credit_items.append(item)
            width = font.measure(text)
            if name is not None:
                self.ramps[name] = ColorRamp(self, lambda colour, item=item: self.itemconfigure(item, fill=colour),
                                             tokens["linkbar_ink"], tokens["muted"], pressed=tokens["text"])
                self.boxes[name] = (x, x + width)
            x += width
        self.credit = self.credit_items[0]
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self.drop_hover)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _face(self, name: str, colour: str) -> tk.PhotoImage:
        image = self._faces.get((name, colour))
        if image is None:
            image = self._faces[(name, colour)] = icon_image(self, name, px(self.scale, ICON), colour)
        return image

    def link_at(self, x: float, y: float):
        if not 0 <= y < self.bar_height:
            return None
        return next((name for name, (left, right) in self.boxes.items() if left <= x < right), None)

    def _set_hover(self, name) -> None:
        if name == self.hovered:
            return
        old, self.hovered = self.hovered, name
        if old is not None and old != self.pressed:
            self.ramps[old].leave()
        if name is not None:
            self.ramps[name].enter()
        self.configure(cursor="hand2" if name is not None else "")

    def drop_hover(self, _event=None) -> None:
        if self.pressed is not None:
            name, self.pressed = self.pressed, None
            self.ramps[name].leave()
        self._set_hover(None)

    def _on_motion(self, event):
        self._set_hover(self.link_at(event.x, event.y))

    def _on_press(self, event):
        name = self.link_at(event.x, event.y)
        if name is not None:
            self._set_hover(name)
            self.pressed = name
            self.ramps[name].press()
        return "break"

    def _on_release(self, event):
        name, self.pressed = self.pressed, None
        if name is None:
            return "break"
        self.ramps[name].release()
        over = self.link_at(event.x, event.y)
        if over != name:
            self.ramps[name].leave()
            self._set_hover(over)
            return "break"
        self.open_link(name)
        return "break"

    def open_link(self, name: str) -> bool:
        """Open icon ``name``'s page in the browser; False (nothing happens) while it has no URL."""
        url = link_url(name)
        if not url:
            return False
        try:
            webbrowser.open(url, new=2)
        except (webbrowser.Error, OSError):
            pass
        return True
