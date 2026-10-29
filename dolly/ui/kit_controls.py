"""Canvas-drawn controls of the page kit (PLAN-5 2.7, 2.8; USER-ADDITIONS UPDATE 4 and 6).

Every card button on the five ported pages is a :class:`CanvasButton`: items on its
card's canvas (a kit plate, its corners precomposed opaque over ``panel``, and a
centred label), no child window. Its hover and press colours ease through a
:class:`.tween.ColorRamp` on the toplevel's one :class:`.tween.Animator` (120 ms in,
180 ms out, 120 ms press; the first step is drawn in the event handler), the corner
faces of the ramp come from pre-warmed steps, and a press dips the button by a pixel
at once. It keeps the surface the app and the tests used on ``ttk.Button``s:
``invoke``, ``configure/cget`` of ``text``/``state``/``command``, ``state()``/``instate()``,
``bind`` for press/release, ``focus_set`` and the ``winfo_*`` geometry, computed from its
box and its canvas (so it also answers in a withdrawn window). :class:`KitButton` is
the same button as a one-control canvas widget for frame-hosted places.

Keyboard: canvas controls are reached through a :class:`FocusGroup`, a 1 x 1 frame
*placed* on the canvas they sit on (never a canvas window item), created where the
controls sit in build order so Tab follows reading order. Nothing here draws a focus
state (N4): keyboard focus on an info icon opens its bubble, the icon stays ``dim``.

Rows: :class:`RowLayout` lays cells out left to right on one canvas (labels and
controls as items, real widgets *placed* on it); ``KitRow`` (cards), the page bands
and :class:`KitStrip` (a standalone row canvas inside frame-hosted composites) use it.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import itertools
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import art, boil, fonts, kit_fields
from .canvas_controls import CanvasControl, CanvasFocus, _track_tab_direction
from .scale import px, ui_scale
from .stage_page import BUBBLE_DELAY_MS, retain_surface
from .tween import Animator, ColorRamp

ELLIPSIS = "…"
CORNERS = ("nw", "ne", "sw", "se")
TONES = ("muted", "dim", "text")
_SERIAL = itertools.count()

# Kit Button [design reference] per kind: (rest, hover, pressed) of the ramped colour, and the label colour
# of a plate kind (a quiet button ramps its label instead).
FACES = {
    "secondary": (("button", "button_hover", "button_active"), "text"),
    "primary": (("accent", "accent_hover", "accent_active"), "accent_ink"),
    "quiet": (("muted", "text", None), None),
}


def page_of(widget):
    """The :class:`~dolly.ui.kit_page.KitPage` that shows ``widget`` (through adopted frames), or None."""
    while widget is not None:
        if getattr(widget, "_is_kit_page", False):
            return widget
        host = getattr(widget, "_kit_host", None)
        if host is not None:
            return host.page
        widget = getattr(widget, "master", None)
    return None


def surface_of(widget) -> str:
    """The colour ``widget`` paints (a tk background, or its ttk style's), ``panel`` if unknown."""
    try:
        if isinstance(widget, ttk.Widget):
            name = str(widget.cget("style")) or widget.winfo_class()
            colour = ttk.Style(widget).lookup(name, "background")
        else:
            colour = str(widget.cget("background"))
        if colour.startswith("#") and len(colour) == 7:
            return colour
    except (AttributeError, tk.TclError):
        pass
    return ui_theme.TOKENS["panel"]


def safe_unbind(widget, sequence, funcid) -> None:
    """Remove only ``funcid`` from ``widget``'s ``sequence`` binding (older tkinter drops them all)."""
    try:
        script = str(widget.tk.call("bind", widget._w, sequence))
        keep = "\n".join(line for line in script.split("\n") if funcid not in line)
        widget.tk.call("bind", widget._w, sequence, keep)
        widget.deletecommand(funcid)
    except tk.TclError:
        pass


def _register(canvas, control) -> None:
    controls = getattr(canvas, "_kit_controls", None)
    if controls is None:
        controls = canvas._kit_controls = []
    controls.append(control)


def _unregister(canvas, control) -> None:
    try:
        canvas._kit_controls.remove(control)
    except (AttributeError, ValueError):
        pass


def buttons(widget) -> list:
    """Every :class:`CanvasButton` and :class:`KitButton` at or below ``widget``, in creation order."""
    found, pending = [], [widget]
    while pending:
        current = pending.pop()
        if isinstance(current, KitButton):
            found.append(current)
        else:
            found.extend(control for control in getattr(current, "_kit_controls", ())
                         if isinstance(control, CanvasButton) and not control._destroyed)
        try:
            pending.extend(current.winfo_children())
        except (AttributeError, tk.TclError):
            pass
    return sorted(found, key=lambda control: control._serial)


class _Looks:
    """What was last sent to Tk per canvas item: Tk repaints an item for every coords or
    itemconfigure, even one that changes nothing."""

    __slots__ = ("canvas", "sent")

    def __init__(self, canvas):
        self.canvas = canvas
        self.sent = {}

    def coords(self, item, *xy):
        key = (item, "coords")
        if self.sent.get(key) != xy:
            self.sent[key] = xy
            self.canvas.coords(item, *xy)

    def config(self, item, **options):
        changed = {}
        for name, value in options.items():
            key = (item, name)
            if self.sent.get(key) != value:
                self.sent[key] = value
                changed[name] = value
        if changed:
            self.canvas.itemconfigure(item, **changed)


# -- text ------------------------------------------------------------------------------------

class KitLabel:
    """A text item on a canvas with a Label-like ``configure``/``cget``.

    ``tone`` is a token (``muted`` labels and values, ``dim`` hints, ``text`` group
    labels). ``wrap`` (px) wraps at word boundaries, ``max_lines`` cuts the rest with
    "…"; a one-line label given a ``clip`` width is cut to fit. ``textvariable`` is
    traced (the trace goes with :meth:`destroy`). ``on_change`` is called when a change
    may have changed the text's size, so the owner can lay out again.
    """

    def __init__(self, canvas, text="", *, textvariable=None, tone="muted", font="body", wrap=0, max_lines=0):
        self.canvas = canvas
        self.on_change = None
        self._font = fonts.font(canvas, font)
        self._tone = tone if tone in TONES else None
        self._fill = ui_theme.TOKENS[tone] if tone in ui_theme.TOKENS else tone
        self._variable = textvariable
        self._text = str(textvariable.get() if textvariable is not None and not text else text)
        self._wrap = int(wrap)
        self._clip = 0
        self._max_lines = int(max_lines)
        self._lines = [""]
        self._widths = {}
        self._destroyed = False
        self.elided = False
        self.looks = _Looks(canvas)
        self.item = canvas.create_text(0, 0, text="", font=self._font, fill=self._fill, anchor="nw",
                                       state="hidden")
        self._trace = textvariable.trace_add("write", self._variable_changed) if textvariable is not None else None
        self._unfont = fonts.on_change(canvas, self._font_changed)
        self._render()

    # sizes
    def measure(self, text) -> int:
        width = self._widths.get(text)
        if width is None:
            if len(self._widths) > 256:
                self._widths.clear()
            width = self._widths[text] = self._font.measure(text)
        return width

    def linespace(self) -> int:
        return int(self._font.metrics("linespace"))

    def natural_width(self) -> int:
        """The one-line width of the whole text (what a hugging cell gives it)."""
        return max((self.measure(line) for line in self._text.split("\n")), default=0)

    def size(self) -> tuple[int, int]:
        """The rendered text's (width, height) in px."""
        return max((self.measure(line) for line in self._lines), default=0), len(self._lines) * self.linespace()

    def last_line_width(self) -> int:
        return self.measure(self._lines[-1]) if self._lines else 0

    def set_wrap(self, width) -> None:
        width = max(0, int(width))
        if width != self._wrap:
            self._wrap = width
            self._render()

    def set_clip(self, width) -> None:
        width = max(0, int(width))
        if width != self._clip:
            self._clip = width
            self._render()

    # drawing
    def place(self, x, y, *, anchor="nw") -> None:
        self.looks.coords(self.item, x, y)
        self.looks.config(self.item, anchor=anchor, state="normal")

    def hide(self) -> None:
        self.looks.config(self.item, state="hidden")

    def _wrapped(self):
        lines = []
        for paragraph in self._text.split("\n"):
            words = paragraph.split(" ")
            line = ""
            for word in words:
                candidate = word if not line else line + " " + word
                if line and self.measure(candidate) > self._wrap:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        return lines

    def _cut(self, text, width):
        if self.measure(text) <= width:
            return text, False
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.measure(text[:middle].rstrip() + ELLIPSIS) <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + ELLIPSIS, True

    def _render(self) -> None:
        if self._destroyed:
            return
        before = self._lines
        elided = False
        if self._wrap > 0:
            lines = self._wrapped()
            if self._max_lines > 0 and len(lines) > self._max_lines:
                lines = lines[:self._max_lines]
                lines[-1], _ = self._cut(lines[-1] + ELLIPSIS, self._wrap)
                elided = True
        else:
            lines = self._text.split("\n")
            if self._clip > 0:
                lines = [self._cut(line, self._clip)[0] for line in lines]
                elided = any(line.endswith(ELLIPSIS) and not raw.endswith(ELLIPSIS)
                             for line, raw in zip(lines, self._text.split("\n")))
        self._lines, self.elided = lines, elided
        self.looks.config(self.item, text="\n".join(lines))
        if lines != before and self.on_change is not None:
            self.on_change()

    def _variable_changed(self, *_args):
        if not self._destroyed:
            self.configure(text=self._variable.get())

    def _font_changed(self):
        self._widths.clear()
        try:
            if self.canvas.winfo_exists():
                self._render()
                if self.on_change is not None:
                    self.on_change()
        except tk.TclError:
            pass

    # Label-like API
    def configure(self, cnf=None, **options):
        if cnf:
            options.update(cnf)
        render = False
        if "text" in options:
            text = str(options.pop("text"))
            render = text != self._text
            self._text = text
        if "tone" in options:
            self._tone = options.pop("tone")
            options["fill"] = ui_theme.TOKENS[self._tone]
        for name in ("fill", "foreground", "fg"):
            if name in options:
                colour = options.pop(name)
                self._fill = ui_theme.TOKENS.get(colour, colour) if not str(colour).startswith("#") else colour
                self.looks.config(self.item, fill=self._fill)
        if "max_lines" in options:
            self._max_lines = int(options.pop("max_lines"))
            render = True
        if options:
            raise tk.TclError("unknown option(s): " + ", ".join(sorted(options)))
        if render:
            self._render()

    config = configure

    def cget(self, option):
        if option == "text":
            return self._text
        if option in ("fill", "foreground", "fg"):
            return self._fill
        if option == "tone":
            return self._tone
        raise tk.TclError(f"unknown option {option!r}")

    __getitem__ = cget

    def bbox(self):
        return self.canvas.bbox(self.item) or None

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._trace is not None:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass
            self._trace = None
        self._unfont()
        try:
            self.canvas.delete(self.item)
        except tk.TclError:
            pass


# -- buttons ---------------------------------------------------------------------------------

class CanvasButton(CanvasControl):
    """A kit button drawn on a canvas (Button [design reference]; N21): ``secondary``, ``primary`` or ``quiet``.

    30 design px tall; it hugs its label (+ 13 px each side; quiet: the label alone) or
    takes ``width`` design px with the label centred. ``plate``: ``"opaque"`` (corners
    precomposed over the canvas's surface, for card canvases and strips), ``"rgba"``
    (transparent outside) or ``"binary"`` (binary alpha; over the backdrop, PR5). The
    owner positions it with :meth:`place`; until then it is hidden.
    """

    def __init__(self, canvas, *, text, command=None, kind="secondary", scale=None, plate="opaque", width=None,
                 state="normal", surface=None):
        if kind not in FACES:
            raise ValueError(f"unknown button kind {kind!r}")
        if plate not in ("opaque", "rgba", "binary"):
            raise ValueError(f"unknown plate {plate!r}")
        scale = ui_scale(canvas) if scale is None else scale
        super().__init__(canvas, command=command, scale=scale)
        self.master = canvas
        self.kind, self.plate = kind, plate
        self.group = None             # the FocusGroup that reaches it from the keyboard
        self.on_resize = None         # the owner lays out again when the hug width changes
        self._serial = next(_SERIAL)
        self._text = str(text)
        self._width = None if width is None else int(width)
        self._state = "disabled" if str(state) == "disabled" else "normal"
        self._font = fonts.font(canvas, "body")
        self._radius = max(1, round(4 * scale))
        self._height = px(scale, 30)
        self._dip_px = max(1, px(scale, 1))
        self._box = None              # (x0, y0, x1, y1) as placed
        self._dipped = False
        self._hooks = {"<ButtonPress-1>": [], "<ButtonRelease-1>": []}
        self._leave_job = None
        self._surface = surface or surface_of(canvas)
        self.looks = _Looks(canvas)
        t = ui_theme.TOKENS
        colours, label = FACES[kind]
        rest, hover, pressed = (t[name] if name else None for name in colours)
        self._label_colour = t[label] if label else None
        tags = (self.tag,)
        if kind == "quiet":
            # The pointer's hit area: the button's box in the surface colour (nothing shows).
            self._fills = (canvas.create_rectangle(0, 0, 0, 0, fill=self._surface, width=0, state="hidden",
                                                   tags=tags),)
            self._corners = ()
        else:
            self._fills = tuple(canvas.create_rectangle(0, 0, 0, 0, fill=rest, width=0, state="hidden", tags=tags)
                                for _ in range(2))
            self._corners = tuple(canvas.create_image(0, 0, anchor="nw", state="hidden", tags=tags)
                                  for _ in CORNERS)
        self.item = canvas.create_text(0, 0, text=self._text, font=self._font, anchor="center", state="hidden",
                                       fill=self._label_colour or rest, tags=tags)
        self.ramp = ColorRamp(canvas, self._paint, rest, hover, pressed=pressed,
                              images=None if kind == "quiet" else self._faces)
        _register(canvas, self)
        self._connect()
        self._draw()

    # -- faces ------------------------------------------------------------------------------
    def _faces(self, colour):
        """The four corners in ``colour`` (cached; the ramp pre-warms its steps)."""
        surface = self._surface if self.plate == "opaque" else None
        root = self.canvas.winfo_toplevel()
        return tuple(art.corner_face(root, self._radius, colour, which, surface=surface,
                                     binary=self.plate == "binary") for which in CORNERS)

    def _paint(self, colour):
        """The ramp's colour: the plate (fills and pre-warmed corner faces), or a quiet label."""
        if self._destroyed:
            return
        if self.kind == "quiet":
            self.looks.config(self.item, fill=colour)
            return
        for item in self._fills:
            self.looks.config(item, fill=colour)
        faces = self.ramp.image(colour) if self.ramp is not None else None
        if faces is not None:
            for item, image in zip(self._corners, faces):
                self.looks.config(item, image=image)

    def _draw(self):
        if self._destroyed or not hasattr(self, "ramp"):
            return
        shown = self._shown and self._box is not None
        state = "normal" if shown else "hidden"
        for item in (*self._fills, *self._corners, self.item):
            self.looks.config(item, state=state)
        t = ui_theme.TOKENS
        if not self.enabled():
            # Disabled: no motion, the kit's Disabled face at once (fill field, label dim).
            if self.ramp.active:
                self.ramp.animator.stop(self.ramp)
            self.ramp.value = 0.0
            if self.kind == "quiet":
                self.looks.config(self.item, fill=t["dim"])
            else:
                for item in self._fills:
                    self.looks.config(item, fill=t["field"])
                for item, image in zip(self._corners, self._faces(t["field"])):
                    self.looks.config(item, image=image)
                self.looks.config(self.item, fill=t["dim"])
            return
        if self._label_colour is not None:
            self.looks.config(self.item, fill=self._label_colour)
        if not self.ramp.active:
            self._paint(self.ramp.colour_at(self.ramp.value))

    # -- layout -----------------------------------------------------------------------------
    def winfo_reqwidth(self) -> int:
        if self._width is not None:
            return px(self.scale, self._width)
        # Its label's width as Figma's hug frame counts it (Inter's own widths, not GDI's hinted ones).
        label = fonts.hug_width(self.canvas, "body", self._text)
        return label if self.kind == "quiet" else label + 2 * px(self.scale, 13)

    reqwidth = winfo_reqwidth

    def winfo_reqheight(self) -> int:
        return self._height

    def place(self, x, y, width=None) -> None:
        """Lay the button out with its top-left corner at canvas (``x``, ``y``)."""
        width = self.winfo_reqwidth() if width is None else int(width)
        box = (int(x), int(y), int(x) + width, int(y) + self._height)
        if box != self._box:
            self._box = box
            self._position()
        self._draw()

    def _position(self):
        x0, y0, x1, y1 = self._box
        dip = self._dip_px if self._dipped else 0
        y0, y1 = y0 + dip, y1 + dip
        r, looks = self._radius, self.looks
        if self.kind == "quiet":
            looks.coords(self._fills[0], x0, y0, x1, y1)
        else:
            looks.coords(self._fills[0], x0 + r, y0, x1 - r, y1)
            looks.coords(self._fills[1], x0, y0 + r, x1, y1 - r)
            for item, (x, y) in zip(self._corners, ((x0, y0), (x1 - r, y0), (x0, y1 - r), (x1 - r, y1 - r))):
                looks.coords(item, x, y)
        looks.coords(self.item, (x0 + x1) // 2, (y0 + y1) // 2)

    def set_visible(self, visible: bool) -> None:
        self._shown = bool(visible)
        if not self._shown:
            self._drop()
        self._draw()

    def hide(self) -> None:
        self.set_visible(False)

    # -- pointer ----------------------------------------------------------------------------
    def _drop(self):
        """Forget hover and press (hidden or disabled under the pointer)."""
        self._cancel_leave()
        if self._hover:
            self._set_cursor(False)
        self._hover = self._pressed = False
        self._set_dip(False)

    _drop_pointer = _drop

    def _set_dip(self, dipped):
        if dipped != self._dipped:
            self._dipped = dipped
            if self._box is not None:
                self._position()

    def _cancel_leave(self):
        if self._leave_job is not None:
            try:
                self.canvas.after_cancel(self._leave_job)
            except tk.TclError:
                pass
            self._leave_job = None

    def _on_enter(self, _event=None):
        self._cancel_leave()
        if self._hover or not self._interactive():
            return
        self._hover = True
        self._set_cursor(True)
        self.ramp.enter()

    def _on_leave(self, _event=None):
        # Moving between the button's own items leaves one and enters the next: settle at idle.
        if self._leave_job is None and self._hover:
            try:
                self._leave_job = self.canvas.after_idle(self._settle)
            except tk.TclError:
                self._leave_job = None

    def _settle(self):
        self._leave_job = None
        try:
            current = self.canvas.find_withtag("current")
            if current and self.tag in self.canvas.gettags(current[0]):
                return
        except tk.TclError:
            return
        self._left()

    def _left(self):
        if not self._hover:
            return
        self._hover = False
        self._set_cursor(False)
        self._pressed = False
        self._set_dip(False)
        if self.enabled():
            self.ramp.leave()

    def _run_hooks(self, sequence, event):
        for func in list(self._hooks[sequence]):
            func(event)

    def _on_press(self, event=None):
        self._run_hooks("<ButtonPress-1>", event)
        if not self._interactive():
            return
        self._pressed = True
        self._set_dip(True)
        self.ramp.press()

    def _on_release(self, event=None):
        self._run_hooks("<ButtonRelease-1>", event)
        was, self._pressed = self._pressed, False
        self._set_dip(False)
        if not was or not self._interactive():
            return
        if event is not None and self._inside(event):
            self.ramp.release()
            self.invoke()
        else:
            self._left()

    def _inside(self, event) -> bool:
        if self._box is None:
            return False
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x0, y0, x1, y1 = self._box
        return x0 <= x < x1 and y0 <= y < y1 + self._dip_px

    def set_focus(self, focused: bool) -> None:
        self._focused = bool(focused)   # nothing is drawn on focus (N4)

    # -- the ttk.Button surface (N21) -------------------------------------------------------
    def bind(self, sequence, func=None, add=None):
        if sequence not in self._hooks:
            raise tk.TclError(f"a canvas button only takes {', '.join(self._hooks)} bindings")
        if func is None:
            return self._hooks[sequence][:]
        if not add:
            self._hooks[sequence].clear()
        self._hooks[sequence].append(func)
        return func

    def unbind(self, sequence, funcid=None):
        hooks = self._hooks.get(sequence, [])
        if funcid is None:
            hooks.clear()
        elif funcid in hooks:
            hooks.remove(funcid)

    def configure(self, cnf=None, **options):
        if cnf:
            options.update(cnf)
        if "width" in options:
            width = options.pop("width")
            width = None if width in (None, "", 0) else int(width)
            if width != self._width:
                self._width = width
                self._resized()
        if "state" in options:
            options["state"] = "disabled" if str(options["state"]) == "disabled" else "normal"
        super().configure(**options)

    config = configure

    def cget(self, option):
        if option == "width":
            return self._width or 0
        return super().cget(option)

    __getitem__ = cget

    def keys(self):
        return ["command", "state", "text", "width"]

    def state(self, spec=None):
        """ttk-style: the current flags, or set ``disabled``/``!disabled`` and return the previous ones."""
        current = ("disabled",) if self._state == "disabled" else ()
        if spec is None:
            return current
        flags = [spec] if isinstance(spec, str) else list(spec)
        for flag in flags:
            if flag == "disabled":
                self.configure(state="disabled")
            elif flag == "!disabled":
                self.configure(state="normal")
        return current

    def instate(self, spec, callback=None, *args, **kw):
        flags = [spec] if isinstance(spec, str) else list(spec)
        disabled = self._state == "disabled"
        result = all((not disabled) if flag == "!disabled" else disabled if flag == "disabled" else False
                     for flag in flags)
        if result and callback is not None:
            return callback(*args, **kw)
        return result

    def _text_value(self) -> str:
        return self._text

    def _set_text(self, text: str) -> None:
        if text == self._text:
            return
        before = self.winfo_reqwidth()
        self._text = text
        self.looks.config(self.item, text=text)
        if self.winfo_reqwidth() != before:
            self._resized()

    def _resized(self):
        if self.on_resize is not None:
            self.on_resize()

    def focus_set(self) -> None:
        if self.group is not None:
            self.group.focus(self)

    focus = focus_set

    # geometry from the box and the canvas (a withdrawn window answers too)
    def winfo_ismapped(self) -> bool:
        try:
            return bool(self._shown and self._box is not None and not self._destroyed
                        and self.canvas.winfo_ismapped())
        except tk.TclError:
            return False

    def winfo_viewable(self) -> bool:
        try:
            return self.winfo_ismapped() and bool(self.canvas.winfo_viewable())
        except tk.TclError:
            return False

    def winfo_rootx(self) -> int:
        x0 = self._box[0] if self._box else 0
        return self.canvas.winfo_rootx() + x0 - int(self.canvas.canvasx(0))

    def winfo_rooty(self) -> int:
        y0 = self._box[1] if self._box else 0
        return self.canvas.winfo_rooty() + y0 - int(self.canvas.canvasy(0))

    def winfo_x(self) -> int:
        return self._box[0] if self._box else 0

    def winfo_y(self) -> int:
        return self._box[1] if self._box else 0

    def winfo_width(self) -> int:
        return self._box[2] - self._box[0] if self._box else 1

    def winfo_height(self) -> int:
        return self._box[3] - self._box[1] if self._box else 1

    def winfo_toplevel(self):
        return self.canvas.winfo_toplevel()

    def winfo_class(self) -> str:
        return "KitCanvasButton"

    def box(self):
        """(x0, y0, x1, y1) on its canvas as laid out, or None before."""
        return self._box

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._cancel_leave()
        if self.ramp is not None and self.ramp.active:
            self.ramp.animator.stop(self.ramp)
        _unregister(self.canvas, self)
        super().destroy()


class KitButton(tk.Canvas):
    """A :class:`CanvasButton` as a widget of its own (one canvas) for places without a card canvas.

    Replay's Browse/Refresh and the curve lane's Rotation Reset (``kind="quiet"``).
    It is ``px(30)`` tall and as wide as the button (``width`` design px or its hug
    width); grid/pack/place and ``winfo_*`` are native. It takes focus (nothing drawn)
    and Space/Return invoke it. ``configure``/``cget`` of ``text``, ``state``,
    ``command`` and ``width`` (design px, 0 = hug) go to the button, everything else to
    the canvas.
    """

    _BUTTON_OPTIONS = ("text", "state", "command", "width")

    def __init__(self, parent, *, text, command=None, kind="secondary", width=None, state="normal", surface=None):
        s = ui_scale(parent)
        background = surface or surface_of(parent)
        super().__init__(parent, background=background, highlightthickness=0, borderwidth=0, takefocus=1,
                         height=px(s, 30), width=1)
        self.button = CanvasButton(self, text=text, command=command, kind=kind, scale=s, width=width, state=state,
                                   surface=background)
        self._serial = self.button._serial
        self.button.on_resize = self._fit
        self._size = None
        self.bind("<Configure>", self._configured, add="+")
        for key in ("<space>", "<Return>", "<KP_Enter>"):
            self.bind(key, self._key, add="+")
        self._fit()

    def _key(self, _event=None):
        self.invoke()
        return "break"

    def _fit(self):
        width = self.button.winfo_reqwidth()
        if int(tk.Canvas.cget(self, "width")) != width:
            tk.Canvas.configure(self, width=width)
        self.button.place(0, 0, max(width, self.winfo_width()) if self.winfo_width() > 1 else width)

    def _configured(self, event):
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self.button.place(0, 0, max(1, event.width))

    def invoke(self):
        return self.button.invoke()

    def configure(self, cnf=None, **options):
        if isinstance(cnf, str):
            return self.cget(cnf) if cnf in self._BUTTON_OPTIONS else tk.Canvas.configure(self, cnf)
        if cnf:
            options.update(cnf)
        forwarded = {key: options.pop(key) for key in self._BUTTON_OPTIONS if key in options}
        if forwarded:
            self.button.configure(**forwarded)
            if "width" in forwarded or "text" in forwarded:
                self._fit()
        if options:
            return tk.Canvas.configure(self, **options)
        return None

    config = configure

    def cget(self, key):
        if key in self._BUTTON_OPTIONS:
            return self.button.cget(key)
        return tk.Canvas.cget(self, key)

    __getitem__ = cget

    def __setitem__(self, key, value):
        self.configure({key: value})

    def state(self, spec=None):
        return self.button.state(spec)

    def instate(self, spec, callback=None, *args, **kw):
        return self.button.instate(spec, callback, *args, **kw)


# -- info icons and bubbles ------------------------------------------------------------------

class CanvasInfoIcon(CanvasControl):
    """The kit Info icon ([design reference]) drawn on a canvas: ``dim``, ``accent`` under the pointer.

    The pointer ramps it to ``accent`` (120 ms in, 180 ms out, pre-warmed step images)
    and opens its bubble after 150 ms; leaving closes the bubble at once. Keyboard focus
    (its :class:`FocusGroup`) opens the bubble at once and the icon stays ``dim`` (N4).
    ``surface``: the colour it is precomposed over (a card canvas), None over the
    backdrop (``binary`` alpha there by default, PR5). ``text`` may change at any time.
    """

    cursor = "question_arrow"

    def __init__(self, canvas, text, *, page=None, scale=None, surface="auto", binary=None):
        scale = ui_scale(canvas) if scale is None else scale
        super().__init__(canvas, command=None, scale=scale)
        self.master = canvas
        self.text = str(text)
        self.page = page
        self.group = None
        self._serial = next(_SERIAL)
        self._surface = surface_of(canvas) if surface == "auto" else surface
        self._binary = (self._surface is None) if binary is None else bool(binary)
        self._job = None
        self._bubble = None
        self._box = None
        self.looks = _Looks(canvas)
        t = ui_theme.TOKENS
        self._size = max(1, int(13 * scale + .5))
        self.item = canvas.create_image(0, 0, anchor="nw", state="hidden", tags=(self.tag,))
        self.ramp = ColorRamp(canvas, self._paint, t["dim"], t["accent"], images=self._face)
        self._paint(t["dim"])
        _register(canvas, self)
        self._connect()
        canvas.bind("<Destroy>", lambda e: self._hide_bubble() if e.widget is canvas else None, add="+")

    def _face(self, colour):
        return art.info_icon_rgba(self.canvas.winfo_toplevel(), self.scale, colour, surface=self._surface,
                                  binary=self._binary)

    def _paint(self, colour):
        if not self._destroyed:
            image = self.ramp.image(colour) if getattr(self, "ramp", None) is not None else self._face(colour)
            if image is not None:
                self.looks.config(self.item, image=image)

    @property
    def shown(self) -> bool:
        return self._bubble is not None

    def winfo_reqwidth(self) -> int:
        return self._size

    reqwidth = winfo_reqwidth

    def winfo_reqheight(self) -> int:
        return self._size

    def place(self, x, y, width=None) -> None:
        self._box = (int(x), int(y), int(x) + self._size, int(y) + self._size)
        self.looks.coords(self.item, int(x), int(y))
        self._draw()

    def set_visible(self, visible: bool) -> None:
        self._shown = bool(visible)
        if not self._shown:
            self._left()
        self._draw()

    def hide(self) -> None:
        self.set_visible(False)

    def _draw(self):
        if not self._destroyed:
            self.looks.config(self.item, state="normal" if self._shown and self._box is not None else "hidden")

    def configure(self, cnf=None, **options):
        if cnf:
            options.update(cnf)
        if "text" in options:
            self.text = str(options.pop("text"))
            if self._bubble is not None:
                self._hide_bubble()
                self._show_bubble()
        super().configure(**options)

    config = configure

    def cget(self, option):
        if option == "text":
            return self.text
        return super().cget(option)

    def _on_enter(self, _event=None):
        if not self._interactive():
            return
        if not self._hover:
            self._hover = True
            self._set_cursor(True)
            self.ramp.enter()
        if self._job is None and self._bubble is None:
            try:
                self._job = self.canvas.after(BUBBLE_DELAY_MS, self._show_bubble)
            except tk.TclError:
                self._job = None

    def _on_leave(self, _event=None):
        self._left()

    def _left(self):
        self._hide_bubble()
        if self._hover:
            self._hover = False
            self._set_cursor(False)
            self.ramp.leave()

    def _on_press(self, _event=None):
        pass

    def _on_release(self, _event=None):
        pass

    def invoke(self):
        if self._interactive():
            self._show_bubble()
        return ""

    def set_focus(self, focused: bool) -> None:
        self._focused = bool(focused)
        if focused:
            self._show_bubble()
        elif not self._hover:
            self._hide_bubble()

    def _show_bubble(self):
        self._job = None
        if self._bubble is not None or self._destroyed or self._box is None:
            return
        page = self.page or page_of(self.canvas)
        if page is not None:
            self._bubble = page.open_bubble((self.canvas, self._box), self.text)
            self._bubble_page = page

    def _hide_bubble(self):
        if self._job is not None:
            try:
                self.canvas.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._bubble is not None:
            self._bubble_page.close_bubble(self._bubble)
            self._bubble = None

    def winfo_ismapped(self) -> bool:
        try:
            return bool(self._shown and self._box is not None and self.canvas.winfo_ismapped())
        except tk.TclError:
            return False

    def box(self):
        return self._box

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._hide_bubble()
        if self.ramp.active:
            self.ramp.animator.stop(self.ramp)
        _unregister(self.canvas, self)
        super().destroy()


# -- keyboard --------------------------------------------------------------------------------

class FocusGroup(CanvasFocus):
    """Keyboard access to a run of canvas controls: a 1 x 1 frame *placed* on their canvas.

    The same Tab/Shift-Tab/Return/space logic as :class:`.canvas_controls.CanvasFocus`,
    but the stand-in is placed (never a canvas window item, PERF-NOTES R11) and nothing
    is drawn for focus (N4). ``parent`` hosts the stand-in when it is not the controls'
    canvas (band controls: the page's sheet, in a gap outside its region). Escape closes
    an open info bubble.
    """

    def __init__(self, page, controls=(), *, canvas, parent=None):
        # Not CanvasFocus.__init__: that one makes the stand-in a canvas window item.
        self.page = page
        self.canvas = canvas
        self.controls = []
        self.index = None
        self._wanted = None
        self._at = None
        host = canvas if parent is None else parent
        self.proxy = tk.Frame(host, width=1, height=1, background=surface_of(host), highlightthickness=0,
                              borderwidth=0, takefocus=1)
        self.proxy._kit_group = self
        self.window = None
        self.proxy.bind("<FocusIn>", self._focus_in)
        self.proxy.bind("<FocusOut>", lambda _e: self._show(None))
        self.proxy.bind("<Tab>", lambda _e: self._move(1))
        self.proxy.bind("<Shift-Tab>", lambda _e: self._move(-1))
        self.proxy.bind("<Escape>", self._escape)
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.proxy.bind(key, self._activate)
        self._top = canvas.winfo_toplevel()
        _track_tab_direction(self._top)
        for control in controls:
            self.add(control)

    def add(self, control) -> None:
        self.controls.append(control)
        control.group = self

    def place(self, x, y) -> None:
        at = (int(x), int(y))
        if at != self._at:
            self._at = at
            self.proxy.place(x=at[0], y=at[1], width=1, height=1)

    def hide(self) -> None:
        if self._at is not None:
            self._at = None
            self.proxy.place_forget()

    def focusable(self) -> bool:
        return any(control.focusable() for control in self.controls)

    def first(self):
        return next((control for control in self.controls if control.focusable()), None)

    def focus(self, control) -> None:
        """Give the keyboard to ``control`` (one of this group's)."""
        if control not in self.controls:
            return
        self._wanted = control
        self.index = self.controls.index(control)
        self._show(self.index)
        self.proxy.focus_set()   # its <FocusIn> keeps this control (Tab order would pick the first)

    def _focus_in(self, _event=None):
        wanted, self._wanted = self._wanted, None
        if wanted is not None and wanted.focusable():
            self.index = self.controls.index(wanted)
            self._show(self.index)
            return
        super()._focus_in(_event)

    def _escape(self, _event=None):
        control = self.focused()
        if control is not None and hasattr(control, "_hide_bubble"):
            control._hide_bubble()
        return "break"


# -- rows --------------------------------------------------------------------------------------

class _Cell:
    __slots__ = ("kind", "obj", "width", "height", "info", "wrap", "placed", "seen")

    def __init__(self, kind, obj=None, width=None, height=None):
        self.kind, self.obj, self.width, self.height = kind, obj, width, height
        self.info = None
        self.wrap = False
        self.placed = None
        self.seen = None


class RowLayout:
    """Cells left to right on one canvas, vertically centred on a row (``KitRow``'s core).

    Cells: ``label`` (text item; ``width`` None hugs, design px is a fixed cell whose
    text is cut with "…" only when it cannot fit; ``wrap=True`` fills the rest and
    wraps), ``widget`` (a real widget whose master is this canvas, *placed* on it;
    ``width`` None hugs its requested width, design px, or ``"fill"``: fills share the
    rest equally), ``button``/``info`` (canvas controls; one :class:`FocusGroup` per run
    of them, a widget ends a run), ``gap`` (design px) and ``push`` (flexible space,
    used only while no cell fills). ``changed`` is called when a cell's size changed.
    """

    def __init__(self, canvas, *, scale, page=None, height=30, surface=None, plate="opaque", changed=None,
                 register=None, proxy_parent=None, label_factory=None):
        self.canvas = canvas
        self.scale = scale
        self.page = page
        self.height = height
        self.surface = surface_of(canvas) if surface is None else surface
        self.plate = plate
        self.changed = changed
        self.register = register
        self.proxy_parent = proxy_parent
        self.label_factory = label_factory or KitLabel
        self.cells: list[_Cell] = []
        self.groups: list[FocusGroup] = []
        self._run = None
        self.row_height = px(scale, height)
        self.extent = None   # (x0, y0, x1, y1) of the last layout

    def _notify(self, *_args):
        if self.changed is not None:
            self.changed()

    # building
    def label(self, text="", *, textvariable=None, width=None, tone="muted", info=None, wrap=False):
        label = self.label_factory(self.canvas, text, textvariable=textvariable, tone=tone)
        label.on_change = self._notify
        cell = _Cell("label", label, width)
        cell.wrap = bool(wrap)
        self.cells.append(cell)
        if info:
            cell.info = self._control(CanvasInfoIcon(self.canvas, info, page=self.page, scale=self.scale,
                                                     surface=self._icon_surface()))
        self._notify()
        return label

    def widget(self, widget, *, width=None, height=None):
        if str(widget.master) != str(self.canvas):
            raise ValueError(f"{widget} must be created with master row.host ({self.canvas}), not {widget.master}")
        if not (width is None or width == "fill" or isinstance(width, (int, float))):
            raise ValueError(f"width must be None, 'fill' or design px, not {width!r}")
        cell = _Cell("widget", widget, width, height)
        self.cells.append(cell)
        self._run = None
        widget.bind("<Configure>", lambda e, c=cell: self._widget_configured(e, c), add="+")
        if self.register is not None:
            self.register(widget)
        self._notify()
        return widget

    def button(self, text, command=None, *, kind="secondary", state="normal", width=None):
        button = CanvasButton(self.canvas, text=text, command=command, kind=kind, scale=self.scale,
                              plate=self.plate, width=width, state=state, surface=self.surface)
        button.on_resize = self._notify
        self.cells.append(_Cell("control", self._control(button)))
        self._notify()
        return button

    def info(self, text):
        icon = CanvasInfoIcon(self.canvas, text, page=self.page, scale=self.scale, surface=self._icon_surface())
        self.cells.append(_Cell("control", self._control(icon)))
        self._notify()
        return icon

    def gap(self, design_px):
        self.cells.append(_Cell("gap", width=design_px))
        self._notify()

    def push(self):
        self.cells.append(_Cell("push"))
        self._notify()

    def custom(self, obj):
        """A cell that lays itself out: ``winfo_reqwidth()``, ``winfo_reqheight()``, ``place(x, y, w)``, ``hide()``."""
        self.cells.append(_Cell("custom", obj))
        self._run = None
        self._notify()
        return obj

    def _icon_surface(self):
        return None if self.plate != "opaque" else self.surface

    def _control(self, control):
        if self._run is None:
            self._run = FocusGroup(self.page, canvas=self.canvas, parent=self.proxy_parent)
            self.groups.append(self._run)
        self._run.add(control)
        return control

    def _widget_configured(self, event, cell):
        if event.widget is not cell.obj:
            return
        size = (event.width, event.height)
        if cell.seen is not None and size != cell.seen:
            cell.seen = size
            self._notify()
        cell.seen = size

    def controls(self):
        found = []
        for cell in self.cells:
            if cell.kind == "control":
                found.append(cell.obj)
            if cell.info is not None:
                found.append(cell.info)
        return found

    def widgets(self):
        return [cell.obj for cell in self.cells if cell.kind == "widget"]

    # layout
    def _label_width(self, cell):
        label = cell.obj
        info = (px(self.scale, 5) + cell.info.winfo_reqwidth()) if cell.info is not None else 0
        if cell.width is not None:
            return px(self.scale, cell.width)
        return label.natural_width() + info

    def layout(self, x0, y, width, *, proxy=None) -> int:
        """Place every cell from canvas (``x0``, ``y``) across ``width`` px; returns the row's height."""
        s, cells = self.scale, self.cells
        widths, fills, pushes = [0] * len(cells), [], []
        for index, cell in enumerate(cells):
            if cell.kind == "label":
                if cell.wrap:
                    fills.append(index)
                else:
                    widths[index] = self._label_width(cell)
            elif cell.kind == "widget":
                if cell.width == "fill":
                    fills.append(index)
                elif cell.width is None:
                    widths[index] = cell.obj.winfo_reqwidth()
                else:
                    widths[index] = px(s, cell.width)
            elif cell.kind in ("control", "custom"):
                widths[index] = cell.obj.winfo_reqwidth()
            elif cell.kind == "gap":
                widths[index] = px(s, cell.width)
            elif cell.kind == "push":
                pushes.append(index)
        rest = max(0, width - sum(widths))
        shared = fills or pushes
        for order, index in enumerate(shared):
            part = rest // len(shared)
            widths[index] = part + (rest - part * len(shared) if order == len(shared) - 1 else 0)
        height = self.row_height
        for index, cell in enumerate(cells):   # wrapped labels may make the row taller
            if cell.kind == "label" and cell.wrap:
                info = (px(s, 5) + cell.info.winfo_reqwidth()) if cell.info is not None else 0
                cell.obj.set_wrap(max(1, widths[index] - info if cell.info is not None else widths[index]))
                height = max(height, cell.obj.size()[1])
        middle = y + self.row_height // 2
        x = x0
        for index, cell in enumerate(cells):
            w = widths[index]
            if cell.kind == "label":
                self._place_label(cell, x, y, middle, w)
            elif cell.kind == "widget":
                self._place_widget(cell, x, middle, w)
            elif cell.kind == "control":
                control = cell.obj
                control.place(x, middle - control.winfo_reqheight() // 2, w)
                if hasattr(control, "set_visible"):
                    control.set_visible(True)
            elif cell.kind == "custom":
                cell.obj.place(x, middle - cell.obj.winfo_reqheight() // 2, w)
            x += w
        for group in self.groups:
            if proxy is not None:
                group.place(*proxy)
        self.extent = (x0, y, x0 + width, y + height)
        return height

    def _place_label(self, cell, x, y, middle, width):
        label, s = cell.obj, self.scale
        info_w = (px(s, 5) + cell.info.winfo_reqwidth()) if cell.info is not None else 0
        if cell.wrap:
            pad = max(0, (px(s, 16) - label.linespace()) // 2) if len(label._lines) > 1 else 0
            top = middle - label.linespace() // 2 if len(label._lines) == 1 else y + pad
            label.place(x, top)
            end_x, end_y = x + label.last_line_width(), top + (len(label._lines) - 1) * label.linespace()
        else:
            label.set_clip(width - info_w if cell.width is not None else 0)
            label.place(x, middle, anchor="w")
            end_x, end_y = x + label.size()[0], middle - label.linespace() // 2
        if cell.info is not None:
            icon = cell.info
            icon.place(end_x + px(s, 5), end_y + (label.linespace() - icon.winfo_reqheight()) // 2)
            icon.set_visible(True)

    def _place_widget(self, cell, x, middle, width):
        widget = cell.obj
        height = px(self.scale, cell.height) if cell.height is not None else widget.winfo_reqheight()
        y = middle - height // 2
        place = (x, y, None if cell.width is None else width, None if cell.height is None else height)
        if place != cell.placed:
            cell.placed = place
            options = {"x": x, "y": y}
            if place[2] is not None:
                options["width"] = place[2]
            if place[3] is not None:
                options["height"] = place[3]
            widget.place_forget()
            widget.place(**options)

    def hide(self) -> None:
        for cell in self.cells:
            if cell.kind == "label":
                cell.obj.hide()
            elif cell.kind == "widget":
                if cell.placed is not None:
                    cell.obj.place_forget()
                    cell.placed = None
            elif cell.kind == "control":
                cell.obj.set_visible(False)
            elif cell.kind == "custom":
                cell.obj.hide()
            if cell.info is not None:
                cell.info.set_visible(False)
        for group in self.groups:
            group.hide()
        self.extent = None


class KitStrip(tk.Canvas):
    """A standalone row canvas (bg ``panel``) with the row building API, for frame-hosted composites.

    The Shot Playback controls row and the Playback options button row: buttons are
    items on it (N21), widgets are placed on it (their master must be the strip). It
    lays itself out when its size changes (and after building).
    """

    def __init__(self, parent, *, height=30, surface=None):
        s = ui_scale(parent)
        background = surface or surface_of(parent)
        super().__init__(parent, background=background, highlightthickness=0, borderwidth=0, takefocus=0,
                         height=px(s, height), width=1)
        self.scale = s
        self.row = RowLayout(self, scale=s, height=height, surface=background, changed=self._schedule)
        self._job = None
        self._size = None
        self.bind("<Configure>", self._configured, add="+")
        self.bind("<Destroy>", lambda e: self._cancel() if e.widget is self else None, add="+")

    def label(self, text="", *, textvariable=None, width=None, tone="muted", info=None, wrap=False):
        return self.row.label(text, textvariable=textvariable, width=width, tone=tone, info=info, wrap=wrap)

    def widget(self, widget, *, width=None, height=None):
        return self.row.widget(widget, width=width, height=height)

    def button(self, text, command=None, *, kind="secondary", state="normal", width=None):
        return self.row.button(text, command, kind=kind, state=state, width=width)

    def info(self, text):
        return self.row.info(text)

    def gap(self, design_px):
        self.row.gap(design_px)

    def push(self):
        self.row.push()

    def controls(self):
        return self.row.controls()

    def _configured(self, event):
        size = (event.width, event.height)
        if size != self._size:
            self._size = size
            self.layout_now()

    def _schedule(self):
        if self._job is None:
            try:
                self._job = self.after_idle(self.layout_now)
            except tk.TclError:
                self._job = None

    def _cancel(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def layout_now(self):
        self._cancel()
        width = self.winfo_width()
        if width <= 1:
            width = int(tk.Canvas.cget(self, "width"))
        height = self.row.layout(0, 0, max(1, width), proxy=(0, 0))
        if int(tk.Canvas.cget(self, "height")) != height:
            tk.Canvas.configure(self, height=height)


# -- the search field ----------------------------------------------------------------------------

class SearchField:
    """The kit search field (Controls' toolbar): its middle on the page's sheet, its ends in the strips.

    The middle (from the sheet's left edge to its width less two card radii, 30 px)
    is a small canvas in ``field`` holding a ``Kit.Flat.TEntry``; the magnifier
    (opaque over ``field``) and the placeholder sit over the entry, as on Replay. The
    rounded ends (radius 4, ``field``) are drawn into the page's plate strips. The
    placeholder shows while the variable is empty and the entry lacks focus; it never
    writes the variable. Without window regions the middle is a canvas window of its own.
    """

    RADIUS = 4

    def __init__(self, page, variable, *, placeholder="", width=None, scale=None):
        self.page = page
        self.scale = s = page.scale if scale is None else scale
        self.width = width
        self.visible = True
        self.rect = None              # page-canvas px of the whole field, ends included
        t = ui_theme.TOKENS
        sheet = page.sheet
        parent = sheet if sheet is not None else page.canvas
        self.canvas = tk.Canvas(parent, background=t["field"], highlightthickness=0, borderwidth=0, takefocus=0,
                                height=px(s, 30), width=1, cursor="xterm")
        self.window = None
        if sheet is None:
            self.window = page.canvas.create_window(0, 0, anchor="nw", window=self.canvas, state="hidden")
            retain_surface(self.canvas)
        self.entry = ttk.Entry(self.canvas, textvariable=variable, style="Kit.Flat.TEntry")
        self.entry.place(x=0, y=0, relwidth=1, relheight=1)
        image = art.magnifier_rgba(self.canvas.winfo_toplevel(), s, t["dim"], surface=t["field"])
        self.icon = tk.Canvas(self.canvas, width=image.width(), height=image.height(), background=t["field"],
                              highlightthickness=0, borderwidth=0, takefocus=0, cursor="xterm")
        self.icon.create_image(0, 0, anchor="nw", image=image)
        self.icon.image = image
        r = px(s, page.CARD_RADIUS)
        self.icon.place(in_=self.entry, x=px(s, 13) - r, rely=.5, anchor="w")
        self.icon.bind("<Button-1>", lambda _e: (self.entry.focus_set(), "break")[1])
        self.placeholder = kit_fields.placeholder(self.entry, variable, placeholder, x=26 - page.CARD_RADIUS)
        self._placed = None

    def winfo_reqwidth(self) -> int:
        """``width`` design px, or the page's main column (comment #3: as wide as the column below)."""
        if self.width is not None:
            return px(self.scale, self.width)
        return self.page.main_width or px(self.scale, 613)

    def winfo_reqheight(self) -> int:
        return px(self.scale, 30)

    def place(self, x, y, width) -> None:
        """Lay the field out at page (``x``, ``y``), ``width`` px wide (its rounded ends included)."""
        s, page = self.scale, self.page
        r = px(s, page.CARD_RADIUS)
        self.rect = (x, y, x + width, y + px(s, 30))
        place = (x, y, max(1, width - 2 * r))
        if place == self._placed:
            return
        self._placed = place
        if self.window is None:
            self.canvas.place(x=x, y=y, width=place[2], height=px(s, 30))   # on the sheet, which starts r in
        else:
            page.canvas.coords(self.window, x + r, y)
            page.canvas.itemconfigure(self.window, width=place[2], height=px(s, 30), state="normal")

    def hide(self) -> None:
        self.rect = None
        if self._placed is not None:
            self._placed = None
            if self.window is None:
                self.canvas.place_forget()
            else:
                self.page.canvas.itemconfigure(self.window, state="hidden")

    def middle(self):
        """The sheet rect of the middle (sheet px), or None while hidden."""
        if self.rect is None or self._placed is None:
            return None
        x, y, w = self._placed
        return (x, y, x + w, y + px(self.scale, 30))


# -- slider --------------------------------------------------------------------------------------

_SLIDER_CLASS = "DollyKitSlider"


class KitSlider(tk.Canvas):
    """The kit Slider ([design reference]): a 4 px track (radius 2), an ``accent`` fill and a 12 px white thumb.

    Any width, 12 design px tall; the thumb centre sits at ``x0 + 6 + f·(w − 12)``.
    Items are made once and moved (the thumb never animates). Click jumps, drag
    follows; Left/Down −1 %, Right/Up +1 % (Shift ±0.1 %), Prior/Next ±10 %, Home/End.
    ``command(str(value))`` runs on every user change (like ``ttk.Scale``), never on
    :meth:`set` or a variable write. ``format="compact"`` writes ``f"{v:.2f}"`` without
    trailing zeros. Its own bindings live on the class tag ``DollyKitSlider``, so a
    caller's ``bind("<ButtonPress-1>"/"<ButtonRelease-1>", …)`` never replaces them. It
    has no wheel binding: the page scrolls over it.
    """

    def __init__(self, parent, *, from_=0.0, to=1.0, variable=None, command=None, format=None, takefocus=1,
                 **options):
        s = ui_scale(parent)
        t = ui_theme.TOKENS
        options.setdefault("width", px(s, 200))
        super().__init__(parent, background=t["panel"], highlightthickness=0, borderwidth=0, takefocus=takefocus,
                         height=px(s, 12), **options)
        self.scale = s
        self._from, self._to = float(from_), float(to)
        self._command = command
        self._format = format
        self._variable = None
        self._trace = None
        self._writing = False
        self._disabled = False
        self._takefocus = takefocus
        self._value = self._from
        self._geometry = None
        self.looks = _Looks(self)
        self._r = max(1, px(s, 2))
        self._thumb_d = px(s, 12)
        self._track = (self.create_rectangle(0, 0, 0, 0, width=0),
                       *(self.create_image(0, 0, anchor="nw") for _ in CORNERS))
        self._fill = (self.create_rectangle(0, 0, 0, 0, width=0),
                      *(self.create_image(0, 0, anchor="nw") for _ in ("nw", "sw")))
        self._thumb = self.create_image(0, 0, anchor="center")
        top = self.winfo_toplevel()
        self.bindtags((str(self), _SLIDER_CLASS, str(top), "all"))
        _slider_bindings(self)
        self.bind("<Configure>", lambda _e: self._redraw(), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        if variable is not None:
            self._attach(variable)
        self._paint_faces()
        self._redraw()

    # value
    def fraction(self) -> float:
        span = self._to - self._from
        return 0.0 if span == 0 else max(0.0, min(1.0, (self._value - self._from) / span))

    def get(self) -> float:
        return self._value

    def set(self, value) -> None:
        """Set the value from code: the thumb moves and the variable follows; no command."""
        self._change(value, user=False)

    def _clamped(self, value):
        low, high = min(self._from, self._to), max(self._from, self._to)
        return max(low, min(high, float(value)))

    def _change(self, value, *, user):
        value = self._clamped(value)
        changed = value != self._value
        self._value = value
        self._redraw()
        if self._variable is not None:
            self._write(value)
        if user and self._command is not None:
            self._command(str(value))
        return changed

    def _write(self, value):
        text = f"{value:.2f}".rstrip("0").rstrip(".") if self._format == "compact" else value
        self._writing = True
        try:
            self._variable.set(text)
        finally:
            self._writing = False

    def _attach(self, variable):
        if self._trace is not None:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass
        self._variable = variable
        self._trace = variable.trace_add("write", self._variable_changed) if variable is not None else None
        if variable is not None:
            self._variable_changed()

    def _variable_changed(self, *_args):
        if self._writing:
            return
        try:
            value = float(self._variable.get())
        except (ValueError, tk.TclError):
            return
        self._value = self._clamped(value)
        self._redraw()

    # drawing
    def _paint_faces(self):
        t = ui_theme.TOKENS
        track, fill, thumb = ((t["edge_disabled"], t["accent_disabled"], t["text_disabled"]) if self._disabled
                              else (t["button"], t["accent"], t["text"]))
        root, r, panel = self.winfo_toplevel(), self._r, t["panel"]
        self.looks.config(self._track[0], fill=track)
        for item, which in zip(self._track[1:], CORNERS):
            self.looks.config(item, image=art.corner_face(root, r, track, which, surface=panel))
        self.looks.config(self._fill[0], fill=fill)
        for item, which in zip(self._fill[1:], ("nw", "sw")):
            self.looks.config(item, image=art.corner_face(root, r, fill, which, surface=panel))
        self.looks.config(self._thumb, image=art.dot_rgba(root, self._thumb_d, thumb))

    def thumb_x(self) -> float:
        width = self.winfo_width() if self.winfo_width() > 1 else int(tk.Canvas.cget(self, "width"))
        d = self._thumb_d
        return d / 2 + self.fraction() * max(0, width - d)

    def _redraw(self):
        s, r, d = self.scale, self._r, self._thumb_d
        width = self.winfo_width() if self.winfo_width() > 1 else int(tk.Canvas.cget(self, "width"))
        y0, y1 = px(s, 4), px(s, 4) + 2 * r
        x = int(round(self.thumb_x()))
        looks = self.looks
        looks.coords(self._track[0], r, y0, max(r, width - r), y1)
        for item, (cx, cy) in zip(self._track[1:], ((0, y0), (width - r, y0), (0, y1 - r), (width - r, y1 - r))):
            looks.coords(item, cx, cy)
        looks.coords(self._fill[0], r, y0, max(r, x), y1)
        looks.coords(self._fill[1], 0, y0)
        looks.coords(self._fill[2], 0, y1 - r)
        looks.coords(self._thumb, x, d // 2)

    # input (class bindings)
    def _value_at(self, x) -> float:
        width = self.winfo_width()
        d = self._thumb_d
        f = 0.0 if width <= d else (x - d / 2) / (width - d)
        return self._from + max(0.0, min(1.0, f)) * (self._to - self._from)

    def _pressed(self, event):
        if self._disabled:
            return
        try:
            self.focus_set()
        except tk.TclError:
            pass
        self._change(self._value_at(event.x), user=True)

    def _dragged(self, event):
        if not self._disabled:
            self._change(self._value_at(event.x), user=True)

    def _key(self, event):
        if self._disabled:
            return None
        span = self._to - self._from
        step = span / (1000 if event.state & 0x1 else 100)
        moves = {"Left": -step, "Down": -step, "Right": step, "Up": step, "Prior": span / 10, "Next": -span / 10}
        if event.keysym in moves:
            value = self._value + moves[event.keysym]
        elif event.keysym == "Home":
            value = self._from
        elif event.keysym == "End":
            value = self._to
        else:
            return None
        self._change(value, user=True)
        return "break"

    # ttk.Scale-like options and state
    def configure(self, cnf=None, **options):
        if isinstance(cnf, str):
            return self.cget(cnf)
        if cnf:
            options.update(cnf)
        redraw = False
        if "from_" in options or "from" in options:
            self._from = float(options.pop("from_", options.pop("from", self._from)))
            redraw = True
        if "to" in options:
            self._to = float(options.pop("to"))
            redraw = True
        if "command" in options:
            self._command = options.pop("command")
        if "format" in options:
            self._format = options.pop("format")
        if "variable" in options:
            self._attach(options.pop("variable"))
        if "state" in options:
            self.state(["disabled"] if str(options.pop("state")) == "disabled" else ["!disabled"])
        if redraw:
            self._value = self._clamped(self._value)
            self._redraw()
        if options:
            return tk.Canvas.configure(self, **options)
        return None

    config = configure

    def cget(self, option):
        if option in ("from", "from_"):
            return self._from
        if option == "to":
            return self._to
        if option == "state":
            return "disabled" if self._disabled else "normal"
        if option == "command":
            return self._command
        if option == "variable":
            return self._variable
        if option == "format":
            return self._format
        return tk.Canvas.cget(self, option)

    __getitem__ = cget

    def state(self, spec=None):
        current = ("disabled",) if self._disabled else ()
        if spec is None:
            return current
        flags = [spec] if isinstance(spec, str) else list(spec)
        for flag in flags:
            if flag in ("disabled", "!disabled"):
                disabled = flag == "disabled"
                if disabled != self._disabled:
                    self._disabled = disabled
                    tk.Canvas.configure(self, takefocus=0 if disabled else self._takefocus)
                    self._paint_faces()
        return current

    def instate(self, spec, callback=None, *args, **kw):
        flags = [spec] if isinstance(spec, str) else list(spec)
        result = all((not self._disabled) if flag == "!disabled" else self._disabled if flag == "disabled"
                     else False for flag in flags)
        if result and callback is not None:
            return callback(*args, **kw)
        return result

    def _destroyed(self, event):
        if event.widget is self and self._trace is not None:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass
            self._trace = None


def _slider_bindings(widget):
    """The KitSlider class bindings, once per interpreter."""
    root = widget._root()
    if getattr(root, "_dolly_kit_slider_bound", False):
        return
    root._dolly_kit_slider_bound = True

    def handler(method):
        def run(event):
            slider = event.widget
            if isinstance(slider, KitSlider):
                return getattr(slider, method)(event)
            return None
        return run
    root.bind_class(_SLIDER_CLASS, "<ButtonPress-1>", handler("_pressed"))
    root.bind_class(_SLIDER_CLASS, "<B1-Motion>", handler("_dragged"))
    for key in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"):
        root.bind_class(_SLIDER_CLASS, f"<KeyPress-{key}>", handler("_key"))
        root.bind_class(_SLIDER_CLASS, f"<Shift-KeyPress-{key}>", handler("_key"))


# -- disclosure ------------------------------------------------------------------------------------

class _ChevronRow:
    """A disclosure row drawn on a canvas: the kit chevron and a ``muted`` label, ``text`` under the pointer."""

    def __init__(self, canvas, title, *, scale, surface):
        self.canvas, self.scale, self.surface = canvas, scale, surface
        t = ui_theme.TOKENS
        self.opened = False
        self.looks = _Looks(canvas)
        self.tag = "kit_disclosure%d" % id(self)
        self.chevron = canvas.create_image(0, 0, anchor="w", tags=(self.tag,))
        self.label = canvas.create_text(0, 0, text=title, anchor="w", font=fonts.font(canvas, "body"),
                                        fill=t["muted"], tags=(self.tag,))
        self.ramp = ColorRamp(canvas, self._paint, t["muted"], t["text"], images=self._faces)
        self._paint(t["muted"])

    def _faces(self, colour):
        root = self.canvas.winfo_toplevel()
        return (art.chevron_rgba(root, self.scale, colour, False, surface=self.surface),
                art.chevron_rgba(root, self.scale, colour, True, surface=self.surface))

    def _paint(self, colour):
        self.looks.config(self.label, fill=colour)
        faces = self.ramp.image(colour) if getattr(self, "ramp", None) is not None else self._faces(colour)
        if faces is not None:
            self.looks.config(self.chevron, image=faces[1 if self.opened else 0])

    def set_opened(self, opened):
        self.opened = bool(opened)
        self._paint(self.ramp.colour)

    def width(self) -> int:
        return px(self.scale, 14) + fonts.font(self.canvas, "body").measure(self.canvas.itemcget(self.label, "text"))

    def place(self, x, middle):
        self.looks.coords(self.chevron, x, middle)
        self.looks.coords(self.label, x + px(self.scale, 14), middle)

    def show(self, shown):
        for item in (self.chevron, self.label):
            self.looks.config(item, state="normal" if shown else "hidden")


class CardDisclosure(tk.Frame):
    """An in-card disclosure for frame-hosted content (Playback options); API of ``widgets.Disclosure``.

    ``toggle`` is a canvas (h 18): the kit chevron (opaque over ``panel``) and the
    title, ``muted``, ``text`` under the pointer through a ramp. It takes focus (nothing
    drawn); Return/space and a click flip it. ``body`` (a ``tk.Frame`` in ``panel``) is
    packed 15 px below the row while open.
    """

    def __init__(self, parent, title):
        t = ui_theme.TOKENS
        super().__init__(parent, background=t["panel"], borderwidth=0, highlightthickness=0, takefocus=0)
        s = self.scale = ui_scale(self)
        self.title = title
        self.opened = False
        self.toggle = tk.Canvas(self, background=t["panel"], highlightthickness=0, borderwidth=0, takefocus=1,
                                height=px(s, 18), width=1, cursor="hand2")
        self.row = _ChevronRow(self.toggle, title, scale=s, surface=t["panel"])
        self.row.place(0, px(s, 18) // 2)
        self.toggle.configure(width=self.row.width())
        self.toggle.pack(anchor="w")
        self.body = tk.Frame(self, background=t["panel"], borderwidth=0, highlightthickness=0, takefocus=0)
        self.toggle.bind("<Enter>", lambda _e: self.row.ramp.enter(), add="+")
        self.toggle.bind("<Leave>", lambda _e: self.row.ramp.leave(), add="+")
        self.toggle.bind("<ButtonRelease-1>", self._clicked, add="+")
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.toggle.bind(key, lambda _e: (self.flip(), "break")[1], add="+")

    def _clicked(self, event):
        if 0 <= event.x < self.toggle.winfo_width() and 0 <= event.y < self.toggle.winfo_height():
            self.flip()

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value=True):
        self.opened = bool(value)
        self.row.set_opened(self.opened)
        if self.opened:
            self.body.pack(fill="both", expand=True, pady=(px(self.scale, 15), 0))
        else:
            self.body.pack_forget()


# -- boil ------------------------------------------------------------------------------------------

class _BoilMember:
    __slots__ = ("widget", "advance")

    def __init__(self, widget, advance):
        self.widget, self.advance = widget, advance

    def winfo_exists(self):
        try:
            return bool(self.widget.winfo_exists())
        except tk.TclError:
            return False


def boil_while(widget, advance):
    """Join the 500 ms boil clock with ``advance()`` until the returned ``stop()`` is called.

    For posterized motion that runs only while something is active (the Listening keycap);
    nothing in the kit is a boil member at rest.
    """
    member = _BoilMember(widget, advance)
    clock = boil.BoilClock.of(widget)
    clock.register(member)
    return lambda: clock.unregister(member)


def finish_motion(*controls) -> None:
    """End the colour ramps of ``controls`` now (a page that hides; tests)."""
    for control in controls:
        ramp = getattr(control, "ramp", None)
        if ramp is not None and ramp.active:
            ramp.finish()


def animator(widget) -> Animator:
    return Animator.of(widget)
