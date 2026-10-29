"""Kit cards (PLAN-5 2.6): a card's plate middle and its content, on the page's sheet.

A card is drawn in two layers (PERF-NOTES R2-R5). Its rounded ends are part of the
page's plate strips (:mod:`.kit_page`). Between them, one ``tk.Canvas`` in ``panel``
(``card.host``, which is also ``card.holder``: the holder and the card canvas merged)
is *placed* on the page's sheet over the plate middle and draws the plate's 1 px top
and bottom edges as items. Everything static of the card is items on that canvas:
the title, an info icon, labels, texts, dividers, disclosure rows and every button
(:class:`.kit_controls.CanvasButton`, N21). Real inputs are children of
``card.host`` *placed* on it with explicit coordinates, never canvas window items (a
canvas lays those out only while it redraws, so they would stay 1 x 1 in a withdrawn
window, R10). The sheet keeps its surface (layered), so a scroll repaints nothing here;
a hover ramp redraws only its own items.

Content is a column of elements laid out top to bottom: rows (cells left to right),
wrapped texts, full-width blocks, wrapping flows, sections (hideable groups), split
sections side by side and disclosures. ``top`` is the design px above an element
(default: the card's title gap for its first, then 10). Hidden elements take no space;
a hidden card leaves the strips and the sheet's region.
"""
from __future__ import annotations

import tkinter as tk

from .. import ui_theme
from . import fonts
from .canvas_controls import CanvasControl
from .kit_controls import (
    CanvasButton, CanvasInfoIcon, FocusGroup, KitLabel, RowLayout, _ChevronRow, _Looks, _register, _unregister,
)
from .scale import hairline, px
from .stage_page import retain_surface

ROW_GAP = 10
TITLE_LINE = 18


class _Element:
    """One block of a card's content column."""

    def __init__(self, owner, top):
        self.owner = owner
        self.card = owner.card
        self.page = owner.card.page
        self.top = top
        self.visible = True
        self.y = None              # card-canvas px of the last layout (None while not laid out)
        self.height = 0
        self.x0 = self.width = 0

    @property
    def host(self) -> tk.Canvas:
        return self.card.host

    def shown(self) -> bool:
        return self.visible and self.owner.shown()

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible != self.visible:
            self.visible = visible
            if not visible:
                self._hide()
            self.card._changed()

    def _hide(self) -> None:
        self.y = None

    def _layout(self, x0, y, width) -> int:
        raise NotImplementedError

    def page_rect(self):
        """Page-canvas px of the element as laid out, or None."""
        if self.y is None or self.card.rect is None:
            return None
        ox, oy = self.card.origin()
        return (ox + self.x0, oy + self.y, ox + self.x0 + self.width, oy + self.y + self.height)

    # compat for app code written against packed frames
    def pack(self, **_ignored) -> None:
        self.set_visible(True)

    def pack_forget(self) -> None:
        self.set_visible(False)

    grid = pack
    grid_remove = pack_forget

    def winfo_manager(self) -> str:
        return "canvas" if self.shown() else ""

    def winfo_ismapped(self) -> bool:
        try:
            return self.shown() and self.card.shown() and bool(self.host.winfo_ismapped())
        except tk.TclError:
            return False


class KitText(KitLabel, _Element):
    """Text on a card canvas (``card.text()``, or a row's label cell): Label-like ``configure``/``cget``.

    ``tone``: ``muted`` (default), ``dim`` or ``text``. A block text wraps to the
    content width (``wrap=False``: one line); ``max_lines`` cuts the rest with "…".
    A text whose height changes lays the page out again.
    """

    def __init__(self, canvas, text="", *, textvariable=None, tone="muted", wrap=False, max_lines=0, owner=None,
                 top=None):
        KitLabel.__init__(self, canvas, text, textvariable=textvariable, tone=tone, max_lines=max_lines)
        self.block = owner is not None
        self.wraps = bool(wrap)
        if self.block:
            _Element.__init__(self, owner, top)
            self.on_change = self.card._changed
        else:
            self.visible = True

    def shown(self) -> bool:
        return self.visible and (self.owner.shown() if self.block else True)

    def _layout(self, x0, y, width) -> int:
        s = self.card.page.scale
        if self.wraps:
            self.set_wrap(width)
        line = self.linespace()
        pad = max(0, (px(s, 16) - line) // 2)
        self.place(x0, y + pad)
        self.x0, self.width = x0, width
        lines = len(self._lines)
        return max(px(s, 16) * lines, line * lines + pad)

    def set_visible(self, visible: bool) -> None:
        if self.block:
            _Element.set_visible(self, visible)
        else:
            self.visible = bool(visible)
            if not self.visible:
                self.hide()
            if self.on_change is not None:
                self.on_change()

    def winfo_manager(self) -> str:
        if self.block:
            return _Element.winfo_manager(self)
        return "canvas" if self.visible else ""

    def winfo_ismapped(self) -> bool:
        if self.block:
            return _Element.winfo_ismapped(self)
        try:
            return self.visible and bool(self.canvas.winfo_ismapped())
        except tk.TclError:
            return False

    def _hide(self) -> None:
        self.hide()
        self.y = None


class KitRow(_Element):
    """A row of cells, vertically centred (height 30, 18 for checkbox rows, 16 for text rows).

    Widgets of the row are created with master ``row.host`` (the card canvas, asserted
    at build time) and placed on it.
    """

    def __init__(self, owner, *, height=30, top=None):
        super().__init__(owner, top)
        card = self.card
        self.core = RowLayout(card.host, scale=card.page.scale, page=card.page, height=height,
                              changed=card._changed, register=lambda w: card.page._register_widget(w, self),
                              label_factory=KitText)

    # building
    def label(self, text="", *, textvariable=None, width=None, tone="muted", info=None, wrap=False) -> KitText:
        return self.core.label(text, textvariable=textvariable, width=width, tone=tone, info=info, wrap=wrap)

    def widget(self, widget, *, width=None, height=None):
        return self.core.widget(widget, width=width, height=height)

    def button(self, text, command=None, *, kind="secondary", state="normal", width=None) -> CanvasButton:
        return self.core.button(text, command, kind=kind, state=state, width=width)

    def info(self, text) -> CanvasInfoIcon:
        return self.core.info(text)

    def gap(self, design_px) -> None:
        self.core.gap(design_px)

    def push(self) -> None:
        self.core.push()

    def controls(self):
        return self.core.controls()

    def widgets(self):
        return self.core.widgets()

    def natural_width(self) -> int:
        s, total = self.card.page.scale, 0
        for cell in self.core.cells:
            if cell.kind == "label":
                total += self.core._label_width(cell)
            elif cell.kind == "widget":
                total += cell.obj.winfo_reqwidth() if cell.width in (None, "fill") else px(s, cell.width)
            elif cell.kind == "control":
                total += cell.obj.winfo_reqwidth()
            elif cell.kind == "gap":
                total += px(s, cell.width)
        return total

    def _layout(self, x0, y, width) -> int:
        self.x0, self.width = x0, width
        return self.core.layout(x0, y, width, proxy=self.card.proxy_at())

    def _hide(self) -> None:
        self.core.hide()
        self.y = None


class KitBlock(_Element):
    """A full-width real widget (a table, the curve workspace, the path canvas), placed on the card.

    ``height`` (design px) fixes its height; without it the widget keeps its requested
    height (the placer follows it, and a new size lays the page out again).
    ``bleed_left`` (px; Tk's tree cell inset is px at any scale) starts it that far
    left of the content edge, keeping its right edge (``stage_page.bleed_left``).
    ``extra`` (px) is added to a fixed height: a bento page grows a table to line its
    card up with its neighbours (:meth:`.kit_page.KitPage.bento`).
    """

    def __init__(self, owner, widget, *, top=None, height=None, bleed_left=0):
        super().__init__(owner, top)
        if str(widget.master) != str(self.card.host):
            raise ValueError(f"{widget} must be created with master card.host ({self.card.host}), "
                             f"not {widget.master}")
        self.widget = widget
        self.fixed = height
        self.extra = 0
        self.bleed = int(bleed_left)
        self._placed = None
        self._seen = None
        widget.bind("<Configure>", self._configured, add="+")
        self.page._register_widget(widget, self)

    def _configured(self, event):
        if event.widget is not self.widget:
            return
        size = (event.width, event.height)
        if self._seen is not None and size != self._seen:
            self.card._changed()
        self._seen = size

    def _layout(self, x0, y, width) -> int:
        s = self.page.scale
        self.x0, self.width = x0, width
        height = px(s, self.fixed) + self.extra if self.fixed is not None else self.widget.winfo_reqheight()
        if self.fixed is None:
            self.page._auto_heights[self] = height   # the page notices a table that grew off-screen
        place = (x0 - self.bleed, y, width + self.bleed, height if self.fixed is not None else None)
        if place != self._placed:
            self._placed = place
            options = {"x": place[0], "y": place[1], "width": place[2]}
            if place[3] is not None:
                options["height"] = place[3]
            self.widget.place_forget()
            self.widget.place(**options)
        return height

    def _hide(self) -> None:
        if self._placed is not None:
            self._placed = None
            self.widget.place_forget()
        self.y = None


class KitFlow(_Element):
    """Hug-width widgets and buttons left to right, wrapping onto new lines (presets, FFmpeg buttons)."""

    def __init__(self, owner, *, gap=10, row_gap=10, indent=0, top=None):
        super().__init__(owner, top)
        self.gap, self.row_gap, self.indent = gap, row_gap, indent
        self.items = []            # [kind, obj, placed]
        self.groups = []
        self._run = None

    def add(self, widget):
        if str(widget.master) != str(self.card.host):
            raise ValueError(f"{widget} must be created with master card.host ({self.card.host}), "
                             f"not {widget.master}")
        self.items.append(["widget", widget, None])
        self._run = None
        widget.bind("<Configure>", lambda e: self.card._changed() if e.widget is widget else None, add="+")
        self.page._register_widget(widget, self)
        self.card._changed()
        return widget

    def button(self, text, command=None, *, kind="secondary", state="normal", width=None) -> CanvasButton:
        card = self.card
        button = CanvasButton(card.host, text=text, command=command, kind=kind, scale=card.page.scale,
                              state=state, width=width)
        button.on_resize = card._changed
        if self._run is None:
            self._run = FocusGroup(card.page, canvas=card.host)
            self.groups.append(self._run)
        self._run.add(button)
        self.items.append(["control", button, None])
        card._changed()
        return button

    def controls(self):
        return [obj for kind, obj, _ in self.items if kind == "control"]

    def _layout(self, x0, y, width) -> int:
        s = self.page.scale
        self.x0, self.width = x0, width
        start, right = x0 + px(s, self.indent), x0 + width
        line_h = px(s, 30)
        x, top, used = start, y, False
        for item in self.items:
            kind, obj, _ = item
            w = obj.winfo_reqwidth()
            if used and x + w > right:
                x, top = start, top + line_h + px(s, self.row_gap)
                used = False
            if kind == "control":
                obj.place(x, top + (line_h - obj.winfo_reqheight()) // 2)
                obj.set_visible(True)
            else:
                h = obj.winfo_reqheight()
                place = (x, top + (line_h - h) // 2)
                if place != item[2]:
                    item[2] = place
                    obj.place_forget()
                    obj.place(x=place[0], y=place[1])
            x += w + px(s, self.gap)
            used = True
        for group in self.groups:
            group.place(*self.card.proxy_at())
        return (top - y) + line_h if self.items else 0

    def _hide(self) -> None:
        for item in self.items:
            kind, obj, placed = item
            if kind == "control":
                obj.set_visible(False)
            elif placed is not None:
                obj.place_forget()
                item[2] = None
        for group in self.groups:
            group.hide()
        self.y = None


class _Container:
    """The element-building API of cards and sections."""

    def _first_gap(self):
        return 0

    def _add(self, element):
        self.elements.append(element)
        self.card._changed()
        return element

    def row(self, *, height=30, top=None) -> KitRow:
        return self._add(KitRow(self, height=height, top=top))

    def text(self, text="", *, textvariable=None, tone="muted", wrap=True, top=None, max_lines=0) -> KitText:
        return self._add(KitText(self.card.host, text, textvariable=textvariable, tone=tone, wrap=wrap,
                                 max_lines=max_lines, owner=self, top=top))

    def block(self, widget, *, top=None, height=None, bleed_left=0) -> KitBlock:
        return self._add(KitBlock(self, widget, top=top, height=height, bleed_left=bleed_left))

    def flow(self, *, gap=10, row_gap=10, indent=0, top=None) -> KitFlow:
        return self._add(KitFlow(self, gap=gap, row_gap=row_gap, indent=indent, top=top))

    def section(self, *, top=None) -> "KitSection":
        return self._add(KitSection(self, top=top))

    def split(self, widths=("fill", 62, 255), *, top=None, stack_below=717, stack_gap=15):
        split = self._add(_Split(self, widths, top=top, stack_below=stack_below, stack_gap=stack_gap))
        return split.first, split.second

    def disclosure(self, title, *, top=None) -> "KitDisclosure":
        return self._add(KitDisclosure(self, title, top=top))

    def _layout_elements(self, x0, y, width, first_gap) -> int:
        s = self.card.page.scale
        cursor, first = y, True
        for element in self.elements:
            if not element.visible:
                continue
            gap = element.top if element.top is not None else (first_gap if first else ROW_GAP)
            cursor += px(s, gap)
            element.y = cursor
            element.height = element._layout(x0, cursor, width)
            cursor += element.height
            first = False
        return cursor - y


class KitSection(_Element, _Container):
    """A group of elements with the card's building API; hide it to hide them all (and their space)."""

    def __init__(self, owner, *, top=None):
        super().__init__(owner, top)
        self.elements = []
        self.disclosure = None       # the KitDisclosure whose body this is

    def _layout(self, x0, y, width) -> int:
        self.x0, self.width = x0, width
        return self._layout_elements(x0, y, width, 0)

    def _hide(self) -> None:
        for element in self.elements:
            element._hide()
        self.y = None


class _Split(_Element):
    """Two sections side by side, a 1 px divider centred in the cell between them; stacked when narrow."""

    def __init__(self, owner, widths, *, top=None, stack_below=717, stack_gap=15):
        super().__init__(owner, top)
        if len(widths) != 3:
            raise ValueError("split widths are (first, middle, second)")
        self.widths, self.stack_below, self.stack_gap = widths, stack_below, stack_gap
        self.first, self.second = KitSection(self), KitSection(self)
        self.first.top = self.second.top = 0
        self.stacked = False
        self.looks = _Looks(self.host)
        self.divider = self.host.create_rectangle(0, 0, 0, 0, width=0, fill=ui_theme.TOKENS["card_edge"],
                                                  state="hidden")

    def _layout(self, x0, y, width) -> int:
        s = self.page.scale
        self.x0, self.width = x0, width
        self.stacked = width < px(s, self.stack_below)
        if self.stacked:
            self.looks.config(self.divider, state="hidden")
            height = 0
            for section in (self.first, self.second):
                if not section.visible:
                    continue
                if height:
                    height += px(s, self.stack_gap)
                section.y = y + height
                section.height = section._layout(x0, y + height, width)
                height += section.height
            return height
        fixed = [px(s, w) for w in self.widths if w != "fill"]
        fills = [i for i, w in enumerate(self.widths) if w == "fill"]
        rest = max(0, width - sum(fixed))
        sizes = [px(s, w) if w != "fill" else rest // max(1, len(fills)) for w in self.widths]
        xs = (x0, x0 + sizes[0], x0 + sizes[0] + sizes[1])
        height = 0
        for section, x, w in ((self.first, xs[0], sizes[0]), (self.second, xs[2], sizes[2])):
            if section.visible:
                section.y = y
                section.height = section._layout(x, y, w)
                height = max(height, section.height)
        both = self.first.visible and self.second.visible
        line = hairline(s)
        middle = xs[1] + (sizes[1] - line) // 2
        self.looks.coords(self.divider, middle, y, middle + line, y + height)
        self.looks.config(self.divider, state="normal" if both and height else "hidden")
        return height

    def _hide(self) -> None:
        self.first._hide()
        self.second._hide()
        self.looks.config(self.divider, state="hidden")
        self.y = None


class _DisclosureToggle(CanvasControl):
    """The chevron row of a card disclosure as a canvas control (focus groups, Return/space)."""

    def __init__(self, disclosure, title):
        card = disclosure.card
        super().__init__(card.host, command=disclosure.flip, scale=card.page.scale)
        self.master = card.host
        self.group = None
        self.row = _ChevronRow(card.host, title, scale=card.page.scale, surface=ui_theme.TOKENS["panel"])
        self.tag = self.row.tag
        self.ramp = self.row.ramp
        self._box = None
        _register(card.host, self)
        self._connect()

    def _on_enter(self, _event=None):
        if self._interactive() and not self._hover:
            self._hover = True
            self._set_cursor(True)
            self.ramp.enter()

    def _on_leave(self, _event=None):
        if self._hover:
            self._hover = False
            self._set_cursor(False)
            self.ramp.leave()

    def set_focus(self, focused: bool) -> None:
        self._focused = bool(focused)

    def place(self, x, y, width):
        self._box = (x, y, x + self.row.width(), y + px(self.scale, 18))
        self.row.place(x, y + px(self.scale, 18) // 2)
        self.row.show(True)

    def set_visible(self, visible):
        self._shown = bool(visible)
        if not self._shown:
            self.row.show(False)
            self._box = None

    def _inside(self, event) -> bool:
        if self._box is None:
            return False
        x0, y0, x1, y1 = self._box
        return x0 <= self.canvas.canvasx(event.x) <= x1 and y0 <= self.canvas.canvasy(event.y) <= y1

    def box(self):
        return self._box

    def destroy(self) -> None:
        _unregister(self.canvas, self)
        super().destroy()


class KitDisclosure(_Element):
    """A canvas chevron row (h 18) that shows and hides ``body`` (a :class:`KitSection`) 15 px below it."""

    def __init__(self, owner, title, *, top=None):
        super().__init__(owner, top)
        self.title = title
        self.opened = False
        self.toggle = _DisclosureToggle(self, title)
        self.group = FocusGroup(self.page, canvas=self.host)
        self.group.add(self.toggle)
        self.body = KitSection(self, top=15)
        self.body.disclosure = self
        self.body.visible = False

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value=True):
        value = bool(value)
        if value == self.opened:
            return
        self.opened = value
        self.toggle.row.set_opened(value)
        if not value:
            self.body._hide()
            focus = self.page._focus_path()
            for widget in self.page._widgets_in(self.body):
                if focus == str(widget) or focus.startswith(str(widget) + "."):
                    self.group.focus(self.toggle)   # never leave the keyboard inside a hidden body
                    break
        self.body.visible = value
        self.card._changed()

    def _layout(self, x0, y, width) -> int:
        s = self.page.scale
        self.x0, self.width = x0, width
        self.toggle.place(x0, y, width)
        self.toggle.set_visible(True)
        self.group.place(*self.card.proxy_at())
        height = px(s, 18)
        if self.opened:
            gap = px(s, self.body.top)
            self.body.y = y + height + gap
            self.body.height = self.body._layout(x0, y + height + gap, width)
            height += gap + self.body.height
        return height

    def _hide(self) -> None:
        self.toggle.set_visible(False)
        self.group.hide()
        self.body._hide()
        self.y = None


class KitCard(_Container):
    """One card: its plate middle (``holder`` = ``host``, a canvas placed on the sheet) and its content.

    ``title`` (VALVE Oracle SemiBold 15 on an 18 px line, or the Inter / Segoe
    fallback; the ``title_font`` role) and an optional info icon ``px(6)`` after it;
    ``trailing`` is a row right-aligned on the title line. Content starts ``title_gap``
    below the title line and ends 21 px (or ``bottom`` + 1) above the plate's bottom.
    """

    def __init__(self, page, title, *, info=None, title_gap=15, bottom=None, edge="card_edge", column=None,
                 title_font="card_title"):
        t = ui_theme.TOKENS
        s = page.scale
        self.page = page
        self.card = self
        self.title = title
        self.title_gap = title_gap
        self.bottom = bottom
        self.edge = edge
        self.column = column            # "main", "side" or None (full width)
        self.elements = []
        self.visible = True
        self.rect = None                # page-canvas px of the plate, while laid out
        self.extra = 0                  # px added above the bottom inset (a bento card with nothing to grow)
        self.inset = px(s, page.CARD_PAD) + hairline(s)
        self._radius = px(s, page.CARD_RADIUS)
        self._size = None               # (width, height) laid out
        self._placed = None
        self._dirty = True
        sheet = page.sheet
        self.holder = self.host = tk.Canvas(sheet if sheet is not None else page.canvas, background=t["panel"],
                                            highlightthickness=0, borderwidth=0, takefocus=0, width=1, height=1)
        self.window = None
        self.retained = page.retained if sheet is not None else retain_surface(self.holder)
        if sheet is None:
            self.window = page.canvas.create_window(0, 0, anchor="nw", window=self.holder, state="hidden",
                                                    tags=("kit_card",))
        self.looks = _Looks(self.host)
        self._edges = tuple(self.host.create_rectangle(0, 0, 0, 0, width=0, fill=t[edge]) for _ in range(2))
        self.title_item = None
        self.info = None
        if title is not None:
            self.title_item = self.host.create_text(0, 0, text=title, anchor="w", fill=t["text"],
                                                    font=fonts.font(page, title_font))
            if info:
                self.info = CanvasInfoIcon(self.host, info, page=page, scale=s, surface=t["panel"])
                self._info_group = FocusGroup(page, canvas=self.host)
                self._info_group.add(self.info)
        self.trailing = KitRow(self, height=30)
        self.trailing.top = 0
        self._unfont = fonts.on_change(page, self._changed)
        self.host.bind("<Destroy>", lambda e: self._unfont() if e.widget is self.host else None, add="+")

    # -- state ----------------------------------------------------------------------------------
    def shown(self) -> bool:
        return self.visible

    def _changed(self, *_args) -> None:
        self._dirty = True
        self.page.layout()

    def set_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self.visible:
            return
        self.visible = visible
        if not visible:
            self._hide_plate()
        self._changed()

    def winfo_manager(self) -> str:
        return "canvas" if self.visible else ""

    def winfo_ismapped(self) -> bool:
        try:
            return self.visible and self.rect is not None and bool(self.host.winfo_ismapped())
        except tk.TclError:
            return False

    def pack(self, **_ignored) -> None:
        self.set_visible(True)

    def pack_forget(self) -> None:
        self.set_visible(False)

    def origin(self):
        """Page-canvas px of the card canvas's (0, 0)."""
        x0, y0, _x1, _y1 = self.rect
        return x0 + self._radius, y0

    def proxy_at(self):
        """Where the card's focus stand-ins sit: a ``panel`` pixel just inside the top edge."""
        return (0, hairline(self.page.scale))

    def content_box(self):
        """(x0, width) of the content in card-canvas px for the last layout."""
        width = self._size[0] if self._size else 0
        return self.inset - self._radius, max(1, width + 2 * self._radius - 2 * self.inset)

    # -- layout ----------------------------------------------------------------------------------
    def measure(self, width) -> int:
        """Lay the content out for a plate ``width`` px wide; returns the plate's height."""
        s = self.page.scale
        r, inset = self._radius, self.inset
        canvas_w = max(1, width - 2 * r)
        x0, content_w = inset - r, max(1, width - 2 * inset)
        y = inset
        first_gap = 0
        if self.title_item is not None:
            middle = y + px(s, TITLE_LINE) // 2
            self.looks.coords(self.title_item, x0, middle)
            if self.info is not None:
                box = self.host.bbox(self.title_item)
                right = box[2] if box else x0
                self.info.place(right + px(s, 6), middle - self.info.winfo_reqheight() // 2)
                self.info.set_visible(True)
                self._info_group.place(*self.proxy_at())
            if self.trailing.core.cells:
                natural = self.trailing.natural_width()
                self.trailing.y = middle - px(s, 30) // 2
                self.trailing.height = self.trailing._layout(x0 + content_w - natural, self.trailing.y, natural)
            y += px(s, TITLE_LINE)
            first_gap = self.title_gap
        y += self._layout_elements(x0, y, content_w, first_gap) + self.extra
        y += px(s, self.bottom) + hairline(s) if self.bottom is not None else inset
        self._size = (canvas_w, y)
        line = hairline(s)
        self.looks.coords(self._edges[0], 0, 0, canvas_w, line)
        self.looks.coords(self._edges[1], 0, y - line, canvas_w, y)
        self._dirty = False
        return y

    def place(self, x, y, width) -> int:
        """Put the plate at page (``x``, ``y``), ``width`` px wide; returns its bottom (page px)."""
        if self._dirty or self._size is None or self._size[0] != max(1, width - 2 * self._radius):
            self.measure(width)
        canvas_w, height = self._size
        self.rect = (x, y, x + width, y + height)
        place = (x, y, canvas_w, height)
        if place != self._placed:
            self._placed = place
            if self.window is None:   # on the sheet, which starts a radius in: sheet x == plate x
                self.holder.place(x=x, y=y, width=canvas_w, height=height)
            else:
                canvas = self.page.canvas
                canvas.coords(self.window, x + self._radius, y)
                canvas.itemconfigure(self.window, width=canvas_w, height=height, state="normal")
        return y + height

    def _hide_plate(self) -> None:
        self.rect = None
        if self._placed is not None:
            self._placed = None
            if self.window is None:
                self.holder.place_forget()
            else:
                self.page.canvas.itemconfigure(self.window, state="hidden")

    def element_rect(self, element):
        return element.page_rect()


class KitColumn:
    """One column of a two-column page: its cards stack from the columns' top, 15 px apart."""

    def __init__(self, page, name):
        self.page = page
        self.name = name

    def add_card(self, title, *, info=None, title_gap=15, bottom=None, edge="card_edge") -> KitCard:
        return self.page._add_card(title, info=info, title_gap=title_gap, bottom=bottom, edge=edge,
                                   column=self.name)

    @property
    def cards(self):
        return [card for card in self.page.cards if card.column == self.name]
