"""Shot Playback content (Figma [design reference]), shared by the Camera and Look pages, pinned in their dock.

One frame, ``timeline_frame``, adopted by whichever page is shown through a kit host card
(:class:`.kit_page.KitHostCard`, PLAN-5 N10, 3.1.1; USER-ADDITIONS UPDATE 7). The host draws
the plate, the title and the card's background art (Figma's fills: the card colour, a faint
halftone tile and a magic-circle drawing, :func:`.art.playback_art`); this frame paints nothing
itself (``background=""``) and holds one canvas, ``timeline_body``, the card's content drawn over
the same art. The host hands the body the card's picture and the body's offset in the card
(``timeline_frame.card_backdrop``); the body shows it as its bottom item, built once per card
size, never touched while scrolling or animating.

So nothing on the card is a flat box over the art: labels are canvas text, the buttons are kit
canvas buttons whose corners blend (``plate="rgba"``), the fields and dropdowns are flat ttk
widgets inside rounded plates drawn on the canvas, and the slider, the disclosure row and the
checkboxes are small canvases that show their own part of the same picture. Without art (no
host, an old art set) everything is drawn on the plain ``panel``, as before.

Layout (design px): the shot-time row (slider · 10 · time field · 5 · "s"), 10 below it the
controls (Play shot, Pause, Stop / restore, 10 apart; Playback speed on the right), 15 below
them the "Playback options" row, whose rows (checkboxes, preview/seek, console smoothing) start
15 below it, 10 apart. Wiring lives in ``gui_layout.py``; this module never imports the
application controller or the export backend.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import art, fonts, kit_styles
from .kit_controls import CORNERS, KitLabel, KitSlider, RowLayout, _ChevronRow, _Looks
from .kit_fields import number_width
from .scale import px, ui_scale

SPEEDS = ("0.05", "0.1", "0.25", "0.5", "1", "2", "4")
# The dropdown shows a speed as "1x" (Figma [design reference]); the app's speed variable keeps the bare number.
SPEED_SUFFIX = "x"
RATES = ("30", "60", "120")
SMOOTHING = ("Off", "Light", "Balanced", "Strong")
SMOOTHING_NOTE = "Light 80 ms / Balanced 160 ms / Strong 280 ms"
# The flat field and dropdown styles: the kit's, without their corner stamps (the card draws the plate).
FIELD_STYLES = {"Playback.TEntry": "Kit.TEntry", "Playback.TCombobox": "Kit.TCombobox"}
ART_TAG = "card_art"


def speed_label(value) -> str:
    """``"1"`` -> ``"1x"``, what the speed dropdown shows (empty stays empty, a suffix is not doubled)."""
    text = str(value).strip()
    return text + SPEED_SUFFIX if text and not text.lower().endswith(SPEED_SUFFIX) else text


def speed_value(text) -> str:
    """``"1x"`` -> ``"1"``, what the app's speed variable holds (text without the suffix is kept)."""
    text = str(text).strip()
    return text[:-1].rstrip() if text.lower().endswith(SPEED_SUFFIX) else text


@dataclass(frozen=True)
class PlaybackState:
    shot_time: tk.Variable
    time_text: tk.Variable
    speed: tk.Variable
    hide_hud: tk.Variable
    frozen: tk.Variable
    seek_relief: tk.Variable
    rate: tk.Variable
    smoothing: tk.Variable


@dataclass(frozen=True)
class PlaybackActions:
    slide: Callable[..., object]           # slide(value): the slider moved by the user
    set_dragging: Callable[..., object]    # set_dragging(flag): the slider is held (the poll leaves the time alone)
    play: Callable[..., object]
    pause: Callable[..., object]
    stop: Callable[..., object]
    apply_playback_speed: Callable[..., object]
    set_seek_relief: Callable[..., object]
    apply_frame: Callable[..., object]
    seek: Callable[..., object]


def install_styles(widget) -> None:
    """``Playback.TEntry`` and ``Playback.TCombobox``: the kit field and dropdown without corner stamps.

    The ttk widget fills the plate between its rounded ends (the card canvas draws those
    over the art), so its text keeps the kit's 13 px inset from the plate's edge.
    Idempotent per interpreter, theme and scale, like :func:`.kit_styles.install`.
    """
    kit_styles.install(widget)
    root = widget._root()
    style = ttk.Style(root)
    s = ui_scale(root)
    key = (style.theme_use(), s)
    done = getattr(root, "_dolly_playback_styles", None)
    if done is None:
        done = root._dolly_playback_styles = set()
    if key in done:
        return
    done.add(key)
    t = ui_theme.TOKENS
    r = max(1, round(4 * s))
    control = kit_styles.kit_padding(root, 13)
    right = {"Playback.TEntry": control[2], "Playback.TCombobox": max(0, px(s, 13) - px(s, 1))}
    for name, base in FIELD_STYLES.items():
        (frame, options), = style.layout(base)
        content = [child for child in options.get("children", ()) if not child[0].startswith(frame + ".")]
        style.layout(name, [(frame + ".fill", {"sticky": "nsew", "children": content})])
        settings = dict(style.configure(base) or {})
        settings.update(padding=(max(0, control[0] - r), control[1], max(0, right[name] - r), control[3]),
                        background=t["field"])
        style.configure(name, **settings)
        maps = dict(style.map(base) or {})
        maps["background"] = [("disabled", t["field_disabled"])]
        style.map(name, **maps)


# -- the art behind the content --------------------------------------------------------------------

def show_art(canvas, image, x, y) -> None:
    """Show ``image`` (the card's picture) at canvas (``x``, ``y``) under everything on ``canvas``; None hides it."""
    items = canvas.find_withtag(ART_TAG)
    if image is None and not items:
        return
    looks = getattr(canvas, "_art_looks", None)
    if looks is None:
        looks = canvas._art_looks = _Looks(canvas)
    if not items:
        items = (canvas.create_image(x, y, anchor="nw", tags=(ART_TAG,)),)
        canvas.tag_lower(items[0])
    if image is None:
        looks.config(items[0], state="hidden")
        return
    looks.coords(items[0], x, y)
    looks.config(items[0], image=image, state="normal")


class _CardSlider(KitSlider):
    """The kit slider over the card's art: its track ends blend into what lies under them (RGBA corners)."""

    def _paint_faces(self):
        t = ui_theme.TOKENS
        track, fill, thumb = ((t["edge_disabled"], t["accent_disabled"], t["text_disabled"]) if self._disabled
                              else (t["button"], t["accent"], t["text"]))
        root, r = self.winfo_toplevel(), self._r
        self.looks.config(self._track[0], fill=track)
        for item, which in zip(self._track[1:], CORNERS):
            self.looks.config(item, image=art.corner_face(root, r, track, which))
        self.looks.config(self._fill[0], fill=fill)
        for item, which in zip(self._fill[1:], ("nw", "sw")):
            self.looks.config(item, image=art.corner_face(root, r, fill, which))
        self.looks.config(self._thumb, image=art.dot_rgba(root, self._thumb_d, thumb))


class _Plated:
    """A ttk field whose plate the card draws: a state change repaints the plate's ends."""

    plate = None

    def configure(self, cnf=None, **kw):
        result = super().configure(cnf, **kw)
        if self.plate is not None and ("state" in kw or (isinstance(cnf, dict) and "state" in cnf)):
            self.plate.paint()
        return result

    config = configure

    def state(self, statespec=None):
        result = super().state(statespec)
        if statespec is not None and self.plate is not None:
            self.plate.paint()
        return result


class _PlatedEntry(_Plated, ttk.Entry):
    pass


class _PlatedCombobox(_Plated, ttk.Combobox):
    pass


class FieldPlate:
    """A kit field or dropdown on the card: the rounded plate on the canvas, the flat ttk widget inside it.

    The plate's two rounded ends (``field``, or ``field_disabled`` while the widget is
    disabled; RGBA corners over the art) are canvas items; the widget is placed between
    them over the whole height. A :class:`.kit_controls.RowLayout` cell (``custom``).
    """

    def __init__(self, canvas, widget, *, width):
        self.canvas, self.widget = canvas, widget
        self.scale = s = ui_scale(canvas)
        self.width = int(width)                 # px, the whole plate
        self.radius = max(1, round(4 * s))
        self.box = None
        self._colour = None
        self.looks = _Looks(canvas)
        self._fills = tuple(canvas.create_rectangle(0, 0, 0, 0, width=0, state="hidden") for _ in range(2))
        self._corners = tuple(canvas.create_image(0, 0, anchor="nw", state="hidden") for _ in CORNERS)
        self._placed = None
        widget.plate = self
        self.paint()

    def winfo_reqwidth(self) -> int:
        return self.width

    def winfo_reqheight(self) -> int:
        return px(self.scale, 30)

    def paint(self) -> None:
        t = ui_theme.TOKENS
        try:
            disabled = self.widget.instate(["disabled"])
        except tk.TclError:
            return
        colour = t["field_disabled"] if disabled else t["field"]
        if colour == self._colour:
            return
        self._colour = colour
        root = self.canvas.winfo_toplevel()
        for item in self._fills:
            self.looks.config(item, fill=colour)
        for item, which in zip(self._corners, CORNERS):
            self.looks.config(item, image=art.corner_face(root, self.radius, colour, which))

    def place(self, x, y, width=None) -> None:
        width = self.width if width is None else int(width)
        h, r = self.winfo_reqheight(), self.radius
        x, y = int(x), int(y)
        self.box = (x, y, x + width, y + h)
        looks = self.looks
        looks.coords(self._fills[0], x, y + r, x + r, y + h - r)
        looks.coords(self._fills[1], x + width - r, y + r, x + width, y + h - r)
        for item, (cx, cy) in zip(self._corners, ((x, y), (x + width - r, y), (x, y + h - r),
                                                  (x + width - r, y + h - r))):
            looks.coords(item, cx, cy)
        for item in (*self._fills, *self._corners):
            looks.config(item, state="normal")
        place = (x + r, y, max(1, width - 2 * r), h)
        if place != self._placed:
            self._placed = place
            self.widget.place_forget()
            self.widget.place(x=place[0], y=place[1], width=place[2], height=place[3])

    def hide(self) -> None:
        self.box = None
        for item in (*self._fills, *self._corners):
            self.looks.config(item, state="hidden")
        if self._placed is not None:
            self._placed = None
            self.widget.place_forget()


def _check_faces(root, scale):
    """The kit checkbox box ([design reference]), off and on, RGBA (it sits on the card's art); cached on the root."""
    faces = getattr(root, "_dolly_playback_checks", None)
    if faces is None:
        faces = root._dolly_playback_checks = {}
    if scale not in faces:
        from ..gui_theme import rounded_image
        t = ui_theme.TOKENS
        size, radius = round(12 * scale), round(4 * scale)
        tick = ((3 * scale, 6 * scale), (5 * scale, 8 * scale), (9 * scale, 4 * scale))   # M0 2 L2 4 L6 0 at (+3, +4)
        off = rounded_image(root, t["field"], t["edge"], radius, size=size, line=scale)
        on = rounded_image(root, t["accent"], t["accent"], radius, size=size, line=scale)
        mark = art.stroke_rgba(root, size, size, (tick,), scale, t["text"])
        on.tk.call(str(on), "copy", str(mark), "-compositingrule", "overlay")
        faces[scale] = (off, on)
    return faces[scale]


class CardCheck(tk.Canvas):
    """A kit checkbox drawn over the card's art (a small canvas: the box, 5 px, the label in ``text``).

    Click or Space toggles ``variable`` and runs ``command``; it takes focus and draws no
    focus state (N4). ``invoke()``, ``cget("text")`` and ``instate`` as on a ttk checkbutton.
    ``resized`` is called when its width changes (the fonts were reconfigured).
    """

    def __init__(self, parent, *, text, variable, command=None, resized=None):
        s = ui_scale(parent)
        t = ui_theme.TOKENS
        super().__init__(parent, background=t["panel"], highlightthickness=0, borderwidth=0, takefocus=1,
                         height=px(s, 18), width=1, cursor="hand2")
        self.scale = s
        self.variable, self.command, self.text = variable, command, str(text)
        self.resized = resized
        self._faces = _check_faces(self.winfo_toplevel(), s)
        size = self._faces[0].width()
        self._box = self.create_image(0, (px(s, 18) - size) // 2, anchor="nw")
        self.label = KitLabel(self, self.text, tone="text")
        self.label.place(size + px(s, 5), px(s, 18) // 2, anchor="w")
        self.label.on_change = self._fit
        self._fit()
        self._trace = variable.trace_add("write", lambda *_args: self._paint())
        self.bind("<ButtonRelease-1>", self._clicked, add="+")
        for key in ("<space>", "<Return>", "<KP_Enter>"):
            self.bind(key, lambda _event: (self.invoke(), "break")[1], add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self._paint()

    def _fit(self):
        width = self._faces[0].width() + px(self.scale, 5) + self.label.natural_width()
        if int(tk.Canvas.cget(self, "width")) != width:
            self.configure(width=width)
            if self.resized is not None:
                self.resized()

    def _paint(self):
        try:
            on = bool(self.variable.get())
        except (tk.TclError, ValueError):
            on = False
        self.itemconfigure(self._box, image=self._faces[1 if on else 0])

    def _clicked(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        self.variable.set(not bool(self.variable.get()))
        if self.command is not None:
            return self.command()
        return None

    def cget(self, key):
        if key == "text":
            return self.text
        return tk.Canvas.cget(self, key)

    __getitem__ = cget

    def instate(self, spec, callback=None, *args, **kw):
        on = bool(self.variable.get())
        flags = [spec] if isinstance(spec, str) else list(spec)
        result = all({"disabled": False, "!disabled": True, "selected": on, "!selected": not on}.get(flag, False)
                     for flag in flags)
        if result and callback is not None:
            return callback(*args, **kw)
        return result

    def _destroyed(self, event):
        if event.widget is self and self._trace is not None:
            try:
                self.variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass
            self._trace = None


class CardToggle(tk.Canvas):
    """The "Playback options" row over the card's art: the kit chevron (RGBA) and the ``muted`` title.

    Hover ramps it to ``text``; it takes focus (nothing drawn) and Return/space/a click flip
    the disclosure.
    """

    def __init__(self, parent, title, command):
        s = ui_scale(parent)
        super().__init__(parent, background=ui_theme.TOKENS["panel"], highlightthickness=0, borderwidth=0,
                         takefocus=1, height=px(s, 18), width=1, cursor="hand2")
        self.scale = s
        self.command = command
        self.row = _ChevronRow(self, title, scale=s, surface=None)
        self.row.place(0, px(s, 18) // 2)
        self.configure(width=self.row.width())
        # The fonts may be reconfigured after the card was built: keep hugging the title.
        unlisten = fonts.on_change(self, lambda: self.configure(width=self.row.width())
                                   if self.winfo_exists() else None)
        self.bind("<Destroy>", lambda e: unlisten() if e.widget is self else None, add="+")
        self.bind("<Enter>", lambda _e: self.row.ramp.enter(), add="+")
        self.bind("<Leave>", lambda _e: self.row.ramp.leave(), add="+")
        self.bind("<ButtonRelease-1>", self._clicked, add="+")
        for key in ("<Return>", "<KP_Enter>", "<space>"):
            self.bind(key, lambda _e: (self.command(), "break")[1], add="+")

    def _clicked(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.command()


class _Rows:
    """Rows of the card that the poll shows and hides (``pack``-compatible for the app's code).

    ``winfo_manager()`` is ``"pack"`` while shown (and, for the disclosure's body, while
    the disclosure is open), ``""`` otherwise, like the packed frames it replaces.
    """

    def __init__(self, card, children=(), *, disclosure=None):
        self.card = card
        self.disclosure = disclosure
        self.shown = True
        self.children = list(children)

    def pack(self, **_ignored) -> None:
        self.set_shown(True)

    def pack_forget(self) -> None:
        self.set_shown(False)

    def set_shown(self, shown) -> None:
        shown = bool(shown)
        if shown != self.shown:
            self.shown = shown
            self.card.layout()

    def winfo_manager(self) -> str:
        shown = self.disclosure.opened if self.disclosure is not None else self.shown
        return "pack" if shown else ""

    def winfo_ismapped(self) -> bool:
        try:
            return (self.winfo_manager() == "pack" and self.card.playback_disclosure.opened
                    and bool(self.card.timeline_body.winfo_ismapped()))
        except tk.TclError:
            return False

    def winfo_children(self):
        return list(self.children)


class CardDisclosure:
    """"Playback options": ``toggle`` (a :class:`CardToggle`) shows ``body`` 15 px below it (``widgets.Disclosure`` API)."""

    def __init__(self, card, title):
        self.card = card
        self.title = title
        self.opened = False
        self.toggle = CardToggle(card.timeline_body, title, self.flip)
        self.body = _Rows(card, disclosure=self)

    def flip(self):
        self.set_open(not self.opened)

    def set_open(self, value=True):
        value = bool(value)
        if value == self.opened:
            return
        self.opened = value
        self.toggle.row.set_opened(value)
        if not value:
            focus = str(self.toggle.tk.call("focus"))
            if any(focus == str(widget) or focus.startswith(str(widget) + ".") for widget in self.card._option_widgets()):
                self.toggle.focus_set()   # never leave the keyboard inside hidden rows
        self.card.layout()


class PlaybackCard:
    def __init__(self, parent, state: PlaybackState, commands: PlaybackActions):
        t = ui_theme.TOKENS
        install_styles(parent)
        s = self.scale = ui_scale(parent)
        self._art = None                 # (image, x, y): the card picture and the body's place in the card
        self._job = None
        self._size = None
        # A frame that paints nothing; its one child fills it (the host draws the plate and the title).
        frame = self.timeline_frame = tk.Frame(parent, background="", borderwidth=0, highlightthickness=0,
                                               takefocus=0)
        body = self.timeline_body = tk.Canvas(frame, background=t["panel"], highlightthickness=0, borderwidth=0,
                                              takefocus=0, width=1, height=1)
        body.pack(fill="both", expand=True)
        frame.card_backdrop = self.card_backdrop      # the host card hands over its picture (kit_page)

        # Shot time: the kit slider fills the row, then the time field and its unit.
        self.slider = _CardSlider(body, from_=0, to=10, variable=state.shot_time, command=commands.slide)
        # The app's dragging flag: the status poll must not move the playhead under the pointer.
        self.slider.bind("<ButtonPress-1>", lambda _event: commands.set_dragging(True), add="+")
        self.slider.bind("<ButtonRelease-1>", lambda _event: commands.set_dragging(False), add="+")
        self.time_entry = _PlatedEntry(body, textvariable=state.time_text, width=1, style="Playback.TEntry")
        # The kit field (min 34 px, as drawn) wide enough for a time like "123.456".
        self.time_field = FieldPlate(body, self.time_entry, width=number_width(self.time_entry, "000.000", 34))
        self.unit = KitLabel(body, "s")
        self.unit.on_change = self.layout

        # Controls: three canvas buttons (one focus group), the speed on the right.
        row = self.controls_row = RowLayout(body, scale=s, height=30, surface=t["panel"], plate="rgba",
                                            changed=self.layout)
        self.play_button = row.button("Play shot", commands.play)
        row.gap(10)
        self.pause_button = row.button("Pause", commands.pause)
        row.gap(10)
        self.stop_button = row.button("Stop / restore", commands.stop)
        row.push()
        row.label("Playback speed")
        row.gap(10)
        # Editable: a typed speed applies on Return (the app parses a number). It reads "1x" (Figma
        # [design reference]) through ``speed_text``; ``state.speed`` keeps the bare number both ways.
        self._speed_var = state.speed
        self.speed_text = tk.StringVar(body, value=speed_label(state.speed.get()))
        self._speed_traces = (state.speed.trace_add("write", self._speed_from_app),
                              self.speed_text.trace_add("write", self._speed_from_field))
        self.speed_combo = _PlatedCombobox(body, textvariable=self.speed_text,
                                           values=tuple(speed_label(value) for value in SPEEDS), width=1,
                                           style="Playback.TCombobox")
        # Hugs its value like Figma's dropdown (55 px for "1x").
        self.speed_field = row.custom(FieldPlate(body, self.speed_combo, width=self._speed_width()))
        self.speed_combo.bind("<<ComboboxSelected>>", lambda _event: commands.apply_playback_speed())
        self.speed_combo.bind("<Return>", lambda _event: commands.apply_playback_speed())

        # Playback options: 15 px below the controls, its rows 15 below it and 10 apart.
        disclosure = self.playback_disclosure = CardDisclosure(self, "Playback options")
        self.checks = []
        for title, variable, command in (("Hide HUD", state.hide_hud, None),
                                         ("Frozen preview", state.frozen, None),
                                         ("Renderer relief during seeks", state.seek_relief, commands.set_seek_relief)):
            self.checks.append(CardCheck(body, text=title, variable=variable, command=command, resized=self.layout))
        self.checks_row = _Rows(self, self.checks)
        row = self.options_row = RowLayout(body, scale=s, height=30, surface=t["panel"], plate="rgba",
                                           changed=self.layout)
        self.preview_frame_button = row.button("Preview frame", commands.apply_frame)
        row.gap(10)
        self.seek_button = row.button("Seek replay", commands.seek)
        row.gap(20)
        row.label("Updates / s")
        row.gap(10)
        self.rate_combo = _PlatedCombobox(body, textvariable=state.rate, values=RATES, state="readonly", width=1,
                                          style="Playback.TCombobox")
        self.rate_field = row.custom(FieldPlate(body, self.rate_combo, width=_dropdown_width(body, "120", 55)))
        self.options_strip = _Rows(self, [self.preview_frame_button, self.seek_button, self.rate_combo])
        # Console driver only: the app's poll packs and unpacks this row.
        row = self.smoothing_layout = RowLayout(body, scale=s, height=30, surface=t["panel"], plate="rgba",
                                                changed=self.layout)
        row.label("Console smoothing")
        row.gap(10)
        self.smoothing_combo = _PlatedCombobox(body, textvariable=state.smoothing, values=SMOOTHING,
                                               state="readonly", width=1, style="Playback.TCombobox")
        self.smoothing_field = row.custom(FieldPlate(body, self.smoothing_combo,
                                                     width=_dropdown_width(body, "Balanced", 100)))
        row.gap(12)
        row.label(SMOOTHING_NOTE, tone="dim")
        self.smoothing_row = _Rows(self, [self.smoothing_combo])
        disclosure.body.children = [self.checks_row, self.options_strip, self.smoothing_row]

        body.bind("<Configure>", self._configured, add="+")
        body.bind("<Destroy>", lambda e: (self._cancel(), self._unlink_speed()) if e.widget is body else None,
                  add="+")
        self.layout_now()

    # -- the speed dropdown ("1x" over the bare number) ------------------------------------------
    def _speed_from_app(self, *_args) -> None:
        value = self._speed_var.get()
        if speed_value(self.speed_text.get()) != str(value).strip():
            self.speed_text.set(speed_label(value))

    def _speed_from_field(self, *_args) -> None:
        value = speed_value(self.speed_text.get())
        if value != str(self._speed_var.get()).strip():
            self._speed_var.set(value)
        width = self._speed_width()
        field = getattr(self, "speed_field", None)
        if field is not None and width != field.width:
            field.width = width
            self.layout()

    def _speed_width(self) -> int:
        return _dropdown_width(self.timeline_body, self.speed_text.get(), 55)

    def _unlink_speed(self) -> None:
        traces, self._speed_traces = self._speed_traces, ()
        for variable, trace in zip((self._speed_var, self.speed_text), traces):
            try:
                variable.trace_remove("write", trace)
            except tk.TclError:
                pass

    def buttons(self):
        """The five canvas buttons in reading order (tests, QA)."""
        return [self.play_button, self.pause_button, self.stop_button, self.preview_frame_button, self.seek_button]

    def _option_widgets(self):
        return [*self.checks, self.rate_combo, self.smoothing_combo,
                *(group.proxy for group in (*self.options_row.groups, *self.smoothing_layout.groups))]

    # -- the host's picture ---------------------------------------------------------------------
    def card_backdrop(self, image, x=0, y=0) -> None:
        """The card's background picture and where this body sits in the card (px); None: plain ``panel``.

        Called by the host card whenever the card's size or the body's place in it changes.
        """
        art_ = (image, int(x), int(y)) if image is not None else None
        if art_ == self._art:
            return
        self._art = art_
        self._show_art()

    def _show_art(self):
        if self._art is None:
            for canvas in (self.timeline_body, self.slider, self.playback_disclosure.toggle, *self.checks):
                show_art(canvas, None, 0, 0)
            return
        image, ox, oy = self._art
        show_art(self.timeline_body, image, -ox, -oy)
        for canvas in (self.slider, self.playback_disclosure.toggle, *self.checks):
            placed = canvas.place_info()
            if placed:
                show_art(canvas, image, -ox - int(placed["x"]), -oy - int(placed["y"]))

    # -- layout ---------------------------------------------------------------------------------
    def layout(self, *_args) -> None:
        """Lay the rows out at idle (coalesced)."""
        if self._job is None:
            try:
                self._job = self.timeline_body.after_idle(self.layout_now)
            except tk.TclError:
                self._job = None

    def _cancel(self):
        if self._job is not None:
            try:
                self.timeline_body.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _configured(self, event):
        if event.widget is self.timeline_body and event.width != (self._size or (0,))[0]:
            self._size = (event.width, event.height)
            self.layout_now()

    @staticmethod
    def _place(widget, x, y, width=None, height=None):
        options = {"x": int(x), "y": int(y)}
        if width is not None:
            options["width"] = max(1, int(width))
        if height is not None:
            options["height"] = max(1, int(height))
        current = widget.place_info()
        if not current or any(str(current.get(key)) != str(value) for key, value in options.items()):
            widget.place_forget()
            widget.place(**options)

    def layout_now(self) -> None:
        self._cancel()
        body, s = self.timeline_body, self.scale
        width = body.winfo_width()
        if width <= 1:
            width = px(s, 888)               # not laid out yet: the design card's content width
        row_h, gap = px(s, 30), px(s, 10)
        # Shot time: slider · 10 · field · 5 · "s" (the unit ends the row).
        unit_w = self.unit.natural_width()
        field_x = width - unit_w - px(s, 5) - self.time_field.width
        slider_h = px(s, 12)
        self._place(self.slider, 0, (row_h - slider_h) // 2, max(1, field_x - gap), slider_h)
        self.time_field.place(field_x, 0)
        self.unit.place(width - unit_w, row_h // 2, anchor="w")
        y = row_h + gap
        self.controls_row.layout(0, y, width, proxy=(0, 0))
        y += row_h + px(s, 15)
        toggle = self.playback_disclosure.toggle
        self._place(toggle, 0, y)
        y += px(s, 18)
        disclosure = self.playback_disclosure
        if disclosure.opened:
            y += px(s, 15)
            x = 0
            for index, check in enumerate(self.checks):
                x += px(s, 20) if index else 0
                self._place(check, x, y)
                x += int(check.cget("width"))
            y += px(s, 18) + gap
            self.options_row.layout(0, y, width, proxy=(0, 0))
            y += row_h
            if self.smoothing_row.shown:
                y += gap
                self.smoothing_layout.layout(0, y, width, proxy=(0, 0))
                y += row_h
            else:
                self.smoothing_layout.hide()
        else:
            for check in self.checks:
                check.place_forget()
            self.options_row.hide()
            self.smoothing_layout.hide()
        if int(body.cget("height")) != y:
            body.configure(height=y)
        self._show_art()


def _dropdown_width(widget, widest, design_px) -> int:
    """A kit dropdown's width in px: ``design_px``, or wider so ``widest`` fits beside the chevron."""
    s = ui_scale(widget)
    font = (ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"])   # the value face ("1x": Inter Medium in Figma)
    measured = int(widget.tk.call("font", "measure", font, widest))
    # 13 px in, 8 px to the 8 px chevron, 13 px out (Dropdown [design reference]).
    return px(s, max(design_px, math.ceil((measured + px(s, 42)) / s)))


__all__ = ["CardCheck", "CardDisclosure", "CardToggle", "FieldPlate", "PlaybackActions", "PlaybackCard", "SPEED_SUFFIX",
           "PlaybackState", "RATES", "SMOOTHING", "SMOOTHING_NOTE", "SPEEDS", "install_styles", "show_art",
           "speed_label", "speed_value"]
