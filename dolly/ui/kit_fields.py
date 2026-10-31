"""Field helpers of the page kit: the Replay page's private ones, made public.

``library_page`` keeps its own copies (Replay is not edited); these behave the same
(PLAN-5 2.8): an at-rest cover that elides a long value with "…", a placeholder that
never writes its variable, a table scrollbar shown only while its rows overflow, and
the hug width of a numeric field.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from .scale import px, ui_scale
from .stage_page import TREE_CELL_INSET, bleed_left

__all__ = ["TREE_CELL_INSET", "bleed_left", "elide_at_rest", "placeholder", "hide_idle_scrollbar", "number_width"]


def _untrace_on_destroy(entry, variable, token):
    def untrace(event):
        if event.widget is entry:
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass  # the interpreter is going away with the window
    entry.bind("<Destroy>", untrace, add="+")


def elide_at_rest(entry, variable) -> ttk.Label:
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
    cover.bind("<Button-1>", click, add="+")
    _untrace_on_destroy(entry, variable, variable.trace_add("write", refresh))
    refresh()   # an entry that is already laid out gets no <Configure> to start from
    return cover


def placeholder(entry, variable, text, *, x=13) -> ttk.Label:
    """A dim ``text`` drawn over ``entry`` while it is empty and unfocused; it never writes ``variable``.

    ``x`` is the design px from the entry's left edge (13: a kit field's text inset).
    Clicking the placeholder focuses the entry. Returns the label (a child of the entry).
    """
    s = ui_scale(entry)
    label = ttk.Label(entry, text=text, style="Placeholder.TLabel", cursor="xterm")
    focused = [False]

    def sync(*_):
        try:
            if variable.get() or focused[0]:
                label.place_forget()
            else:
                label.place(x=px(s, x), rely=.5, anchor="w")
        except tk.TclError:
            pass  # being destroyed

    def focus(value):
        focused[0] = value
        sync()
    entry.bind("<FocusIn>", lambda _e: focus(True), add="+")
    entry.bind("<FocusOut>", lambda _e: focus(False), add="+")
    label.bind("<Button-1>", lambda _e: (entry.focus_set(), "break")[1], add="+")
    _untrace_on_destroy(entry, variable, variable.trace_add("write", sync))
    sync()
    return label


def hide_idle_scrollbar(table, tree) -> None:
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


def number_width(widget, widest: str, design_px: int) -> int:
    """The hug width of a numeric field: ``design_px``, or wider when ``widest`` would not fit (px).

    ``widest`` is measured in the value face (an entry's text) plus the field's 13 px insets.
    """
    s = ui_scale(widget)
    font = (ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"])
    measured = int(widget.tk.call("font", "measure", font, widest))
    return max(px(s, design_px), measured + px(s, 26))
