"""Message dialog with the standard OK plus a copy-details button."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import __version__

COPY_LABEL = "Copy error details"
COPIED_LABEL = "Copied"


def format_error_details(title: str, message: str, version: str = __version__) -> str:
    headline = f"Deadlock Dolly {version}"
    if str(title).strip():
        headline += f" | {str(title).strip()}"
    body = str(message).strip()
    return f"{headline}\n{body}" if body else headline


def show_error(parent, title, message, version: str = __version__) -> None:
    details = format_error_details(title, message, version)
    try:
        _open(parent, str(title), str(message).strip(), details)
    except tk.TclError:
        try:
            messagebox.showerror(title, str(message), parent=parent)
        except tk.TclError:
            pass


def _open(parent, title, message, details) -> None:
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.resizable(False, False)
    window.protocol("WM_DELETE_WINDOW", window.destroy)

    frame = ttk.Frame(window, padding=(14, 14, 14, 10))
    frame.pack(fill="both", expand=True)

    body = tk.Message(frame, text=message, width=440, justify="left", anchor="w",
                      font=tkfont.nametofont("TkDefaultFont"))
    body.pack(fill="x")

    def copy_details(*_event):
        window.clipboard_clear()
        window.clipboard_append(details)
        window.update_idletasks()
        copy_button.configure(text=COPIED_LABEL)
        window.after(1500, restore_label)
        return "break"

    def restore_label():
        try:
            copy_button.configure(text=COPY_LABEL)
        except tk.TclError:
            pass

    def close_dialog(*_event):
        try:
            window.destroy()
        except tk.TclError:
            pass
        return "break"

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    ok_button = ttk.Button(buttons, text="OK", command=close_dialog)
    ok_button.pack(side="right")
    copy_button = ttk.Button(buttons, text=COPY_LABEL, command=copy_details)
    copy_button.pack(side="right", padx=(0, 8))

    window.bind("<Control-c>", copy_details)
    window.bind("<Return>", close_dialog)
    window.bind("<Escape>", close_dialog)

    window.update_idletasks()
    width = window.winfo_reqwidth()
    height = window.winfo_reqheight()
    x = parent.winfo_rootx() + max((parent.winfo_width() - width) // 2, 0)
    y = parent.winfo_rooty() + max((parent.winfo_height() - height) // 3, 0)
    window.geometry(f"+{x}+{y}")

    window.grab_set()
    ok_button.focus_set()
    try:
        parent.wait_window(window)
    finally:
        try:
            window.grab_release()
        except tk.TclError:
            pass
