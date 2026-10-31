"""Stage canvas: page header, update-notice host, page host and footer.

Everything that sits on the animated backdrop is a canvas item (titles, game
status, footer links, status line, stickers, progress line). The page and the
update notice are ordinary frames placed over it, so their widgets keep native
behaviour. Geometry follows Figma [design reference] / [design reference] in design px.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_layout.py, agr/gui_theme.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, NamedTuple, Sequence

from .. import ui_theme
from . import fonts
from .boil import IdleUpdate
from .canvas_controls import CanvasDot, CanvasFocus, CanvasLink, CanvasProgress, CanvasText
from .scale import px
from .stickers import CanvasSticker

# Shorter stages use the compact rows, narrower ones the narrow side padding.
DESIGN_HEIGHT = 780
WIDE_WIDTH = 940
PAD = (40, 24)
# Rows in design px as (design, compact): header rows from the top, footer rows from the bottom.
TITLE_Y = (43, 28)
SUBTITLE_Y = (67, 52)
NOTICE_TOP = (107, 76)    # the update banner takes the page's first row (Figma [design reference])
PAGE_TOP = (107, 76)
PAGE_BOTTOM = (112, 98)   # design: the page ends where the footer's 36 px link row begins
LINKS_Y = (94, 73)
STATUS_Y = (58, 45)
STICKERS_TOP = (82, 63)
PROGRESS_Y = (24, 12)
NOTICE_GAP = 30           # banner to the page's cards ([design reference])
HEADER_WIDGET_GAP = 16    # the update reminder to the game status ([design reference])
LINK_GAP = 20
STICKER_GAP = 10
CONTEXT_WIDTH = 360
STATUS_DOT_X, STATUS_TEXT_X = 2.5, 10
STATUS_MIN_WRAP = 120
STATUS_STICKER_GAP = 30
BUBBLE_DELAY_MS = 150
BUBBLE_WIDTH = 300
BUBBLE_PAD = (10, 6)
BUBBLE_OFFSET = 6

# Sticker faces and resting poses (Figma [design reference] "Frame 25", chrome.md 4.3): positive tilt is
# counter-clockwise. CANCEL has no Figma node: the neutral paper of STOP/RESTORE, posed like
# START, and it takes START's place while a startup can be cancelled.
STICKERS = {
    "stop": {"label": "STOP/RESTORE", "kind": "secondary", "tilt": -.65, "seed": 3,
             "paper": (155.10, 34.58), "paper_offset": (.4, -.9)},
    "cancel": {"label": "CANCEL", "kind": "secondary", "tilt": .87, "seed": 5,
               "paper": (78.47, 34.72), "paper_offset": (-.6, -.1)},
    "start": {"label": "START", "kind": "primary", "tilt": .87, "seed": 7,
              "paper": (78.47, 34.72), "paper_offset": (-.6, -.1)},
}

_TONE_TOKENS = {"dim": "dim", "muted": "muted", "error": "status_error"}


class FooterLink(NamedTuple):
    text: "str | tk.Variable"
    command: "Callable[[], object] | None"
    action: "object | None" = None


class Stage(tk.Canvas):
    """The content column beside the sidebar.

    ``notice_host`` is grid-managed with row 2 free for the update notice;
    ``workspace`` receives the page notebook (row 0) and the timeline (row 1).
    It covers the page rectangle plus an optional right gutter for the page
    rail (:meth:`set_rail_gutter`). Text, links, stickers, the status dot and
    the progress line follow the variables and actions bound here; every
    refresh is coalesced at idle.
    """

    def __init__(self, master, *, scale, backdrop=None):
        tokens = ui_theme.TOKENS
        self.ui_scale = scale
        super().__init__(master, background=tokens["bg"], highlightthickness=0, borderwidth=0,
                         width=1, height=1, takefocus=0)
        self.backdrop = backdrop
        self.mode = None                  # "design" or "compact" after the first layout
        self.secondary_sticker = None     # STOP/RESTORE, every page
        self.cancel_sticker = None        # CANCEL, in START's place while a startup can be cancelled
        self.primary_sticker = None       # START of the shown page, if it has one
        self.header_widget = None         # left of the game status: the update reminder (set_header_widget)
        self._header_row = None           # (right x, title row centre y) from the last layout
        self._pages = {}
        self._page = None
        self._primary_stickers = {}
        self._shown = {}
        self._links = []
        self._traces = []
        self._unsubscribe = []
        self._page_rect = (0, 0, 0, 0)
        self._workspace_rect = (0, 0, 0, 0)
        self._gutter = 0                  # design px of the page rail's gutter right of the page rect
        self._size = (0, 0)
        self._status_provider = None
        self._status_line = None          # (text x, dot x, centre y, wrap) from the last layout
        self._session = None
        self._context_provider = None
        self._bubble = None
        self._bubble_job = None
        self._body_font = fonts.font(self, "body")
        self.title_text = CanvasText(self, text="", font=fonts.font(self, "page_title"), fill=tokens["text"])
        self.subtitle_text = CanvasText(self, text="", font=self._body_font, fill=tokens["muted"])
        self.session_text = CanvasText(self, text="", font=self._body_font, fill=tokens["dim"], anchor="e")
        self.context_text = CanvasText(self, text="", font=fonts.font(self, "small"), fill=tokens["dim"],
                                       anchor="e", wraplength=px(scale, CONTEXT_WIDTH), max_lines=1)
        self.status_label = CanvasText(self, text="", font=self._body_font, fill=tokens["muted"], max_lines=2)
        self.status_dot = CanvasDot(self, scale=scale)
        self.progress = CanvasProgress(self, scale=scale)
        self.notice_host = ttk.Frame(self, style="TFrame")
        self.notice_host.columnconfigure(0, weight=1)
        # Grid keeps the old size of a master whose last slave leaves; this empty
        # row keeps it managed, so the host shrinks back to 1 px with the notice.
        ttk.Frame(self.notice_host, style="TFrame", width=0, height=0).grid(row=0, column=0, sticky="ew")
        self.workspace = ttk.Frame(self, style="TFrame")
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        # Placed, not window items: the placer sizes them even before the canvas
        # first draws, and the host reports its own size changes.
        self.notice_host.place(x=0, y=0, width=1)
        self.workspace.place(x=0, y=0, width=1, height=1)
        self._focus = CanvasFocus(self, [])
        self._layout_update = IdleUpdate(self, self._layout_now)
        self._status_update = IdleUpdate(self, self._refresh_status)
        self._header_update = IdleUpdate(self, self._refresh_header)
        self.bind("<Configure>", self.layout, add="+")
        self.notice_host.bind("<Configure>", self.layout, add="+")
        self.bind("<Motion>", self._pointer_moved, add="+")
        self.bind("<Leave>", lambda _e: self._hide_bubble(), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self._unsubscribe.append(fonts.on_change(self.winfo_toplevel(), self.layout))
        if backdrop is not None:
            backdrop.set_frame(self)
            backdrop.attach(self, self.open_areas)

    # ---------------------------------------------------------------- pages

    def register_page(self, key, *, title, subtitle="", primary=None):
        """Header copy for a notebook page; ``primary`` adds its START sticker."""
        key = str(key)
        self._pages[key] = (title, subtitle, primary)
        if primary is not None and id(primary) not in self._primary_stickers:
            self._primary_stickers[id(primary)] = self._sticker(primary, "start")
        if key == self._page:
            self.show_page(key)

    def show_page(self, key):
        key = str(key)
        self._page = key
        title, subtitle, primary = self._pages.get(key, ("", "", None))
        self.title_text.configure(text=title)
        self.subtitle_text.configure(text=subtitle)
        current = self._primary_stickers.get(id(primary)) if primary is not None else None
        for sticker in self._primary_stickers.values():
            if sticker is not current:
                self._show_sticker(sticker, False)
        self.primary_sticker = current
        self._show_sticker(current, not self._cancelling())
        self.layout()
        if self.backdrop is not None:
            self.backdrop.invalidate(self)

    @property
    def page(self):
        return self._page

    # --------------------------------------------------------------- footer

    def set_secondary(self, action):
        """STOP/RESTORE: shown on every page."""
        if self.secondary_sticker is not None:
            self._drop_sticker(self.secondary_sticker)
        self.secondary_sticker = self._sticker(action, "stop")
        self._show_sticker(self.secondary_sticker, True)

    def set_cancel(self, action):
        """CANCEL in START's place, shown only while ``action`` is enabled."""
        if self.cancel_sticker is not None:
            self._drop_sticker(self.cancel_sticker)
        sticker = self._sticker(action, "cancel")
        self.cancel_sticker = sticker

        def follow(_action=None):
            if self.cancel_sticker is sticker:
                self._show_sticker(sticker, action.enabled())
                self._show_sticker(self.primary_sticker, not self._cancelling())

        self._unsubscribe.append(action.subscribe(follow))
        follow()

    def set_links(self, links: Sequence[FooterLink]):
        """Text links left of the status line, in order, ``px(20)`` apart."""
        for link in self.links:
            link.destroy()
        self._links = []
        for spec in links:
            text = ({"textvariable": spec.text} if isinstance(spec.text, tk.Variable) else {"text": spec.text})
            link = CanvasLink(self, command=spec.command, action=spec.action, font=self._body_font,
                              scale=self.ui_scale, **text)
            self._links.append((spec, link))
            if isinstance(spec.text, tk.Variable):
                self._trace((spec.text,), self.layout)
            if spec.action is not None:
                self._unsubscribe.append(spec.action.subscribe(lambda _action=None: self.layout()))
        self.layout()

    @property
    def links(self):
        return [link for _spec, link in self._links]

    def _cancelling(self):
        """True while CANCEL stands in for START."""
        return self.cancel_sticker is not None and bool(self._shown.get(id(self.cancel_sticker)))

    def visible_stickers(self):
        """Shown stickers left to right: STOP/RESTORE, then CANCEL or START."""
        return [sticker for sticker in (self.secondary_sticker, self.cancel_sticker, self.primary_sticker)
                if sticker is not None and self._shown.get(id(sticker))]

    def bind_progress(self, proxy):
        self.progress.bind(proxy)

    def bind_status(self, provider: Callable[[], tuple[str, str]], variables: Sequence[tk.Variable]):
        """Status line: ``provider()`` returns (text, "ready" | "busy" | "error")."""
        self._status_provider = provider
        self._trace(variables, self._status_update.schedule)
        self._status_update.schedule()

    def bind_session(self, variable: tk.Variable, tone: Callable[[str], str]):
        """Game status at the header's right; ``tone(text)`` is "dim", "muted" or "error"."""
        self._session = (variable, tone)
        self._trace((variable,), self._header_update.schedule)
        self._header_update.schedule()

    def bind_context(self, provider: Callable[[], str], variables: Sequence[tk.Variable]):
        """Second header line at the right (shot name and current work), elided to one line."""
        self._context_provider = provider
        self._trace(variables, self._header_update.schedule)
        self._header_update.schedule()

    def set_header_widget(self, widget):
        """Place ``widget`` (a child of the stage) in the header row, left of the game status.

        Its right edge sits ``HEADER_WIDGET_GAP`` px left of the status text (at the page's
        right edge without one), centred on the title row: the update reminder (Figma
        [design reference]). It follows layouts and status changes; None removes the current one.
        """
        old, self.header_widget = self.header_widget, widget
        if old is not None and old is not widget:
            try:
                old.place_forget()
            except tk.TclError:
                pass
        self._place_header_widget()

    def _place_header_widget(self):
        widget, row = self.header_widget, self._header_row
        if widget is None or row is None:
            return
        right, middle = row
        box = self.session_text.bbox() if self.session_text.full_text() else None
        x = box[0] - px(self.ui_scale, HEADER_WIDGET_GAP) if box and box[2] > box[0] else right
        try:
            widget.place(in_=self, x=x, y=middle, anchor="e")
        except tk.TclError:
            self.header_widget = None

    # --------------------------------------------------------------- layout

    def page_rect(self):
        """The page column (header and footer align to it; the update notice is this wide)."""
        return self._page_rect

    def workspace_rect(self):
        """Where the pages are: :meth:`page_rect` plus the rail gutter on its right."""
        return self._workspace_rect

    def set_rail_gutter(self, design_px):
        """Widen the page area by a right gutter of ``design_px`` for the page rail (PLAN-5 N8).

        The gutter lies inside the right padding, so pages laid out in the page width keep
        it when their rail shows. ``page_rect()`` does not change; 0 (the default) is the
        page rect alone. The stage lays itself out again.
        """
        gutter = max(0, int(design_px))
        if gutter != self._gutter:
            self._gutter = gutter
            self.layout()

    def open_areas(self):
        """Where the backdrop shows: the stage minus the workspace rectangle."""
        width, height = self.winfo_width(), self.winfo_height()
        x0, y0, x1, y1 = self._workspace_rect
        if x1 <= x0 or y1 <= y0:
            return [(0, 0, width, height)]
        return [(0, 0, width, y0), (0, y0, x0, y1), (x1, y0, width, y1), (0, y1, width, height)]

    def layout(self, *_args):
        """Re-place everything at idle; a burst of changes becomes one pass."""
        self._layout_update.schedule()

    def _layout_now(self):
        try:
            width, height = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            return
        s = self.ui_scale
        row = 0 if height >= px(s, DESIGN_HEIGHT) else 1
        self.mode = ("design", "compact")[row]
        pad = px(s, PAD[0] if width >= px(s, WIDE_WIDTH) else PAD[1])
        left, right = pad, max(pad + 1, width - pad)
        self.title_text.place(left, px(s, TITLE_Y[row]))
        self.subtitle_text.place(left, px(s, SUBTITLE_Y[row]))
        self.session_text.place(right, px(s, TITLE_Y[row]))
        self.context_text.place(right, px(s, SUBTITLE_Y[row]))
        self._header_row = (right, px(s, TITLE_Y[row]))
        self._place_header_widget()
        page_top = px(s, PAGE_TOP[row])
        notice = self.notice_host.winfo_reqheight()
        if notice > 1:
            notice_top = px(s, NOTICE_TOP[row])
            self.notice_host.place_configure(x=left, y=notice_top, width=right - left)
            page_top = max(page_top, notice_top + notice + px(s, NOTICE_GAP))
        else:
            # Empty: tucked under the page but still managed, so the notice's
            # arrival resizes it and reports through <Configure>.
            self.notice_host.place_configure(x=left, y=page_top, width=1)
        page_bottom = max(page_top + 1, height - px(s, PAGE_BOTTOM[row]))
        gutter = max(0, min(px(s, self._gutter), width - right))   # inside the right padding
        self.workspace.place_configure(x=left, y=page_top, width=right + gutter - left,
                                       height=page_bottom - page_top)
        rect = (left, page_top, right, page_bottom)
        workspace = (left, page_top, right + gutter, page_bottom)
        changed = rect != self._page_rect or workspace != self._workspace_rect or (width, height) != self._size
        self._page_rect, self._workspace_rect, self._size = rect, workspace, (width, height)
        self._place_footer(left, right, height, row)
        if changed and self.backdrop is not None:
            self.backdrop.sync(self, redraw=True)

    def _place_footer(self, left, right, height, row):
        s = self.ui_scale
        controls = []
        x, y = left, height - px(s, LINKS_Y[row])
        for link in self.links:
            if not link.visible():
                continue
            link.place(x, y, anchor="w")
            x += link.width() + px(s, LINK_GAP)
            controls.append(link)
        # Right-aligned: START right-most, STOP/RESTORE takes its slot when START is hidden.
        stickers = self.visible_stickers()
        x, top = right, height - px(s, STICKERS_TOP[row])
        for sticker in reversed(stickers):
            x -= sticker.width()
            sticker.place(x, top)
            x -= px(s, STICKER_GAP)
        block = right - x - px(s, STICKER_GAP) if stickers else 0
        self._focus.set_controls(controls + stickers)
        self._focus.place(0, height - 1)    # the 1 px stand-in, clear of every control
        wrap = max(px(s, STATUS_MIN_WRAP), right - left - block - px(s, STATUS_STICKER_GAP))
        self.status_label.configure(wraplength=wrap)
        self._status_line = (left + px(s, STATUS_TEXT_X), left + px(s, STATUS_DOT_X),
                             height - px(s, STATUS_Y[row]), wrap)
        self._place_status()
        self.progress.place(left, right, height - px(s, PROGRESS_Y[row]))

    def _place_status(self):
        """The message centres on the status line; when it wraps, the dot stays by its first line."""
        if self._status_line is None:
            return
        text_x, dot_x, line, _wrap = self._status_line
        self.status_label.place(text_x, line)
        linespace = self._body_font.metrics("linespace")
        self.status_dot.place(dot_x, line - (self._status_lines() - 1) * linespace / 2)

    def _status_lines(self):
        box = self.status_label.bbox()
        if not box:
            return 1
        return max(1, round((box[3] - box[1]) / max(1, self._body_font.metrics("linespace"))))

    # ------------------------------------------------------------- refresh

    def _refresh_status(self):
        if self._status_provider is None:
            return
        text, state = self._status_provider()
        tokens = ui_theme.TOKENS
        self.status_label.configure(text=text, fill=tokens["status_error" if state == "error" else "muted"])
        self.status_dot.configure(state=state)
        self._place_status()
        if not self.status_label.elided():
            self._hide_bubble()

    def _refresh_header(self):
        if self._session is not None:
            variable, tone = self._session
            text = variable.get()
            self.session_text.configure(text=text, fill=ui_theme.TOKENS[_TONE_TOKENS.get(tone(text), "muted")])
            self._place_header_widget()
        if self._context_provider is not None:
            self.context_text.configure(text=self._context_provider())

    def _trace(self, variables, callback):
        for variable in variables:
            name = variable.trace_add("write", lambda *_args: callback())
            self._traces.append((variable, name))

    # ------------------------------------------------------------- stickers

    def _sticker(self, action, name):
        """A hidden sticker for ``action`` drawn as ``STICKERS[name]``."""
        sticker = CanvasSticker(self, action=action, scale=self.ui_scale, font=fonts.font(self, "sticker"),
                                **STICKERS[name])
        sticker.set_shown(False)
        self._shown[id(sticker)] = False
        return sticker

    def _show_sticker(self, sticker, shown):
        if sticker is None or self._shown.get(id(sticker)) == shown:
            return
        self._shown[id(sticker)] = shown
        sticker.set_shown(shown)
        self.layout()

    def _drop_sticker(self, sticker):
        self._shown.pop(id(sticker), None)
        sticker.destroy()

    # --------------------------------------------------- full status bubble

    def _pointer_moved(self, event):
        line = self._status_line
        if line is None or not self.status_label.elided():
            self._hide_bubble()
            return
        text_x, _dot_x, y, wrap = line
        linespace = self._body_font.metrics("linespace")
        if text_x <= event.x <= text_x + wrap and abs(event.y - y) <= linespace:
            if self._bubble_job is None and not (self._bubble is not None and self._bubble.winfo_ismapped()):
                self._bubble_job = self.after(BUBBLE_DELAY_MS, self._show_bubble)
        else:
            self._hide_bubble()

    def _show_bubble(self):
        """The whole elided status message, just above the status line."""
        self._bubble_job = None
        if self._status_line is None:
            return
        s = self.ui_scale
        tokens = ui_theme.TOKENS
        if self._bubble is None:
            self._bubble = tk.Canvas(self, background=tokens["field"], highlightthickness=0, borderwidth=0,
                                     takefocus=0)
            self._bubble_text = self._bubble.create_text(px(s, BUBBLE_PAD[0]), px(s, BUBBLE_PAD[1]), anchor="nw",
                                                         font=self._body_font, fill=tokens["text"],
                                                         width=px(s, BUBBLE_WIDTH))
        self._bubble.itemconfigure(self._bubble_text, text=self.status_label.full_text())
        x0, y0, x1, y1 = self._bubble.bbox(self._bubble_text) or (0, 0, 0, 0)
        width, height = x1 - x0 + 2 * px(s, BUBBLE_PAD[0]), y1 - y0 + 2 * px(s, BUBBLE_PAD[1])
        text_x, _dot_x, line, _wrap = self._status_line
        top = line - self._status_lines() * self._body_font.metrics("linespace") / 2
        x = max(0, min(text_x - px(s, BUBBLE_OFFSET), self.winfo_width() - width - px(s, BUBBLE_OFFSET)))
        y = max(0, int(top) - px(s, BUBBLE_OFFSET) - height)
        self._bubble.configure(width=width, height=height)
        self._bubble.place(x=x, y=y)
        tk.Misc.lift(self._bubble)

    def _hide_bubble(self):
        if self._bubble_job is not None:
            try:
                self.after_cancel(self._bubble_job)
            except tk.TclError:
                pass
            self._bubble_job = None
        if self._bubble is not None:
            try:
                self._bubble.place_forget()
            except tk.TclError:
                pass  # already gone with the stage

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._hide_bubble()
        for variable, name in self._traces:
            try:
                variable.trace_remove("write", name)
            except tk.TclError:
                pass
        self._traces = []
        for unsubscribe in self._unsubscribe:
            try:
                unsubscribe()
            except tk.TclError:
                pass
        self._unsubscribe = []
        # Controls follow actions that outlive this canvas: let go of them too.
        for control in [*self.links, *self._primary_stickers.values(), self.secondary_sticker,
                        self.cancel_sticker, self.status_dot, self.progress]:
            if control is not None:
                control.destroy()


def style_title_bar(root) -> None:
    """Recolour the native title bar dark through DWM once the window first maps.

    The frame stays the real Windows frame (snapping, resizing and caption
    buttons keep working); DWM only recolours it. Caption, text and border
    colours need Windows 11; Windows 10 (20H1+) gets the dark title bar only.
    ``DOLLY_UI_DARK_TITLE=0`` keeps the system title bar. Never raises.
    """
    if sys.platform != "win32" or os.environ.get("DOLLY_UI_DARK_TITLE") == "0":
        return

    def mapped(event=None):
        if event is not None and event.widget is not root:
            return
        if getattr(root, "_dolly_title_styled", False):
            return
        root._dolly_title_styled = True
        _paint_title_bar(root)

    root.bind("<Map>", mapped, add="+")
    try:
        if root.winfo_ismapped():
            mapped()
    except tk.TclError:
        pass


def _paint_title_bar(root):
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(root.wm_frame(), 16))
        dwm = ctypes.windll.dwmapi

        def set_attribute(attribute, value):
            data = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0

        def colorref(color):  # "#rrggbb" -> 0x00bbggrr
            red, green, blue = ui_theme.rgb(color)
            return red | green << 8 | blue << 16

        tokens = ui_theme.TOKENS
        set_attribute(20, 1) or set_attribute(19, 1)      # DWMWA_USE_IMMERSIVE_DARK_MODE (and pre-20H1)
        set_attribute(35, colorref(tokens["bg"]))           # DWMWA_CAPTION_COLOR
        set_attribute(36, colorref(tokens["text"]))         # DWMWA_TEXT_COLOR
        set_attribute(34, colorref(tokens["bg"]))           # DWMWA_BORDER_COLOR
    except Exception:  # noqa: BLE001 - a plain title bar is the safe fallback
        pass


def work_area(root) -> tuple[int, int, int, int]:
    """Usable desktop (left, top, right, bottom) without the taskbar, in this process's pixels.

    The monitor the window is on once it is mapped; before that the primary
    monitor, where a new window opens. Falls back to the whole screen.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if root.winfo_ismapped():
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.MonitorFromWindow.restype = wintypes.HMONITOR
                user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(info)
                monitor = user32.MonitorFromWindow(wintypes.HWND(int(root.wm_frame(), 16)), 2)  # NEAREST
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    work = info.rcWork
                    return work.left, work.top, work.right, work.bottom
            rect = wintypes.RECT()
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError, ValueError, tk.TclError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def frame_size(*, menu: bool = False) -> tuple[int, int]:
    """Width and height the native frame (borders, title bar, a menu bar if ``menu``) adds to a client area.

    Dialogs and the main window when it keeps a native caption; no Dolly window has a native
    menu bar (File and Edit live on the main window's own title bar, USER-ADDITIONS UPDATE 20).
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT(0, 0, 0, 0)
            # WS_OVERLAPPEDWINDOW at the process's DPI (system DPI once DPI aware).
            if ctypes.windll.user32.AdjustWindowRectEx(ctypes.byref(rect), 0x00CF0000, bool(menu), 0):
                return rect.right - rect.left, rect.bottom - rect.top
        except (AttributeError, OSError):
            pass
    return 16, 60 if menu else 40
