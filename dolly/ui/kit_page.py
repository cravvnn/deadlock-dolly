"""The page kit: card pages over the window's backdrop, on the PERF-NOTES sheet model (PLAN-5 2.5).

Camera, Look, Export, Controls and Settings are built from it (Replay keeps its
``StagePage``, which this mirrors). What one scroll step costs decides the shape
(PERF-NOTES §2, R1-R13): Tk has no blit-scroll, so every step redraws the whole
visible scrolling canvas and moves every canvas window. So:

- one scrolling ``tk.Canvas``, only as wide as the card block plus the rail gutter
  (``B + px(18)``), a static ``side`` canvas for the backdrop right of it and, on a
  pinned page, a static ``dock`` canvas below it (R1);
- one **sheet** (``tk.Frame`` in ``panel``) is the scrolling canvas's only window item:
  it spans the content from y 0, is shaped to the visible plate middles
  (:func:`.stage_page.set_window_region`) and keeps its surface
  (:func:`.stage_page.retain_surface`), so a scroll moves one window and repaints
  nothing on it (R2). Every card is a canvas *placed* on it (:mod:`.kit_card`);
- the plates' rounded ends are a few ``art.plate_strip`` column images (≤ 4 for two
  columns, 2 for one), rebuilt only when a plate moves (R5); the rail is a
  :class:`.stage_page.CanvasRail` drawn on the canvas (R6);
- the wheel goes through :class:`.tween.WheelScroller` (eased 140 ms; instant with
  motion off); a scroll step is exactly ``yview_scroll``, the rail's ``follow()``, the
  backdrop's ``sync`` and (Camera) the fade's update (R8). Rail drags, keys, reveal and
  code scrolls are immediate and stop the eased motion first (R9).

Without window regions (not Windows, or ``SetWindowRgn`` failing) every card is a
canvas window of its own (each retained), never one per widget.

The scroll viewport ends ``END_GAP`` above the footer's link row (USER-ADDITIONS UPDATE
9: 29 design px, Replay's gap under its last card in the design window; 15 in the
shell's compact mode): a static ``foot`` canvas of backdrop fills the gap, or, on a
pinned page, the dock does (its card ends the same gap above the links). Content cut at
either scroll edge fades out (:class:`.fade.ScrollFade`, on every page; never in a
hidden or cloaked window).

Columns follow PLAN-5 3.0.2: the side column shrinks first, then the main column;
below the main minimum the page is one column (main cards, side cards, full-width
cards). The block never grows past the design width (left-aligned, like Replay).
:meth:`KitPage.bento` lines two-column cards up in rows instead (UPDATE 8).

Portions adapted from RenderLock 0.3.0b16 (agr/gui_layout.py, agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import art, fonts, kit_styles
from .backdrop import Backdrop
from .boil import IdleUpdate
from .canvas_controls import CanvasControl
from .fade import ScrollFade
from .kit_card import KitBlock, KitCard, KitColumn, KitFlow, KitRow, KitSection, KitText, _Element
from .kit_controls import RowLayout, SearchField, finish_motion, safe_unbind
from .scale import hairline, px, ui_scale
from .stage_page import BUBBLE_RADIUS, BUBBLE_WIDTH, END_GAP, REVEAL_MARGIN, CanvasRail, StagePage, end_gap, \
    retain_surface, set_window_region
from .tween import WheelScroller
from .widgets import WHEEL_PIXELS_PER_NOTCH, WHEEL_UNIT_PIXELS, ScrollPage


@dataclass(frozen=True)
class ColumnSpec:
    """A two-column page's widths in design px (PLAN-5 3.0.2)."""
    main: int = 613
    side: int = 297
    gap: int = 20
    main_min: int = 613
    side_min: int = 297


def column_layout(spec: ColumnSpec, content: int, scale: float) -> tuple[bool, int, int, int]:
    """``(two_columns, main px, side x px, block px)`` for ``content`` px of width (PLAN-5 3.0.2).

    The side column shrinks first (to ``side_min``), then the main column; below
    ``main_min`` the page is one column ``min(content, main)`` wide. Positions are
    rounded from design px, so the block is exactly ``px(main + gap + side)`` at full size.
    """
    c = content / scale
    need = spec.main + spec.gap + spec.side
    full = c >= need - 1e-6
    if full:
        main, side = spec.main, spec.side
    else:
        side = max(spec.side_min, c - spec.gap - spec.main)
        main = min(spec.main, c - spec.gap - side)
    if full or main >= spec.main_min - 1e-6:
        return True, px(scale, main), px(scale, main + spec.gap), min(content, px(scale, main + spec.gap + side))
    one = min(content, px(scale, spec.main))
    return False, one, 0, one


_user32 = None


def _discard_expose(widget) -> None:
    """Tell Windows the canvas is fully painted right after Tk repainted it for a scroll.

    (The technique of ``stage_page._discard_expose``, kept private there: a scroll
    repaints the whole canvas and moves the sheet during that repaint; the move marks
    the strip it uncovers dirty and Windows would ask for a second near-full repaint.)
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


class KitBand:
    """A row drawn on the backdrop across the block (Controls' toolbar and feedback line).

    Its labels and buttons are items on the scrolling canvas (they scroll with the
    cards); buttons use binary-alpha corners (PR5). :meth:`search` adds the kit
    :class:`.kit_controls.SearchField`, whose middle sits on the sheet. Its focus
    stand-ins sit on the sheet just below the row, outside the sheet's region.
    """

    def __init__(self, page, *, height=30, top=None):
        self.page = page
        self.top = top
        self.visible = True
        self.y = None
        self.height = 0
        self.searches = []
        parent = page.sheet if page.sheet is not None else page
        self.core = RowLayout(page.canvas, scale=page.scale, page=page, height=height, surface=None,
                              plate="binary", changed=page.layout, proxy_parent=parent)

    def label(self, text="", *, textvariable=None, width=None, tone="muted", info=None, wrap=False):
        return self.core.label(text, textvariable=textvariable, width=width, tone=tone, info=info, wrap=wrap)

    def text(self, text="", *, textvariable=None, tone="muted", info=None):
        """A wrapping text filling the rest of the row (its info icon after the last line)."""
        return self.core.label(text, textvariable=textvariable, tone=tone, info=info, wrap=True)

    def button(self, text, command=None, *, kind="secondary", state="normal", width=None):
        return self.core.button(text, command, kind=kind, state=state, width=width)

    def info(self, text):
        return self.core.info(text)

    def gap(self, design_px):
        self.core.gap(design_px)

    def push(self):
        self.core.push()

    def search(self, variable, *, placeholder="", width=None) -> SearchField:
        field = SearchField(self.page, variable, placeholder=placeholder, width=width)
        self.searches.append(field)
        return self.core.custom(field)

    def controls(self):
        return self.core.controls()

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible != self.visible:
            self.visible = visible
            if not visible:
                self.core.hide()
                self.y = None
            self.page.layout()

    def _layout(self, y, width) -> int:
        page = self.page
        proxy = (0, y + self.core.row_height) if page.sheet is not None else (0, 0)
        height = self.core.layout(0, y, width, proxy=proxy)
        if page.sheet is None:   # under the canvas: there for focus, never seen
            for group in self.core.groups:
                tk.Misc.lower(group.proxy, page.canvas)
        return height


class _AdoptedBlock(_Element):
    """The content of a flow host card: the adopted frame, placed ``in_`` the card's canvas."""

    def __init__(self, host):
        super().__init__(host.card, None)
        self.hostcard = host

    def _layout(self, x0, y, width) -> int:
        self.x0, self.width = x0, width
        frame = self.hostcard.adopted
        if frame is None or self.hostcard.mode != "flow":
            return 0
        self.hostcard._place(("flow", x0, y, width), in_=self.card.host)
        return frame.winfo_reqheight()

    def _hide(self) -> None:
        self.y = None


class KitHostCard:
    """A card that shows a frame owned elsewhere (Shot Playback, N10): ``"flow"`` or ``"dock"``.

    ``adopt(frame)`` takes the frame from its previous host and places it ``in_`` this
    card (flow: the card's canvas on the sheet, so it moves with the sheet and stays
    mapped while scrolled away; dock: the page's static dock canvas). The card's plate
    and title show only while it holds a frame. A docked card becomes the last card of
    the scroll area while the viewport would drop below ``MIN_VIEWPORT`` (N10).

    ``art(root, scale, width, height, box=None)`` (optional, e.g. :func:`.art.playback_art`)
    paints the card's background: one image per card size, the bottom item of the plate
    (inside its 1 px edge, the edge and the title on top), made at layout only. The
    adopted frame gets the same picture and its own offset in the card through its
    ``card_backdrop(image, x, y)`` hook (None while it has no art host), so what it
    draws sits on the same picture.
    """

    def __init__(self, page, title, *, where, edge="card_edge_pinned", art=None, title_font="card_title"):
        if where not in ("flow", "dock"):
            raise ValueError(f"where must be 'flow' or 'dock', not {where!r}")
        if where == "dock" and page.dock is None:
            raise ValueError("a docked host card needs a pinned page (KitPage(pinned=True))")
        self.page = page
        self.where = self.mode = where
        self.title = title
        self.edge = edge
        self.art = art
        self.adopted = None
        self.card = KitCard(page, title, edge=edge, column="host", title_font=title_font)
        self.card.visible = False
        self.block = _AdoptedBlock(self)
        self.card.elements.append(self.block)
        self._previous = {}
        self._binding = None
        self._seen = None
        self._placed = None
        self._dock = None
        self._art_images = {}           # where -> the image(s) shown (canvas items need them alive)
        self._backdrop = None           # what the adopted frame was last handed
        self._flow_art = None           # the art item on the card's canvas (flow)
        if where == "dock":
            t, dock = ui_theme.TOKENS, page.dock
            self._dock = {"strips": [dock.create_image(0, 0, anchor="nw", state="hidden") for _ in range(2)],
                          "middle": dock.create_rectangle(0, 0, 0, 0, width=0, fill=t["panel"], state="hidden"),
                          "art": dock.create_image(0, 0, anchor="nw", state="hidden"),
                          "edges": [dock.create_rectangle(0, 0, 0, 0, width=0, fill=t[edge], state="hidden")
                                    for _ in range(2)],
                          "title": dock.create_text(0, 0, text=title, anchor="w", fill=t["text"], state="hidden",
                                                    font=fonts.font(page, title_font)),
                          "images": None, "key": None, "rect": None}

    # -- adoption -------------------------------------------------------------------------------
    def adopt(self, frame) -> None:
        previous = getattr(frame, "_kit_host", None)
        if previous is self and self.adopted is frame:
            return
        if previous is not None:
            previous.release(frame)
        if self.adopted is not None:
            self.release(self.adopted)
        self.adopted = frame
        frame._kit_host = self
        self._placed = None
        self._backdrop = None
        if not getattr(frame, "_kit_retained", False):
            frame._kit_retained = True
            retain_surface(frame)
        tk.Misc.lift(frame)
        script = self.page._focus_script()
        pending = [frame]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                value = str(widget.cget("takefocus"))
            except tk.TclError:
                continue
            if value in ("ttk::takefocus", "1"):
                self._previous[widget] = value
                widget.configure(takefocus=script)
        frame.scroll_page_wheel = self.page.scroll_page_wheel
        self._seen = None
        self._binding = frame.bind("<Configure>", self._configured, add="+")
        self.card._dirty = True
        self.page.layout_now()

    def release(self, frame) -> None:
        if self.adopted is not frame:
            return
        self.adopted = None
        self._placed = None
        try:
            frame.place_forget()
            for widget, value in self._previous.items():
                if widget.winfo_exists():
                    widget.configure(takefocus=value)
        except tk.TclError:
            pass
        self._previous = {}
        if getattr(frame, "scroll_page_wheel", None) == self.page.scroll_page_wheel:
            del frame.scroll_page_wheel
        if self._binding is not None:
            safe_unbind(frame, "<Configure>", self._binding)
            self._binding = None
        frame._kit_host = None
        self._hand_backdrop(frame, None)
        self._show_flow_art(None)
        self.card.visible = False
        self.card._hide_plate()
        if self._dock is not None:   # at once, also on a page that is being hidden
            self.page._layout_dock(None, self.page.content_width)
        self.page.layout()

    def _configured(self, event):
        if event.widget is self.adopted:
            size = (event.width, event.height)
            if size != self._seen:
                self._seen = size
                self.card._changed()

    def _place(self, key, *, in_):
        if key != self._placed and self.adopted is not None:
            self._placed = key
            _mode, x, y, width = key
            self.adopted.place_forget()
            self.adopted.place(in_=in_, x=x, y=y, width=width)
            tk.Misc.lift(self.adopted)

    # -- the dock -------------------------------------------------------------------------------
    def card_height(self) -> int:
        s, card = self.page.scale, self.card
        frame = self.adopted.winfo_reqheight() if self.adopted is not None else 0
        return card.inset + px(s, card.page.TITLE_LINE) + px(s, card.title_gap) + frame + card.inset

    def dock_height(self) -> int:
        page = self.page
        return px(page.scale, page.DOCK_TOP_GAP) + self.card_height() + page.end_gap()

    def _show_dock(self, shown):
        if self._dock is None:
            return
        dock, state = self.page.dock, "normal" if shown else "hidden"
        for item in (*self._dock["strips"], self._dock["middle"], *self._dock["edges"], self._dock["title"]):
            dock.itemconfigure(item, state=state)
        dock.itemconfigure(self._dock["art"], state=state if shown and self._art_images.get("dock") else "hidden")
        if not shown:
            self._dock["rect"] = None

    def _draw_dock(self, block) -> None:
        page, s, d = self.page, self.page.scale, self._dock
        dock, t = page.dock, ui_theme.TOKENS
        r, line, inset = px(s, page.CARD_RADIUS), hairline(s), self.card.inset
        top, height = px(s, page.DOCK_TOP_GAP), self.card_height()
        key = (block, height)
        if key != d["key"]:
            d["key"] = key
            root = dock.winfo_toplevel()
            images = tuple(art.plate_strip(root, height, r, line, t["panel"], t[self.edge], side, ((0, height),))
                           for side in ("left", "right"))
            for item, image, x in zip(d["strips"], images, (0, block - r)):
                dock.coords(item, x, top)
                dock.itemconfigure(item, image=image)
            d["images"] = images
            dock.coords(d["middle"], r, top, block - r, top + height)
            dock.coords(d["edges"][0], r, top, block - r, top + line)
            dock.coords(d["edges"][1], r, top + height - line, block - r, top + height)
            dock.coords(d["title"], inset, top + inset + px(s, page.TITLE_LINE) // 2)
            # The card's art fills the plate middle inside its edge lines (the rounded ends keep the plate).
            middle = self._art_image(block, height, box=(r, line, block - r, height - line))
            self._art_images["dock"] = middle
            dock.coords(d["art"], r, top + line)
            dock.itemconfigure(d["art"], image=middle or "")
        d["rect"] = (0, top, block, top + height)
        self._show_dock(True)
        y = top + inset + px(s, page.TITLE_LINE) + px(s, self.card.title_gap)
        self._place(("dock", inset, y, max(1, block - 2 * inset)), in_=dock)
        if self.adopted is not None:
            self._hand_backdrop(self.adopted, self._art_image(block, height), inset,
                                inset + px(s, page.TITLE_LINE) + px(s, self.card.title_gap))

    # -- the card's art -------------------------------------------------------------------------
    def _art_image(self, width, height, box=None):
        if self.art is None:
            return None
        try:
            return self.art(self.page.winfo_toplevel(), self.page.scale, width, height, box=box)
        except (OSError, ValueError, KeyError, tk.TclError):
            return None

    def _hand_backdrop(self, frame, image, x=0, y=0) -> None:
        """Give the adopted frame the card's picture and its place in the card (None: none)."""
        hook = getattr(frame, "card_backdrop", None)
        if hook is None:
            return
        state = (image, x, y) if image is not None else None
        if state != self._backdrop or image is None:
            self._backdrop = state
            self._art_images["frame"] = image
            hook(image, x, y)

    def _show_flow_art(self, image) -> None:
        """The card's art as the bottom item of its canvas on the sheet (flow); None hides it."""
        host = self.card.host
        if image is None:
            if self._flow_art is not None:
                host.itemconfigure(self._flow_art, state="hidden")
            return
        self._art_images["flow"] = image
        if self._flow_art is None:
            self._flow_art = host.create_image(0, 0, anchor="nw")
            host.tag_lower(self._flow_art)
        # The card's canvas starts a radius in from the plate's left edge.
        host.coords(self._flow_art, -px(self.page.scale, self.page.CARD_RADIUS), 0)
        host.itemconfigure(self._flow_art, image=image, state="normal")

    def _flow_laid_out(self) -> None:
        """After the page placed this card in the scroll area (flow): its art and the frame's share of it."""
        if self.art is None or self.card.rect is None or self.adopted is None:
            return
        x0, y0, x1, y1 = self.card.rect
        image = self._art_image(x1 - x0, y1 - y0)
        self._show_flow_art(image)
        placed = self._placed
        if placed is not None and placed[0] == "flow":
            self._hand_backdrop(self.adopted, image, placed[1] + px(self.page.scale, self.page.CARD_RADIUS), placed[2])


class KitPage(ttk.Frame):
    """One page of kit cards in one scroll region (PLAN-5 2.5); see the module notes."""

    CARD_GAP = 15
    CARD_PAD = 20
    CARD_RADIUS = 6
    TITLE_LINE = 18
    TITLE_GAP = 15
    ROW_GAP = 10
    RAIL_WIDTH = 10
    RAIL_GAP = 8
    GUTTER = 18
    MIN_VIEWPORT = 160
    DOCK_TOP_GAP = 15
    # Between the scroll viewport (or the docked card) and the footer's link row, design / compact
    # (the shell's Stage.mode): Replay's gap under its last card in the 1200 x 810 design window
    # (USER-ADDITIONS UPDATE 9; stage_page.END_GAP), in whole px rounded down (end_gap()).
    END_GAP = END_GAP
    DOCK_BOTTOM_GAP = END_GAP

    _is_kit_page = True

    def __init__(self, parent, *, columns: ColumnSpec | None = None, pinned: bool = False, fade: bool = True):
        super().__init__(parent)
        self.scale = s = ui_scale(self)
        kit_styles.install(self)
        t = ui_theme.TOKENS
        self.spec = columns
        self.pinned = pinned
        self.content_width = self.content_height = 0
        self.main_width = 0
        self.two_columns = columns is not None
        self.cards: list[KitCard] = []
        self.bands: list[KitBand] = []
        self.hosts: list[KitHostCard] = []
        self._plate_rects = []       # page px of the visible plates (cards, search fields, flow hosts)
        self._plates = []            # the same with their strip style
        self._widgets = {}           # placed input widget -> its element (inputs; composites once)
        self._auto_heights = {}      # KitBlock -> requested height at its last layout
        self._viewport = (1, 1)
        self._view = (0.0, 1.0)
        self._bar = None
        self._region_key = None
        self._strip_key = None
        self._strip_items = []
        self._strip_images = []
        self._scrollregion = None
        self._dirty = False
        self._dock_packed = False
        self._foot_packed = False
        self._bento = None           # (rows, grow): KitPage.bento
        self.dock = None
        if pinned:
            self.dock = tk.Canvas(self, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=0,
                                  height=1)
        # The gap between the scroll viewport and the footer's links (a docked card brings its own).
        self.foot = tk.Canvas(self, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=0, height=1)
        # The canvas that scrolls is only as wide as the cards and the rail: Tk
        # redraws all of it on every scroll step. The backdrop right of it is a
        # canvas that never scrolls (and, pinned, the dock below it).
        self.canvas = tk.Canvas(self, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=0,
                                yscrollincrement=WHEEL_UNIT_PIXELS)
        self.canvas.pack(side="left", fill="y")
        self.side = tk.Canvas(self, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=0,
                              width=1, height=1)
        self.side.pack(side="left", fill="both", expand=True)
        self.scrollbar = CanvasRail(self.canvas, self._yview)
        self.canvas.configure(yscrollcommand=self._scrolled)
        # Every card sits on one sheet: a window spanning the content, shaped to the
        # plates (the canvas shows, and takes the pointer, in the gaps), keeping its
        # surface. Without window regions every card is a canvas window of its own.
        self.sheet = tk.Frame(self.canvas, background=t["panel"], borderwidth=0, highlightthickness=0, takefocus=0)
        self.retained = False
        self._sheet_item = None
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
        self.bind("<Map>", self._mapped, add="+")
        self.bind("<Unmap>", self._unmapped, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.side.bind("<Configure>", self._side_resized, add="+")
        self.foot.bind("<Configure>", self._foot_resized, add="+")
        self.bind_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.backdrop = Backdrop.of(self)
        if self.backdrop is not None:
            self.backdrop.attach(self.canvas, self.open_areas)
            self.backdrop.attach(self.side)
            self.backdrop.attach(self.foot)
            if self.dock is not None:
                self.backdrop.attach(self.dock, self._dock_areas)
        self.fade = ScrollFade(self) if fade else None

    # -- building -------------------------------------------------------------------------------
    def band(self, *, height: int = 30, top: int | None = None) -> KitBand:
        band = KitBand(self, height=height, top=top)
        self.bands.append(band)
        self.layout()
        return band

    def columns(self) -> tuple[KitColumn, KitColumn]:
        if self.spec is None:
            raise ValueError("columns() needs KitPage(columns=ColumnSpec(...))")
        return KitColumn(self, "main"), KitColumn(self, "side")

    def add_card(self, title, *, info=None, title_gap=15, bottom=None, edge="card_edge") -> KitCard:
        """A full-width card (below the columns)."""
        return self._add_card(title, info=info, title_gap=title_gap, bottom=bottom, edge=edge, column=None)

    def _add_card(self, title, *, info=None, title_gap=15, bottom=None, edge="card_edge", column=None) -> KitCard:
        card = KitCard(self, title, info=info, title_gap=title_gap, bottom=bottom, edge=edge, column=column)
        self.cards.append(card)
        self.layout()
        return card

    def host_card(self, title, *, where: str, edge="card_edge_pinned", art=None,
                  title_font="card_title") -> KitHostCard:
        host = KitHostCard(self, title, where=where, edge=edge, art=art, title_font=title_font)
        self.hosts.append(host)
        self.layout()
        return host

    def bento(self, rows, *, grow=None) -> None:
        """Lay the two columns out as rows whose cards share their top and bottom edges (UPDATE 8).

        ``rows``: ``((main cards, side cards), ...)`` top to bottom; each side stacks its
        cards ``CARD_GAP`` apart and a row is as tall as its taller side. The shorter side
        grows to match: ``grow`` maps a card to the :class:`KitBlock` that takes the extra
        (a table: more of it shows), else the side's last card takes it above its bottom
        inset. Rows follow each other ``CARD_GAP`` apart. One column (a narrow window)
        stacks the cards as usual, at their own heights.
        """
        self._bento = (tuple((tuple(main), tuple(side)) for main, side in rows), dict(grow or {}))
        self.layout()

    def watch_focus(self) -> None:
        """Let Tab reach every control of the page, scrolled into view first (takefocus scripts).

        Disabled, hidden and off-page controls are skipped. Call it once the cards are
        built. (Not a ``<FocusIn>`` binding: that also fires when the window is only
        re-activated, and would yank the page back to the last field.)
        """
        script = self._focus_script()
        roots = [self.sheet] if self.sheet is not None else [card.holder for card in self.all_cards()]
        roots += [child for child in self.winfo_children() if getattr(child, "_kit_group", None) is not None]
        if self.sheet is None:
            roots += [band_field.canvas for band in self.bands for band_field in band.searches]
        pending = list(roots)
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                value = str(widget.cget("takefocus"))
            except tk.TclError:
                continue
            if value in ("ttk::takefocus", "1"):
                widget.configure(takefocus=script)

    def _focus_script(self):
        script = getattr(self, "_focus_command", None)
        if script is None:
            script = self._focus_command = self.register(self._focus_ok)
        return script

    def all_cards(self):
        return self.cards + [host.card for host in self.hosts]

    # -- registry -------------------------------------------------------------------------------
    def _register_widget(self, widget, element) -> None:
        self._widgets[widget] = element

    def _owner(self, widget):
        """(registered widget, element) for ``widget`` or its nearest registered ancestor, or (None, None)."""
        current = widget
        while current is not None:
            element = self._widgets.get(current)
            if element is not None:
                return current, element
            current = getattr(current, "master", None)
        return None, None

    def _widgets_in(self, element):
        def inside(candidate):
            while candidate is not None:
                if candidate is element:
                    return True
                candidate = getattr(candidate, "owner", None) if candidate is not candidate.card else None
            return False
        return [widget for widget, owner in self._widgets.items() if inside(owner)]

    def _focus_path(self) -> str:
        try:
            return str(self.tk.call("focus"))
        except tk.TclError:
            return ""

    # -- layout ---------------------------------------------------------------------------------
    def layout(self, *_args) -> None:
        """Lay the page out at idle (coalesced); a page hidden in a shown window waits for <Map>."""
        if self._deferred():
            self._dirty = True
            return
        self._update.schedule()

    def layout_now(self) -> None:
        self._update.cancel()
        self._dirty = False
        self._layout()

    def _deferred(self) -> bool:
        try:
            return not self.winfo_ismapped() and bool(self.winfo_toplevel().winfo_ismapped())
        except tk.TclError:
            return False

    def _mapped(self, event):
        # A page shown again lays out what changed while it was hidden before its first paint.
        if event.widget is self and self._dirty:
            self.layout_now()

    def end_gap(self) -> int:
        """px between the viewport (or the docked card) and the footer's link row (``END_GAP``)."""
        return end_gap(self.scale, self._compact())

    def _compact(self) -> bool:
        widget = self.master
        while widget is not None:
            mode = getattr(widget, "mode", None)
            if mode in ("design", "compact"):
                return mode == "compact"
            widget = getattr(widget, "master", None)
        return False

    def _layout(self):
        canvas, s = self.canvas, self.scale
        gutter, gap = px(s, self.GUTTER), px(s, self.CARD_GAP)
        available = self.winfo_width()
        if available <= 1:
            spec = self.spec or ColumnSpec()
            design = spec.main + spec.gap + spec.side if self.spec is not None else spec.main
            available = px(s, design) + gutter   # not laid out yet
        content = max(px(s, 160), available - gutter)
        if self.spec is not None:
            two, main, side_x, block = column_layout(self.spec, content, s)
        else:
            two, main, side_x, block = False, min(content, px(s, 613)), 0, min(content, px(s, 613))
        self.two_columns, self.content_width = two, block
        self.main_width = main if two else block
        width = block + gutter
        if int(canvas.cget("width")) != width:
            canvas.configure(width=width)   # the column; the side canvas takes the rest
        # The dock first: it decides whether the docked card scrolls (it never depends on the content).
        docked = next((h for h in self.hosts if h.where == "dock" and h.adopted is not None), None)
        dock_height = 0
        if docked is not None:
            dock_height = docked.dock_height()
            page_height = self.winfo_height()
            docked.mode = "flow" if 1 < page_height and page_height - dock_height < px(s, self.MIN_VIEWPORT) \
                else "dock"
        self._layout_foot(docked)
        plates = []
        y = 0
        shown = [band for band in self.bands if band.visible]
        for index, band in enumerate(shown):
            y += px(s, band.top if band.top is not None else (0 if index == 0 else self.ROW_GAP))
            band.y = y
            band.height = band._layout(y, block)
            for field in band.searches:
                if field.rect is not None:
                    plates.append((*field.rect, "search"))
            y += band.height
        above = bool(shown)
        mains = [card for card in self.cards if card.column == "main"]
        sides = [card for card in self.cards if card.column == "side"]
        fulls = [card for card in self.cards if card.column is None]
        top = y + (gap if above and any(card.visible for card in mains + sides) else 0)
        if two and self._bento is not None:
            y = self._bento_rows(top, main, side_x, block, plates)
        elif two:
            self._natural_heights()
            bottoms = [top]
            for cards, x, w in ((mains, 0, main), (sides, side_x, block - side_x)):
                bottoms.append(self._stack(cards, x, w, top, plates))
            y = max(bottoms)
        else:
            self._natural_heights()
            y = self._stack(mains + sides, 0, block, top, plates)
        above = y > 0
        for card in fulls + [host.card for host in self.hosts]:
            host = next((h for h in self.hosts if h.card is card), None)
            if host is not None:
                card.visible = host.adopted is not None and host.mode == "flow"
            if not card.visible:
                card._hide_plate()
                if host is not None:
                    host._show_flow_art(None)
                continue
            y += gap if above else 0
            y = card.place(0, y, block)
            plates.append((*card.rect, "pinned" if card.edge == "card_edge_pinned" else None))
            if host is not None:
                host._flow_laid_out()
            above = True
        self.content_height = y
        self._plates = plates
        self._plate_rects = [(x0, y0, x1, y1) for x0, y0, x1, y1, _style in plates]
        self._update_strips(plates)
        self._update_sheet(block, y, plates)
        # The viewport already ends END_GAP above the links (the foot or the dock): no end margin.
        height = canvas.winfo_height()
        overflow = height > 1 and y > height
        region = (0, 0, width, y)
        if region != self._scrollregion:
            self._scrollregion = region
            canvas.configure(scrollregion=region)   # (a configure repaints the whole canvas)
        bar = (block + px(s, self.RAIL_GAP), height) if overflow else None
        if bar != self._bar:
            if bar is None:
                self.scrollbar.place_forget()
            else:
                self.scrollbar.place(*bar)
            self._bar = bar
        if not overflow and height > 1:   # a withdrawn window has no viewport to fit
            self.scroller.stop()
            if canvas.yview() != (0.0, 1.0) or canvas.canvasy(0):
                canvas.yview_moveto(0)
        self._layout_dock(docked, block)
        if self.backdrop is not None:
            self.backdrop.sync(canvas, redraw=True)
            if self.dock is not None and self._dock_packed:
                self.backdrop.sync(self.dock, redraw=True)
            if self._foot_packed:
                self.backdrop.sync(self.foot, redraw=True)
        if self.fade is not None:
            self.fade.update()

    def _natural_heights(self) -> None:
        """Cards at their own heights (a bento page in one column, or before its rows are matched)."""
        if self._bento is None:
            return
        rows, grow = self._bento
        for main, side in rows:
            for card in (*main, *side):
                self._set_extra(card, grow, 0)

    @staticmethod
    def _set_extra(card, grow, extra) -> None:
        target = grow.get(card) or card
        if target.extra != extra:
            target.extra = extra
            card._dirty = True

    def _bento_rows(self, top, main, side_x, block, plates) -> int:
        """Place the bento rows from ``top``: each side of a row ends on the row's bottom (see :meth:`bento`)."""
        s = self.scale
        gap = px(s, self.CARD_GAP)
        rows, grow = self._bento
        y = top
        for main_cards, side_cards in rows:
            sides = ((tuple(card for card in main_cards if card.visible), 0, main),
                     (tuple(card for card in side_cards if card.visible), side_x, block - side_x))
            if not sides[0][0] and not sides[1][0]:
                continue
            natural = []
            for cards, _x, width in sides:
                for card in cards:
                    self._set_extra(card, grow, 0)
                heights = [card.measure(width) for card in cards]
                natural.append(sum(heights) + gap * max(0, len(heights) - 1))
            row_height = max(natural)
            for (cards, x, width), height in zip(sides, natural):
                if cards and height < row_height:
                    # The spare height goes to a card that can show more (its table), else the last card.
                    card = next((card for card in cards if card in grow), cards[-1])
                    self._set_extra(card, grow, row_height - height)
                    card.measure(width)
                cursor = y
                for card in cards:
                    cursor = card.place(x, cursor, width) + gap
                    plates.append((*card.rect, "pinned" if card.edge == "card_edge_pinned" else None))
            y += row_height + gap
        bento = {card for main_cards, side_cards in rows for card in (*main_cards, *side_cards)}
        for card in self.cards:   # cards outside the rows are not shown in this layout
            if card.column in ("main", "side") and card not in bento:
                card._hide_plate()
        return y - gap if y > top else top

    def _layout_foot(self, docked) -> None:
        """The static backdrop strip between the viewport and the footer's links (unless the dock is there)."""
        wanted = docked is None or docked.mode != "dock"
        if wanted:
            height = self.end_gap()
            if int(self.foot.cget("height")) != height:
                self.foot.configure(height=height)
            if not self._foot_packed:
                self.foot.pack(side="bottom", fill="x", before=self.canvas)
                self._foot_packed = True
        elif self._foot_packed:
            self.foot.pack_forget()
            self._foot_packed = False

    def _stack(self, cards, x, width, top, plates):
        """Stack the visible ``cards`` from ``top`` at ``x``; returns the bottom (``top`` if none)."""
        y, first = top, True
        for card in cards:
            if not card.visible:
                card._hide_plate()
                continue
            if not first:
                y += px(self.scale, self.CARD_GAP)
            y = card.place(x, y, width)
            plates.append((*card.rect, "pinned" if card.edge == "card_edge_pinned" else None))
            first = False
        return y

    def _layout_dock(self, docked, block):
        if self.dock is None:
            return
        wanted = docked is not None and docked.mode == "dock"
        if wanted:
            height = docked.dock_height()
            if int(self.dock.cget("height")) != height:
                self.dock.configure(height=height)
            if not self._dock_packed:
                self.dock.pack(side="bottom", fill="x", before=self.canvas)
                self._dock_packed = True
            docked._draw_dock(block)
        else:
            for host in self.hosts:
                host._show_dock(False)
            if self._dock_packed:
                self.dock.pack_forget()
                self._dock_packed = False

    def _strip_style(self, style):
        s, t = self.scale, ui_theme.TOKENS
        if style == "search":
            return (px(s, SearchField.RADIUS), t["field"], None, 0)
        if style == "pinned":
            return (px(s, self.CARD_RADIUS), t["panel"], t["card_edge_pinned"], hairline(s))
        return None

    def _update_strips(self, plates):
        """The plates' rounded ends: one column image per distinct edge x, made when the plates move.

        Each is ``radius`` px wide and spans the content: every plate's two corners on
        that side (anti-aliased, transparent outside) and its side edge and fill between
        them, transparent between plates. Full-width cards share the outer columns'
        images, so a two-column page draws at most four, a one-column page two.
        """
        s, t, canvas = self.scale, ui_theme.TOKENS, self.canvas
        r, line = px(s, self.CARD_RADIUS), hairline(s)
        groups = {}
        for x0, y0, x1, y1, style in plates:
            groups.setdefault(("left", x0), []).append((y0, y1, style))
            groups.setdefault(("right", x1 - r), []).append((y0, y1, style))
        key = (r, line, tuple((side, x, tuple(sorted(spans, key=lambda v: v[0])))
                              for (side, x), spans in sorted(groups.items())))
        if key == self._strip_key:
            return
        self._strip_key = key
        while len(self._strip_items) < len(groups):
            self._strip_items.append(canvas.create_image(0, 0, anchor="nw", state="hidden",
                                                         tags=("kitplate", "plate_strip")))
        root, images = canvas.winfo_toplevel(), []
        for item, (side, x, spans) in zip(self._strip_items, key[2]):
            styles = [self._strip_style(style) for _a, _b, style in spans]
            image = art.plate_strip(root, spans[-1][1], r, line, t["panel"], t["card_edge"], side,
                                    tuple((a, b) for a, b, _ in spans),
                                    styles=styles if any(style is not None for style in styles) else None)
            canvas.coords(item, x, 0)
            canvas.itemconfigure(item, image=image, state="normal")
            images.append(image)
        for item in self._strip_items[len(groups):]:
            canvas.itemconfigure(item, state="hidden", image="")
        self._strip_images = images   # the canvas items need the images alive

    def _update_sheet(self, block, height, plates):
        """Size the sheet to the content and shape it to the visible plate middles (when they moved)."""
        if self.sheet is None:
            return
        r = px(self.scale, self.CARD_RADIUS)
        rects = [(x0, y0, x1 - 2 * r, y1) for x0, y0, x1, y1, _style in plates]
        key = (block, height, tuple(rects))
        if key == self._region_key:
            return
        self._region_key = key
        canvas = self.canvas
        if not rects:
            canvas.itemconfigure(self._sheet_item, state="hidden")
            return
        canvas.coords(self._sheet_item, r, 0)
        canvas.itemconfigure(self._sheet_item, width=max(1, block - 2 * r), height=max(1, height), state="normal")
        set_window_region(self.sheet, rects)

    # -- areas ----------------------------------------------------------------------------------
    def open_areas(self):
        """Where the backdrop shows on the scrolling canvas: the viewport minus the plates (viewport px)."""
        width, height = self._viewport
        top = self.canvas.canvasy(0)
        plates = [(x0, y0 - top, x1, y1 - top) for x0, y0, x1, y1 in self._plate_rects
                  if y1 - top > 0 and y0 - top < height]
        edges = sorted({0, height, *(max(0, min(height, int(y))) for _x0, y0, _x1, y1 in plates for y in (y0, y1))})
        areas, previous = [], None
        for a, b in zip(edges, edges[1:]):
            if b <= a:
                continue
            covered = sorted((x0, x1) for x0, y0, x1, y1 in plates if y0 < b and y1 > a)
            gaps, x = [], 0
            for x0, x1 in covered:
                if x0 > x:
                    gaps.append((x, x0))
                x = max(x, x1)
            if x < width:
                gaps.append((x, width))
            if previous is not None and previous[0] == gaps:
                previous[1] = b                     # the same columns as the band above: one taller area
                continue
            if previous is not None:
                areas.extend((x0, previous[2], x1, previous[1]) for x0, x1 in previous[0])
            previous = [gaps, b, a]
        if previous is not None:
            areas.extend((x0, previous[2], x1, previous[1]) for x0, x1 in previous[0])
        return [area for area in areas if area[2] > area[0] and area[3] > area[1]]

    def _dock_areas(self):
        dock = self.dock
        width, height = dock.winfo_width(), dock.winfo_height()
        rect = next((h._dock["rect"] for h in self.hosts if h._dock is not None and h._dock["rect"]), None)
        if rect is None:
            return [(0, 0, width, height)]
        x0, y0, x1, y1 = rect
        areas = [(0, 0, width, y0), (0, y1, width, height), (x1, y0, width, y1)]
        return [a for a in areas if a[2] > a[0] and a[3] > a[1]]

    # -- scrolling --------------------------------------------------------------------------------
    #
    # Tk repaints the whole visible canvas for every scroll. The backdrop is fixed to
    # the window, so its items move by the same amount the other way; doing that in the
    # same breath as the scroll keeps it to one repaint per step.

    def _scrolled(self, first, last):
        view = (float(first), float(last))
        if view != self._view:
            # Tk calls this at the end of the whole-canvas repaint a scroll causes.
            self._view = view
            if self.canvas.winfo_ismapped():
                _discard_expose(self.canvas)
        self.scrollbar.set(first, last)
        # The page's own scrolls already moved rail and backdrop; this catches the
        # rest (a scrollregion that shrank under the view, a test's yview_moveto).
        self._follow_view()
        # A table off-screen gets no layout when its rows grow.
        if any(block.widget.winfo_reqheight() != height for block, height in self._auto_heights.items()):
            self.layout()

    def _follow_view(self):
        self.scrollbar.follow()
        if self.backdrop is not None:
            self.backdrop.sync(self.canvas)
        if self.fade is not None:
            self.fade.update()

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

    def _resized(self, event=None):
        self.scroller.stop()
        if event is not None:
            self._viewport = (event.width, event.height)
        self.layout()

    def _side_resized(self, _event=None):
        if self.backdrop is not None:
            self.backdrop.sync(self.side, redraw=True)

    def _foot_resized(self, _event=None):
        if self.backdrop is not None:
            self.backdrop.sync(self.foot, redraw=True)

    def _owns_wheel(self, widget) -> bool:
        if isinstance(widget, (ScrollPage, StagePage, KitPage, ttk.Treeview, tk.Text, tk.Listbox, ttk.Combobox,
                               tk.Spinbox, ttk.Spinbox, tk.Scrollbar, ttk.Scrollbar)):
            return True
        try:
            return bool(widget.bind("<MouseWheel>"))
        except (AttributeError, tk.TclError):
            return False

    def _owns(self, widget) -> bool:
        """True when the page's wheel may scroll for ``widget`` (it is on this page or adopted by it)."""
        target = widget
        while target is not None and target is not self:
            if self._owns_wheel(target):
                return False
            host = getattr(target, "_kit_host", None)
            if host is not None:
                return host.page is self
            target = getattr(target, "master", None)
        return target is self

    def _wheel(self, event):
        if isinstance(event.widget, str) or not self._owns(event.widget):
            return None
        if self.canvas.yview() != (0.0, 1.0):
            self.scroll_page_wheel(event)
            return "break"
        return None

    def scroll_page_wheel(self, event) -> None:
        # Replay's distances: subpixel input kept, 60 design px per notch; the scroller
        # travels them eased, or at once with motion off.
        notch = WHEEL_PIXELS_PER_NOTCH * self.scale
        if getattr(event, "delta", 0):
            pixels = -event.delta / 120 * notch
        else:
            pixels = -notch if getattr(event, "num", 0) == 4 else notch
        first, last = self.canvas.yview()
        if (pixels < 0 and first <= 0.0) or (pixels > 0 and last >= 1.0):
            self.scroller.stop()   # outward at an edge: no debt for the way back
            return
        self.scroller.add(pixels)

    def scroll_to(self, y) -> None:
        """Scroll at once so page y ``y`` is at the top (clamped to the content)."""
        self.scroller.stop()
        total = self._scrollregion[3] if self._scrollregion else self.content_height
        viewport = self.canvas.winfo_height()
        if total > viewport > 0:
            self.canvas.yview_moveto(max(0, min(y, total - viewport)) / total)
        self._follow_view()

    # -- reveal -----------------------------------------------------------------------------------
    def _open_disclosures(self, element) -> None:
        while element is not None and not isinstance(element, KitCard):
            if isinstance(element, KitSection) and element.disclosure is not None and not element.disclosure.opened:
                element.disclosure.set_open(True)
            element = getattr(element, "owner", None)

    def reveal(self, target) -> None:
        """Scroll so ``target`` is fully in view (a widget, row, card, text or canvas control); immediate.

        A closed disclosure holding it opens first. On the sheet every card and input is
        placed, so it has its size even in a withdrawn window.
        """
        widget_owner = None
        if isinstance(target, _Element):
            self._open_disclosures(target)
        elif not isinstance(target, (KitCard, CanvasControl)) and hasattr(target, "winfo_toplevel"):
            _registered, widget_owner = self._owner(target)
            if widget_owner is not None:
                self._open_disclosures(widget_owner)
        elif isinstance(target, CanvasControl):
            owner = self._control_owner(target)
            if owner is not None:
                self._open_disclosures(owner)
        self.layout_now()
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        rect = self.rect_of(target)
        if rect is None:
            return
        top, bottom = rect[1], rect[3]
        view, viewport = self.canvas.canvasy(0), self.canvas.winfo_height()
        if viewport <= 1 or (top >= view and bottom <= view + viewport):
            return
        margin = px(self.scale, REVEAL_MARGIN)
        self.scroll_to(top - margin if top < view or bottom - top > viewport else bottom + margin - viewport)
        self.update_idletasks()

    def _control_owner(self, control):
        for card in self.all_cards():
            if control.canvas is card.host:
                for element in self._elements(card):
                    controls = element.controls() if hasattr(element, "controls") else []
                    if control in controls or getattr(element, "toggle", None) is control:
                        return element
        return None

    def _elements(self, container):
        for element in getattr(container, "elements", ()):
            yield element
            if isinstance(element, KitSection):
                yield from self._elements(element)
            elif hasattr(element, "first") and hasattr(element, "second"):
                yield from self._elements(element.first)
                yield from self._elements(element.second)
            elif hasattr(element, "body"):
                yield from self._elements(element.body)

    def _card_of_canvas(self, canvas):
        return next((card for card in self.all_cards() if card.host is canvas), None)

    def rect_of(self, target):
        """``target``'s rect in page-canvas px (as laid out), or None (tests, QA, reveal)."""
        if isinstance(target, KitCard):
            return target.rect
        if isinstance(target, KitHostCard):
            return target.card.rect
        if isinstance(target, KitBand):
            return None if target.y is None else (0, target.y, self.content_width, target.y + target.height)
        if isinstance(target, _Element):
            return target.page_rect()
        if isinstance(target, CanvasControl) or hasattr(target, "box"):
            box = target.box() if callable(getattr(target, "box", None)) else None
            if box is None:
                return None
            canvas = target.canvas
            if canvas is self.canvas:
                return box
            card = self._card_of_canvas(canvas)
            if card is not None and card.rect is not None:
                ox, oy = card.origin()
                return (ox + box[0], oy + box[1], ox + box[2], oy + box[3])
            base = self.rect_of(canvas)
            if base is None:
                return None
            return (base[0] + box[0], base[1] + box[1], base[0] + box[2], base[1] + box[3])
        return self._widget_rect(target)

    def _widget_rect(self, widget):
        try:
            registered, element = self._owner(widget)
            if registered is not None:
                base = self._placed_rect(registered, element)
                if base is None:
                    return None
                if widget is registered:
                    return base
                dx = widget.winfo_rootx() - registered.winfo_rootx()
                dy = widget.winfo_rooty() - registered.winfo_rooty()
                return (base[0] + dx, base[1] + dy, base[0] + dx + widget.winfo_width(),
                        base[1] + dy + widget.winfo_height())
            current = widget
            while current is not None:
                card = self._card_of_canvas(current)
                if card is not None and card.rect is not None:
                    ox, oy = card.origin()
                    dx, dy = widget.winfo_rootx() - current.winfo_rootx(), widget.winfo_rooty() - current.winfo_rooty()
                    return (ox + dx, oy + dy, ox + dx + widget.winfo_width(), oy + dy + widget.winfo_height())
                host = getattr(current, "_kit_host", None)
                if host is not None:
                    if host.page is not self:
                        return None
                    if host.mode == "dock":
                        return None    # the dock never scrolls
                    placed, card = host._placed, host.card
                    if placed is None or card.rect is None:
                        return None
                    ox, oy = card.origin()
                    dx, dy = widget.winfo_rootx() - current.winfo_rootx(), widget.winfo_rooty() - current.winfo_rooty()
                    x, y = ox + placed[1] + dx, oy + placed[2] + dy
                    return (x, y, x + widget.winfo_width(), y + widget.winfo_height())
                current = getattr(current, "master", None)
        except tk.TclError:
            return None
        return None

    def _placed_rect(self, widget, element):
        card = element.card
        if card.rect is None or element.y is None:
            return None
        ox, oy = card.origin()
        if isinstance(element, KitBlock):
            placed = element._placed
            if placed is None:
                return None
            height = placed[3] if placed[3] is not None else widget.winfo_reqheight()
            return (ox + placed[0], oy + placed[1], ox + placed[0] + placed[2], oy + placed[1] + height)
        if isinstance(element, KitRow):
            cell = next((c for c in element.core.cells if c.obj is widget), None)
            placed = cell.placed if cell is not None else None
        elif isinstance(element, KitFlow):
            item = next((i for i in element.items if i[1] is widget), None)
            placed = (*item[2], None, None) if item is not None and item[2] is not None else None
        else:
            placed = None
        if placed is None:
            return element.page_rect()
        x, y, w, h = placed
        w = w if w is not None else widget.winfo_reqwidth()
        h = h if h is not None else widget.winfo_reqheight()
        return (ox + x, oy + y, ox + x + w, oy + y + h)

    # -- focus ------------------------------------------------------------------------------------
    def _shown_widget(self, widget) -> bool:
        registered, element = self._owner(widget)
        if element is not None:
            return element.shown() and element.card.visible
        current = widget
        while current is not None:
            card = self._card_of_canvas(current)
            if card is not None:
                return card.visible
            if getattr(current, "_kit_host", None) is not None:
                return True
            current = getattr(current, "master", None)
        return True

    def _focus_ok(self, path):
        """Takefocus script: skip disabled, hidden and off-page controls; reveal the rest."""
        try:
            widget = self.nametowidget(path)
        except KeyError:
            return 0
        try:
            if not self.winfo_viewable():
                return 0
            group = getattr(widget, "_kit_group", None)
            if group is not None:
                first = group.first()
                if first is None:
                    return 0
                self.reveal(first)
                return 1
            if isinstance(widget, ttk.Widget):
                if widget.instate(["disabled"]):
                    return 0
            elif "state" in widget.keys() and str(widget.cget("state")) == "disabled":
                return 0
            if not self._shown_widget(widget):
                return 0
            self.reveal(widget)
        except tk.TclError:
            return 0
        return 1

    # -- info bubbles -----------------------------------------------------------------------------
    def open_bubble(self, anchor, text):
        """Show ``text`` in a kit Info bubble ``px(6)`` below ``anchor`` (a widget, or (canvas, bbox)).

        Its left edge sits ``px(6)`` left of the anchor, clamped to the viewport; it opens
        upwards when there is no room below, when it would enter a fade band, or when it
        would cover an adopted frame (it cannot stack above it). A window item on the
        page canvas, lifted above the sheet.
        """
        canvas, s = self.canvas, self.scale
        if isinstance(anchor, tuple):
            host, box = anchor
            rect = self.rect_of(_Boxed(host, box))
            widget, offset = host, (box[0] - host.canvasx(0), box[1] - host.canvasy(0), box[2] - box[0],
                                    box[3] - box[1])
        else:
            rect = self.rect_of(anchor)
            widget, offset = anchor, None
        if rect is None:   # not on the scrolling content (the dock): from the screen
            try:
                if not widget.winfo_ismapped():
                    return None
                x = canvas.canvasx(0) + widget.winfo_rootx() - canvas.winfo_rootx()
                y = canvas.canvasy(0) + widget.winfo_rooty() - canvas.winfo_rooty()
                if offset is None:
                    rect = (x, y, x + widget.winfo_width(), y + widget.winfo_height())
                else:
                    rect = (x + offset[0], y + offset[1], x + offset[0] + offset[2], y + offset[1] + offset[3])
            except (AttributeError, tk.TclError):
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
        for which, cx, cy in (("nw", 0, 0), ("ne", w - r, 0), ("sw", 0, h - r), ("se", w - r, h - r)):
            tip.create_image(cx, cy, anchor="nw", image=art.corner_rgba(root, r, t["field"], t["field"], which))
        tip.tag_raise(label)
        left, top = canvas.canvasx(0), canvas.canvasy(0)
        viewport_w, viewport_h = canvas.winfo_width(), canvas.winfo_height()
        x = max(left, min(rect[0] - px(s, 6), left + max(1, viewport_w) - w - 1))
        below, above = rect[3] + px(s, 6), rect[1] - h - px(s, 6)
        band = 0
        if self.fade is not None and self.canvas.yview()[1] < 1.0:
            band = self.fade.band_height(viewport_h)
        y = below
        if below + h > top + viewport_h - band or self._covers_adopted((x, below, x + w, below + h)):
            y = above
        item = canvas.create_window(x, y, window=tip, anchor="nw", tags=("bubble",))
        tk.Misc.lift(tip)   # the window above the sheet (Canvas.lift would raise an item)
        return tip, item

    def _covers_adopted(self, rect) -> bool:
        for host in self.hosts:
            if host.adopted is not None and host.mode == "flow" and host.card.rect is not None:
                x0, y0, x1, y1 = host.card.rect
                if rect[0] < x1 and rect[2] > x0 and rect[1] < y1 and rect[3] > y0:
                    return True
        return False

    def close_bubble(self, handle) -> None:
        if handle is not None:
            tip, item = handle
            try:
                self.canvas.delete(item)
                tip.destroy()
            except tk.TclError:
                pass

    # -- accounting -------------------------------------------------------------------------------
    def window_counts(self) -> dict:
        """Window items and placed widgets (PR2/PR3; tests and QA).

        ``page``: window items on the scrolling canvas (1 = the sheet, + open bubbles);
        ``moving``: windows a scroll frame moves (the sheet, or the card windows without
        it, + a frame adopted in flow); ``inputs``: real widgets placed on card canvases
        (a composite once); ``proxies``: focus stand-ins.
        """
        canvas = self.canvas
        windows = [item for item in canvas.find_all() if canvas.type(item) == "window"]
        shown = [item for item in windows if canvas.itemcget(item, "state") != "hidden"
                 and "bubble" not in canvas.gettags(item)]
        moving = len(shown) + sum(1 for host in self.hosts if host.adopted is not None and host.mode == "flow")
        proxies, pending = 0, [self]
        while pending:
            widget = pending.pop()
            if getattr(widget, "_kit_group", None) is not None:
                proxies += 1
            pending.extend(widget.winfo_children())
        return {"page": len(windows), "moving": moving, "inputs": len(self._widgets), "proxies": proxies}

    # -- lifetime ---------------------------------------------------------------------------------
    def _unmapped(self, event):
        if event.widget is not self:
            return
        self.scroller.stop()
        controls = list(getattr(self.canvas, "_kit_controls", ()))
        for card in self.all_cards():
            controls += list(getattr(card.host, "_kit_controls", ()))
        finish_motion(*controls)

    def _destroyed(self, event):
        if event.widget is self:
            self.scroller.stop()
            self.scrollbar._cancel_repeat()
            safe_unbind(self.winfo_toplevel(), "<MouseWheel>", self.bind_id)
            if self.backdrop is not None:
                self.backdrop.detach(self.canvas)
                self.backdrop.detach(self.side)
                self.backdrop.detach(self.foot)
                if self.dock is not None:
                    self.backdrop.detach(self.dock)


class _Boxed:
    """A canvas box as a reveal/rect target (a bubble's anchor)."""

    def __init__(self, canvas, box):
        self.canvas, self._box = canvas, box

    def box(self):
        return self._box


__all__ = ["ColumnSpec", "column_layout", "KitPage", "KitBand", "KitHostCard", "KitColumn", "KitCard",
           "KitText", "KitRow", "KitBlock", "KitFlow", "KitSection"]
