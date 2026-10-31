"""Dolly's name-entry prompt with Tk's standard modal lifecycle."""
from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk

from .. import ui_theme
from . import fonts
from .chrome import style_title_bar
from .scale import ui_scale, px


class _TextDialog(simpledialog.Dialog):
    def __init__(self, parent, title, prompt, initialvalue=None):
        self.prompt = prompt
        self.initialvalue = initialvalue
        super().__init__(parent, title)

    def body(self, master):
        scale = ui_scale(self)
        dp = lambda value: px(scale, value)  # noqa: E731
        self.configure(bg=ui_theme.TOKENS["bg"])
        master.configure(bg=ui_theme.TOKENS["bg"])
        style_title_bar(self)
        content = ttk.Frame(master, padding=dp((19, 17, 19, 19)))
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, minsize=dp(330), weight=1)
        tk.Label(content, text=self.title(), bg=ui_theme.TOKENS["bg"], fg=ui_theme.TOKENS["text"],
                 font=fonts.font(self, "page_title"), anchor="w").grid(
                     row=0, column=0, sticky="ew", pady=dp((0, 15)))
        ttk.Label(content, text=self.prompt, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=dp((0, 6)))
        self.entry = ttk.Entry(content, width=1)
        self.entry.grid(row=2, column=0, sticky="ew")
        if self.initialvalue is not None:
            self.entry.insert(0, self.initialvalue)
            self.entry.select_range(0, tk.END)
        return self.entry

    def buttonbox(self):
        scale = ui_scale(self)
        dp = lambda value: px(scale, value)  # noqa: E731
        actions = ttk.Frame(self, padding=dp((24, 0, 24, 20)))
        actions.pack(fill="x")
        ttk.Button(actions, text="OK", style="Primary.TButton", command=self.ok).pack(side="right")
        ttk.Button(actions, text="Cancel", command=self.cancel).pack(side="right", padx=dp((0, 10)))
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        self.result = self.entry.get()
        return True


def askstring(title, prompt, *, initialvalue=None, parent=None):
    """Return the entered string, or ``None`` on Cancel, Escape, or close."""
    previous = parent.grab_current() if parent is not None else None
    status = previous.grab_status() if previous is not None else None
    try:
        return _TextDialog(parent, title, prompt, initialvalue).result
    finally:
        if previous is not None:
            try:
                if previous.winfo_exists():
                    if status == "global":
                        previous.grab_set_global()
                    else:
                        previous.grab_set()
            except tk.TclError:
                pass
