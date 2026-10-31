"""Shared Tk widgets; independent of page builders and application state."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import time

from .. import ui_theme
from .scale import even_px, scaler, ui_scale

# Spacing is in design px (100 %); builders convert it with the window's display scale.
GAP = 14


WHEEL_PIXELS_PER_NOTCH = 60
WHEEL_UNIT_PIXELS = 1


class NavigationMarker(tk.Canvas):
    """Fade complete selection outlines behind real, unobstructed buttons."""

    DURATION = .14
    FRAME_MS = 16

    def __init__(self, parent, *, scale=1.0):
        super().__init__(parent, background=ui_theme.TOKENS["bg"],
                         highlightthickness=0, borderwidth=0, takefocus=False)
        self.scale = scale
        self._target = None
        self._target_bindings = []
        self._job = None
        self._box = self._destination = None
        self._closed = False
        self._outline = self._new_outline()
        self._outlines = {}
        self._strengths = {}
        self._boxes = {}
        self._item_boxes = {}
        self._colors = (ui_theme.rgb(ui_theme.TOKENS["bg"]), ui_theme.rgb(ui_theme.TOKENS["accent"]))
        self.bind("<Destroy>", self._destroyed, add="+")
        self.bind("<Map>", self._layout_changed, add="+")
        self.bind("<Configure>", self._layout_changed, add="+")
        self._layout_binding = parent.bind("<Configure>", self._layout_changed, add="+")
        self._top = parent.winfo_toplevel()
        self._visibility_bindings = [(sequence, self._top.bind(sequence, self._visibility_changed, add="+"))
                                     for sequence in ("<Map>", "<Unmap>")]

    def _new_outline(self):
        return self.create_polygon(0, 0, 0, 0, 0, 0, fill="",
            outline=ui_theme.TOKENS["accent"], width=max(1, round(self.scale)),
            smooth=True, splinesteps=24, state="hidden")

    def _cancel(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _detach_target(self):
        if self._target is not None:
            for sequence, token in self._target_bindings:
                try:
                    self._target.unbind(sequence, token)
                except tk.TclError:
                    pass
        self._target_bindings = []

    def _hide(self):
        self._cancel()
        self._box = self._destination = None
        self._strengths = {}
        self._boxes = {}
        self.itemconfigure("all", state="hidden")
        self.place_forget()

    def _defer_position(self, animate):
        self._cancel()
        def ready():
            self._job = None
            self._position(animate)
        self._job = self.after_idle(ready)

    def select(self, button):
        if self._closed:
            return
        if button is self._target:
            if self._geometry(button) != self._destination or self._box is None:
                self._cancel()
                self._position(False)
            return
        self._cancel()
        self._detach_target()
        self._target = button
        if button is None:
            self._hide()
            return
        for sequence in ("<Configure>", "<Map>", "<Unmap>"):
            self._target_bindings.append((sequence, button.bind(sequence, self._layout_changed, add="+")))
        self._target_bindings.append(("<Destroy>", button.bind("<Destroy>", self._target_destroyed, add="+")))
        if not self._ready(button):
            self._defer_position(False)
        else:
            self._position(self._box is not None)

    def _ready(self, button):
        try:
            return (button is not None and button.winfo_exists() and button.winfo_viewable()
                    and button.winfo_width() > 1 and button.winfo_height() > 1)
        except tk.TclError:
            return False

    def _layout_changed(self, _event=None):
        if self._target is not None and not self._closed:
            if self._geometry(self._target) == self._destination and self._box is not None:
                return
            self._cancel()
            self._position(False)

    def _visibility_changed(self, event):
        if event.widget is self._top:
            self._layout_changed()

    def _geometry(self, button=None):
        if button is None:
            button = self._target
        if (not self._ready(button) or not self.winfo_viewable()
                or self.winfo_width() <= 1 or self.winfo_height() <= 1):
            return None
        # Stay just outside the opaque ttk child, with no empty halo between
        # its rounded fill and this stroke. Widget bounds are half-open.
        outset = max(1, (max(1, round(self.scale)) + 1) // 2)
        x = button.winfo_rootx() - self.winfo_rootx()
        y = button.winfo_rooty() - self.winfo_rooty()
        return (x - outset, y - outset, x + button.winfo_width() - 1 + outset,
                y + button.winfo_height() - 1 + outset)

    def _color(self, strength):
        # Tk Canvas color changes affect just these tiny outline items. There
        # is no ttk.Style configure/map or global theme invalidation per frame.
        return "#" + "".join(f"{round(a+(b-a)*strength):02x}" for a, b in zip(*self._colors))

    def _draw_item(self, item, box, strength):
        if self._item_boxes.get(item) != box:
            left, top, right, bottom = box
            # A broader corner would pass under the native button window and
            # leave disconnected edges when this close to the child bounds.
            outset = max(1, (max(1, round(self.scale)) + 1) // 2)
            radius = min(4 * outset, (right-left)/2, (bottom-top)/2)
            self.coords(item,
                left + radius, top, right - radius, top, right, top, right, top + radius,
                right, bottom - radius, right, bottom, right - radius, bottom,
                left + radius, bottom, left, bottom, left, bottom - radius,
                left, top + radius, left, top)
            self._item_boxes[item] = box
        self.itemconfigure(item, outline=self._color(strength),
                           state="normal" if strength > 0 else "hidden")

    def _render(self, strengths):
        self._strengths = {}
        self._boxes = {}
        for button, item in tuple(self._outlines.items()):
            if not self._ready(button):
                self.itemconfigure(item, state="hidden")
                # Removed buttons must not become retained widget references.
                if not button.winfo_exists():
                    self.delete(item)
                    self._item_boxes.pop(item, None)
                    del self._outlines[button]
                continue
            strength = strengths.get(button, 0)
            if strength <= 0:
                self.itemconfigure(item, state="hidden")
                continue
            box = self._geometry(button)
            if box is None:
                self.itemconfigure(item, state="hidden")
                continue
            self._draw_item(item, box, strength)
            self._strengths[button] = strength
            self._boxes[button] = box

    def _position(self, animate):
        if self._closed:
            return
        button = self._target
        if not self._ready(button):
            self._hide()
            return
        if not self.winfo_viewable() or self.winfo_width() <= 1 or self.winfo_height() <= 1:
            self.place(x=0, y=0, relwidth=1, relheight=1, bordermode="ignore")
            self.tk.call("lower", self._w)
            self._defer_position(animate)
            return
        target = self._box = self._destination = self._geometry(button)
        if button not in self._outlines:
            unused = self._outline not in self._outlines.values() and bool(self.find_withtag(self._outline))
            self._outlines[button] = self._outline if unused else self._new_outline()
        self._outline = self._outlines[button]
        if not animate or not self._strengths or self._strengths == {button: 1}:
            self._render({button: 1})
            return
        # Keep every currently visible box at its actual button. Rapid clicks
        # blend from rendered strengths, never from stale selected endpoints.
        start, began = dict(self._strengths), time.monotonic()
        start.setdefault(button, 0)
        def tick():
            self._job = None
            if self._closed:
                return
            if self._geometry(button) != target:
                self._position(False)
                return
            progress = min(1.0, (time.monotonic()-began)/self.DURATION)
            eased = 1-(1-progress)**3
            self._render({item: value+((1 if item is button else 0)-value)*eased
                          for item, value in start.items()})
            if progress < 1:
                self._job = self.after(self.FRAME_MS, tick)
        tick()

    def _target_destroyed(self, event):
        if event.widget is self._target:
            self._detach_target()
            self._target = None
            self._hide()

    def _destroyed(self, event):
        if event.widget is self:
            self._closed = True
            self._cancel()
            self._detach_target()
            try:
                self.master.unbind("<Configure>", self._layout_binding)
            except tk.TclError:
                pass
            for sequence, token in self._visibility_bindings:
                try:
                    self._top.unbind(sequence, token)
                except tk.TclError:
                    pass

class ScrollPage(ttk.Frame):
    """One vertical scroll region; wheel events stay within this page."""
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=ui_theme.TOKENS["bg"], highlightthickness=0)
        self.scroll_gutter = ttk.Frame(self)
        self.scrollbar = ttk.Scrollbar(self.scroll_gutter, orient="vertical", command=self.canvas.yview)
        self.scroll_gutter.configure(width=self.scrollbar.winfo_reqwidth())
        self.scroll_gutter.pack_propagate(False)
        self._scrollbar_visible = None
        self.canvas.configure(yscrollcommand=self._scroll_position, yscrollincrement=WHEEL_UNIT_PIXELS)
        # Reserve the narrow rail before the canvas asks for its default
        # width. Otherwise a small/high-DPI page can allocate the gutter 0px.
        self.scroll_gutter.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.ui_scale = ui_scale(self)
        self.body = ttk.Frame(self.canvas, padding=even_px(self.ui_scale, (16, 12)))
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._viewport_changed)
        self.body.bind("<Configure>", self._resize)
        self._wheel_pixels = 0.0
        self._scrollregion = None
        self.bind_id = self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Destroy>", self._destroyed, add="+")

    def _viewport_changed(self, event):
        if int(float(self.canvas.itemcget(self.window,"width"))) != event.width:
            self.canvas.itemconfigure(self.window,width=event.width)

    def _scroll_position(self, first, last):
        self.scrollbar.set(first,last)
        # Keep the gutter's width when the rail disappears. Crossing the
        # overflow threshold must not narrow/widen the page and wrap it again.
        width=self.scrollbar.winfo_reqwidth()
        if int(self.scroll_gutter.cget("width")) != width:
            self.scroll_gutter.configure(width=width)
        visible=float(first)>0 or float(last)<1
        if visible != self._scrollbar_visible:
            self._scrollbar_visible=visible
            if visible:
                self.scrollbar.pack(fill="both",expand=True)
            else:
                self.scrollbar.pack_forget()

    def _resize(self, _event=None):
        region = self.canvas.bbox("all")
        if region != self._scrollregion:
            self._scrollregion = region
            self.canvas.configure(scrollregion=region)

    def _wheel(self, event):
        target = event.widget
        while target is not None and target is not self:
            # A nested page or a control with its own wheel semantics owns the
            # event. Registration order of toplevel bindings must not let an
            # outer page scroll first or select a combo and scroll together.
            if isinstance(target, (ScrollPage, ttk.Treeview, tk.Text, tk.Listbox,
                                   ttk.Combobox, tk.Spinbox, ttk.Spinbox,
                                   tk.Scrollbar, ttk.Scrollbar)):
                return
            target = getattr(target, "master", None)
        if target is self and self.canvas.yview() != (0.0, 1.0):
            self.scroll_page_wheel(event)
            return "break"

    def scroll_page_wheel(self, event):
        # Keep subpixel input, without quantizing precision wheels into large
        # jumps. A normal Windows wheel notch still travels 60 design pixels.
        notch = WHEEL_PIXELS_PER_NOTCH * self.ui_scale
        if event.delta:
            pixels = -event.delta / 120 * notch
        else:
            pixels = -notch if getattr(event, "num", 0) == 4 else notch
        first, last = self.canvas.yview()
        if (pixels < 0 and first <= 0.0) or (pixels > 0 and last >= 1.0):
            # Outward input at an edge must not become debt that delays the
            # next movement back into the page.
            self._wheel_pixels = 0.0
            return
        if pixels * self._wheel_pixels < 0:
            self._wheel_pixels = 0.0
        self._wheel_pixels += pixels
        units = int(self._wheel_pixels / WHEEL_UNIT_PIXELS)
        if not units:
            return
        self._wheel_pixels -= units * WHEEL_UNIT_PIXELS
        self.canvas.yview_scroll(units, "units")
        first, last = self.canvas.yview()
        if (pixels < 0 and first <= 0.0) or (pixels > 0 and last >= 1.0):
            self._wheel_pixels = 0.0

    def _destroyed(self, event):
        if event.widget is self:
            self.winfo_toplevel().unbind("<MouseWheel>", self.bind_id)

    def reveal(self, widget):
        self.update_idletasks()
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        height=max(1,self.body.winfo_height())
        viewport=self.canvas.winfo_height()
        top=self.canvas.canvasy(0)
        bottom=y+widget.winfo_height()
        if y>=top and bottom<=top+viewport:
            return
        gap=even_px(self.ui_scale,GAP)
        target=y-gap if y<top or widget.winfo_height()>viewport else bottom+gap-viewport
        self.canvas.yview_moveto(max(0,min(target,height-viewport))/height)


def surface_style(parent):
    name = parent.cget("style") or "TFrame"
    if "Card." in name:
        return "Card.TFrame"
    background = ttk.Style(parent).lookup(name, "background")
    return "Card.TFrame" if background == ttk.Style(parent).lookup("Card.TFrame", "background") else "TFrame"


class Disclosure(ttk.Frame):
    def __init__(self, parent, title):
        frame_style = surface_style(parent)
        super().__init__(parent, style=frame_style)
        self.title = title
        self.opened = False
        self.toggle = ttk.Button(self, text=">  " + title, command=self.flip, style="Disclosure.Card.TButton" if frame_style == "Card.TFrame" else "Disclosure.TButton")
        self.toggle.pack(anchor="w")
        self.body = ttk.Frame(self, style=frame_style, padding=even_px(ui_scale(self), (0, 8, 0, 0)))

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value=True):
        self.opened = value
        self.toggle.configure(text=("v  " if value else ">  ") + self.title)
        if value:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()


def card(parent, title):
    dp = scaler(parent)
    frame = ttk.Frame(parent, style="Rounded.Card.TFrame", padding=dp(14))
    frame.pack(fill="x", pady=dp((0, GAP)))
    ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=dp((0, GAP)))
    return frame


def field(parent, label, variable, *, values=None):
    dp = scaler(parent)
    frame = ttk.Frame(parent, style=surface_style(parent))
    frame.pack(fill="x", pady=dp((0, GAP)))
    ttk.Label(frame, text=label, style="CardMuted.TLabel" if surface_style(parent) == "Card.TFrame" else "Muted.TLabel").pack(anchor="w", pady=dp((0, 6)))
    widget = (ttk.Combobox(frame, textvariable=variable, values=values, state="readonly",
                            style="Card.TCombobox" if surface_style(parent) == "Card.TFrame" else "TCombobox")
              if values is not None else ttk.Entry(frame, textvariable=variable,
                                                style="Card.TEntry" if surface_style(parent) == "Card.TFrame" else "TEntry"))
    widget.pack(fill="x")
    return widget


def actions(parent, specs, columns=3):
    """Bounded wrapping rows, never an unbounded horizontal pack of buttons."""
    dp = scaler(parent)
    frame = ttk.Frame(parent, style=surface_style(parent))
    frame.pack(fill="x")
    buttons = []
    for i, spec in enumerate(specs):
        label, command, *style = spec
        button_style = style[0] if style else "TButton"
        if surface_style(parent) == "Card.TFrame":
            button_style = "Card." + button_style
        b = ttk.Button(frame, text=label, command=command, style=button_style)
        b.grid(row=i // columns, column=i % columns, sticky="w", padx=dp((0, 10 if i % columns < columns-1 else 0)), pady=dp((0, 10)))
        frame.columnconfigure(i % columns, weight=0)
        buttons.append(b)
    return buttons


def disclosure(parent, title):
    d = Disclosure(parent, title)
    d.pack(fill="x", pady=even_px(ui_scale(parent), (0, GAP)))
    return d


