"""Controls drawn straight onto a host canvas (the stage, the sidebar).

Tk widgets are opaque rectangles; canvas items are not, and canvas images carry real alpha.
Everything that sits directly on the animated background is therefore a canvas item: footer
links, the status text and dot, the progress line and the stickers (:mod:`dolly.ui.stickers`).
:class:`CanvasFocus` gives them keyboard access.

A control either owns its state and command or mirrors an
:class:`~dolly.ui.action_proxy.ActionProxy`; state, text and command then live on the proxy,
so the poll's ``_set_widget_state`` and the tests keep driving one object. Never place a
window item over these controls: the canvas reports ``<Leave>`` when the pointer reaches it.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from .. import ui_theme
from . import art, boil, fonts
from .scale import hairline, px
from .tween import ColorRamp

ELLIPSIS = "…"


def mix(color: str, other: str, amount: float) -> str:
    """``amount`` of ``color`` over ``other`` (0..1), as ``#rrggbb``."""
    a, b = ui_theme.rgb(color), ui_theme.rgb(other)
    return "#%02x%02x%02x" % tuple(int(y + (x - y) * amount + .5) for x, y in zip(a, b))


def font_object(widget, font) -> tkfont.Font:
    """A Font for measuring ``font`` (a Font, a named font or a font description)."""
    if isinstance(font, tkfont.Font):
        return font
    if isinstance(font, str) and font in tkfont.names(widget):
        return tkfont.nametofont(font, root=widget)
    return tkfont.Font(root=widget, font=font)


class CanvasControl:
    """Shared state, hover, press and focus handling for one control on a host canvas.

    Each control owns a unique tag; ``<Enter>``/``<Leave>``/press/release are bound on it. A
    release counts only inside the control's bbox, so dragging off cancels. Disabled clears
    hover and press; hidden controls are neither hovered, focused nor invoked.
    """

    cursor = "hand2"
    _SEQUENCES = ("<Enter>", "<Leave>", "<ButtonPress-1>", "<ButtonRelease-1>")

    def __init__(self, canvas: tk.Canvas, *, command=None, action=None, scale: float = 1.0):
        self.canvas = canvas
        self.scale = scale
        self.tag = "dolly_control%d" % id(self)
        self.action = action
        self._command = command
        self._state = "normal"
        self._hover = self._pressed = self._focused = False
        self._shown = True
        self._destroyed = False
        self._unsubscribe = None

    def _connect(self):
        """Bind the pointer on the control's tag and follow its action (once items exist)."""
        handlers = (self._on_enter, self._on_leave, self._on_press, self._on_release)
        for sequence, handler in zip(self._SEQUENCES, handlers):
            self.canvas.tag_bind(self.tag, sequence, handler)
        if self.action is not None:
            self._unsubscribe = self.action.subscribe(self._action_changed)

    # -- state ---------------------------------------------------------------------------

    def enabled(self) -> bool:
        if self.action is not None:
            return bool(self.action.enabled())
        return self._state != "disabled"

    def visible(self) -> bool:
        return self._shown and not self._destroyed

    def _interactive(self) -> bool:
        return self.visible() and self.enabled()

    def focusable(self) -> bool:
        return self._interactive()

    def set_focus(self, focused: bool) -> None:
        self._focused = bool(focused)
        self._draw()

    def winfo_exists(self) -> bool:
        if self._destroyed:
            return False
        try:
            return bool(self.canvas.winfo_exists())
        except tk.TclError:
            return False

    def bbox(self):
        """The control's hit box in canvas coordinates, or None while it is hidden."""
        try:
            return self.canvas.bbox(self.tag) or None
        except tk.TclError:
            return None

    def _action_changed(self, *_args):
        if self._destroyed:
            return
        if not self.enabled():
            self._drop_pointer()
        self._draw()

    def _drop_pointer(self):
        """Forget hover and press (the control was disabled or hidden under the pointer)."""
        if self._hover:
            self._set_cursor(False)
        self._hover = self._pressed = False

    # -- pointer -------------------------------------------------------------------------

    def _on_enter(self, _event=None):
        self._set_hover(True)

    def _on_leave(self, _event=None):
        self._set_hover(False)

    def _set_hover(self, hover: bool) -> None:
        hover = bool(hover) and self._interactive()
        fresh = hover and not self._hover
        self._hover = hover
        if not hover:
            self._pressed = False
        self._set_cursor(hover)
        if fresh:
            self._entered()
        self._draw()

    def _entered(self):
        """Called once when the pointer arrives (the sticker takes a fresh pose)."""

    def _set_cursor(self, hover: bool) -> None:
        try:
            self.canvas.configure(cursor=self.cursor if hover else "")
        except tk.TclError:
            pass

    def _on_press(self, _event=None):
        if self._interactive():
            self._pressed = True
            self._draw()

    def _on_release(self, event=None):
        was, self._pressed = self._pressed, False
        self._draw()
        if was and event is not None and self._inside(event):
            self.invoke()

    def _inside(self, event) -> bool:
        box = self.bbox()
        if box is None:
            return False
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    # -- Tk-button-like API --------------------------------------------------------------

    def invoke(self):
        if not self._interactive():
            return ""
        if self.action is not None:
            return self.action.invoke()
        return self._command() if self._command is not None else ""

    def configure(self, cnf=None, **options):
        if cnf:
            options.update(cnf)
        if self.action is not None:
            forwarded = {key: options.pop(key) for key in ("state", "command", "text") if key in options}
            if forwarded:
                self.action.configure(**forwarded)
        else:
            if "command" in options:
                self._command = options.pop("command")
            if "state" in options:
                state = "disabled" if str(options.pop("state")) == "disabled" else "normal"
                if state != self._state:
                    self._state = state
                    if state == "disabled":
                        self._drop_pointer()
                    self._draw()
            if "text" in options:
                self._set_text(str(options.pop("text")))
        if options:
            raise tk.TclError("unknown option(s): " + ", ".join(sorted(options)))

    config = configure

    def cget(self, option):
        if option == "state":
            if self.action is not None:
                return self.action.cget("state")
            return self._state
        if option == "command":
            return self.action.cget("command") if self.action is not None else self._command
        if option == "text":
            return self._text_value()
        raise tk.TclError(f"unknown option {option!r}")

    __getitem__ = cget

    def _text_value(self) -> str:
        return self.action.cget("text") if self.action is not None else ""

    def _set_text(self, text: str) -> None:
        pass

    def _draw(self):
        pass

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        try:
            for sequence in self._SEQUENCES:
                self.canvas.tag_unbind(self.tag, sequence)
            if self._hover:
                self.canvas.configure(cursor="")
            self._delete_items()
        except tk.TclError:
            pass

    def _delete_items(self):
        self.canvas.delete(self.tag)


class CanvasLink(CanvasControl):
    """A text-only action ("Open output folder", "Show log"): ``muted``, ``text`` while
    hovered, pressed or focused, ``dim`` when disabled. A link backed by an action is shown
    only while the action is enabled; ``textvariable`` keeps the label live.

    The pointer's colour change is a short ramp on the shared animator (120 ms in, 180 ms
    out, PLAN-5 2.11), its first step drawn in the event itself; keyboard focus, state and
    visibility show their colour at once."""

    def __init__(self, canvas, *, text=None, textvariable=None, command=None, action=None, font, scale):
        super().__init__(canvas, command=command, action=action, scale=scale)
        self._font = font
        self._measure = font_object(canvas, font)
        self._variable = textvariable
        self._trace = None
        self._follows_action = text is None and textvariable is None and action is not None
        if text is None:
            text = textvariable.get() if textvariable is not None else (action.cget("text") if action else "")
        self._text = str(text)
        self.item = canvas.create_text(0, 0, text=self._text, font=font, anchor="w", tags=(self.tag,))
        self.ring = canvas.create_rectangle(0, 0, 0, 0, outline="", width=hairline(scale), state="hidden")
        t = ui_theme.TOKENS
        self._ramp = ColorRamp(canvas, self._paint_label, t["muted"], t["text"])
        self._ringed = None       # keyboard focus as last drawn (None: nothing drawn yet)
        if textvariable is not None:
            self._trace = textvariable.trace_add("write", self._variable_changed)
        self._connect()
        self._draw()

    def _paint_label(self, colour):
        self.canvas.itemconfigure(self.item, fill=colour)

    def visible(self) -> bool:
        return super().visible() and (self.action is None or self.enabled())

    def width(self) -> int:
        # Spaced like Figma's quiet buttons: the footer links are 108 / 53 / 107 (Inter's own widths).
        role = fonts.ROLES.get(str(self._font))
        return fonts.hug_width(self.canvas, role, self._text) if role else self._measure.measure(self._text)

    def place(self, x, y, anchor="w"):
        self.canvas.coords(self.item, x, y)
        self.canvas.itemconfigure(self.item, anchor=anchor)
        self._draw()

    def set_visible(self, visible: bool) -> None:
        self._shown = bool(visible)
        if not self._shown:
            self._drop_pointer()
        self._draw()

    def _action_changed(self, *_args):
        if self._follows_action and not self._destroyed and self.action.cget("text") != self._text:
            self._text = str(self.action.cget("text"))
            self.canvas.itemconfigure(self.item, text=self._text)
        if not self.visible():
            self._drop_pointer()
        super()._action_changed()

    def _variable_changed(self, *_args):
        if not self._destroyed:
            self._set_text(self._variable.get())

    def _text_value(self) -> str:
        return self._text

    def _set_text(self, text: str) -> None:
        self._text = text
        self.canvas.itemconfigure(self.item, text=text)
        self._draw()

    def _draw(self):
        if self._destroyed:
            return
        t = ui_theme.TOKENS
        c = self.canvas
        shown = self.visible()
        lit = self._hover or self._pressed or self._focused
        if not self.enabled():
            self._ramp.jump(t["dim"])
        elif not shown or self._focused != self._ringed or self._ramp.colour == t["dim"]:
            self._ramp.jump(t["text"] if lit else t["muted"])   # keyboard, state, visibility: at once
        elif lit:
            self._ramp.enter()
        else:
            self._ramp.leave()
        self._ringed = self._focused
        c.itemconfigure(self.item, state="normal" if shown else "hidden")
        box = c.bbox(self.item) if shown else None
        if self._focused and shown and box:
            pad_x, pad_y = px(self.scale, 3), px(self.scale, 1)
            c.coords(self.ring, box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y)
            c.itemconfigure(self.ring, outline=t["accent"], state="disabled")
        else:
            c.itemconfigure(self.ring, state="hidden")

    def destroy(self) -> None:
        if self._trace is not None and self._variable is not None:
            try:
                self._variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass
            self._trace = None
        if not self._destroyed:
            self._ramp.finish()   # no frame after the items are gone
        super().destroy()

    def _delete_items(self):
        self.canvas.delete(self.item, self.ring)


class CanvasText:
    """A canvas text item with a Label-like ``configure(text=, foreground=, wraplength=)``.

    With ``max_lines`` (and a ``wraplength``) longer text is cut at the last character that
    still fits and ends with an ellipsis; :meth:`elided` and :meth:`full_text` let the owner
    show the whole message in a bubble.
    """

    def __init__(self, canvas, *, text="", font, fill, anchor="w", wraplength=0, max_lines=0):
        self.canvas = canvas
        self._font = font
        self._measure = font_object(canvas, font)
        self._text = str(text)
        self._fill = fill
        self._wrap = int(wraplength)
        self._max_lines = int(max_lines)
        self._elided = False
        self.item = canvas.create_text(0, 0, text="", font=font, fill=fill, anchor=anchor, width=self._wrap)
        self._render()
        # A named font reconfigured in place (the private display font arriving) changes
        # where the text has to be cut.
        fonts.on_change(canvas, self._font_changed)

    def _font_changed(self):
        try:
            if self.canvas.winfo_exists():
                self._render()
        except tk.TclError:
            pass

    def place(self, x, y, anchor=None):
        self.canvas.coords(self.item, x, y)
        if anchor is not None:
            self.canvas.itemconfigure(self.item, anchor=anchor)

    def bbox(self):
        return self.canvas.bbox(self.item) or None

    def elided(self) -> bool:
        return self._elided

    def full_text(self) -> str:
        return self._text

    def _lines(self) -> int:
        box = self.canvas.bbox(self.item)
        if not box:
            return 0
        return max(1, round((box[3] - box[1]) / max(1, self._measure.metrics("linespace"))))

    def _render(self):
        c = self.canvas
        c.itemconfigure(self.item, width=self._wrap, text=self._text, fill=self._fill)
        self._elided = False
        if self._max_lines <= 0 or self._wrap <= 0 or self._lines() <= self._max_lines:
            return
        low, high = 0, len(self._text)
        while low < high:  # the longest prefix that still fits with the ellipsis
            middle = (low + high + 1) // 2
            c.itemconfigure(self.item, text=self._text[:middle].rstrip() + ELLIPSIS)
            if self._lines() <= self._max_lines:
                low = middle
            else:
                high = middle - 1
        c.itemconfigure(self.item, text=self._text[:low].rstrip() + ELLIPSIS)
        self._elided = True

    def configure(self, cnf=None, **options):
        if cnf:
            options.update(cnf)
        changed = False
        if "text" in options:
            text = str(options.pop("text"))
            changed |= text != self._text
            self._text = text
        for name in ("fill", "foreground", "fg"):
            if name in options:
                color = options.pop(name)
                changed |= color != self._fill
                self._fill = color
        if "wraplength" in options:
            wrap = int(options.pop("wraplength"))
            changed |= wrap != self._wrap
            self._wrap = wrap
        if "max_lines" in options:
            lines = int(options.pop("max_lines"))
            changed |= lines != self._max_lines
            self._max_lines = lines
        if "font" in options:
            self._font = options.pop("font")
            self._measure = font_object(self.canvas, self._font)
            self.canvas.itemconfigure(self.item, font=self._font)
            changed = True
        if options:
            raise tk.TclError("unknown option(s): " + ", ".join(sorted(options)))
        if changed:
            self._render()

    config = configure

    def cget(self, option):
        if option == "text":
            return self._text
        if option in ("fill", "foreground", "fg"):
            return self._fill
        if option == "wraplength":
            return self._wrap
        if option == "max_lines":
            return self._max_lines
        return self.canvas.itemcget(self.item, option)

    __getitem__ = cget


class CanvasDot:
    """The status dot: an anti-aliased RGBA image (transparent around, so the background shows).

    ``configure(state=...)`` picks ``ready``, ``busy`` or ``error``. A busy dot blinks on the
    boil clock between ``status_busy`` and half of it over the backdrop; otherwise it is steady.
    """

    COLORS = {"ready": "status_ready", "busy": "status_busy", "error": "status_error"}

    def __init__(self, canvas, *, scale):
        self.canvas = canvas
        self.scale = scale
        self._diameter = max(3, px(scale, 5))
        self._state = "ready"
        self._blink = False
        self._image = None
        self._images = {}             # colour -> image: a blinking dot only swaps two
        self._destroyed = False
        self.item = canvas.create_image(0, 0, anchor="center", state="disabled")
        self._draw()
        boil.BoilClock.of(canvas).register(self)

    def place(self, x, y):
        self.canvas.coords(self.item, x, y)

    def color(self) -> str:
        t = ui_theme.TOKENS
        color = t[self.COLORS[self._state]]
        if self._state == "busy" and self._blink:
            return mix(color, t["backdrop"], .5)
        return color

    def configure(self, state=None, **options):
        state = options.pop("state", state)
        if options:
            raise tk.TclError("unknown option(s): " + ", ".join(sorted(options)))
        if state is not None:
            if state not in self.COLORS:
                raise ValueError(f"unknown status dot state {state!r}")
            if state != self._state:
                self._state = state
                self._blink = False
                self._draw()

    config = configure

    def cget(self, option):
        if option == "state":
            return self._state
        raise tk.TclError(f"unknown option {option!r}")

    def advance(self):
        if self._state == "busy":
            self._blink = not self._blink
            self._draw()

    def winfo_exists(self) -> bool:
        try:
            return not self._destroyed and bool(self.canvas.winfo_exists())
        except tk.TclError:
            return False

    def _draw(self):
        if self._destroyed:
            return
        color = self.color()
        image = self._images.get(color)
        if image is None:
            image = self._images[color] = art.dot_rgba(self.canvas.winfo_toplevel(), self._diameter, color)
        if image is not self._image:
            self._image = image
            self.canvas.itemconfigure(self.item, image=image)

    def destroy(self):
        if not self._destroyed:
            self._destroyed = True
            self._images.clear()
            try:
                boil.BoilClock.of(self.canvas).unregister(self)
                self.canvas.delete(self.item)
            except tk.TclError:
                pass


class CanvasProgress:
    """The startup progress line: an accent segment that steps across its slot in six held
    positions on the boil clock (posterized, never tweened). It follows a
    :class:`~dolly.ui.action_proxy.ProgressProxy` and is hidden while that is stopped.
    With motion off it rests on the third position."""

    STEPS = 6
    STILL_STEP = 2

    def __init__(self, canvas, *, scale):
        self.canvas = canvas
        self.scale = scale
        self._segment = px(scale, 205)
        self._height = max(1, px(scale, 3))
        self._box = (0, 0, 0)
        self._motion = boil.motion_enabled()
        self._step = 0 if self._motion else self.STILL_STEP
        self._running = False
        self._proxy = None
        self._unsubscribe = None
        self._destroyed = False
        self.item = canvas.create_rectangle(0, 0, 0, 0, fill=ui_theme.TOKENS["accent"], width=0, state="hidden")
        boil.BoilClock.of(canvas).register(self)

    @property
    def step(self) -> int:
        return self._step

    @property
    def running(self) -> bool:
        return self._running

    def bind(self, proxy) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
        self._proxy = proxy
        self._unsubscribe = proxy.subscribe(self._changed)
        self._changed()

    def _changed(self, *_args):
        if self._destroyed or self._proxy is None:
            return
        running = bool(self._proxy.running)
        if running and not self._running:
            self._step = 0 if self._motion else self.STILL_STEP
        self._running = running
        self._draw()

    def place(self, x0, x1, y):
        self._box = (x0, x1, y)
        self._draw()

    def positions(self) -> list[int]:
        """Left edge of the segment at each step."""
        x0, x1, _y = self._box
        span = max(0, x1 - self._segment - x0)
        return [int(x0 + span * index / (self.STEPS - 1) + .5) for index in range(self.STEPS)]

    def advance(self):
        if self._running and self._motion:
            self._step = (self._step + 1) % self.STEPS
            self._draw()

    def winfo_exists(self) -> bool:
        try:
            return not self._destroyed and bool(self.canvas.winfo_exists())
        except tk.TclError:
            return False

    def _draw(self):
        if self._destroyed:
            return
        if not self._running:
            self.canvas.itemconfigure(self.item, state="hidden")
            return
        x0, x1, y = self._box
        left = self.positions()[self._step]
        self.canvas.coords(self.item, left, y, min(x1, left + self._segment), y + self._height)
        self.canvas.itemconfigure(self.item, state="disabled")

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        try:
            boil.BoilClock.of(self.canvas).unregister(self)
            self.canvas.delete(self.item)
        except tk.TclError:
            pass


def _track_tab_direction(top) -> None:
    """Remember whether the last Tab in this window went forwards or backwards (one binding
    pair per toplevel), so a proxy reached with Shift+Tab starts at its last control."""
    if getattr(top, "_dolly_tab_tracked", False):
        return
    top._dolly_tab_tracked = True
    top._dolly_tab_backward = False
    top.bind("<Tab>", lambda _e: setattr(top, "_dolly_tab_backward", False), add="+")
    top.bind("<Shift-Tab>", lambda _e: setattr(top, "_dolly_tab_backward", True), add="+")


class CanvasFocus:
    """Keyboard focus for the controls drawn on one canvas.

    Focus sits on a 1 px stand-in frame on the canvas, not on the canvas: a canvas holding
    widgets also hears their focus changes, which would light a ring while the user types.
    Tab/Shift+Tab move between the focusable controls and then on to the next widget;
    Return/space invoke the focused one. Created after the canvas's other widgets, the
    stand-in comes after them in Tab order.
    """

    def __init__(self, canvas, controls=()):
        self.canvas = canvas
        self.controls = list(controls)
        self.index = None
        self.proxy = tk.Frame(canvas, width=1, height=1, background=canvas.cget("background"),
                              highlightthickness=0, borderwidth=0, takefocus=1)
        self.window = canvas.create_window(0, 0, window=self.proxy, anchor="nw")
        self.proxy.bind("<FocusIn>", self._focus_in)
        self.proxy.bind("<FocusOut>", lambda _e: self._show(None))
        self.proxy.bind("<Tab>", lambda _e: self._move(1))
        self.proxy.bind("<Shift-Tab>", lambda _e: self._move(-1))
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.proxy.bind(key, self._activate)
        self._top = canvas.winfo_toplevel()
        _track_tab_direction(self._top)

    def place(self, x, y) -> None:
        """Move the 1 px stand-in (keep it where no control is drawn)."""
        self.canvas.coords(self.window, x, y)

    def set_controls(self, controls) -> None:
        current = self.focused()
        self.controls = list(controls)
        if current in self.controls and current.focusable():
            self.index = self.controls.index(current)
        else:
            if current is not None:
                current.set_focus(False)
            self.index = None
            if self.has_focus():
                self._focus_in()
                return
        self._show(self.index)

    def focused(self):
        if self.index is None or self.index >= len(self.controls):
            return None
        return self.controls[self.index]

    def has_focus(self) -> bool:
        try:
            return self.proxy.focus_get() is self.proxy
        except (KeyError, tk.TclError):
            return False

    def _show(self, index):
        for i, control in enumerate(self.controls):
            control.set_focus(i == index)

    def _focus_in(self, _event=None):
        backward = bool(getattr(self._top, "_dolly_tab_backward", False))
        order = range(len(self.controls) - 1, -1, -1) if backward else range(len(self.controls))
        self.index = next((i for i in order if self.controls[i].focusable()), None)
        self._show(self.index)
        if self.index is None:  # nothing to focus here: pass focus on in the same direction
            target = self.proxy.tk_focusPrev() if backward else self.proxy.tk_focusNext()
            if target is not None and target is not self.proxy:
                target.focus_set()

    def _move(self, step):
        start = self.index if self.index is not None else (-1 if step > 0 else len(self.controls))
        i = start + step
        while 0 <= i < len(self.controls):
            if self.controls[i].focusable():
                self.index = i
                self._show(i)
                return "break"
            i += step
        self.index = None
        self._show(None)
        return None  # past the last control: Tk moves on to the next widget

    def _activate(self, _event=None):
        control = self.focused()
        if control is not None and control.focusable():
            control.invoke()
        return "break"
