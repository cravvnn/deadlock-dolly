"""Library page view: explicit variables/actions, with locally owned widgets.

The Replay screen (Figma [design reference]; its starting state [design reference] is drawn by the
shell). Three cards sit on a :class:`StagePage` over the window's backdrop.
Shot files (New, Open, Save, Save as, Rename) stay in the File menu until the
design gives them a place; Stop / restore and Cancel startup are footer
stickers. The START and CANCEL stickers and the startup progress line are
drawn by the shell from the proxies this page exports. Browse and Refresh are
canvas-drawn kit buttons, like the card buttons of the other five pages.

The page never scrolls (USER-ADDITIONS UPDATE 17.5): in a window shorter than the
design the Replay Library table shows fewer of its rows (at least one) and scrolls
inside itself, so the three cards always end Replay's gap above the footer links.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from ..replays import DEFAULT_REPLAY_SORT, REPLAY_SORTS
from . import art
from .action_proxy import ActionProxy, ProgressProxy
from .kit_controls import KitButton
from .scale import hairline, px
from .stage_page import TREE_CELL_INSET, InfoIcon, StagePage, bleed_left, form_row

TITLE = "Replay & Shots"
SUBTITLE = "Choose a replay, open it in Dolly, then frame your cameras inside Deadlock."
LAUNCH_INFO = "Choose your Deadlock executable and the camera driver Dolly uses in game."
SELECTED_INFO = ("Open in the paused in-game camera editor. "
                 "F8 panel - Frame cameras, edit the lens, and record inside Deadlock.")
LIBRARY_INFO = "Replays found in your replay folder. Double-click one to use it."
CAPTURE_INFO = "In-game camera capture follows this switch; set the shortcut in Controls."
DRIVERS = ("Native (experimental)", "Console (legacy)")
# The Replay Library table's rows: the design's three, never fewer than one when the
# window is short (the page gives it whatever room is left; UPDATE 17.5).
TABLE_ROWS = 3
TABLE_MIN_ROWS = 1


@dataclass(frozen=True)
class LibraryState:
    camera_driver: tk.Variable
    demo_path: tk.Variable
    game_path: tk.Variable
    hotkey_enabled: tk.Variable
    hotkey_label: tk.Variable
    path_summary: tk.Variable
    project_text: tk.Variable
    replay_search: tk.Variable
    replay_sort: tk.Variable
    startup_progress: tk.Variable


@dataclass(frozen=True)
class LibraryActions:
    browse_demo: Callable[..., object]
    browse_game: Callable[..., object]
    cancel_startup: Callable[..., object]
    filter_replays: Callable[..., object]
    refresh_replays: Callable[..., object]
    repair_replay: Callable[..., object]
    select_replay: Callable[..., object]
    start_editing_session: Callable[..., object]
    toggle_capture_hotkey: Callable[..., object]
    tree: Callable[..., object]
    use_selected_replay: Callable[..., object]
    new: Callable[..., object]
    open: Callable[..., object]
    rename: Callable[..., object]
    save: Callable[..., object]
    stop_session: Callable[..., object]
    save_as: Callable[..., object]


class LibraryPage:
    def __init__(self, parent, state: LibraryState, commands: LibraryActions):
        page = StagePage(parent)
        page.pack(fill="both", expand=True)
        self.library_page = page
        s = page.scale
        gap = px(s, 10)
        button_width = _button_width(page, s)
        # The shell draws these: START and CANCEL stickers, the footer progress line.
        self.play_replay_button = ActionProxy("Open replay in Dolly", commands.start_editing_session,
                                              label="START")
        self.cancel_startup_button = ActionProxy("Cancel startup", commands.cancel_startup,
                                                 label="CANCEL", state="disabled")
        self.startup_bar = ProgressProxy()

        launch = self.launch_card = page.add_card("Launch Setup", info=LAUNCH_INFO)
        row, _ = form_row(launch.body, "Deadlock Executable")
        self.game_entry = ttk.Entry(row, textvariable=state.game_path, style="Stage.Card.TEntry")
        self.game_entry.grid(row=0, column=1, sticky="ew", padx=(gap, 0))
        self.game_path_cover = _elide_at_rest(self.game_entry, state.game_path)
        KitButton(row, text="Browse", command=commands.browse_game, width=button_width).grid(
            row=0, column=2, padx=(gap, 0))
        row, _ = form_row(launch.body, "Camera driver", pady=(gap, 0))
        self.home_camera_driver_combo = ttk.Combobox(row, textvariable=state.camera_driver, values=DRIVERS,
                                                     state="readonly", style="Stage.Card.TCombobox")
        self.home_camera_driver_combo.grid(row=0, column=1, sticky="ew", padx=(gap, 0))

        selected = self.selected_card = page.add_card("Selected Replay", info=SELECTED_INFO)
        row, _ = form_row(selected.body, "Replay File")
        self.selected_replay_text = tk.StringVar(page)
        ttk.Label(row, textvariable=self.selected_replay_text, style="Chip.Card.TLabel").grid(
            row=0, column=1, sticky="w", padx=(gap, 0))
        self.repair_replay_button = KitButton(row, text="Repair...", command=commands.repair_replay, width=75)
        self.repair_replay_button.grid(row=0, column=2, sticky="e", padx=(gap,0))
        def update_name(*_):
            self.selected_replay_text.set(Path(state.demo_path.get()).name if state.demo_path.get() else "Choose a replay")
        state.demo_path.trace_add("write", update_name)
        update_name()
        # The switch sits on the kit's 18 px line, 15 px under the file row.
        capture = ttk.Frame(selected.body, style="Card.TFrame")
        capture.pack(fill="x", pady=(px(s, 15), 0))
        capture.rowconfigure(0, minsize=px(s, 18))
        self.library_capture_toggle = ttk.Checkbutton(
            capture, textvariable=state.hotkey_label, variable=state.hotkey_enabled,
            command=commands.toggle_capture_hotkey)
        self.library_capture_toggle.grid(row=0, column=0)
        InfoIcon(capture, CAPTURE_INFO, page=page).grid(row=0, column=1, padx=(px(s, 5), 0))

        # The table's last row keeps 10 px under its text, so the card closes 10 px
        # below the table (20 px below the text, as in the design).
        library = self.library_card = page.add_card("Replay Library", info=LIBRARY_INFO, bottom=10)
        toolbar = ttk.Frame(library.body, style="Card.TFrame")
        toolbar.pack(fill="x")
        toolbar.columnconfigure(1, weight=1)
        self.search_entry, self.search_placeholder = self._search(toolbar, state.replay_search, s)
        state.replay_search.trace_add("write", lambda *_: commands.filter_replays())
        if not state.replay_sort.get():
            state.replay_sort.set(DEFAULT_REPLAY_SORT)
        sort_group = ttk.Frame(toolbar, style="Card.TFrame")
        sort_group.grid(row=0, column=1, sticky="e", padx=(gap, gap))
        # Three restrained, descending horizontal strokes match the app's line icons.
        icon_size = px(s, 12)
        strokes = tuple(((1.5*s, y*s), (end*s, y*s)) for y, end in ((3, 10.5), (6, 8), (9, 5.5)))
        self.sort_icon = art.stroke_rgba(page, icon_size, icon_size, strokes, max(1, 1.2*s),
                                         ui_theme.TOKENS["muted"], surface=ui_theme.TOKENS["panel"])
        ttk.Label(sort_group, image=self.sort_icon, style="Card.TLabel").pack(side="left", padx=(0, px(s, 4)))
        ttk.Label(sort_group, text="Sort", style="CardMuted.TLabel").pack(side="left", padx=(0, px(s, 5)))
        self.sort_combo = ttk.Combobox(sort_group, textvariable=state.replay_sort, values=tuple(REPLAY_SORTS),
                                       state="readonly", width=10, style="Stage.Card.TCombobox")
        self.sort_combo.pack(side="left")
        state.replay_sort.trace_add("write", lambda *_: commands.filter_replays())
        KitButton(toolbar, text="Browse", command=commands.browse_demo, width=button_width).grid(
            row=0, column=2)
        KitButton(toolbar, text="Refresh", command=commands.refresh_replays, width=button_width).grid(
            row=0, column=3, padx=(gap, 0))
        # Headings and rows start on the content edge (the table bleeds left by
        # Tk's cell indent); the last column takes that extra width.
        holder = ttk.Frame(library.body, style="Card.TFrame", height=1)
        holder.pack(fill="x", pady=(px(s, 20), 0))
        table, self.replay_tree = commands.tree(holder, ("name", "size", "modified"),
                                                ("Replay", "Size", "Modified"),
                                                (px(s, 349), px(s, 85), px(s, 137) + TREE_CELL_INSET),
                                                height=TABLE_ROWS)
        bleed_left(holder, table, TREE_CELL_INSET)
        self.replay_tree.configure(style="Library.Treeview")
        for column, minwidth in (("name", 100), ("size", 50), ("modified", 70)):
            self.replay_tree.column(column, anchor="w", minwidth=px(s, minwidth), stretch=column == "name")
            self.replay_tree.heading(column, anchor="w")
        self.replay_tree.bind("<<TreeviewSelect>>", commands.select_replay)
        self.replay_tree.bind("<Double-1>", lambda _e: commands.use_selected_replay())
        _hide_idle_scrollbar(table, self.replay_tree)
        page.watch_focus()
        # The page never scrolls: the table gives up rows when the window is short.
        self._table_resized = False
        page.fit(self._fit_table)

    def _fit_table(self, spare) -> bool:
        """The page's fit: as many of the design's rows as the cards have room for, at least one.

        ``spare`` is what the cards have left above Replay's gap to the footer links
        (negative: how far they run past it). Returns True when the row count changed.
        """
        tree = self.replay_tree
        rows = int(tree.cget("height"))
        wanted = max(TABLE_MIN_ROWS, min(TABLE_ROWS, rows + math.floor(spare / _row_pitch(tree))))
        if wanted != rows:
            tree.configure(height=wanted)
            self._table_resized = True
            return True
        if self._table_resized:
            # The page asks again once the table has its new height: keep the chosen
            # replay among the rows still shown.
            self._table_resized = False
            selected = tree.selection()
            if selected:
                tree.see(selected[0])
        return False

    @staticmethod
    def _search(toolbar, variable, s):
        """The search field: a magnifier and a "Search" placeholder drawn over a plain entry.

        Neither writes the variable, so filtering only ever sees what was typed.
        Returns the entry and the placeholder label.
        """
        frame = ttk.Frame(toolbar, style="Card.TFrame", width=px(s, 262))
        frame.grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=variable, style="Search.Card.TEntry")
        frame.configure(height=max(px(s, 30), entry.winfo_reqheight()))
        entry.place(x=0, y=0, relwidth=1, relheight=1)
        t = ui_theme.TOKENS
        size, line = px(s, 8), hairline(s)
        icon = tk.Canvas(frame, width=size + 1, height=size + 1, background=t["field"], highlightthickness=0,
                         borderwidth=0, takefocus=0, cursor="xterm")
        ring = 2 * 3.33 * s
        icon.create_oval(.5, .5, ring + .5, ring + .5, outline=t["dim"], width=line)
        icon.create_line(ring * .85, ring * .85, size, size, fill=t["dim"], width=line, capstyle="round")
        icon.place(in_=entry, x=px(s, 13), rely=.5, anchor="w")
        placeholder = ttk.Label(frame, text="Search", style="Placeholder.TLabel", cursor="xterm")
        focused = [False]
        def sync(*_):
            try:
                if variable.get() or focused[0]:
                    placeholder.place_forget()
                else:
                    placeholder.place(in_=entry, x=px(s, 26), rely=.5, anchor="w")
            except tk.TclError:
                pass  # being destroyed
        def focus(value):
            focused[0] = value
            sync()
        entry.bind("<FocusIn>", lambda _e: focus(True), add="+")
        entry.bind("<FocusOut>", lambda _e: focus(False), add="+")
        for widget in (icon, placeholder):
            widget.bind("<Button-1>", lambda _e: (entry.focus_set(), "break")[1])
        token = variable.trace_add("write", sync)
        def untrace(_event):
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass  # the interpreter is going away with the window
        entry.bind("<Destroy>", untrace, add="+")
        sync()
        return entry, placeholder


def _button_width(widget, s) -> int:
    """Design px for Browse and Refresh: the width of the 8-character card buttons they replaced.

    Those were 8 widths of "0" plus 11 px a side (70 px at 100 %, Figma's 69.5 on [design reference]).
    Tk counts the "0"s in whole pixels, so at 150 % they came to 104 px rather than 105;
    keeping that width keeps the fields beside them where they were. Counted in Segoe UI,
    the face they had (as :func:`.scale.chars` does for it): Inter's wider "0" would make
    them 86 px.
    """
    family, points = ui_theme.FONT_FALLBACK, ui_theme.FONT_SIZES["body"]
    zero = int(widget.tk.call("font", "measure", (family, points), "0"))
    count = 8
    if s != 1.0:
        design = float(widget.tk.call("font", "measure", (family, -round(points * 96 / 72)), "0"))
        if design > 0 and zero > 0:
            count = max(1, int(count * design * s / zero + .5))
    return round((count * zero + 2 * px(s, 11)) / s)


def _row_pitch(tree) -> int:
    """The px one more row adds to ``tree``: its style's row height, measured when the style has none."""
    try:
        pitch = int(float(ttk.Style(tree).lookup(str(tree.cget("style")) or "Treeview", "rowheight") or 0))
    except (ValueError, tk.TclError):
        pitch = 0
    if pitch <= 0:
        # Tk's own default row height (no Dolly theme): the requested height is
        # updated as soon as the row count is set.
        rows = int(tree.cget("height"))
        before = tree.winfo_reqheight()
        tree.configure(height=rows + 1)
        pitch = tree.winfo_reqheight() - before
        tree.configure(height=rows)
    return max(1, pitch)


def _elide_at_rest(entry, variable):
    """Show ``entry``'s value cut with "…" where it overflows, while nobody edits it.

    The design draws a long path elided; a Tk entry only clips it mid-letter. A
    label in the entry's font covers its text line while the entry lacks focus;
    focusing it (Tab, or a click, which lands on the clicked character) hands the
    full value back to the entry. Returns the cover label.
    """
    style = ttk.Style(entry)
    name = str(entry.cget("style")) or "TEntry"
    pad = [entry.tk.getint(value) for value in entry.tk.splitlist(style.lookup(name, "padding"))] or [0]
    left, top, right = pad[0], pad[1 % len(pad)], pad[2] if len(pad) > 2 else pad[0]
    # Measured in the face the cover draws (the value face, like the entry's own TkTextFont).
    font = style.lookup("FieldValue.TLabel", "font") or style.lookup(name, "font") or "TkDefaultFont"
    line = int(entry.tk.call("font", "metrics", font, "-linespace"))
    def measure(text):
        return int(entry.tk.call("font", "measure", font, text))
    cover = ttk.Label(entry, style="FieldValue.TLabel", cursor="xterm")
    focused = [False]

    def elide(text, width):
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if measure(text[:middle] + "…") <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def refresh(*_):
        try:
            width = entry.winfo_width() - left - right
            text = variable.get()
            if focused[0] or width <= 1 or measure(text) <= width:
                cover.place_forget()
                return
            entry.xview_moveto(0)   # the entry shows the same start when it takes over
            cover.configure(text=elide(text, width))
            cover.place(x=left, y=top, width=width, height=line)
        except tk.TclError:
            pass  # being destroyed

    def focus(value):
        focused[0] = value
        refresh()

    def click(event):
        entry.focus_set()
        entry.icursor(entry.index(f"@{event.x + left}"))
        return "break"
    entry.bind("<FocusIn>", lambda _e: focus(True), add="+")
    entry.bind("<FocusOut>", lambda _e: focus(False), add="+")
    entry.bind("<Configure>", refresh, add="+")
    cover.bind("<Button-1>", click)
    token = variable.trace_add("write", refresh)
    def untrace(event):
        if event.widget is entry:
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass  # the interpreter is going away with the window
    entry.bind("<Destroy>", untrace, add="+")
    return cover


def _hide_idle_scrollbar(table, tree):
    """Show the table's own scrollbar only while its rows overflow (if it has one)."""
    bar = next((child for child in table.winfo_children() if isinstance(child, ttk.Scrollbar)), None)
    if bar is None:
        return
    shown = [True]
    def scrolled(first, last):
        bar.set(first, last)
        needed = float(first) > 0 or float(last) < 1
        if needed != shown[0]:
            shown[0] = needed
            if needed:
                bar.grid()
            else:
                bar.grid_remove()
    tree.configure(yscrollcommand=scrolled)
