"""Dolly-styled Yes/No prompts using Tk's modal window lifecycle."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox as _system, simpledialog, ttk

from .. import ui_theme
from . import fonts
from .chrome import style_title_bar
from .scale import px, ui_scale


class _QuestionDialog(simpledialog.Dialog):
    def __init__(self, parent, title, message, *, cancel, kind="question"):
        self.message = message
        self.has_cancel = cancel
        self.kind = kind
        super().__init__(parent, title)

    def body(self, master):
        dp = lambda value: px(ui_scale(self), value)  # noqa: E731
        bg = ui_theme.TOKENS["bg"]
        self.configure(bg=bg)
        master.configure(bg=bg)
        style_title_bar(self)
        content = ttk.Frame(master, padding=dp((19, 17, 41, 19)))
        content.grid(row=0, column=0, sticky="nsew")
        text_width = 438 if self.title() == "Native DOF support" else 318
        content.columnconfigure(1, minsize=dp(text_width), weight=1)
        icon = tk.Canvas(content, width=dp(22), height=dp(22), bg=bg,
                         highlightthickness=0, borderwidth=0, takefocus=0)
        icon.grid(row=0, column=0, sticky="nw", padx=dp((0, 14)))
        ink = ui_theme.TOKENS["warn"] if self.kind == "warning" else ui_theme.TOKENS["accent"]
        if self.kind == "warning":
            icon.create_polygon(dp(11), dp(1), dp(21), dp(19), dp(1), dp(19),
                                fill=bg, outline=ink, width=max(1, dp(1)))
        else:
            icon.create_oval(dp(1), dp(1), dp(21), dp(21), outline=ink, width=max(1, dp(1)))
        icon.create_text(dp(11), dp(11), text={"question": "?", "info": "i", "warning": "!"}[self.kind], fill=ink,
                         font=(ui_theme.FONT_FAMILY, -dp(13)))
        tk.Label(content, text=self.title(), bg=bg, fg=ui_theme.TOKENS["text"],
                 font=fonts.font(self, "page_title"), anchor="w", justify="left", wraplength=dp(text_width)).grid(
                     row=0, column=1, sticky="ew", pady=dp((0, 15)))
        if len(self.message) > 400 or self.message.count("\n") > 8:
            # Reports can be much longer than a screen; retain all text and
            # make it scrollable instead of clipping diagnostic details.
            area = tk.Frame(content, bg=bg)
            area.grid(row=1, column=1, sticky="ew")
            view = tk.Text(area, width=1, height=12, wrap="word", bg=bg, fg=ui_theme.TOKENS["text"],
                           relief="flat", borderwidth=0, highlightthickness=0,
                           font=(ui_theme.FONT_FAMILY, -dp(13)))
            scroll = ttk.Scrollbar(area, orient="vertical", command=view.yview)
            view.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            view.pack(side="left", fill="both", expand=True)
            view.insert("1.0", self.message)
            view.configure(state="disabled")
        elif self.kind == "warning" and self.title() == "Dolly preferences" and "\n\n" in self.message:
            explanation, detail = self.message.split("\n\n", 1)
            tk.Label(content, text=explanation, bg=bg, fg=ui_theme.TOKENS["text"],
                     font=(ui_theme.FONT_FAMILY, -dp(13)), anchor="w", justify="left", wraplength=dp(text_width)).grid(
                         row=1, column=1, sticky="ew")
            tk.Label(content, text=detail, bg=ui_theme.TOKENS["field"], fg=ui_theme.TOKENS["muted"],
                     font=(ui_theme.FONT_FAMILY, -dp(12)), anchor="w", justify="left", wraplength=dp(text_width - 24),
                     padx=dp(12), pady=dp(10)).grid(row=2, column=1, sticky="ew", pady=dp((10, 0)))
        else:
            tk.Label(content, text=self.message, bg=bg, fg=ui_theme.TOKENS["text"],
                     font=(ui_theme.FONT_FAMILY, -dp(13)), anchor="w", justify="left", wraplength=dp(text_width)).grid(
                         row=1, column=1, sticky="ew")
        return self

    def buttonbox(self):
        dp = lambda value: px(ui_scale(self), value)  # noqa: E731
        actions = ttk.Frame(self, padding=dp((24, 0, 46, 20)))
        actions.pack(fill="x")
        ttk.Button(actions, text="Yes", style="Primary.TButton", command=self.yes).pack(side="right")
        ttk.Button(actions, text="No", command=self.no).pack(side="right", padx=dp((0, 10)))
        if self.has_cancel:
            ttk.Button(actions, text="Cancel", style="Quiet.TButton", command=self.cancel).pack(
                side="right", padx=dp((0, 10)))
        self.bind("<Return>", self.yes)
        self.bind("<Escape>", self.cancel)

    def yes(self, _event=None):
        self.result = True
        self.cancel()

    def no(self, _event=None):
        self.result = False
        self.cancel()


def _ask(title, message, *, parent, cancel):
    previous = parent.grab_current() if parent is not None else None
    status = previous.grab_status() if previous is not None else None
    try:
        return _QuestionDialog(parent, title, str(message), cancel=cancel).result
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


def askyesno(title, message, *, parent=None):
    """Return True only when Yes is chosen; close or Escape means No."""
    try:
        return bool(_ask(title, message, parent=parent, cancel=False))
    except tk.TclError:
        return bool(_system.askyesno(title, message, parent=parent))


def askyesnocancel(title, message, *, parent=None):
    """Return True, False or None for Yes, No or cancellation."""
    try:
        return _ask(title, message, parent=parent, cancel=True)
    except tk.TclError:
        return _system.askyesnocancel(title, message, parent=parent)


class _NoticeDialog(_QuestionDialog):
    def __init__(self, parent, title, message, kind):
        super().__init__(parent, title, message, cancel=False, kind=kind)

    def buttonbox(self):
        dp = lambda value: px(ui_scale(self), value)  # noqa: E731
        actions = ttk.Frame(self, padding=dp((24, 0, 46, 20)))
        actions.pack(fill="x")
        ttk.Button(actions, text="OK", style="Primary.TButton", command=self.ok).pack(side="right")
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)


def _notice(title, message, *, parent, kind):
    previous = parent.grab_current() if parent is not None else None
    status = previous.grab_status() if previous is not None else None
    try:
        _NoticeDialog(parent, title, str(message), kind)
        return "ok"
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


def showinfo(title, message, *, parent=None):
    try:
        return _notice(title, message, parent=parent, kind="info")
    except tk.TclError:
        return _system.showinfo(title, message, parent=parent)


def showwarning(title, message, *, parent=None):
    try:
        return _notice(title, message, parent=parent, kind="warning")
    except tk.TclError:
        return _system.showwarning(title, message, parent=parent)
