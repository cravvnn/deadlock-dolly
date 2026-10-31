"""Sidebar column: brand, live driver line and the page rows on one canvas.

Everything is a canvas item. The background is one opaque image: the gradient
with the faint statue art and the right edge line composited in by Tk itself
(``copy``, at C speed) whenever the height changes, and each row's fade is
composited over the part of that image it covers. Tk then only copies pixels
when it repaints the column; an image with partial alpha would be blended
pixel by pixel on every repaint. Pages still switch through the hidden-tab
notebook: a row only runs its command.

Motion (USER-ADDITIONS UPDATE 4, on the shared :class:`.tween.Animator`): a
row's hover ramps in over 120 ms and out over 180 ms, its fade and icon through
:data:`.tween.STEPS` faces built in idle slices once the column has mapped, its
label colour per frame. A new selection is immediate (the page, the labels and
the icons); only the highlight, the selected fade and its bar, slides to the row
over 200 ms, and a click during the slide retargets it from where it is. With
motion off everything lands at once, as before.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_layout.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

from pathlib import Path
import struct
import tkinter as tk
from tkinter import font as tkfont
import zlib

from .. import ui_theme
from . import art, brand_mark, fonts, image_resample, tween
from .scale import bucket, hairline, px

# Design px (Figma [design reference], Sidebar [design reference]).
WIDTH = 190
HEIGHT = 361        # brand block plus six rows: the column's requested height
FILL_RAMP = 444     # the gradient reaches the window colour here
EDGE_RAMP = 591     # the right edge line has faded out here
ART_TOP = 260       # the statue art's box starts here (its visible part starts at 355)
ART_HEIGHT = 550    # her own height: the box in the default frame, 260 to 810 (manifest "rect")
BRAND_X = 17
MARK_GAP = 8        # between Dolly's mark and the wordmark (UPDATE 21)
NAV_TOP = 121
NAV_HEIGHT = 40
BAR_WIDTH = 3
ICON_X, ICON_Y = 16, 12
LABEL_X, LABEL_Y = 42, 20
SELECTED_START = .19  # the selected fade starts this far into its gradient
IDLE_ALPHA, HOVER_ALPHA = .20, .60   # the row fade's left end at rest and hovered

NATIVE_DRIVER = "DRIVER · NATIVE"
VERSION_SEPARATOR = "  ·  "   # between the version and the driver in use (Figma [design reference])

# Segoe Fluent Icons / MDL2 glyphs, drawn only when the icon sheet is unavailable.
GLYPHS = {"replay": "", "camera": "", "look": "", "export": "",
          "controls": "", "settings": ""}


LAST_STEP = tween.STEPS - 1   # hover ramp step of a hovered row (0: at rest)


def _mix(first: str, second: str, amount: float) -> str:
    """``first`` moved ``amount`` of the way towards ``second``."""
    a, b = ui_theme.rgb(first), ui_theme.rgb(second)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3))


def _fade(step):
    """The row fade of a hover ramp step: the two ends are the rest and hover fades."""
    return "idle" if step <= 0 else "hover" if step >= LAST_STEP else step


def _ratio(width: int, height: int, source_width: int, source_height: int) -> tuple[int, int]:
    """Whole numbers ``(zoom, subsample)``, subsample up to 8, for a Tk photo ``copy`` that turns the
    source into a picture at least ``width`` x ``height``, as little bigger as they get.

    Tk keeps every subsample-th pixel (the first one too) and repeats each ``zoom`` times; one pair
    for both axes keeps the proportions. Counting the pixels Tk really writes (not the bare ratio)
    lets 125 % use 5/8: 1100 rows give 690, her 688.
    """
    best = None
    for subsample in range(1, 9):
        kept_width, kept_height = -(-source_width // subsample), -(-source_height // subsample)
        zoom = max(1, -(-width // kept_width), -(-height // kept_height))
        if best is None or zoom * kept_height < best[2]:
            best = (zoom, subsample, zoom * kept_height)
    return best[0], best[1]


def _png_rgba(path) -> tuple[int, int, bytes] | None:
    """An 8-bit RGBA PNG whose rows all use filter 0 (the build tool's icon sheets), else None."""
    try:
        data = Path(path).read_bytes()
        position, header, compressed = 8, None, []
        while position + 8 <= len(data):
            length, kind = struct.unpack(">I4s", data[position:position + 8])
            if kind == b"IHDR":
                header = struct.unpack(">IIBBBBB", data[position + 8:position + 21])
            elif kind == b"IDAT":
                compressed.append(data[position + 8:position + 8 + length])
            position += 12 + length
        width, height, *form = header
        stride = 1 + 4 * width
        raw = zlib.decompress(b"".join(compressed))
        if form != [8, 6, 0, 0, 0] or len(raw) != stride * height or raw[::stride] != bytes(height):
            return None
        return width, height, b"".join(raw[row + 1:row + stride] for row in range(0, len(raw), stride))
    except (OSError, TypeError, ValueError, struct.error, zlib.error):
        return None


def _blend_rgba(first: bytes, second: bytes, amount: float) -> bytes:
    """``amount`` of the way from ``first`` to ``second`` (RGBA rows), as if one faded into the other."""
    out = bytearray(len(first))
    keep = 1.0 - amount
    for i in range(0, len(first), 4):
        a, b = first[i + 3] * keep, second[i + 3] * amount
        alpha = a + b
        if alpha:
            for c in range(i, i + 3):
                out[c] = min(255, int((first[c] * a + second[c] * b) / alpha + .5))
            out[i + 3] = min(255, int(alpha + .5))
    return bytes(out)


class NavItem:
    """One page row; keeps the small button API the shell relied on."""

    def __init__(self, sidebar, key, title, icon, command, top):
        self.key = key
        self.title = title
        self.icon = icon
        self.top = top
        self._sidebar = sidebar
        self._command = command
        self._state = "normal"
        self.images = None      # (idle, bright) icon cells, or None for a glyph
        self.cells = {}         # hover ramp step -> icon cell between those two (pre-warmed)
        self.strip = self.glyph = self.label = None
        self.look = None        # (fade image, colour, icon image or colour) as last sent to the canvas
        self.level = 0.0        # where the hover ramp is: 0 at rest, 1 hovered
        self.motion = ("sidebar row", str(sidebar), key)   # its Animator key

    def enabled(self):
        return self._state != "disabled"

    def invoke(self):
        if not self.enabled() or self._command is None:
            return ""
        return self._command()

    def cget(self, option):
        if option == "text":
            return self.title
        if option == "state":
            return self._state
        raise tk.TclError(f'unknown option "-{option}"')

    __getitem__ = cget

    def configure(self, **options):
        if "state" in options:
            self._state = "disabled" if str(options.pop("state")) == "disabled" else "normal"
            self._sidebar._state_changed(self)
        if options:
            raise tk.TclError(f'unknown option "-{next(iter(options))}"')

    config = configure


class Sidebar(tk.Canvas):
    """Wordmark, tagline, version and driver, then one row per page.

    Rows answer the pointer (hover, click) and the keyboard: Tab reaches the
    selected row, Up/Down/Home/End move the ring, Return or Space opens it.
    """

    def __init__(self, master, *, scale, version, driver_var, assets=None):
        tokens = ui_theme.TOKENS
        self.ui_scale = scale
        self._width = px(scale, WIDTH)
        self._row = px(scale, NAV_HEIGHT)
        super().__init__(master, width=self._width, height=px(scale, HEIGHT), background=tokens["bg"],
                         highlightthickness=0, borderwidth=0, takefocus=0)
        self.items: dict[str, NavItem] = {}
        self._top = self.winfo_toplevel()
        self._assets = assets if assets is not None else art.load(self._top)
        self._version = version
        self._driver_var = driver_var
        self._selected = self._hover = self._focus = None
        self._fill_height = None
        self._fill_image = None
        self._columns = {}      # (top, bottom, ramp) -> that gradient's ramp as a 1 px column (_gradient)
        self._rows = {}         # (row top, fade) -> that row's fade composited over the fill
        self._ramp_strips = {}  # hover ramp step -> the fade between the idle and hover strips
        self._warming = set()   # (row key, step) queued for pre-warming
        self._icon_rows = None  # the icon sheet's RGBA rows (decoded once, for the icon steps), or False
        self._glide = None      # the selected fade while it slides (made on the first slide)
        self._glide_y = None    # its top while it slides, else None
        self._slide = ("sidebar slide", str(self))   # the slide's Animator key
        self._fill = self.create_image(0, 0, anchor="nw")
        # The statue is faint (alpha 6/255 at most), so it stays still; a boil would not show.
        # It is composited into the fill, never shown as an image of its own.
        self._art_image = self._sheet("sidebar_art")
        self._icon_sheet = self._sheet("nav_icons")
        x = px(scale, BRAND_X)
        # UPDATE 21: Dolly's mark left of the wordmark, when both fit the column (fallback fonts may not).
        wordmark = fonts.font(self, "wordmark")
        self.mark_image = brand_mark.photo(self, scale)
        text_x = x + self.mark_image.width() + px(scale, MARK_GAP)
        if text_x + tkfont.Font(self, font=wordmark).measure("Deadlock Dolly") > self._width - x:
            self.mark_image, text_x = None, x
        self.mark_item = None if self.mark_image is None else self.create_image(
            x, px(scale, 43), anchor="w", image=self.mark_image)
        self.create_text(text_x, px(scale, 43), anchor="w", text="Deadlock Dolly",
                         font=wordmark, fill=tokens["text"])
        self.create_text(x, px(scale, 67), anchor="w", text="Deadlock camera tool",
                         font=fonts.font(self, "body"), fill=tokens["text"])
        self.version_item = self.create_text(x, px(scale, 83), anchor="w", text="",
                                             font=fonts.font(self, "small"), fill=tokens["dim"])
        # The driver in use follows the version on its baseline as an item of its own (Figma
        # [design reference]), so only "Console driver" takes the warn colour; placed by _place_driver.
        self.driver_item = self.create_text(x, px(scale, 83), anchor="w", text="",
                                            font=fonts.font(self, "small"), fill=tokens["dim"])
        self._unfont = fonts.on_change(self, self._place_driver)
        selected_from = _mix(tokens["selected"], tokens["nav_selected_to"], SELECTED_START)
        self._strips = {
            "idle": art.h_gradient_strip(self._top, self._width, self._row,
                                         [(0, tokens["rail_hover"], IDLE_ALPHA), (1, tokens["nav_idle_to"], 0)]),
            "hover": art.h_gradient_strip(self._top, self._width, self._row,
                                          [(0, tokens["rail_hover"], HOVER_ALPHA), (1, tokens["nav_idle_to"], 0)]),
            "selected": art.h_gradient_strip(self._top, self._width, self._row,
                                             [(0, selected_from, .60 * (1 - SELECTED_START)),
                                              (1, tokens["nav_selected_to"], 0)]),
        }
        self._bar = self.create_rectangle(0, 0, px(scale, BAR_WIDTH), self._row, fill=tokens["text"],
                                          width=0, state="hidden")
        self._ring = self.create_rectangle(0, 0, 0, 0, outline=tokens["accent"], width=hairline(scale),
                                           state="hidden")
        # Keyboard focus sits on a 1 px stand-in, so the canvas never needs focus itself.
        self._proxy = tk.Frame(self, width=1, height=1, background=tokens["sidebar_top"],
                               highlightthickness=0, borderwidth=0, takefocus=1)
        self.create_window(0, 0, window=self._proxy, anchor="nw")
        self._proxy.bind("<FocusIn>", self._focus_in)
        self._proxy.bind("<FocusOut>", self._focus_out)
        self._proxy.bind("<Up>", lambda _e: self._move(-1))
        self._proxy.bind("<Down>", lambda _e: self._move(1))
        self._proxy.bind("<Home>", lambda _e: self._move(None, end=0))
        self._proxy.bind("<End>", lambda _e: self._move(None, end=-1))
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self._proxy.bind(key, self._activate)
        self.bind("<Motion>", self._motion, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<Button-1>", self._click, add="+")
        self.bind("<Configure>", self._resized, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self._driver_trace = driver_var.trace_add("write", self._driver_changed)
        self._driver_changed()
        self._resized()

    # ------------------------------------------------------------------ rows

    def add_item(self, key, title, icon, command):
        """Append a page row; ``icon`` is one of replay, camera, look, export, controls, settings."""
        top = px(self.ui_scale, NAV_TOP) + len(self.items) * self._row
        item = NavItem(self, key, title, icon, command, top)
        item.strip = self.create_image(0, top, anchor="nw")
        if self._glide is not None:
            self.tag_lower(item.strip, self._glide)   # the sliding highlight passes over every fade
        item.images = self._icon_cells(icon)
        icon_x, icon_y = px(self.ui_scale, ICON_X), top + px(self.ui_scale, ICON_Y)
        if item.images is not None:
            item.glyph = self.create_image(icon_x, icon_y, anchor="nw", image=item.images[0], tags=("nav_ink",))
        else:
            item.glyph = self.create_text(icon_x, top + self._row // 2, anchor="w", text=GLYPHS.get(icon, ""),
                                          font=fonts.font(self, "icon"), fill=ui_theme.TOKENS["muted"],
                                          tags=("nav_ink",))
        item.label = self.create_text(px(self.ui_scale, LABEL_X), top + px(self.ui_scale, LABEL_Y), anchor="w",
                                      text=title, font=fonts.font(self, "body"), fill=ui_theme.TOKENS["muted"],
                                      tags=("nav_ink",))
        self.items[key] = item
        self.tag_raise(self._bar)
        self.tag_raise(self._ring)
        self._paint()
        self._warm_rows([item])
        return item

    def select(self, key):
        """Mark the shown page; called by the shell after every notebook change.

        The selection, the labels and the icons change at once. The highlight slides from
        where it is (the old row, or mid-way through an earlier slide) to the new row.
        """
        key = key if key in self.items else None
        if key == self._selected:
            return
        previous, self._selected = self._selected, key
        if previous is not None and key is not None and tween.smooth(self):
            target = self.items[key]
            self._row_image(target.top, "selected")   # its face once the highlight arrives
            start = self._glide_y if self._glide_y is not None else self.items[previous].top
            self._show_glide(start)
            tween.Animator.of(self).run(self._slide, tween.SLIDE_MS, self._slide_to, start=start, end=target.top,
                                        done=self._slid)
        elif self._glide_y is not None:
            tween.Animator.of(self).stop(self._slide)
            self._slid()
        self._paint()

    @property
    def selected(self):
        return self._selected

    def _paint(self):
        """Draw every row as it is now: at rest, hovered, selected or part-way along its ramp."""
        for key, item in self.items.items():
            moving = self._moving(item.motion)
            if not moving:
                item.level = 1.0 if key == self._hover and item.enabled() else 0.0
            self._draw_row(item, self._look(item, item.level), frame=moving)
        selected = self.items.get(self._selected)
        if self._glide_y is None:   # (while the highlight slides, the bar moves with it: _slide_to)
            if selected is not None:
                self.coords(self._bar, 0, selected.top, px(self.ui_scale, BAR_WIDTH), selected.top + self._row)
                self.itemconfigure(self._bar, state="normal")
            else:
                self.itemconfigure(self._bar, state="hidden")
        focused = self.items.get(self._focus)
        if focused is not None:
            inset = hairline(self.ui_scale)
            self.coords(self._ring, inset, focused.top + inset,
                        self._width - 2 * inset, focused.top + self._row - inset)
            self.itemconfigure(self._ring, state="normal")
        else:
            self.itemconfigure(self._ring, state="hidden")

    def _look(self, item, level):
        """(fade, label colour, icon step) of ``item`` with its hover ramp at ``level`` (0..1)."""
        tokens = ui_theme.TOKENS
        selected = item.key == self._selected
        if selected and self._glide_y is None:
            return "selected", tokens["text"], LAST_STEP
        step = int(level * LAST_STEP + .5)
        if selected:   # the highlight is on its way: label and icon are bright at once
            return _fade(step), tokens["text"], LAST_STEP
        if not item.enabled():
            return "idle", tokens["dim"], 0
        return _fade(step), tween.mix(tokens["text"], tokens["muted"], level), step

    def _draw_row(self, item, look, *, frame=False):
        """Send what changed of ``look`` to the row's items.

        In an animation frame only faces built already are shown (the nearest step); nothing
        is built there.
        """
        fade, colour, icon = look
        strip = self._built_row(item.top, fade) if frame else self._row_image(item.top, fade)
        glyph = colour if item.images is None else self._icon_step(item, icon, build=not frame)
        sent = item.look or (None, None, None)
        if strip is not sent[0]:
            self.itemconfigure(item.strip, image=strip)
        if colour != sent[1]:
            self.itemconfigure(item.label, fill=colour)
        if glyph != sent[2]:
            if item.images is None:
                self.itemconfigure(item.glyph, fill=glyph)
            else:
                self.itemconfigure(item.glyph, image=glyph)
        item.look = (strip, colour, glyph)

    def _state_changed(self, item):
        """A row was enabled or disabled: its face changes at once."""
        animator = getattr(self._top, "_dolly_animator", None)
        if animator is not None:
            animator.stop(item.motion)
        self._paint()

    # -------------------------------------------------------------- motion

    def _moving(self, key) -> bool:
        animator = getattr(self._top, "_dolly_animator", None)
        return animator is not None and animator.active(key)

    def _hover_to(self, key, hovered):
        """Ramp row ``key`` to its hover face (120 ms) or back to rest (180 ms)."""
        item = self.items.get(key)
        if item is None:
            return
        target = 1.0 if hovered and item.enabled() else 0.0
        animator = tween.Animator.of(self)
        if item.level == target and not animator.active(item.motion):
            return
        if item.key == self._selected and self._glide_y is None:
            animator.stop(item.motion)   # the selected face is the same hovered or not
            item.level = target
            return
        self._row_image(item.top, "idle")   # both ends exist before a frame needs them
        self._row_image(item.top, "hover")
        animator.run(item.motion, tween.HOVER_IN_MS if target else tween.HOVER_OUT_MS,
                     lambda level, item=item: self._ramped(item, level), start=item.level, end=target)

    def _ramped(self, item, level):
        """One step of a row's hover ramp (the first one inside the pointer's event)."""
        item.level = level
        self._draw_row(item, self._look(item, level), frame=True)

    def _show_glide(self, y):
        """Lift the selected fade off its row, at ``y``, for the slide."""
        if self._glide is None:
            # Over every row's fade, under the icons and labels; Tk blends its alpha over the
            # rows it passes, only while it slides.
            self._glide = self.create_image(0, 0, anchor="nw", image=self._strips["selected"], state="hidden")
            self.tag_raise("nav_ink")
            self.tag_raise(self._bar)
            self.tag_raise(self._ring)
        self._glide_y = y
        self.itemconfigure(self._glide, state="normal")
        self.itemconfigure(self._bar, state="normal")

    def _slide_to(self, y):
        """One step of the slide: the selected fade and its bar at ``y``."""
        self._glide_y = y
        top = int(y + .5)
        self.coords(self._glide, 0, top)
        self.coords(self._bar, 0, top, px(self.ui_scale, BAR_WIDTH), top + self._row)

    def _slid(self):
        """The highlight arrived: the selected row shows its own fade again."""
        self._glide_y = None
        if self._glide is not None:
            self.itemconfigure(self._glide, state="hidden")
        self._paint()

    def _warm_rows(self, items):
        """Queue the faces between rest and hover of ``items``, one per idle slice once mapped."""
        due = [(item.key, step) for item in items for step in range(1, LAST_STEP)
               if (item.key, step) not in self._warming]
        if due:
            self._warming.update(due)
            tween.prewarm(self, self._warm, due)

    def _warm(self, due):
        self._warming.discard(due)
        key, step = due
        item = self.items.get(key)
        if item is not None and self._fill_image is not None:
            self._row_image(item.top, step)
            if item.images is not None:
                self._icon_step(item, step)

    # ------------------------------------------------------------- artwork

    def _sheet(self, group, scale=None):
        if self._assets is None:
            return None
        try:
            return self._assets.bucket_photo(self._top, group, self.ui_scale if scale is None else scale)
        except (tk.TclError, OSError, KeyError, ValueError):
            return None

    def _icon_cells(self, icon):
        """(idle, bright) cells cut from the icon sheet, or None to draw a glyph instead."""
        if self._icon_sheet is None:
            return None
        order = list(self._assets.manifest.get("nav_icons", {}).get("order", GLYPHS))
        if icon not in order:
            return None
        cell = self._icon_sheet.width() // len(order)
        x0 = order.index(icon) * cell
        cells = []
        for row in range(2):  # row 0 is drawn in muted, row 1 in text
            image = tk.PhotoImage(master=self, width=cell, height=cell)
            self.tk.call(image, "copy", self._icon_sheet, "-from", x0, row * cell, x0 + cell, (row + 1) * cell)
            cells.append(image)
        return tuple(cells)

    def _icon_step(self, item, step, *, build=True):
        """The icon of a hover ramp step: a sheet cell at the ends, a blend of both between them.

        Without ``build`` (in an animation frame) the nearest step built so far.
        """
        if step > 0 and step < LAST_STEP and step not in item.cells and build:
            self._build_cell(item, step)
        built = {0: item.images[0], LAST_STEP: item.images[1], **item.cells}
        return built[min(built, key=lambda other: (abs(other - step), other))]

    def _build_cell(self, item, step):
        sheet = self._icon_sheet_rows()
        if not sheet:
            return
        width, _height, rows = sheet
        cell = item.images[0].width()
        order = list(self._assets.manifest.get("nav_icons", {}).get("order", GLYPHS))
        x0 = order.index(item.icon) * cell

        def cut(y0):
            return b"".join(rows[4 * (y * width + x0):4 * (y * width + x0 + cell)] for y in range(y0, y0 + cell))
        item.cells[step] = art.photo_rgba(self._top, cell, cell, _blend_rgba(cut(0), cut(cell), step / LAST_STEP))

    def _icon_sheet_rows(self):
        """The shown icon sheet's RGBA rows (read once, for the icon faces between its two rows), or False."""
        if self._icon_rows is None:
            self._icon_rows = False
            try:
                name = self._assets.manifest["nav_icons"]["files"][bucket(self.ui_scale)]
                if self._assets.photo(self._top, name) is self._icon_sheet:   # the very file on screen
                    sheet = _png_rgba(self._assets.path(name))
                    if sheet is not None and sheet[:2] == (self._icon_sheet.width(), self._icon_sheet.height()):
                        self._icon_rows = sheet
            except (KeyError, OSError, ValueError, tk.TclError):
                pass
        return self._icon_rows

    def _lay_art(self, fill, height):
        """Fill the statue box proportionally, with smooth scaling and centered side crops.

        Short windows keep her design height and crop the bottom; taller windows
        enlarge her to fill the box.  Prefer the verified high-resolution source
        and Windows' bicubic resampler; keep the existing Tk fallback when either
        is unavailable.
        """
        top = px(self.ui_scale, ART_TOP)
        box, own = height - top, px(self.ui_scale, ART_HEIGHT)
        if box <= 0:
            return
        art_image = self._art_image
        if self._assets is not None:
            try:
                files = self._assets.manifest["sidebar_art"]["files"]
                name = files.get("4") or files.get("2")
                if name:
                    pixels = image_resample.scale_centered(self._assets.path(name), self._width, box, max(box, own))
                    if pixels is not None:
                        smooth = art.photo_rgba(self._top, self._width, box, pixels)
                        fill.tk.call(fill.name, "copy", smooth.name, "-to", 0, top)
                        return
            except (KeyError, OSError, ValueError, tk.TclError):
                pass
        if box <= own and art_image.height() == own:
            fill.tk.call(fill.name, "copy", art_image.name, "-to", 0, top)
            return
        source = self._sheet("sidebar_art", scale=2) or art_image
        zoom, subsample = _ratio(self._width, max(box, own), source.width(), source.height())
        trim = max(0, int((source.width() - self._width * subsample / zoom) / 2))
        fill.tk.call(fill.name, "copy", source.name, "-from", trim, 0, source.width() - trim, source.height(),
                     "-to", 0, top, self._width, height, "-zoom", zoom, zoom, "-subsample", subsample, subsample)

    def _gradient(self, image, x, width, top, bottom, ramp):
        """Columns ``x`` to ``x + width`` of ``image`` as :func:`.art.column_gradient` paints them:
        ``top`` blending to ``bottom`` at row ``ramp``, then solid.

        A row's colour depends on its y alone, so the blend is one 1 px column made once and
        tiled by Tk: a new height costs two calls at C speed instead of a colour string per row.
        """
        key = (top, bottom, ramp)
        column = self._columns.get(key)
        if column is None:
            column = self._columns[key] = art.column_gradient(self._top, 1, ramp, top, bottom, ramp)
        rows = min(ramp, image.height())
        image.put(bottom, to=(x, 0, x + width, image.height()))
        image.tk.call(image.name, "copy", column.name, "-from", 0, 0, 1, rows, "-to", x, 0, x + width, rows)

    def _resized(self, _event=None):
        """Rebuild the fill for a new height; the art fills its box as in the default frame."""
        height = max(1, self.winfo_height() if self.winfo_height() > 1 else self.winfo_reqheight())
        if height == self._fill_height:
            return
        self._fill_height = height
        tokens = ui_theme.TOKENS
        s = self.ui_scale
        fill = tk.PhotoImage(master=self._top, width=self._width, height=height)
        self._gradient(fill, 0, self._width, tokens["sidebar_top"], tokens["bg"], px(s, FILL_RAMP))
        # Tk's "copy" composites alpha over what is there (its default rule), so the faint
        # art lands in the opaque gradient. The edge line goes on top, as Figma draws a
        # frame's stroke over its children.
        if self._art_image is not None:
            self._lay_art(fill, height)
        self._gradient(fill, self._width - hairline(s), hairline(s), tokens["sidebar_edge_top"],
                       tokens["sidebar_edge_bottom"], px(s, EDGE_RAMP))
        self._fill_image = fill
        self.itemconfigure(self._fill, image=fill)
        self._rows = {}
        for item in self.items.values():
            if item.look is not None:   # only the fades are new; labels and icons stay as they were sent
                item.look = (None, *item.look[1:])
        self._paint()
        if self._glide_y is not None and self._selected in self.items:
            self._row_image(self.items[self._selected].top, "selected")   # for the slide's last frame
        self._warm_rows(self.items.values())   # the ramp faces over the new fill

    def _row_image(self, top, fade):
        """The row at ``top`` with its ``fade`` strip composited over the fill: opaque.

        ``fade`` is "idle", "hover", "selected" or a hover ramp step between idle and hover.
        """
        image = self._rows.get((top, fade))
        if image is None:
            strip = self._strips[fade] if isinstance(fade, str) else self._ramp_strip(fade)
            image = tk.PhotoImage(master=self._top, width=self._width, height=self._row)
            bottom = min(top + self._row, self._fill_image.height())   # a short window cuts the last rows
            if bottom > top:
                image.tk.call(image.name, "copy", self._fill_image.name, "-from", 0, top, self._width, bottom)
            image.tk.call(image.name, "copy", strip.name)
            self._rows[(top, fade)] = image
        return image

    def _built_row(self, top, fade):
        """:meth:`_row_image` without building: a step not warmed yet shows the nearest one that is."""
        image = self._rows.get((top, fade))
        if image is not None or fade == "selected":
            return image if image is not None else self._row_image(top, fade)
        step = 0 if fade == "idle" else LAST_STEP if fade == "hover" else fade
        for other in sorted(range(tween.STEPS), key=lambda other: (abs(other - step), other)):
            image = self._rows.get((top, _fade(other)))
            if image is not None:
                return image
        return self._row_image(top, fade)

    def _ramp_strip(self, step):
        """The fade of a hover ramp step: its left end between the idle and the hover alpha."""
        strip = self._ramp_strips.get(step)
        if strip is None:
            tokens = ui_theme.TOKENS
            alpha = IDLE_ALPHA + (HOVER_ALPHA - IDLE_ALPHA) * step / LAST_STEP
            strip = self._ramp_strips[step] = art.h_gradient_strip(
                self._top, self._width, self._row, [(0, tokens["rail_hover"], alpha), (1, tokens["nav_idle_to"], 0)])
        return strip

    def _driver_changed(self, *_args):
        """The version line (Figma [design reference]): "<version>  ·  " in dim, then the driver in use,
        "Native driver" in dim or "Console driver" in the warn colour."""
        try:
            native = self._driver_var.get() == NATIVE_DRIVER
            tokens = ui_theme.TOKENS
            self._set_text(self.version_item, f"{self._version}{VERSION_SEPARATOR}", tokens["dim"])
            self._set_text(self.driver_item, "Native driver" if native else "Console driver",
                           tokens["dim" if native else "warn"])
            self._place_driver()
        except tk.TclError:
            pass

    def _set_text(self, item, text, fill):
        if (self.itemcget(item, "text"), self.itemcget(item, "fill")) != (text, fill):
            self.itemconfigure(item, text=text, fill=fill)   # Tk repaints even a same value

    def _place_driver(self):
        """The driver starts where the version ends, on its baseline (again after a font change)."""
        x, y = self.coords(self.version_item)
        x += fonts.font(self, "small").measure(self.itemcget(self.version_item, "text"))
        if self.coords(self.driver_item) != [x, y]:
            self.coords(self.driver_item, x, y)

    # ---------------------------------------------------------------- input

    def _item_at(self, y):
        for item in self.items.values():
            if item.top <= y < item.top + self._row:
                return item
        return None

    def _set_cursor(self, cursor):
        # A canvas configure redraws the whole column: only when the cursor really changes.
        if str(self.cget("cursor")) != cursor:
            self.configure(cursor=cursor)

    def _motion(self, event):
        item = self._item_at(self.canvasy(event.y))
        key = item.key if item is not None and item.enabled() else None
        self._set_cursor("hand2" if key is not None else "")
        if key != self._hover:
            left, self._hover = self._hover, key
            self._hover_to(left, False)
            self._hover_to(key, True)

    def _leave(self, _event):
        self._set_cursor("")
        if self._hover is not None:
            left, self._hover = self._hover, None
            self._hover_to(left, False)

    def _click(self, event):
        item = self._item_at(self.canvasy(event.y))
        if item is not None:
            item.invoke()

    def _keys(self):
        return [key for key, item in self.items.items() if item.enabled()]

    def _focus_in(self, _event):
        keys = self._keys()
        self._focus = self._selected if self._selected in keys else (keys[0] if keys else None)
        self._paint()

    def _focus_out(self, _event):
        self._focus = None
        self._paint()

    def _move(self, step, *, end=None):
        keys = self._keys()
        if keys and self._focus in keys:
            index = end if end is not None else max(0, min(len(keys) - 1, keys.index(self._focus) + step))
            self._focus = keys[index]
            self._paint()
        return "break"

    def _activate(self, _event):
        item = self.items.get(self._focus)
        if item is not None:
            item.invoke()
        return "break"

    def _destroyed(self, event):
        if event.widget is not self:
            return
        animator = getattr(self._top, "_dolly_animator", None)
        if animator is not None:   # no last frame on a canvas that is going away
            for item in self.items.values():
                animator.stop(item.motion)
            animator.stop(self._slide)
        if self._unfont is not None:
            self._unfont()
            self._unfont = None
        if self._driver_trace is not None:
            try:
                self._driver_var.trace_remove("write", self._driver_trace)
            except tk.TclError:
                pass
            self._driver_trace = None
