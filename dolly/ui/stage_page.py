"""Replay page stage: cards on a canvas, so the window's backdrop shows between them.

``ScrollPage`` keeps one opaque body frame over its whole canvas; this page gives
every card its own canvas window instead, inside a rounded plate drawn on the
canvas. The gaps, the area below the last card and the rounded corners stay
open, so the animated backdrop (when the shell installed one) shows through and
stays fixed to the window while the cards scroll over it. Scrolling, the wheel
rules and ``reveal`` follow ``ScrollPage``; the wheel is eased
(:class:`.tween.WheelScroller`), everything else scrolls at once.

What one scroll step costs (PERF-NOTES): Tk redraws the whole visible canvas
and moves every canvas window. So the canvas is only as wide as the card column,
holds few items (the plates' two end images, the backdrop, the rail drawn as
items) and one window: the sheet that carries every card, shaped to them
(:func:`set_window_region`) and keeping its whole surface
(:func:`retain_surface`), so its widgets never repaint while it moves.

Replay never scrolls (USER-ADDITIONS UPDATE 17.5): its page asks :meth:`StagePage.fit`
to keep every card above ``END_GAP`` (the gap under the last card in the design window,
UPDATE 9), and a short window takes the room out of the card content that can give it
(the replay table shows fewer rows). Only when that content has nothing left to give
(a page area smaller than the shell's minimum window makes) do the cards still
overflow; then the viewport ends ``END_GAP`` above the footer's link row over a
static ``foot`` canvas of backdrop and the page scrolls. While they fit, the foot
does not exist: the page is drawn exactly as before. This page never fades its
scroll edges (UPDATE 12); the kit pages do.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_layout.py, agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import art, fonts
from .backdrop import Backdrop
from .boil import IdleUpdate
from .scale import hairline, px, ui_scale
from .tween import ColorRamp, WheelScroller
from .widgets import WHEEL_PIXELS_PER_NOTCH, WHEEL_UNIT_PIXELS, ScrollPage

# Segoe Fluent Icons / Segoe MDL2 Assets glyphs: Info, ChevronRight, ChevronDown.
INFO_GLYPH, CLOSED_GLYPH, OPEN_GLYPH = "\ue946", "\ue76c", "\ue70d"
ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
BUBBLE_DELAY_MS = 150
BUBBLE_WIDTH = 300
BUBBLE_RADIUS = 7
REVEAL_MARGIN = 14
TITLE_LINE = 18   # card title line height in the design (VALVE Oracle SemiBold 15 / 18)
# Tk 8.6 draws Treeview cell text this many px inside its column, at any scale
# (not configurable); table headings use the same indent.
TREE_CELL_INSET = 4
# Between a scrolling page's viewport and the bottom of the page area, design / compact (the
# shell's Stage.mode; USER-ADDITIONS UPDATE 9). Measured from the shell's rows: in the 1200 x 810
# design window this page's last card ends 29 design px above the footer's link row (page area 591
# tall, cards 562; the 36 px link row begins where the page area ends, chrome.PAGE_BOTTOM 112 =
# LINKS_Y 94 + 18). The compact rows start that link row 7 px below the page area (PAGE_BOTTOM 98,
# LINKS_Y 73 + 18 = 91), so the same 29 px above the links is 22 above the page area there.
# Every scrolling page keeps it.
END_GAP = (29, 22)
# A page that fits its cards (StagePage.fit) lets them end up to this many design px into
# END_GAP: whole-pixel rounding makes Replay's cards a few px taller than the design at some
# scales (706 px at 125 % for the design's 702.5), which must not cost a table row in the
# design window.
FIT_ROUNDING = 3


def end_gap(scale: float, compact: bool = False) -> int:
    """``END_GAP`` in whole px, rounded down: never more than Replay's own gap at the design window."""
    return int(END_GAP[1 if compact else 0] * scale + 1e-9)


def _icon_font(widget):
    """The icon font, and whether Tk really found Segoe's icon glyphs for it."""
    icon = fonts.font(widget, "icon")
    return icon, icon.actual("family") in ICON_FAMILIES


def _focus_path(widget):
    # focus_get() raises for Tcl-only windows such as an open combobox list.
    return str(widget.tk.call("focus"))


_user32 = None


def _discard_expose(widget) -> None:
    """Tell Windows that ``widget``'s own window is fully painted (its children keep theirs).

    Only right after Tk repainted all of it. A scroll repaints the whole canvas
    and moves the card windows during that repaint; each move marks the strip
    it uncovers as dirty, and Windows reports their bounding box as one Expose,
    which Tk answers with a second near-full repaint of what it just drew.
    """
    global _user32
    if sys.platform != "win32":
        return
    try:
        if _user32 is None:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32")
            user32.ValidateRect.argtypes = (wintypes.HWND, ctypes.c_void_p)
            user32.ValidateRect.restype = wintypes.BOOL
            _user32 = user32
        _user32.ValidateRect(widget.winfo_id(), None)
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass


_GWL_EXSTYLE, _WS_EX_LAYERED, _LWA_ALPHA = -20, 0x00080000, 0x2
_layering = None   # user32 once loaded; False where layered child windows are unavailable


def _user32_layering():
    global _layering
    if _layering is None:
        _layering = False
        if sys.platform == "win32" and sys.getwindowsversion()[:2] >= (6, 2):
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32")
            user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte,
                                                          wintypes.DWORD)
            user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
            user32.GetLayeredWindowAttributes.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_ubyte),
                ctypes.POINTER(wintypes.DWORD))
            user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
            _layering = user32
    return _layering


def retain_surface(widget) -> bool:
    """Let Windows keep the whole surface of ``widget``'s child window; True when it does.

    A plain child window holds only the pixels it shows. When a scroll moves part
    of it out from under the canvas edge, Windows asks every widget in the strip
    it uncovers to repaint, and a ttk widget repaints all of itself (the replay
    table took 2-5 ms per scroll frame). A layered child window (Windows 8 and
    later; constant alpha 255, so it looks exactly as before) keeps its whole
    surface and moves without any repaint. ``DOLLY_UI_LAYERED=0`` turns this
    off; anything unexpected leaves the window as it was.
    """
    if os.environ.get("DOLLY_UI_LAYERED", "1") == "0":
        return False
    try:
        user32 = _user32_layering()
        if not user32:
            return False
        import ctypes
        from ctypes import wintypes
        hwnd = widget.winfo_id()
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        alpha, flags = ctypes.c_ubyte(0), wintypes.DWORD(0)
        if not style & _WS_EX_LAYERED:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
        ok = (user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_LAYERED
              and user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)
              and user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
              and alpha.value == 255 and flags.value & _LWA_ALPHA)
        if not ok:
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style & ~_WS_EX_LAYERED)
        return bool(ok)
    except (AttributeError, OSError, ValueError, tk.TclError):
        return False


_regions = None   # (user32, gdi32) once loaded; False off Windows


def set_window_region(widget, rects) -> bool:
    """Shape ``widget``'s window to the union of ``rects`` ((x0, y0, x1, y1), window px); True when done.

    Outside the region the window is not there at all: its parent shows (and gets
    the pointer) in the gaps. Windows only; elsewhere returns False.
    """
    global _regions
    try:
        if _regions is None:
            _regions = False
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
                gdi32.CreateRectRgn.argtypes = (ctypes.c_int,) * 4
                gdi32.CreateRectRgn.restype = wintypes.HRGN
                gdi32.CombineRgn.argtypes = (wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int)
                gdi32.CombineRgn.restype = ctypes.c_int
                gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
                user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
                user32.SetWindowRgn.restype = ctypes.c_int
                _regions = (user32, gdi32)
        if not _regions:
            return False
        user32, gdi32 = _regions
        hwnd = widget.winfo_id()
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        if not region:
            return False
        for x0, y0, x1, y1 in rects:
            part = gdi32.CreateRectRgn(int(x0), int(y0), int(x1), int(y1))
            gdi32.CombineRgn(region, region, part, 2)   # RGN_OR
            gdi32.DeleteObject(part)
        if user32.SetWindowRgn(hwnd, region, True):
            return True   # the system owns the region now
        gdi32.DeleteObject(region)
        return False
    except (AttributeError, OSError, ValueError, tk.TclError):
        return False


def _lay_out_unshown(canvas, item, frame):
    """Size the canvas window ``item`` (``frame``) although the canvas never draws.

    A canvas lays its windows out while it redraws, and a withdrawn window never
    redraws (the hidden bundle self-test reveals controls in one). A geometry
    request still places a window that lies in view: move the card to the view's
    corner, grow it by a pixel and back so it makes one, then put it back.
    """
    x, y = canvas.coords(item)
    canvas.coords(item, canvas.canvasx(0), canvas.canvasy(0))
    frame.configure(padding=(0, 0, 0, 1))
    frame.update_idletasks()
    frame.configure(padding=0)
    frame.update_idletasks()
    canvas.coords(item, x, y)


class CardPlate:
    """One card: a rounded plate on the page canvas, its content in a window inset in it.

    ``body`` takes the content. The title row (display-font title and an optional
    info icon) sits above it, ``title_gap`` design px apart. ``bottom`` overrides
    the padding under the content (design px) when the content brings its own.
    """

    def __init__(self, page, title, *, info=None, title_gap=15, bottom=None, disclosure=None):
        self.page = page
        self.disclosure = disclosure
        self.visible = True
        self.top = self.bottom = 0
        canvas, s = page.canvas, page.scale
        self.bottom_inset = page.inset if bottom is None else px(s, bottom) + hairline(s)
        fill, edge = ui_theme.TOKENS["panel"], ui_theme.TOKENS["card_edge"]
        line = hairline(s)
        # The plate between its rounded ends is a plain frame in the plate colour,
        # its 1 px top and bottom edges two thin frames, and the card frame inset in
        # it. The ends (anti-aliased corners, transparent outside, and the side
        # edges between them) are the page's two column images, shared by every
        # card (StagePage._update_strips). The frame sits on the page's sheet, the
        # one window that holds every card (StagePage.sheet); without a sheet (no
        # window regions) it is a canvas window of its own.
        sheet = page.sheet
        self.holder = tk.Frame(canvas if sheet is None else sheet, background=fill, borderwidth=0,
                               highlightthickness=0, takefocus=0)
        self.retained = page.retained if sheet is not None else retain_surface(self.holder)
        self._edges = [tk.Frame(self.holder, background=edge, borderwidth=0, highlightthickness=0, takefocus=0)
                       for _ in range(2)]
        self._edges[0].place(x=0, y=0, relwidth=1, height=line)
        self._edges[1].place(x=0, rely=1, y=-line, relwidth=1, height=line)
        self.frame = ttk.Frame(self.holder, style="Card.TFrame")
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        self.title_row = self.title_label = self.info = None
        self._title_gap = title_gap
        if title is not None:
            self.title_row = ttk.Frame(self.frame, style="Card.TFrame")
            self.title_row.grid(row=0, column=0, sticky="w")
            self.title_label = ttk.Label(self.title_row, text=title, style="Stage.CardTitle.TLabel")
            self.title_label.pack(side="left")
            if info:
                self.info = InfoIcon(self.title_row, info, page=page)
                self.info.pack(side="left", padx=(px(s, 6), 0))
            self._fit_title()
            refit = fonts.on_change(canvas, lambda: self.frame.after_idle(self._fit_title))
            self.frame.bind("<Destroy>", lambda e: refit() if e.widget is self.frame else None, add="+")
        self.body = ttk.Frame(self.frame, style="Card.TFrame")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.tag = "card%d" % id(self)
        self._radius = r = px(s, page.CARD_RADIUS)
        self.frame.place(x=page.inset - r, y=page.inset)
        self.window = None if sheet is not None else canvas.create_window(
            0, 0, anchor="nw", window=self.holder, tags=(self.tag,))
        self._placed = None
        self._size = None
        self.frame.bind("<Configure>", self._configured, add="+")

    def _configured(self, event):
        # Only a new size can change the layout: a relayout per wheel step cost a
        # second whole-canvas repaint.
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self.page.layout()

    def _fit_title(self):
        # Keep the design's 18 px title line: a display face (and its descender
        # room) taller than that takes the excess out of the gap below, so the
        # content starts where the design puts it whichever face loaded.
        try:
            s = self.page.scale
            extra = max(0, self.title_label.winfo_reqheight() - px(s, TITLE_LINE))
            self.title_row.grid_configure(pady=(0, max(0, px(s, self._title_gap) - extra)))
        except tk.TclError:
            pass  # the card is being destroyed

    def set_visible(self, visible: bool):
        self.visible = bool(visible)
        if self.window is not None:
            self.page.canvas.itemconfigure(self.tag, state="normal" if self.visible else "hidden")
        elif not self.visible:
            self.holder.place_forget()
            self._placed = None
        self.page.layout()

    def height(self):
        return self.frame.winfo_reqheight() + self.page.inset + self.bottom_inset

    def place(self, y, width):
        """Lay the plate out from ``y`` (canvas px), ``width`` wide; returns its bottom."""
        canvas, inset, r = self.page.canvas, self.page.inset, self._radius
        x0, y0, x1, y1 = 0, y, width, y + self.height()
        self.top, self.bottom = y0, y1
        # Tk repaints an item's area for every coords call, even one that moves nothing.
        if self._placed == (y0, y1, width):
            return y1
        if self._placed is None or self._placed[2] != width:
            self.frame.place_configure(width=width - 2 * inset)
        self._placed = (y0, y1, width)
        if self.window is None:   # on the sheet, which spans the canvas from y 0 at x r
            self.holder.place(x=0, y=y0, width=x1 - x0 - 2 * r, height=y1 - y0)
        else:
            canvas.coords(self.window, x0 + r, y0)
            canvas.itemconfigure(self.window, width=x1 - x0 - 2 * r, height=y1 - y0)
        return y1


class StageDisclosure:
    """A quiet link row on the page canvas; opening it shows a card built in advance.

    The row is canvas text over the backdrop (chevron and title, ``muted``, white
    on hover or keyboard focus; the pointer's change is a short colour ramp, focus
    shows at once). A one-pixel frame takes keyboard focus for it: Return or Space
    opens and closes the card.
    """

    def __init__(self, page, title):
        self.page = page
        self.title = title
        self.opened = False
        self.top = self.bottom = 0
        self._hover = self._focus = False
        self._leave_job = None
        canvas, s = page.canvas, page.scale
        t = ui_theme.TOKENS
        self.tag = "disclosure%d" % id(self)
        icon, glyphs = _icon_font(page)
        body = fonts.font(page, "body")
        self._glyphs = (CLOSED_GLYPH, OPEN_GLYPH) if glyphs else (">", "v")
        self._chevron = canvas.create_text(0, 0, text=self._glyphs[0], font=icon if glyphs else body,
                                           fill=t["muted"], anchor="center", tags=(self.tag,))
        self._label = canvas.create_text(0, 0, text=title, font=body, fill=t["muted"], anchor="w", tags=(self.tag,))
        self._ring = canvas.create_rectangle(0, 0, 0, 0, outline=t["accent"], width=hairline(s), state="hidden")
        self._ramp = ColorRamp(canvas, lambda colour: canvas.itemconfigure(self.tag, fill=colour),
                               t["muted"], t["text"])
        canvas.tag_bind(self.tag, "<Enter>", self._entered, add="+")
        canvas.tag_bind(self.tag, "<Leave>", self._left, add="+")
        canvas.tag_bind(self.tag, "<Button-1>", lambda _e: self.flip(), add="+")
        # The proxy, not the canvas, takes focus: a canvas also hears the focus
        # events of the card fields it hosts. Created before the card, so Tab
        # reaches the row first and then the card's controls. On the page's sheet
        # it sits in the gap, outside the sheet's region: there, but never seen.
        self.proxy = tk.Frame(canvas if page.sheet is None else page.sheet, width=1, height=1,
                              background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=1)
        self._proxy_item = None if page.sheet is not None else canvas.create_window(0, 0, anchor="nw",
                                                                                    window=self.proxy)
        self.proxy.bind("<FocusIn>", lambda _e: self._set_focus(True), add="+")
        self.proxy.bind("<FocusOut>", lambda _e: self._set_focus(False), add="+")
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.proxy.bind(key, lambda _e: (self.flip(), "break")[1], add="+")
        self.card = CardPlate(page, None, disclosure=self)
        self.card.visible = False
        canvas.itemconfigure(self.card.tag, state="hidden")

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value: bool = True):
        self.opened = bool(value)
        canvas = self.page.canvas
        canvas.itemconfigure(self._chevron, text=self._glyphs[1 if self.opened else 0])
        focus, card = _focus_path(self.proxy), str(self.card.frame)
        if not self.opened and (focus == card or focus.startswith(card + ".")):
            self.proxy.focus_set()  # never leave the keyboard inside a hidden card
        self.card.set_visible(self.opened)

    def _entered(self, _event=None):
        if self._leave_job is not None:
            self.page.canvas.after_cancel(self._leave_job)
            self._leave_job = None
        self._hover = True
        self.page.canvas.configure(cursor="hand2")
        self._paint()

    def _left(self, _event=None):
        # Moving from the chevron to the title leaves one item and enters the
        # other in the same pick; settle at idle so the row does not flicker.
        def settle():
            self._leave_job = None
            self._hover = False
            try:
                self.page.canvas.configure(cursor="")
                self._paint()
            except tk.TclError:
                pass
        if self._leave_job is None:
            self._leave_job = self.page.canvas.after_idle(settle)

    def _set_focus(self, focused):
        self._focus = focused
        self.page.canvas.itemconfigure(self._ring, state="normal" if focused else "hidden")
        self._paint(now=True)

    def _paint(self, *, now=False):
        """Hover ramps the row's colour (120 ms in, 180 ms out); keyboard focus shows it at once."""
        lit = self._hover or self._focus
        if now:
            self._ramp.jump(ui_theme.TOKENS["text" if lit else "muted"])
        elif lit:
            self._ramp.enter()
        else:
            self._ramp.leave()

    def place(self, y, width):
        """Lay the row (and the card, when open) out from ``y``; returns the bottom."""
        canvas, s = self.page.canvas, self.page.scale
        middle = y + px(s, 9)
        canvas.coords(self._chevron, px(s, 4), middle)
        canvas.coords(self._label, px(s, 14), middle)
        # Off the text, so the pointer never leaves the row over the proxy.
        if self._proxy_item is None:
            r = px(s, self.page.CARD_RADIUS)
            self.proxy.place(x=width - 2 * r - 1, y=y)
        else:
            canvas.coords(self._proxy_item, width - 1, y)
        x0, y0, x1, y1 = canvas.bbox(self._chevron, self._label) or (0, y, width, y + px(s, 18))
        canvas.coords(self._ring, x0 - px(s, 3), y0 - px(s, 1), x1 + px(s, 3), y1 + px(s, 1))
        self.top, self.bottom = y, y + px(s, 18)
        if not self.opened:
            return self.bottom
        return self.card.place(self.bottom + px(s, self.page.CARD_GAP), width)

    def extent(self):
        """How tall :meth:`place` makes the row and, when open, its card."""
        s = self.page.scale
        return px(s, 18) + (px(s, self.page.CARD_GAP) + self.card.height() if self.opened else 0)


class InfoIcon(tk.Label):
    """A small "i" beside a card title or option; hover (after 150 ms) or focus shows ``text``.

    The bubble is a window item on the page canvas, above the cards, so it never
    takes focus and is never clipped by the card it explains. Under the pointer the
    icon ramps from ``dim`` to ``accent`` (120 ms in, 180 ms out); keyboard focus
    opens the bubble at once and leaves the icon ``dim`` (no drawn focus state,
    PLAN-5 N4).
    """

    def __init__(self, parent, text: str, *, page):
        self.text = text
        self.page = page
        self._job = None
        self._bubble = None
        icon, glyphs = _icon_font(parent)
        glyph, font = (INFO_GLYPH, icon) if glyphs else ("\u24d8", fonts.font(parent, "body"))
        t = ui_theme.TOKENS
        super().__init__(parent, text=glyph, font=font, background=t["panel"], foreground=t["dim"],
                         borderwidth=0, padx=0, pady=0, highlightthickness=0, takefocus=1,
                         cursor="question_arrow")
        self._ramp = ColorRamp(self, lambda colour: self.configure(foreground=colour), t["dim"], t["accent"])
        self.bind("<Enter>", lambda _e: self._later(), add="+")
        self.bind("<Leave>", lambda _e: self._left(), add="+")
        self.bind("<FocusIn>", lambda _e: self._show(), add="+")
        self.bind("<FocusOut>", lambda _e: self._hide(), add="+")
        self.bind("<Destroy>", lambda e: self._destroyed() if e.widget is self else None, add="+")

    @property
    def shown(self) -> bool:
        return self._bubble is not None

    def _later(self):
        self._ramp.enter()
        if self._job is None and self._bubble is None:
            self._job = self.after(BUBBLE_DELAY_MS, self._show)

    def _left(self):
        self._ramp.leave()
        self._hide()

    def _show(self):
        self._job = None
        if self._bubble is None:
            self._bubble = self.page.open_bubble(self, self.text)

    def _hide(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.page.close_bubble(self._bubble)
        self._bubble = None

    def _destroyed(self):
        self._hide()
        self._ramp.finish()


class CanvasRail:
    """A page's vertical scrollbar drawn on the page canvas, for the canvas that scrolls under it.

    It draws and behaves like the themed ``ttk.Scrollbar`` it stands in for
    (``Vertical.TScrollbar`` on ``surface``; see ``gui_theme.apply``): a trough of
    the surface colour, a rounded thumb in ``edge`` that turns ``scroll_hover``
    under the pointer and ``accent`` while pressed, ttk's thumb geometry and
    minimum length, drag, trough paging with auto-repeat (ttk's 300/100 ms) and
    the middle-button jump. A ttk scrollbar is a window of its own that redraws
    whole (~1-2 ms) on every scroll step; these four items are part of the
    canvas redraw the scroll makes anyway. ``command`` gets the scrollbar's
    ``moveto``/``scroll`` arguments. The page keeps it fixed in the viewport
    (:meth:`follow`, from its scroll path, so a scroll is still one redraw).

    The thumb's colour changes through a short ramp (hover 120 ms in, 180 ms out,
    press and release 120 ms, PLAN-5 2.11): its middle is mixed per frame, its two
    ends are the nearest of the ramp's pre-warmed caps. Rest, hover and pressed
    themselves are exactly the ttk faces; hiding the rail shows rest at once.
    """

    REPEAT_DELAY_MS, REPEAT_INTERVAL_MS = 300, 100   # ttk::Repeatedly

    def __init__(self, canvas, command, *, surface="bg"):
        self.canvas = canvas
        self.master = canvas
        self.command = command
        s = ui_scale(canvas)
        t = ui_theme.TOKENS
        self.thickness, self.minimum, self.radius = round(10 * s), round(16 * s), max(1, round(4 * s))
        self.first, self.last = 0.0, 1.0
        self._place = None          # (x, height) while shown
        self._looks = {}            # item -> last coords sent
        self._face = None
        self._hover = self._pressed = False
        self._drag = None           # (pointer y, first) while dragging the thumb
        self._repeat = None
        self._leave_job = None
        self.tag = "rail%d" % id(self)
        surface = t.get(surface, surface)
        self._faces = {face: self._caps(canvas, t[colour], surface)
                       for face, colour in (("rest", "edge"), ("hover", "scroll_hover"), ("pressed", "accent"))}
        tags = (self.tag, "page_rail")
        self._trough = canvas.create_rectangle(0, 0, 0, 0, fill=surface, width=0, state="hidden", tags=tags)
        self._middle = canvas.create_rectangle(0, 0, 0, 0, width=0, state="hidden", tags=tags)
        self._ends = tuple(canvas.create_image(0, 0, anchor="nw", state="hidden", tags=tags) for _ in range(2))
        self._caps_shown = None
        self._ramp = ColorRamp(canvas, self._paint_face, t["edge"], t["scroll_hover"], pressed=t["accent"],
                               images=lambda colour: self._caps(canvas, colour, surface))
        self._set_face("rest")
        canvas.tag_bind(self.tag, "<Enter>", self._entered, add="+")
        canvas.tag_bind(self.tag, "<Motion>", self._moved, add="+")
        canvas.tag_bind(self.tag, "<Leave>", self._left, add="+")
        canvas.tag_bind(self.tag, "<ButtonPress-1>", self._press, add="+")
        canvas.tag_bind(self.tag, "<B1-Motion>", self._dragged, add="+")
        canvas.tag_bind(self.tag, "<ButtonRelease-1>", self._release, add="+")
        canvas.tag_bind(self.tag, "<ButtonPress-2>", self._jump, add="+")
        canvas.tag_bind(self.tag, "<B2-Motion>", self._dragged, add="+")
        canvas.tag_bind(self.tag, "<ButtonRelease-2>", self._release, add="+")

    def _caps(self, canvas, colour, surface):
        """The thumb's two rounded ends in ``colour``: the end rows of the ttk thumb image."""
        from .. import gui_theme
        root, r, w = canvas.winfo_toplevel(), self.radius, self.thickness
        height = max(64, self.minimum)
        key = ("rail_caps", w, height, r, colour, surface)
        cache = art._cache(root)
        caps = cache.get(key)
        if caps is None:
            full = gui_theme.rounded_image(root, colour, colour, r, size=(w, height), line=0, surface=surface)
            caps = []
            for y in (0, height - r):
                cap = tk.PhotoImage(master=root, width=w, height=r)
                cap.tk.call(str(cap), "copy", str(full), "-from", 0, y, w, y + r)
                caps.append(cap)
            caps = cache[key] = (tuple(caps), colour)
        return caps

    # -- the ttk.Scrollbar surface the page uses ----------------------------------------------

    def winfo_reqwidth(self) -> int:
        return self.thickness

    def winfo_ismapped(self) -> bool:
        return self._place is not None

    def get(self):
        return self.first, self.last

    def set(self, first, last):
        first, last = float(first), float(last)
        if (first, last) != (self.first, self.last):
            self.first, self.last = first, last
            self._draw()

    def place(self, x, height):
        if self._place != (x, height):
            self._place = (x, height)
            for item in (self._trough, self._middle, *self._ends):
                self.canvas.itemconfigure(item, state="normal")
            self._draw()

    def place_forget(self):
        if self._place is not None:
            self._place = None
            self._cancel_repeat()
            self._drag = None
            self._hover = self._pressed = False
            self._set_face("rest")
            for item in (self._trough, self._middle, *self._ends):
                self.canvas.itemconfigure(item, state="hidden")

    def follow(self):
        """Keep the rail in the viewport after the canvas scrolled (before it redraws)."""
        self.first, self.last = map(float, self.canvas.yview())
        self._draw()

    # -- drawing ---------------------------------------------------------------------------

    def thumb(self):
        """The thumb's (top, height) in viewport px, as ttk lays it out."""
        height = self._place[1] if self._place else 0
        size = height - self.minimum
        top = int(size * self.first)
        return top, int(size * self.last) + self.minimum - top

    def _draw(self):
        if self._place is None:
            return
        x, height = self._place
        y0 = self.canvas.canvasy(0)
        top, length = self.thumb()
        w, r = self.thickness, self.radius
        self._coords(self._trough, x, y0, x + w, y0 + height)
        self._coords(self._ends[0], x, y0 + top)
        self._coords(self._middle, x, y0 + top + r, x + w, y0 + top + length - r)
        self._coords(self._ends[1], x, y0 + top + length - r)

    def _coords(self, item, *xy):
        # Tk redraws an item's area for every coords call, even one that moves nothing.
        if self._looks.get(item) != xy:
            self._looks[item] = xy
            self.canvas.coords(item, *xy)

    def _set_face(self, face):
        """Show ``face`` ("rest", "hover" or "pressed") at once."""
        self._face = face
        self._ramp.jump(self._faces[face][1])

    def _restyle(self):
        """Ramp to the face the pointer asks for."""
        face = "pressed" if self._pressed else "hover" if self._hover else "rest"
        if face == self._face:
            return
        previous, self._face = self._face, face
        if face == "pressed":
            self._ramp.press()
        elif face == "hover" and previous == "pressed":
            self._ramp.release()
        elif face == "hover":
            self._ramp.enter()
        else:
            self._ramp.leave()

    def _paint_face(self, colour):
        """The thumb in ``colour``: its middle exactly, its ends the nearest caps built so far."""
        (top, bottom), _colour = self._ramp.image(colour)
        canvas = self.canvas
        if top is not self._caps_shown:
            self._caps_shown = top
            canvas.itemconfigure(self._ends[0], image=top)
            canvas.itemconfigure(self._ends[1], image=bottom)
        canvas.itemconfigure(self._middle, fill=colour)

    # -- pointer (ttk's scrollbar bindings and element tracking) -------------------------------

    def _span(self):
        # ttk maps the trough minus the minimum thumb onto 0..1 ("fraction", "delta").
        return (self._place[1] if self._place else 0) - self.minimum

    def _on_thumb(self, event):
        top, length = self.thumb()
        return top <= event.y < top + length

    def _entered(self, event):
        if self._leave_job is not None:
            self.canvas.after_cancel(self._leave_job)
            self._leave_job = None
        self._moved(event)

    def _moved(self, event):
        hover = self._on_thumb(event)
        if hover != self._hover:
            self._hover = hover
            self._restyle()

    def _left(self, _event):
        # Moving between the rail's own items leaves one and enters the next.
        def settle():
            self._leave_job = None
            if self._hover and not self._pressed:
                self._hover = False
                try:
                    self._restyle()
                except tk.TclError:
                    pass   # the page is going away
        if self._leave_job is None:
            self._leave_job = self.canvas.after_idle(settle)

    def _fraction(self, event):
        span = self._span()
        return event.y / span if span > 0 else 0.0

    def _press(self, event):
        if self._place is None:
            return
        if self._on_thumb(event):
            self._drag = (event.y, self.first)
            self._pressed = True
            self._restyle()
            return
        direction = -1 if self._fraction(event) < self.first else 1
        self._repeatedly(lambda: self.command("scroll", direction, "pages"), self.REPEAT_DELAY_MS)

    def _jump(self, event):
        if self._place is None:
            return
        fraction = self._fraction(event)
        self.command("moveto", fraction)
        self._drag = (event.y, fraction)

    def _dragged(self, event):
        if self._drag is None:
            return
        y, first = self._drag
        span = self._span()
        self.command("moveto", first + ((event.y - y) / span if span > 0 else 0.0))

    def _release(self, event):
        self._cancel_repeat()
        self._drag = None
        if self._pressed:
            self._pressed = False
            self._hover = self._on_thumb(event)
            self._restyle()

    def _repeatedly(self, action, delay):
        self._cancel_repeat()
        def again():
            self._repeat = self.canvas.after(self.REPEAT_INTERVAL_MS, again)
            action()
        self._repeat = self.canvas.after(delay, again)
        action()

    def _cancel_repeat(self):
        if self._repeat is not None:
            try:
                self.canvas.after_cancel(self._repeat)
            except tk.TclError:
                pass
            self._repeat = None


class StagePage(ttk.Frame):
    """One vertical scroll region of cards on a canvas; the backdrop shows around them."""

    CARD_MAX = 613
    CARD_PAD = 20
    CARD_GAP = 15
    CARD_RADIUS = 6
    TITLE_GAP = 15
    # A disclosure row sits closer under the cards: with the design's cards it
    # then still ends above the footer links of the 810 px design window.
    DISCLOSURE_GAP = 10
    END_GAP = END_GAP          # under the viewport while the cards overflow (UPDATE 9)

    def __init__(self, parent):
        super().__init__(parent)
        self.scale = s = ui_scale(self)
        # Content starts inside the card's 1 px edge (Figma: 20 + 1).
        self.inset = px(s, self.CARD_PAD) + hairline(s)
        self.card_width = px(s, self.CARD_MAX)
        self.content_height = 0
        # The canvas that scrolls is only as wide as the card column and its rail:
        # Tk redraws all of it on every scroll step. The backdrop right of it is a
        # second canvas that never scrolls.
        self.canvas = tk.Canvas(self, background=ui_theme.TOKENS["bg"], highlightthickness=0,
                                borderwidth=0, takefocus=0, yscrollincrement=WHEEL_UNIT_PIXELS)
        self.canvas.pack(side="left", fill="y")
        self.side = tk.Canvas(self, background=ui_theme.TOKENS["bg"], highlightthickness=0, borderwidth=0,
                              takefocus=0, width=1, height=1)
        self.side.pack(side="left", fill="both", expand=True)
        # Under the viewport while the cards overflow: the gap to the footer's links.
        self.foot = tk.Canvas(self, background=ui_theme.TOKENS["bg"], highlightthickness=0, borderwidth=0,
                              takefocus=0, height=1)
        self._foot_packed = False
        # The rail is drawn on the canvas, in the redraw every scroll makes anyway
        # (a ttk.Scrollbar redrew whole on every step).
        self.scrollbar = CanvasRail(self.canvas, self._yview)
        self._bar_place = None    # (x, height) while the rail is shown
        self._view = (0.0, 1.0)   # the view Tk last reported
        self.canvas.configure(yscrollcommand=self._scrolled)
        self._entries = []        # cards and disclosures, top to bottom
        self._plates = {}         # card frame -> CardPlate
        self._holders = {}        # card window (the frame's holder) -> CardPlate
        self._disclosures = []
        self._heights = {}        # card frame -> requested height at the last layout
        self._fit = None          # fit(): what gives the cards room instead of scrolling
        self._fitting = False
        # The plates' rounded ends: one image per side for every card at once.
        self._strips = tuple(self.canvas.create_image(0, 0, anchor="nw", state="hidden",
                                                      tags=("card_plate", "plate_strip")) for _ in range(2))
        self._strip_images = (None, None)
        self._strip_key = None
        # Every card sits on one sheet: a window spanning the content, shaped to
        # the cards (the canvas shows, and takes the pointer, in the gaps). A scroll
        # then moves one window, not one per card, and the sheet never leaves the
        # view, so no card has to be painted whole again when it scrolls back in
        # (a retained surface is dropped while its window is unmapped). Without
        # window regions every card is a canvas window of its own.
        self.sheet = tk.Frame(self.canvas, background=ui_theme.TOKENS["panel"], borderwidth=0,
                              highlightthickness=0, takefocus=0)
        self.retained = False
        self._sheet_item = None
        self._sheet_key = None
        if set_window_region(self.sheet, ()):
            self.retained = retain_surface(self.sheet)
            self._sheet_item = self.canvas.create_window(0, 0, anchor="nw", window=self.sheet, state="hidden")
        else:
            self.sheet.destroy()
            self.sheet = None
        # Wheel notches ease (motion on); the rail, keys, reveal and code scroll at once.
        self.scroller = WheelScroller(self.canvas, self._scroll_units, unit_pixels=WHEEL_UNIT_PIXELS)
        self._update = IdleUpdate(self.canvas, self._layout)
        self.canvas.bind("<Configure>", self._resized, add="+")
        self.bind("<Configure>", lambda e: self.layout() if e.widget is self else None, add="+")
        self.side.bind("<Configure>", self._side_resized, add="+")
        self.foot.bind("<Configure>", self._foot_resized, add="+")
        self.bind_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Unmap>", lambda e: self.scroller.stop() if e.widget is self else None, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.backdrop = Backdrop.of(self)
        if self.backdrop is not None:
            self.backdrop.attach(self.canvas, self.open_areas)
            self.backdrop.attach(self.side)
            self.backdrop.attach(self.foot)

    # -- building ------------------------------------------------------------------------

    def add_card(self, title: str, *, info: str | None = None, title_gap: int = TITLE_GAP,
                 bottom: int | None = None) -> CardPlate:
        plate = CardPlate(self, title, info=info, title_gap=title_gap, bottom=bottom)
        self._entries.append(plate)
        self._plates[plate.frame] = plate
        self._holders[plate.holder] = plate
        self.layout()
        return plate

    def add_disclosure(self, title: str) -> StageDisclosure:
        """A link row below the cards so far, and its hidden card (built now, shown on open)."""
        disclosure = StageDisclosure(self, title)
        self._entries.append(disclosure)
        self._disclosures.append(disclosure)
        self._plates[disclosure.card.frame] = disclosure.card
        self._holders[disclosure.card.holder] = disclosure.card
        self.layout()
        return disclosure

    def fit(self, flex):
        """Keep every card on the page instead of scrolling (USER-ADDITIONS UPDATE 17.5).

        Each layout tells ``flex(spare)`` how many px the cards have left above
        ``END_GAP`` (less ``FIT_ROUNDING``; negative: how many too many); it gives its
        growable content that much more or less room and returns True when it changed
        a size. The page then lets the new size reach the cards and asks again with
        the new sizes until ``flex`` returns False, so the cards are never stacked on a
        stale height (and ``flex`` sees its content laid out at its new size). The page
        still scrolls when ``flex`` has nothing left to give.
        """
        self._fit = flex
        self.layout()

    def watch_focus(self):
        """Let Tab reach every control on the page, scrolled into view first.

        Tk skips windows it cannot see when tabbing, and a card scrolled out of
        the canvas is unmapped. Each focusable control asks ``_focus_ok`` instead
        (a takefocus script), which scrolls it into view and says yes. Call it
        once the cards are built. (Not a ``<FocusIn>`` binding: that also fires
        when the window is merely re-activated, and would yank the page back to
        the last field after the user scrolled away.)
        """
        script = self.register(self._focus_ok)
        pending = [plate.frame for plate in self._plates.values()]
        pending += [disclosure.proxy for disclosure in self._disclosures]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if str(widget.cget("takefocus")) in ("ttk::takefocus", "1"):
                widget.configure(takefocus=script)

    # -- layout --------------------------------------------------------------------------

    def layout(self):
        """Lay the cards out at idle (coalesced)."""
        self._update.schedule()

    def _layout(self):
        if self._fitting:
            return   # called from the fit's own update: the pass that runs the fit lays the page out
        if self._fit is not None:
            self._fit_cards()
        canvas, s = self.canvas, self.scale
        height = canvas.winfo_height()
        reserve = self.scrollbar.winfo_reqwidth() + px(s, 8)
        available = self.winfo_width()
        if available <= 1:
            available = px(s, self.CARD_MAX) + reserve  # not laid out yet
        self.card_width = card = max(px(s, 160), min(available - reserve, px(s, self.CARD_MAX)))
        width = card + reserve
        if canvas.winfo_reqwidth() != width:
            canvas.configure(width=width)   # the column; the side canvas takes the rest
        y = 0
        for entry in self._entries:
            if isinstance(entry, CardPlate) and not entry.visible:
                continue
            if y:
                y += px(s, self.DISCLOSURE_GAP if isinstance(entry, StageDisclosure) else self.CARD_GAP)
            y = entry.place(y, card)
        self._update_strips(card)
        self._update_sheet(card, y)
        self._heights = {frame: frame.winfo_reqheight() for frame in self._plates}
        self.content_height = y
        self._layout_foot(y)
        if tuple(map(float, canvas.tk.splitlist(canvas.cget("scrollregion")))) != (0, 0, width, y):
            canvas.configure(scrollregion=(0, 0, width, y))   # (a configure repaints the whole canvas)
        shown = height > 1 and y > height
        bar = (card + px(s, 8), height) if shown else None
        if bar != self._bar_place:
            if bar is None:
                self.scrollbar.place_forget()
            else:
                self.scrollbar.place(*bar)
            self._bar_place = bar
        if not shown and height > 1:  # a withdrawn window has no viewport to fit
            self.scroller.stop()
            canvas.yview_moveto(0)
        if self.backdrop is not None:
            self.backdrop.sync(canvas, redraw=True)
            if self._foot_packed:
                self.backdrop.sync(self.foot, redraw=True)

    def _stack_height(self):
        """How tall the cards stack (canvas px), as :meth:`_layout` places them."""
        s, y = self.scale, 0
        for entry in self._entries:
            if isinstance(entry, CardPlate) and not entry.visible:
                continue
            if y:
                y += px(s, self.DISCLOSURE_GAP if isinstance(entry, StageDisclosure) else self.CARD_GAP)
            y += entry.height() if isinstance(entry, CardPlate) else entry.extent()
        return y

    def _room(self, page) -> int:
        """How tall the cards may stack on a page ``page`` px tall before they overflow."""
        room = page - end_gap(self.scale, self._compact())
        return room if self._fit is None else room + px(self.scale, FIT_ROUNDING)

    def _fit_cards(self):
        """Give the fit's content the room the page has above the gap to the footer's links."""
        page = self.winfo_height()
        if page <= 1 or self._fitting:
            return   # not laid out yet (no room to fit), or already fitting
        self._fitting = True
        try:
            # One change settles it; the next call, on the new sizes, confirms it.
            for _ in range(3):
                if not self._fit(self._room(page) - self._stack_height()):
                    break
                # The new size travels up to the card frames through the geometry
                # managers at idle; let it arrive before the cards are measured again.
                self.update_idletasks()
        finally:
            self._fitting = False

    def _compact(self) -> bool:
        widget = self.master
        while widget is not None:
            mode = getattr(widget, "mode", None)
            if mode in ("design", "compact"):
                return mode == "compact"
            widget = getattr(widget, "master", None)
        return False

    def _layout_foot(self, content) -> None:
        """Keep the gap to the footer's links under the viewport while the cards do not fit above it."""
        gap = end_gap(self.scale, self._compact())
        page = self.winfo_height()
        wanted = page > 1 and content > self._room(page)
        if wanted:
            if int(self.foot.cget("height")) != gap:
                self.foot.configure(height=gap)
            if not self._foot_packed:
                self.foot.pack(side="bottom", fill="x", before=self.canvas)
                self._foot_packed = True
        elif self._foot_packed:
            self.foot.pack_forget()
            self._foot_packed = False

    def _update_strips(self, width):
        """Give the visible plates their rounded ends: two column images, made when the cards move.

        Each is ``radius`` px wide and spans the content: every card's two corners
        on that side (anti-aliased, transparent outside) and its side edge and fill
        between them, transparent between cards. Two blended images per scroll step
        instead of one per plate row or corner.
        """
        s, t, canvas = self.scale, ui_theme.TOKENS, self.canvas
        radius, line = px(s, self.CARD_RADIUS), hairline(s)
        spans = tuple(sorted((p.top, p.bottom) for p in self._plates.values() if p.visible))
        key = (width, radius, line, spans)
        if key == self._strip_key:
            return
        self._strip_key = key
        if not spans:
            for item in self._strips:
                canvas.itemconfigure(item, state="hidden")
            self._strip_images = (None, None)
            return
        root, height = canvas.winfo_toplevel(), spans[-1][1]
        images = tuple(art.plate_strip(root, height, radius, line, t["panel"], t["card_edge"], side, spans)
                       for side in ("left", "right"))
        for item, image, x in zip(self._strips, images, (0, width - radius)):
            canvas.coords(item, x, 0)
            canvas.itemconfigure(item, image=image, state="normal")
        self._strip_images = images   # the canvas items need the images alive

    def _update_sheet(self, width, height):
        """Size the sheet to the content and shape it to the visible cards (when they moved)."""
        if self.sheet is None:
            return
        r = px(self.scale, self.CARD_RADIUS)
        spans = tuple(sorted((p.top, p.bottom) for p in self._plates.values() if p.visible))
        key = (width, height, spans)
        if key == self._sheet_key:
            return
        self._sheet_key = key
        canvas = self.canvas
        if not spans:
            canvas.itemconfigure(self._sheet_item, state="hidden")
            return
        canvas.coords(self._sheet_item, r, 0)
        canvas.itemconfigure(self._sheet_item, width=width - 2 * r, height=max(1, height), state="normal")
        set_window_region(self.sheet, [(0, top, width - 2 * r, bottom) for top, bottom in spans])

    def open_areas(self):
        """Where the backdrop shows: the viewport minus the visible cards (viewport px)."""
        canvas = self.canvas
        width, height, top = canvas.winfo_width(), canvas.winfo_height(), canvas.canvasy(0)
        column = min(width, self.card_width)
        areas = [(column, 0, width, height)] if width > column else []
        y = 0
        for plate in sorted((p for p in self._plates.values() if p.visible), key=lambda p: p.top):
            y0, y1 = max(0, plate.top - top), min(height, plate.bottom - top)
            if y1 <= 0 or y0 >= height:
                continue
            if y0 > y:
                areas.append((0, y, column, y0))
            y = max(y, y1)
        if y < height:
            areas.append((0, y, column, height))
        return [area for area in areas if area[2] > area[0] and area[3] > area[1]]

    def _scrolled(self, first, last):
        view = (float(first), float(last))
        if view != self._view:
            # Tk calls this at the end of the whole-canvas repaint a scroll causes:
            # what the card moves left for Windows to repaint is already drawn.
            self._view = view
            if self.canvas.winfo_ismapped():
                _discard_expose(self.canvas)
        self.scrollbar.set(first, last)
        # Tk reports a scroll only after it has repainted the canvas for it; the
        # page's own scrolls have already moved the backdrop (see _follow_view),
        # this catches the rest (a scrollregion that shrank under the view).
        self._follow_view()
        # A card off-screen gets no <Configure> when its content grows.
        if any(frame.winfo_reqheight() != height for frame, height in self._heights.items()):
            self.layout()

    # -- scrolling -----------------------------------------------------------------------
    #
    # Tk repaints the whole visible canvas for every scroll. The backdrop is fixed
    # to the window, so its items move by the same amount the other way; doing that
    # in the same breath as the scroll keeps it to one repaint per step (moving
    # them from the scroll report would schedule a second whole-canvas repaint).

    def _follow_view(self):
        self.scrollbar.follow()
        if self.backdrop is not None:
            self.backdrop.sync(self.canvas)

    def _yview(self, *args):
        """The rail's command: scroll at once, then keep the backdrop in place."""
        self.scroller.stop()
        self.canvas.yview(*args)
        self._follow_view()

    def _scroll_units(self, units):
        """One immediate scroll by ``units`` (the wheel's path); returns the units really moved."""
        canvas = self.canvas
        before = canvas.canvasy(0)
        canvas.yview_scroll(units, "units")
        self._follow_view()
        return int(round((canvas.canvasy(0) - before) / WHEEL_UNIT_PIXELS))

    def _resized(self, _event=None):
        self.scroller.stop()
        self.layout()

    def _side_resized(self, _event=None):
        if self.backdrop is not None:
            self.backdrop.sync(self.side, redraw=True)

    def _foot_resized(self, _event=None):
        if self.backdrop is not None:
            self.backdrop.sync(self.foot, redraw=True)

    def _wheel(self, event):
        target = event.widget
        while target is not None and target is not self:
            # A nested page or a control with its own wheel semantics owns the event.
            if isinstance(target, (ScrollPage, StagePage, ttk.Treeview, tk.Text, tk.Listbox,
                                   ttk.Combobox, tk.Spinbox, ttk.Spinbox, tk.Scrollbar, ttk.Scrollbar)):
                return
            target = getattr(target, "master", None)
        if target is self and self.canvas.yview() != (0.0, 1.0):
            self.scroll_page_wheel(event)
            return "break"

    def scroll_page_wheel(self, event):
        # Same distances as ScrollPage: subpixel input kept, 60 design px per notch;
        # the scroller travels them eased, or at once with motion off.
        notch = WHEEL_PIXELS_PER_NOTCH * self.scale
        if event.delta:
            pixels = -event.delta / 120 * notch
        else:
            pixels = -notch if getattr(event, "num", 0) == 4 else notch
        first, last = self.canvas.yview()
        if (pixels < 0 and first <= 0.0) or (pixels > 0 and last >= 1.0):
            self.scroller.stop()   # outward at an edge: no debt for the way back
            return
        self.scroller.add(pixels)

    def _scroll_to(self, y):
        total, viewport = self.content_height, self.canvas.winfo_height()
        if total > viewport > 0:
            self.scroller.stop()
            self.canvas.yview_moveto(max(0, min(y, total - viewport)) / total)
            self._follow_view()

    def _plate_of(self, widget):
        host = self.canvas if self.sheet is None else self.sheet
        while widget is not None and widget.master is not host:
            widget = widget.master
        return self._holders.get(widget)

    def reveal(self, widget):
        """Scroll so ``widget`` is fully in view; a closed disclosure opens first."""
        disclosure = next((d for d in self._disclosures if d.proxy is widget), None)
        plate = None if disclosure is not None else self._plate_of(widget)
        if disclosure is None and plate is None:
            return
        if plate is not None and plate.disclosure is not None and not plate.disclosure.opened:
            plate.disclosure.set_open(True)
        self._update.cancel()
        self._layout()
        self.update_idletasks()
        if disclosure is not None:
            top, bottom = disclosure.top, disclosure.bottom
        else:
            if plate.window is not None and not plate.holder.winfo_ismapped():
                # Tk sizes a card window's controls only once the card is in view.
                # (On the sheet, the cards are placed and sized whether shown or not.)
                self._scroll_to(plate.top)
                self.update_idletasks()
                if not plate.holder.winfo_ismapped():
                    _lay_out_unshown(self.canvas, plate.window, plate.frame)
                    self._update.cancel()
                    self._layout()
            top = plate.top + self.inset
            if widget is not plate.frame:
                top += widget.winfo_rooty() - plate.frame.winfo_rooty()
            bottom = top + widget.winfo_height()
        view, viewport = self.canvas.canvasy(0), self.canvas.winfo_height()
        if top >= view and bottom <= view + viewport:
            return
        margin = px(self.scale, REVEAL_MARGIN)
        self._scroll_to(top - margin if top < view or bottom - top > viewport else bottom + margin - viewport)
        self.update_idletasks()

    def _focus_ok(self, path):
        """Takefocus script: skip disabled and hidden controls, reveal the rest."""
        try:
            widget = self.nametowidget(path)
        except KeyError:
            return 0
        if not self.winfo_viewable():
            return 0
        if isinstance(widget, ttk.Widget):
            if widget.instate(["disabled"]):
                return 0
        elif "state" in widget.keys() and str(widget.cget("state")) == "disabled":
            return 0
        plate = self._plate_of(widget)
        if plate is not None and not plate.visible:
            return 0
        self.reveal(widget)
        return 1

    # -- info bubbles --------------------------------------------------------------------

    def open_bubble(self, widget, text):
        """Show ``text`` in a bubble ``px(6)`` below ``widget`` (above when there is no room)."""
        canvas, s = self.canvas, self.scale
        try:
            if not widget.winfo_ismapped():
                return None
        except tk.TclError:
            return None
        t = ui_theme.TOKENS
        pad_x, pad_y = px(s, 10), px(s, 6)
        tip = tk.Canvas(canvas, background=t["panel"], highlightthickness=0, borderwidth=0, takefocus=0)
        label = tip.create_text(pad_x, pad_y, text=text, anchor="nw", fill=t["text"],
                                font=fonts.font(self, "body"), width=px(s, BUBBLE_WIDTH))
        x0, y0, x1, y1 = tip.bbox(label)
        w, h = x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y
        tip.configure(width=w, height=h)
        r = min(px(s, BUBBLE_RADIUS), w // 2, h // 2)
        tip.create_rectangle(r, 0, w - r, h, fill=t["field"], width=0)
        tip.create_rectangle(0, r, w, h - r, fill=t["field"], width=0)
        root = canvas.winfo_toplevel()
        for which, x, y in (("nw", 0, 0), ("ne", w - r, 0), ("sw", 0, h - r), ("se", w - r, h - r)):
            tip.create_image(x, y, anchor="nw", image=art.corner_rgba(root, r, t["field"], t["field"], which))
        tip.tag_raise(label)
        left, top = canvas.canvasx(0), canvas.canvasy(0)
        ox = left + widget.winfo_rootx() - canvas.winfo_rootx()
        oy = top + widget.winfo_rooty() - canvas.winfo_rooty()
        x = max(left, min(ox - px(s, 6), left + canvas.winfo_width() - w - 1))
        y = oy + widget.winfo_height() + px(s, 6)
        if y + h > top + canvas.winfo_height():  # no room below: open upwards
            y = oy - h - px(s, 6)
        item = canvas.create_window(x, y, window=tip, anchor="nw", tags=("bubble",))
        tk.Misc.lift(tip)  # the window above the cards (Canvas.lift would raise an item)
        return tip, item

    def close_bubble(self, handle):
        if handle is not None:
            tip, item = handle
            try:
                self.canvas.delete(item)
                tip.destroy()
            except tk.TclError:
                pass

    def _destroyed(self, event):
        if event.widget is self:
            self.scroller.stop()
            self.scrollbar._cancel_repeat()
            self.winfo_toplevel().unbind("<MouseWheel>", self.bind_id)
            if self.backdrop is not None:
                self.backdrop.detach(self.canvas)
                self.backdrop.detach(self.side)
                self.backdrop.detach(self.foot)


def bleed_left(holder, widget, pixels: int) -> None:
    """Show ``widget`` across ``holder``, starting ``pixels`` left of it (clipped there).

    A table's cell text sits :data:`TREE_CELL_INSET` px inside its columns; bleeding
    the table that far left puts the text on the card's content edge, in line with
    the controls above it. ``holder`` keeps the widget's requested height.
    """
    widget.place(in_=holder, x=-pixels, y=0, relwidth=1, width=pixels)
    def follow(event):
        if event.widget is widget and int(holder.cget("height")) != event.height:
            holder.configure(height=event.height)
    widget.bind("<Configure>", follow, add="+")


def form_row(parent, label: str, *, pady=0) -> tuple[ttk.Frame, ttk.Label]:
    """A card row: a muted label hugging its text, then the field column (column 1)."""
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill="x", pady=pady)
    row.columnconfigure(1, weight=1)
    text = ttk.Label(row, text=label, style="Stage.CardMuted.TLabel")
    text.grid(row=0, column=0, sticky="w")
    return row, text
