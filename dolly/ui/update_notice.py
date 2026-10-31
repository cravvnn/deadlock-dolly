"""The update notice under the page header (Figma page [design reference], section 5: [design reference], [design reference], [design reference]).

Kara's "Update banner" ([design reference]): a dark card with a thin accent edge and rounded corners, an
accent badge with the download glyph, the title "Dolly <version> is available" (VALVE Oracle 14)
over a muted detail line, and the kit buttons Later (quiet), What's changed (secondary) and Update
now (primary) at the right. Its states ([design reference] downloading ... [design reference] updater could not start)
change only the detail line and the Update now button; an updater that could not start shows the
detail in the error colour. "What's changed" turns into "Hide changes" and opens the release
highlights inside the same card ([design reference]): a pane with the RELEASE HIGHLIGHTS eyebrow and Full
release notes, then one card per note in two independent columns (note i in column i % 2), each
with its title and Before / After rows; a release without short notes shows one line instead
([design reference]). The pane shows every note in full and scrolls only when it is taller than the room the
page leaves it. After Later the banner collapses into the "Update available · <version>" reminder
in the page header, left of the game status ([design reference]); clicking it reopens the banner.

Everything is canvas-drawn (no ttk widget); the canvases paint the window's backdrop under their
rounded corners, like the stage's own controls. The banner and the reminder fade in when they
appear (USER-ADDITIONS UPDATE 30): 220 ms, ease-out, on the toplevel's shared
:class:`.tween.Animator`, with real alpha (a layered child window, Windows 8 and later). With motion
off, a window that is not viewable or no layering they appear at once. Opening and closing the
highlights open and close with a restrained, monotonic ease over 180 ms on the same animator.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import font as tkfont

from .. import ui_theme
from . import art, fonts, tween
from .backdrop import Backdrop
from .kit_controls import CanvasButton, FocusGroup
from .scale import display_scale, hairline, px
from .stage_page import CanvasRail
from .widgets import WHEEL_PIXELS_PER_NOTCH

FADE_MS = 220           # the banner and the reminder fade in (~200-250 ms, ease-out)
NOTES_MS = 180          # the highlights open and close


def _notes_ease(t):
    """Smoothly start and stop the pane without overshooting its target."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)

# Update banner [design reference], design px. 70 tall with a one-line detail: padding 16 (+ the 1 px edge),
# 20 on the right; badge, text block and buttons centred in the height.
BANNER_HEIGHT = 70
CARD_RADIUS = 6
INSET = 17              # badge left: 16 padding + the edge
RIGHT_INSET = 21        # buttons' right end: 20 padding + the edge
BADGE = 36
BADGE_RADIUS = 4
ICON_INSET = 9          # "Icon / Update", 18 px, centred in the badge
ICON_BOX = 18
# Its vector ([design reference]): 12 x 13 at (3, 2.5) in the box, stroked 1.6 with round caps and joins.
ICON_LINES = (((6, 0), (6, 9)), ((2, 5.5), (6, 9.5), (10, 5.5)), ((0, 13), (12, 13)))
ICON_ORIGIN = (3, 2.5)
ICON_STROKE = 1.6
TEXT_X = 67             # badge + 14
TEXT_GAP = 14           # text column to the buttons
TEXT_MARGIN = 31        # banner height minus the text block (70 - 39): the block stays centred
TITLE_SIZE, TITLE_LINE, TITLE_GAP, DETAIL_LINE = 14, 18, 5, 16
BUTTON_GAP = 10
BUTTON_HEIGHT = 30
# Release highlights [design reference]: the pane sits 20 px in from the card's sides and bottom.
PANE_X = 21             # 20 + the card's edge
PANE_BOTTOM = 21
PANE_PAD = 17           # 16 padding + the pane's 1 px edge
HEADER = 30
HEADER_GAP = 16
EYEBROW = "RELEASE HIGHLIGHTS"
EYEBROW_SIZE, EYEBROW_TRACKING = 11, .06       # Inter SemiBold 11, 6 % letter spacing
COLUMN_GAP = 16
CARD_GAP = 10
NOTE_PAD = 17           # 16 padding + the note card's edge
NOTE_GAP = 12           # between a note's title, Before and After rows
LINE = 18               # note titles and rows: 12 / 18
TAG_SIZE, TAG_LINE, TAG_PAD_Y, TAG_PAD_X, TAG_RADIUS, TAG_GAP = 10, 14, 2, 6, 3, 10
# Before / After rows: (label, tag fill, ink of the tag text and of the row's text).
TAGS = (("Before:", "edge", "muted"), ("After:", "accent", "text"))
FALLBACK = "Short notes aren't available for this release. You can still read the full release notes."
REMINDER_PAD = 13       # the reminder hugs its label like a kit button

_GWL_EXSTYLE, _WS_EX_LAYERED, _LWA_ALPHA = -20, 0x00080000, 0x2
_RDW_REPAINT = 0x0001 | 0x0004 | 0x0080        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN
_user32 = None          # user32 once loaded; False where layered child windows are unavailable


def _wrap(font, text, width) -> list[str]:
    """``text`` in lines no wider than ``width`` px, broken at spaces (an overlong word is cut)."""
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = word if not line else f"{line} {word}"
            if font.measure(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while len(word) > 1 and font.measure(word) > width:
                cut = _fit(font, word, width)
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _fit(font, word, width) -> int:
    """How many leading characters of ``word`` fit in ``width`` px (at least one)."""
    low, high = 1, len(word)
    while low < high:
        middle = (low + high + 1) // 2
        if font.measure(word[:middle]) <= width:
            low = middle
        else:
            high = middle - 1
    return low


def _top(font, line_px) -> int:
    """Where a Figma line box ``line_px`` tall puts the top of ``font``'s glyph box (Tk's nw anchor)."""
    return (line_px - int(font.metrics("linespace"))) // 2


def _layering():
    global _user32
    if _user32 is None:
        _user32 = False
        if sys.platform == "win32" and sys.getwindowsversion()[:2] >= (6, 2):
            try:
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
                user32.RedrawWindow.argtypes = (wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)
                user32.RedrawWindow.restype = wintypes.BOOL
                _user32 = user32
            except (AttributeError, OSError):
                _user32 = False
    return _user32


class _Fade:
    """Real alpha for one child window while it fades in: a layered child window (Windows 8 and later).

    :meth:`begin` makes the window layered and invisible before it maps, :meth:`alpha` sets its
    opacity (0..1) and :meth:`end` makes it opaque and a plain window again. Where Windows refuses a
    layered child window, or with ``DOLLY_UI_LAYERED=0``, :meth:`begin` returns False and the window
    simply shows.
    """

    def __init__(self, widget):
        self.widget = widget
        self.hwnd = None

    @property
    def active(self) -> bool:
        return self.hwnd is not None

    def begin(self) -> bool:
        if self.hwnd is not None:
            return True
        if os.environ.get("DOLLY_UI_LAYERED", "1") == "0":
            return False
        user32 = _layering()
        if not user32:
            return False
        try:
            hwnd = self.widget.winfo_id()
            style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED)
            if not (user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_LAYERED
                    and user32.SetLayeredWindowAttributes(hwnd, 0, 0, _LWA_ALPHA)):
                user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style & ~_WS_EX_LAYERED)
                return False
        except (AttributeError, OSError, ValueError, TypeError, tk.TclError):
            return False
        self.hwnd = hwnd
        return True

    def alpha(self, value) -> None:
        if self.hwnd is not None:
            try:
                _layering().SetLayeredWindowAttributes(self.hwnd, 0, max(0, min(255, round(255 * value))),
                                                       _LWA_ALPHA)
            except (AttributeError, OSError, ValueError, TypeError):
                pass

    def end(self) -> None:
        """Opaque, and a plain child window again (Windows frees the layer and repaints it)."""
        hwnd, self.hwnd = self.hwnd, None
        if hwnd is None:
            return
        user32 = _layering()
        try:
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)
            user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & ~_WS_EX_LAYERED)
            user32.RedrawWindow(hwnd, None, None, _RDW_REPAINT)
        except (AttributeError, OSError, ValueError, TypeError):
            pass


class _Faces:
    """The notice's text faces in px at the display scale, following the faces Dolly resolved.

    The title takes the card titles' face (VALVE Oracle SemiBold when the game's fonts are
    available, else Inter SemiBold) at 14 px; note titles Inter Medium 12, the eyebrow Inter
    SemiBold 11, tags Inter 10 and every other text the body face (Inter 12). Without Inter they
    fall back like the rest of Dolly.
    """

    def __init__(self, widget, scale):
        self.widget, self.scale = widget, scale
        self.body = fonts.font(widget, "body")
        self.title = tkfont.Font(root=widget)
        self.value = tkfont.Font(root=widget)
        self.eyebrow = tkfont.Font(root=widget)
        self.tag = tkfont.Font(root=widget)
        self.update()

    def update(self) -> None:
        s = self.scale
        card = fonts.font(self.widget, "card_title")
        self.title.configure(family=card.cget("family"), weight=card.cget("weight"), size=-px(s, TITLE_SIZE))
        self.value.configure(family=ui_theme.FONT_VALUE, size=-px(s, 12))
        family, weight = self._semibold()
        self.eyebrow.configure(family=family, weight=weight, size=-px(s, EYEBROW_SIZE))
        self.tag.configure(family=ui_theme.FONT_FAMILY, size=-px(s, TAG_SIZE))

    def _semibold(self):
        """Inter SemiBold, or the card titles' system fallback (as ``fonts`` would choose it)."""
        families = set(tkfont.families(self.widget))
        forced = os.environ.get("DOLLY_UI_FONTS", "").strip().lower() == "fallback"
        candidates = [spec for spec in ui_theme.DISPLAY_FONTS["card_title"] if not spec[0].startswith("VALVE")]
        for family, _size, style in candidates:
            if forced and family.startswith(ui_theme.UI_FACE):
                continue
            if family in families:
                return family, "bold" if "bold" in style.split() else "normal"
        family, _size, style = candidates[-1]
        return family, "bold" if "bold" in style.split() else "normal"


class _Lines:
    """Text drawn one canvas item per line, on Figma's line height (Tk spaces lines by the font's)."""

    def __init__(self, canvas, font, fill, *, tags=()):
        self.canvas, self.font, self.fill, self.tags = canvas, font, fill, tags
        self.items = []
        self.lines = []

    def draw(self, lines, x, y, line_px) -> int:
        """Show ``lines`` from (``x``, ``y``), ``line_px`` apart; the height they take."""
        self.lines = list(lines)
        c, top = self.canvas, _top(self.font, line_px)
        while len(self.items) < len(self.lines):
            self.items.append(c.create_text(0, 0, text="", anchor="nw", font=self.font, fill=self.fill,
                                            tags=self.tags))
        for index, item in enumerate(self.items):
            if index < len(self.lines):
                c.coords(item, x, y + index * line_px + top)
                c.itemconfigure(item, text=self.lines[index], state="normal")
            else:
                c.itemconfigure(item, text="", state="hidden")
        return len(self.lines) * line_px

    def set_fill(self, fill) -> None:
        if fill != self.fill:
            self.fill = fill
            for item in self.items:
                self.canvas.itemconfigure(item, fill=fill)

    def hide(self) -> None:
        for item in self.items:
            self.canvas.itemconfigure(item, state="hidden")


class _Plate:
    """A rounded rectangle as canvas items: fill, an optional 1 px edge and anti-aliased corners.

    ``surface`` precomposes the corners over that colour (a plate on a card or on the pane);
    without it they are transparent outside (over the backdrop). ``top=False``/``bottom=False``
    leave that side square and open: the fill and the side edges run on to it (the card goes on
    in the next canvas).
    """

    CORNERS = ("nw", "ne", "sw", "se")

    def __init__(self, canvas, *, fill, radius, edge=None, surface=None, line=1, tags=()):
        self.canvas = canvas
        self.root = canvas.winfo_toplevel()
        self.fill, self.edge, self.surface = fill, edge, surface
        self.radius, self.line = max(1, int(radius)), max(1, int(line))
        self.box = None

        def rectangle(colour):
            return canvas.create_rectangle(0, 0, 0, 0, fill=colour, width=0, state="hidden", tags=tags)
        self.fills = [rectangle(fill), rectangle(fill)]
        self.edges = [rectangle(edge) for _ in range(4)] if edge else []      # top, bottom, left, right
        self.corners = [canvas.create_image(0, 0, anchor="nw", state="hidden", tags=tags) for _ in self.CORNERS]

    def faces(self, fill=None):
        """The four corner images in ``fill`` (cached by :mod:`.art`)."""
        fill = self.fill if fill is None else fill
        return tuple(art.corner_face(self.root, self.radius, fill, which, outline=self.edge, surface=self.surface)
                     for which in self.CORNERS)

    def place(self, x0, y0, x1, y1, *, top=True, bottom=True) -> None:
        c, r, n = self.canvas, self.radius, self.line
        rt, rb = (r if top else 0), (r if bottom else 0)
        side = n if self.edge else 0
        et, eb = (side if top else 0), (side if bottom else 0)
        c.coords(self.fills[0], x0 + r, y0 + et, x1 - r, y1 - eb)
        c.coords(self.fills[1], x0 + side, y0 + rt, x1 - side, y1 - rb)
        for item in self.fills:
            c.itemconfigure(item, state="normal")
        if self.edges:
            spans = ((x0 + r, y0, x1 - r, y0 + n, top), (x0 + r, y1 - n, x1 - r, y1, bottom),
                     (x0, y0 + rt, x0 + n, y1 - rb, True), (x1 - n, y0 + rt, x1, y1 - rb, True))
            for item, (a, b, e, d, shown) in zip(self.edges, spans):
                c.coords(item, a, b, e, d)
                c.itemconfigure(item, state="normal" if shown else "hidden")
        spots = ((x0, y0, top), (x1 - r, y0, top), (x0, y1 - r, bottom), (x1 - r, y1 - r, bottom))
        for item, image, (x, y, shown) in zip(self.corners, self.faces(), spots):
            c.coords(item, x, y)
            c.itemconfigure(item, image=image, state="normal" if shown else "hidden")
        self.box = (x0, y0, x1, y1)

    def paint(self, fill, faces=None) -> None:
        """Another fill (a hover ramp); ``faces`` are the corners already built for it."""
        self.fill = fill
        for item in self.fills:
            self.canvas.itemconfigure(item, fill=fill)
        for item, image in zip(self.corners, faces or self.faces(fill)):
            self.canvas.itemconfigure(item, image=image)

    def raise_end(self) -> None:
        """Bottom edge and corners over everything drawn later (the card's end cuts the pane)."""
        for item in ((self.edges[1],) if self.edges else ()) + tuple(self.corners[2:]):
            self.canvas.tag_raise(item)

    def hide(self) -> None:
        for item in (*self.fills, *self.edges, *self.corners):
            self.canvas.itemconfigure(item, state="hidden")
        self.box = None


class _Reminder(tk.Canvas):
    """The "Update available · <version>" button ([design reference]): a kit secondary button, accent edge.

    30 design px tall, hugging its label + 13 px each side; its fill eases to the kit's hover and
    pressed colours and it dips a pixel while pressed. It takes focus (nothing is drawn for it);
    Space and Return click it. Its corners show the backdrop.
    """

    def __init__(self, master, *, command, scale):
        t = ui_theme.TOKENS
        super().__init__(master, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=1,
                         width=1, height=px(scale, BUTTON_HEIGHT), cursor="hand2")
        self.scale = scale
        self.command = command
        self._text = ""
        self._hover = self._pressed = False
        self._dip = 0
        self._font = fonts.font(self, "body")
        self.plate = _Plate(self, fill=t["button"], radius=max(1, round(4 * scale)), edge=t["accent"],
                            line=hairline(scale))
        self.label = self.create_text(0, 0, text="", font=self._font, fill=t["text"], anchor="center")
        self.ramp = tween.ColorRamp(self, self._paint, t["button"], t["button_hover"], pressed=t["button_active"],
                                    images=self.plate.faces)
        self.bind("<Enter>", self._entered, add="+")
        self.bind("<Leave>", self._left, add="+")
        self.bind("<ButtonPress-1>", self._pressed_down, add="+")
        self.bind("<ButtonRelease-1>", self._released, add="+")
        for key in ("<space>", "<Return>", "<KP_Enter>"):
            self.bind(key, self._key, add="+")
        self.bind("<Configure>", self._configured, add="+")
        self.backdrop = Backdrop.of(master)
        if self.backdrop is not None:
            self.backdrop.attach(self, self._corner_areas)
        self._unfont = fonts.on_change(self, self._fit)
        self.bind("<Destroy>", lambda event: self._unfont() if event.widget is self else None, add="+")
        self._fit()

    def _corner_areas(self):
        w, h, r = self.winfo_width(), self.winfo_height(), self.plate.radius
        return [(0, 0, r, r), (w - r, 0, w, r), (0, h - r, r, h), (w - r, h - r, w, h)]

    def _paint(self, colour):
        self.plate.paint(colour, self.ramp.image(colour))

    def _fit(self):
        width = self._font.measure(self._text) + 2 * px(self.scale, REMINDER_PAD)
        if int(tk.Canvas.cget(self, "width")) != width:
            tk.Canvas.configure(self, width=width)
        self._draw()

    def _draw(self):
        width, height = int(tk.Canvas.cget(self, "width")), px(self.scale, BUTTON_HEIGHT)
        self.plate.place(0, self._dip, width, height + self._dip)
        self.coords(self.label, width // 2, height // 2 + self._dip)
        self.itemconfigure(self.label, text=self._text)

    def _configured(self, _event=None):
        if self.backdrop is not None:
            self.backdrop.sync(self, redraw=True)

    def _set_dip(self, dipped):
        dip = max(1, px(self.scale, 1)) if dipped else 0
        if dip != self._dip:
            self._dip = dip
            self._draw()

    def _entered(self, _event=None):
        self._hover = True
        self.ramp.enter()

    def _left(self, _event=None):
        self._hover = False
        if not self._pressed:
            self.ramp.leave()

    def _pressed_down(self, _event=None):
        self._pressed = True
        self._set_dip(True)
        self.ramp.press()

    def _released(self, event=None):
        was, self._pressed = self._pressed, False
        self._set_dip(False)
        inside = event is not None and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._hover = inside
        if inside:
            self.ramp.release()
        else:
            self.ramp.leave()
        if was and inside:
            self.invoke()

    def _key(self, _event=None):
        self.invoke()
        return "break"

    def invoke(self):
        return self.command() if self.command is not None else None

    def configure(self, cnf=None, **options):
        if isinstance(cnf, str):
            return self.cget(cnf) if cnf in ("text", "command") else tk.Canvas.configure(self, cnf)
        if cnf:
            options.update(cnf)
        if "command" in options:
            self.command = options.pop("command")
        if "text" in options:
            text = str(options.pop("text"))
            if text != self._text:
                self._text = text
                self._fit()
        if options:
            return tk.Canvas.configure(self, **options)
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "command":
            return self.command
        return tk.Canvas.cget(self, key)

    __getitem__ = cget


class UpdateNotice:
    """Kara's update banner, its release highlights and the header reminder (see the module doc).

    ``parent`` is the stage's notice host (any grid-managed frame works; the banner takes row 0).
    When ``parent``'s master is the stage (it has ``set_header_widget``) the reminder goes to the
    header row, else it is gridded below in the parent. ``height_limit()`` is the room in px the
    open highlights may take (the page area they push down); without it they take at most
    :attr:`MAX_NOTES_HEIGHT` design px.
    """

    FADE_MS = FADE_MS
    NOTES_MS = NOTES_MS
    MAX_NOTES_HEIGHT = 480      # design px: Figma's pane with four notes is 478 below the banner

    def __init__(self, parent, *, update, notes, full_notes, later, reopen, height_limit=None):
        self.parent = parent
        self.height_limit = height_limit
        self.scale = display_scale(parent.tk)
        s, t = self.scale, ui_theme.TOKENS
        self.line = hairline(s)
        self.notes_open = False
        self.title_text = ""
        self.detail_text = ""
        self.error = False
        self.note_layout = []           # per note: (column, box, title lines, rows) as last drawn
        self._notes_release = None
        self._notes_content = None
        self._notes_height = 0          # the highlights' height now (px), eased
        self._notes_target = 0          # the height they head for
        self._content_height = 0        # the note cards (or the fallback line), px
        self._view = (0, 0, 0, 0)       # notes viewport: x, y, width, height for the target
        self._built_width = None        # viewport width the cards were drawn for
        self._laid_width = None         # banner width of the last layout
        self._shown = False             # the banner is up
        self._reminder_shown = False
        self._destroyed_flag = False
        stage = getattr(parent, "master", None)
        self._stage = stage if hasattr(stage, "set_header_widget") else None
        self.frame = tk.Frame(parent, background=t["bg"], highlightthickness=0, borderwidth=0)
        self.frame.columnconfigure(0, weight=1)
        self.faces = _Faces(self.frame, s)

        # -- the banner: card top, badge and glyph, title and detail, the three kit buttons
        self.banner = tk.Canvas(self.frame, background=t["bg"], highlightthickness=0, borderwidth=0, takefocus=0,
                                width=1, height=self._dp(BANNER_HEIGHT))
        self.banner.grid(row=0, column=0, sticky="ew")
        self._card = _Plate(self.banner, fill=t["panel"], radius=self._dp(CARD_RADIUS), edge=t["accent"],
                            line=self.line)
        self._badge = _Plate(self.banner, fill=t["accent"], radius=self._dp(BADGE_RADIUS), surface=t["panel"])
        self._icon_image = self._glyph()
        self._icon = self.banner.create_image(0, 0, anchor="nw", image=self._icon_image, state="hidden")
        self._title = _Lines(self.banner, self.faces.title, t["text"])
        self._detail = _Lines(self.banner, self.faces.body, t["muted"])
        self.later_button = CanvasButton(self.banner, text="Later", command=later, kind="quiet", scale=s,
                                         surface=t["panel"])
        self.notes_button = CanvasButton(self.banner, text="What's changed", command=notes, kind="secondary",
                                         scale=s, surface=t["panel"])
        self.update_button = CanvasButton(self.banner, text="Update now", command=update, kind="primary", scale=s,
                                          surface=t["panel"])
        self._focus = FocusGroup(None, (self.later_button, self.notes_button, self.update_button),
                                 canvas=self.banner)
        self._focus.proxy.configure(background=t["panel"])
        for button in (self.later_button, self.notes_button, self.update_button):
            button.on_resize = self._layout_now

        # -- the highlights: the card going on, the pane, its header and the scrolling notes
        self.notes_clip = tk.Canvas(self.frame, background=t["bg"], highlightthickness=0, borderwidth=0,
                                    takefocus=0, width=1, height=1)
        self._body = _Plate(self.notes_clip, fill=t["panel"], radius=self._dp(CARD_RADIUS), edge=t["accent"],
                            line=self.line)
        self._pane = _Plate(self.notes_clip, fill=t["notes_pane"], radius=self._dp(CARD_RADIUS),
                            edge=t["card_edge"], surface=t["panel"], line=self.line)
        self._eyebrow = [self.notes_clip.create_text(0, 0, text=char, anchor="nw", font=self.faces.eyebrow,
                                                     fill=t["muted"], state="hidden") for char in EYEBROW]
        self.full_notes_button = CanvasButton(self.notes_clip, text="Full release notes", command=full_notes,
                                              kind="secondary", scale=s, surface=t["notes_pane"])
        self.full_notes_button.on_resize = self._layout_now
        self._notes_focus = FocusGroup(None, (self.full_notes_button,), canvas=self.notes_clip)
        self._notes_focus.proxy.configure(background=t["notes_pane"])
        self._body.raise_end()
        self.notes_view = tk.Canvas(self.notes_clip, background=t["notes_pane"], highlightthickness=0,
                                    borderwidth=0, takefocus=0, width=1, height=1, yscrollincrement=1)
        self.rail = CanvasRail(self.notes_view, self._rail_command, surface="notes_pane")
        self.notes_view.configure(yscrollcommand=self._view_scrolled)
        self.scroller = tween.WheelScroller(self.notes_view, self._scroll_units)
        for widget in (self.notes_view, self.notes_clip):
            widget.bind("<MouseWheel>", self._wheel, add="+")

        # -- the reminder, the fades and the backdrop under the rounded corners
        self.reminder = _Reminder(self._stage if self._stage is not None else parent, command=reopen, scale=s)
        self._banner_fade = _Fade(self.frame)
        self._reminder_fade = _Fade(self.reminder)
        self._backdrop = Backdrop.of(parent)
        if self._backdrop is not None:
            for canvas in (self.banner, self.notes_clip):
                self._backdrop.attach(canvas, self._corner_areas(canvas))
        self.banner.bind("<Configure>", self._banner_configured, add="+")
        self.notes_clip.bind("<Configure>", lambda _event: self._sync_backdrop(), add="+")
        parent.bind("<Configure>", lambda _event: self._sync_backdrop(), add="+")
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self._unfont = fonts.on_change(self.frame, self._fonts_changed)

    # -- helpers ---------------------------------------------------------------------------------

    def _dp(self, value):
        """Design pixels at the notice's display scale."""
        return px(self.scale, value)

    def _glyph(self):
        """The download glyph ([design reference]) in its 18 px box, white, opaque over the badge."""
        s = self.scale
        size = self._dp(ICON_BOX)
        ox, oy = ICON_ORIGIN
        lines = tuple(tuple(((x + ox) * s, (y + oy) * s) for x, y in line) for line in ICON_LINES)
        return art.stroke_rgba(self.frame, size, size, lines, ICON_STROKE * s, "text", surface="accent")

    def _corner_areas(self, canvas):
        def areas():
            w, h, r = canvas.winfo_width(), canvas.winfo_height(), self._dp(CARD_RADIUS)
            return [(0, 0, r, r), (w - r, 0, w, r), (0, h - r, r, h), (w - r, h - r, w, h)]
        return areas

    def _sync_backdrop(self):
        if self._backdrop is not None and not self._destroyed_flag:
            for canvas in (self.banner, self.notes_clip):
                self._backdrop.sync(canvas)

    def _width(self) -> int:
        """The banner's width, or the page column's before it is laid out (the stage gives it)."""
        try:
            width = self.banner.winfo_width()
            if width > 1:
                return width
            if self._stage is not None and hasattr(self._stage, "page_rect"):
                left, _top, right, _bottom = self._stage.page_rect()
                if right - left > 1:
                    return right - left
            width = self.parent.winfo_width()
            if width > 1:
                return width
            # Tk does not assign a placed widget its geometry until its toplevel maps.
            # Use the explicit place width while preparing a hidden preview or test.
            if self.parent.winfo_manager() == "place":
                placed_width = self.parent.place_info().get("width", "")
                if placed_width:
                    return int(float(placed_width))
            return width
        except tk.TclError:
            return 0

    def _chrome(self) -> int:
        """The highlights' height around the notes viewport: pane header and paddings, card end.

        The pane starts a line below the banner: in [design reference] the banner (70) sits inside the
        card's 1 px edge, so the pane wrap begins at 71.
        """
        dp = self._dp
        return self.line + dp(PANE_PAD) + dp(HEADER) + dp(HEADER_GAP) + dp(PANE_PAD) + dp(PANE_BOTTOM)

    def _clip_shown(self) -> bool:
        try:
            return bool(self.notes_clip.winfo_manager())
        except tk.TclError:
            return False

    # -- layout -----------------------------------------------------------------------------------

    def _banner_configured(self, event):
        if event.width != self._laid_width:
            self._layout_now()
        self._sync_backdrop()

    def _fonts_changed(self):
        if self._destroyed_flag:
            return
        self.faces.update()
        self._built_width = None
        self._layout_now()

    def _layout_now(self):
        if self._destroyed_flag:
            return
        width = self._width()
        if width <= 1:
            return
        self._laid_width = width
        self._layout_banner(width)
        rebuilt = self._ensure_notes(width)
        self._layout_notes(width)
        if rebuilt and self.notes_open:
            self.fit_height()           # rewrapped cards: the open highlights take their new height

    def _view_width(self, width) -> int:
        """The notes viewport's width in a card ``width`` px wide (854 at 930)."""
        dp = self._dp
        return max(dp(120), width - 2 * dp(PANE_X) - 2 * dp(PANE_PAD))

    def _ensure_notes(self, width) -> bool:
        """Draw the cards for this width unless they are drawn already; True when they were drawn now."""
        view_w = self._view_width(width)
        if view_w == self._built_width:
            return False
        self._build_notes(view_w)
        return True

    def _layout_banner(self, width):
        c, dp, t = self.banner, self._dp, ui_theme.TOKENS
        x, spots = width - dp(RIGHT_INSET), []
        for button in (self.update_button, self.notes_button, self.later_button):
            x -= button.winfo_reqwidth()
            spots.append((button, x))
            x -= dp(BUTTON_GAP)
        text_x = dp(TEXT_X)
        text_w = max(dp(120), x + dp(BUTTON_GAP) - dp(TEXT_GAP) - text_x)
        title = _wrap(self.faces.title, self.title_text, text_w)
        detail = _wrap(self.faces.body, self.detail_text, text_w)
        text_h = len(title) * dp(TITLE_LINE) + dp(TITLE_GAP) + len(detail) * dp(DETAIL_LINE)
        # Round the complete design height once. Rounding each line, gap and
        # margin separately made the nominal 70 px banner 106 px at 150%,
        # one pixel taller than the intended 105 px.
        design_height = (len(title) * TITLE_LINE + TITLE_GAP + len(detail) * DETAIL_LINE + TEXT_MARGIN)
        height = max(dp(BANNER_HEIGHT), dp(design_height))
        if int(c.cget("height")) != height:
            c.configure(height=height)
        self._card.place(0, 0, width, height, bottom=not self._clip_shown())
        top = (height - dp(BADGE) + 1) // 2
        self._badge.place(dp(INSET), top, dp(INSET) + dp(BADGE), top + dp(BADGE))
        c.coords(self._icon, dp(INSET) + dp(ICON_INSET), top + dp(ICON_INSET))
        c.itemconfigure(self._icon, state="normal")
        y = (height - text_h) // 2
        self._title.draw(title, text_x, y, dp(TITLE_LINE))
        self._detail.set_fill(t["status_error"] if self.error else t["muted"])
        self._detail.draw(detail, text_x, y + len(title) * dp(TITLE_LINE) + dp(TITLE_GAP), dp(DETAIL_LINE))
        button_y = (height - dp(BUTTON_HEIGHT)) // 2
        for button, bx in spots:
            button.place(bx, button_y)
        self._focus.place(dp(4), height // 2)

    def _layout_notes(self, width):
        dp, n = self._dp, self.line
        pane_x0, pane_x1 = dp(PANE_X), width - dp(PANE_X)
        view_x, view_w = pane_x0 + dp(PANE_PAD), self._built_width or self._view_width(width)
        header_y = n + dp(PANE_PAD)
        view_top = header_y + dp(HEADER) + dp(HEADER_GAP)
        view_h = max(0, min(self._content_height, self._notes_target - self._chrome()))
        self._view = (view_x, view_top, view_w, view_h)
        self._pane.place(pane_x0, n, pane_x1, view_top + view_h + dp(PANE_PAD))
        self._draw_eyebrow(pane_x0 + dp(PANE_PAD), header_y + _top(self.faces.eyebrow, dp(HEADER)))
        button = self.full_notes_button
        button.place(pane_x1 - dp(PANE_PAD) - button.winfo_reqwidth(), header_y)
        self._notes_focus.place(pane_x0 + dp(4), header_y + dp(HEADER) // 2)
        gutter = dp(PANE_PAD) - n
        self.notes_view.configure(scrollregion=(0, 0, view_w + gutter, max(self._content_height, view_h)))
        if self._content_height > view_h > 0:
            self.rail.place(view_w + (dp(COLUMN_GAP) - self.rail.thickness) // 2, view_h)
            self.rail.follow()
        else:
            self.rail.place_forget()
        self._apply_height(self._notes_height)

    def _draw_eyebrow(self, x, y):
        """RELEASE HIGHLIGHTS one item per letter: Tk text has no letter spacing (Figma: 6 %)."""
        c, font = self.notes_clip, self.faces.eyebrow
        tracking = EYEBROW_TRACKING * EYEBROW_SIZE * self.scale
        for index, item in enumerate(self._eyebrow):
            c.coords(item, x + font.measure(EYEBROW[:index]) + round(index * tracking), y)
            c.itemconfigure(item, state="normal")

    def _build_notes(self, view_w):
        """Draw every note card (or the fallback line) for a viewport ``view_w`` px wide."""
        c, dp, t, faces = self.notes_view, self._dp, ui_theme.TOKENS, self.faces
        c.delete("note")
        self.note_layout = []
        self._built_width = view_w
        content = self._notes_content or ()
        if not content:
            lines = _wrap(faces.body, FALLBACK, view_w)
            self._content_height = _Lines(c, faces.body, t["muted"], tags=("note",)).draw(lines, 0, 0, dp(LINE))
            return
        column_w = (view_w - dp(COLUMN_GAP)) // 2
        xs, ys = (0, column_w + dp(COLUMN_GAP)), [0, 0]
        for index, note in enumerate(content):
            column = index % 2
            ys[column] += self._note_card(xs[column], ys[column], column_w, column, *note) + dp(CARD_GAP)
        self._content_height = max(ys) - dp(CARD_GAP)

    def _note_card(self, x, y, width, column, title, before, after) -> int:
        """One note ([design reference]): title, then a Before and an After row with their tags; its height."""
        c, dp, t, faces = self.notes_view, self._dp, ui_theme.TOKENS, self.faces
        tags = ("note",)
        pad, inner = dp(NOTE_PAD), width - 2 * dp(NOTE_PAD)
        title_lines = _wrap(faces.value, title, inner)
        chip_h = dp(TAG_LINE) + 2 * dp(TAG_PAD_Y)
        rows = []
        for (label, fill, ink), text in zip(TAGS, (before, after)):
            chip_w = faces.tag.measure(label) + 2 * dp(TAG_PAD_X)
            lines = _wrap(faces.body, text, max(dp(40), inner - chip_w - dp(TAG_GAP)))
            rows.append((label, fill, ink, chip_w, lines, max(chip_h, len(lines) * dp(LINE))))
        height = pad + len(title_lines) * dp(LINE) + sum(dp(NOTE_GAP) + row[-1] for row in rows) + pad
        plate = _Plate(c, fill=t["panel"], radius=dp(CARD_RADIUS), edge=t["card_edge"], surface=t["notes_pane"],
                       line=self.line, tags=tags)
        plate.place(x, y, x + width, y + height)
        _Lines(c, faces.value, t["text"], tags=tags).draw(title_lines, x + pad, y + pad, dp(LINE))
        row_y = y + pad + len(title_lines) * dp(LINE)
        drawn = []
        for label, fill, ink, chip_w, lines, row_h in rows:
            row_y += dp(NOTE_GAP)
            chip = _Plate(c, fill=t[fill], radius=dp(TAG_RADIUS), surface=t["panel"], tags=tags)
            chip.place(x + pad, row_y, x + pad + chip_w, row_y + chip_h)
            c.create_text(x + pad + dp(TAG_PAD_X), row_y + dp(TAG_PAD_Y) + _top(faces.tag, dp(TAG_LINE)), text=label,
                          anchor="nw", font=faces.tag, fill=t[ink], tags=tags)
            _Lines(c, faces.body, t[ink], tags=tags).draw(lines, x + pad + chip_w + dp(TAG_GAP), row_y, dp(LINE))
            drawn.append((label, chip.box, fill, ink, tuple(lines)))
            row_y += row_h
        self.note_layout.append((column, (x, y, x + width, y + height), tuple(title_lines), tuple(drawn)))
        return height

    # -- the highlights' height ---------------------------------------------------------------------

    def _target_height(self) -> int:
        requested = self._chrome() + self._content_height
        if self.height_limit is None:
            limit = round(self.MAX_NOTES_HEIGHT * self.scale)
        else:
            limit = max(1, int(self.height_limit()))
        return min(limit, max(1, requested))

    def _apply_height(self, value) -> None:
        """The highlights ``value`` px tall: the card's end follows, the notes stop above it."""
        height = max(0, int(round(value)))
        self._notes_height = height
        if self._destroyed_flag or not self._clip_shown():
            return
        if int(self.notes_clip.cget("height")) != max(1, height):
            self.notes_clip.configure(height=max(1, height))
        width = self._laid_width or self._width()
        self._body.place(0, 0, width, height, top=False)
        view_x, view_top, view_w, view_h = self._view
        visible = min(view_h, height - self.line - view_top)
        if visible > 0:
            self.notes_view.place(x=view_x, y=view_top, width=view_w + self._dp(PANE_PAD) - self.line,
                                  height=visible)
        else:
            self.notes_view.place_forget()

    def _animate_to(self, target) -> None:
        self._notes_target = target
        try:
            animator = tween.Animator.of(self.frame)
        except tk.TclError:
            return
        # Start at the displayed height, without jumping ahead a frame on the
        # click. This also keeps reversals continuous.
        animator.run(self._notes_key, self.NOTES_MS, self._apply_height, start=float(self._notes_height),
                     end=float(target), ease=_notes_ease, done=self._notes_settled, lead_frame=False)

    @property
    def _notes_key(self):
        return (id(self), "notes")

    def _notes_settled(self) -> None:
        if not self.notes_open and self._notes_height <= 0:
            self._remove_clip()

    def _remove_clip(self) -> None:
        self.notes_view.place_forget()
        if self._clip_shown():
            self.notes_clip.grid_remove()
            self._layout_now()          # the banner's bottom corners round again

    def _stop_notes(self) -> None:
        try:
            tween.Animator.of(self.frame).stop(self._notes_key)
        except tk.TclError:
            pass

    def fit_height(self):
        """Keep the open highlights within their room after a window resize."""
        if not self.notes_open or self._destroyed_flag:
            return
        target = self._target_height()
        if target != self._notes_target:
            self._notes_target = target
            self._layout_now()
            self._animate_to(target)

    def toggle_notes(self):
        if self._destroyed_flag:
            return
        self.notes_open = not self.notes_open
        self.notes_button.configure(text="Hide changes" if self.notes_open else "What's changed")
        if self.notes_open:
            self.scroller.stop()
            if not self._clip_shown():
                self._notes_height = 0
                self.notes_clip.configure(height=1)
                self.notes_clip.grid(row=1, column=0, sticky="ew")
            width = self._width()
            if width > 1:
                self._ensure_notes(width)
            self._notes_target = self._target_height()
            self._layout_now()
            self._animate_to(self._notes_target)
        else:
            self._animate_to(0)

    def close_notes(self):
        self._stop_notes()
        self.scroller.stop()
        self.notes_open = False
        self._notes_height = self._notes_target = 0
        self.notes_clip.configure(height=1)
        self.notes_button.configure(text="What's changed")
        self._remove_clip()

    # -- the notes' scrolling -------------------------------------------------------------------------

    def _scroll_units(self, units) -> int:
        c = self.notes_view
        before = c.canvasy(0)
        c.yview_scroll(int(units), "units")
        self.rail.follow()
        return int(round(c.canvasy(0) - before))

    def _rail_command(self, *args):
        self.scroller.stop()
        self.notes_view.yview(*args)
        self.rail.follow()

    def _view_scrolled(self, first, last):
        self.rail.set(first, last)

    def _wheel(self, event):
        first, last = self.notes_view.yview()
        if first <= 0.0 and last >= 1.0:
            return None
        notch = WHEEL_PIXELS_PER_NOTCH * self.scale
        delta = getattr(event, "delta", 0)
        pixels = -delta / 120 * notch if delta else (-notch if getattr(event, "num", 0) == 4 else notch)
        if (pixels < 0 and first <= 0.0) or (pixels > 0 and last >= 1.0):
            self.scroller.stop()
        else:
            self.scroller.add(pixels)
        return "break"

    # -- content ------------------------------------------------------------------------------------

    def set_notes(self, notes, *, release):
        content = tuple((item["title"], item["before"], item["after"]) for item in notes)
        if release == self._notes_release and content == self._notes_content:
            return
        if release != self._notes_release:
            self.close_notes()
        self._notes_release, self._notes_content = release, content
        self._built_width = None
        self.scroller.stop()
        width = self._width()
        if width > 1:
            self._laid_width = width
            self._ensure_notes(width)
            self._layout_notes(width)
        self.notes_view.yview_moveto(0)
        if self.notes_open:
            target = self._target_height()
            self._notes_target = target
            self._layout_now()
            self._animate_to(target)

    # -- showing and hiding ----------------------------------------------------------------------------

    def _fade_in(self, fade, show) -> None:
        """``show()`` the window, fading it in when motion runs and Windows layers it."""
        fading = not self._destroyed_flag and tween.smooth(self.parent) and fade.begin()
        show()
        if not fading:
            return
        try:
            tween.Animator.of(self.frame).run(fade, self.FADE_MS, fade.alpha, start=0.0, end=1.0, done=fade.end)
        except tk.TclError:
            fade.end()

    def _end_fade(self, fade) -> None:
        if fade.active:
            try:
                tween.Animator.of(self.frame).stop(fade)
            except tk.TclError:
                pass
            fade.end()

    def _hide_banner(self) -> None:
        self._end_fade(self._banner_fade)
        self.frame.grid_remove()
        self._shown = False

    def _place_reminder(self, shown) -> None:
        stage = self._stage
        if stage is not None:
            if shown:
                stage.set_header_widget(self.reminder)
            elif getattr(stage, "header_widget", None) is self.reminder:
                stage.set_header_widget(None)
        elif shown:
            self.reminder.grid(row=2, column=0, columnspan=3, sticky="e", pady=self._dp((8, 0)))
        else:
            self.reminder.grid_remove()

    def _hide_reminder(self) -> None:
        self._end_fade(self._reminder_fade)
        if self._reminder_shown:
            self._reminder_shown = False
            self._place_reminder(False)

    def show(self, version, detail, *, busy=False, error=False):
        """The banner for ``version``: ``detail`` (red when ``error``); ``busy``: Downloading..."""
        self.title_text = f"Dolly {version} is available"
        self.detail_text = str(detail)
        self.error = bool(error)
        self.update_button.configure(text="Downloading..." if busy else "Update now",
                                     state="disabled" if busy else "normal")
        self._hide_reminder()
        if self._shown:
            self._layout_now()
            return
        self._shown = True

        def appear():
            # Row 0, over the host's empty keeper frame (made first, so it stays underneath): the
            # host is then exactly as tall as the card and the card starts at the host's top.
            self.frame.grid(row=0, column=0, sticky="ew")
            self._layout_now()
        self._fade_in(self._banner_fade, appear)

    def collapse(self, version):
        """After Later: the reminder in the header instead of the banner."""
        self.close_notes()
        self._hide_banner()
        self.reminder.configure(text=f"Update available · {version}")
        if not self._reminder_shown:
            self._reminder_shown = True
            self._fade_in(self._reminder_fade, lambda: self._place_reminder(True))

    def hide(self):
        self.close_notes()
        self._hide_banner()
        self._hide_reminder()

    def _destroyed(self, event):
        if event.widget is not self.frame or self._destroyed_flag:
            return
        self._destroyed_flag = True
        self._stop_notes()
        self.scroller.stop()
        for fade in (self._banner_fade, self._reminder_fade):
            self._end_fade(fade)
        self._unfont()
        if self._reminder_shown:
            self._place_reminder(False)
        try:
            self.reminder.destroy()
        except tk.TclError:
            pass
