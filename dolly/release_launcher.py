"""Self-contained public Dolly.exe: startup checks and interrupted-update recovery.

The editor runtime lives under _internal. This launcher bundles its own Python
and Tk so recovery can start even when the editor runtime was partly replaced.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
from . import __version__, updater, update_worker
from .settings import load_settings


def launcher_process_ids():
    """Exclude our own onefile bootloader, and wait for it before replacement."""
    child = os.getpid()
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return (child,)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    api.OpenProcess.restype = ctypes.c_void_p
    api.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    parent = os.getppid()
    handle = api.OpenProcess(0x1000, False, parent)
    if handle:
        try:
            text = ctypes.create_unicode_buffer(32768)
            size = ctypes.c_uint(len(text))
            if api.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
                if Path(text.value).resolve() == Path(sys.executable).resolve():
                    return child, parent
        finally:
            api.CloseHandle(handle)
    return (child,)


def open_editor(target, arguments=(), *, wait=False):
    command = [str(target / "_internal/DollyApp.exe"), *arguments]
    if not arguments:
        command.append("--updated")  # The public launcher already handled startup.
    return update_worker.launch_program(command, target, wait=wait)


def check_for_update(target, events, excluded):
    """Worker emits data only; the window and installation stay on the UI thread."""
    try:
        update_worker.check_processes(target, exclude_pid=excluded)
        release = updater.check_latest(__version__)
        if release is None:
            events.put(("open", "Dolly is up to date. Opening the editor..."))
        else:
            work = updater.download_update(release, target.parent,
                lambda message: events.put(("status", message)))
            events.put(("ready", work))
    except Exception as error:
        events.put(("open", "Update deferred. Opening Dolly..."))
        # Avoid writing preferences or application files on a failed check.
        if sys.stderr is not None:
            print("Dolly update deferred:", error, file=sys.stderr)


class StartupWindow:
    def __init__(self, target):
        self.target = target
        self.events = queue.Queue()
        self.closed = False
        self.outcome = "open"
        self.excluded = launcher_process_ids()
        self.root = tk.Tk()
        self.root.title("Deadlock Dolly")
        self.root.resizable(False, False)
        self.root.configure(background="#11171c")

        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets/dolly.ico"
        if icon.is_file():
            self.root.iconbitmap(str(icon))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Startup.TFrame", background="#11171c")
        style.configure("Startup.TLabel", background="#11171c", foreground="#e1e8eb", font=("Segoe UI", 10))
        style.configure("Title.Startup.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Startup.Horizontal.TProgressbar", background="#95dbcb", troughcolor="#23323b", bordercolor="#23323b", lightcolor="#95dbcb", darkcolor="#95dbcb", borderwidth=0)
        style.configure("Startup.TButton", padding=(12, 7), background="#23323b", foreground="#e1e8eb", font=("Segoe UI", 10), borderwidth=0)
        body = ttk.Frame(self.root, style="Startup.TFrame", padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="DEADLOCK DOLLY", style="Title.Startup.TLabel").pack(anchor="w")
        self.status = tk.StringVar(value="Checking for updates...")
        ttk.Label(body, textvariable=self.status, style="Startup.TLabel", wraplength=390).pack(anchor="w", pady=(18, 14))
        self.progress = ttk.Progressbar(body, mode="indeterminate", length=390, style="Startup.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.progress.start(15)
        ttk.Button(body, text="Open Dolly", style="Startup.TButton", command=self.finish).pack(anchor="e", pady=(20, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.finish)
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        self.root.geometry(f"+{max(0, (self.root.winfo_screenwidth()-width)//2)}+{max(0, (self.root.winfo_screenheight()-height)//2)}")

    def finish(self):
        if not self.closed:
            self.closed = True
            self.root.destroy()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "open":
                    self.status.set(value)
                    self.root.after(600, self.finish)
                elif kind == "ready":
                    try:
                        # Recheck after download; another editor/game may have opened.
                        update_worker.check_processes(self.target, exclude_pid=self.excluded)
                        update_worker.launch_worker(self.target, value, parent_pid=self.excluded[-1])
                    except (OSError, RuntimeError, ValueError):
                        self.status.set("Update deferred. Opening Dolly...")
                        self.root.after(600, self.finish)
                    else:
                        self.outcome = "updating"
                        self.finish()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def run(self):
        threading.Thread(target=check_for_update, args=(self.target, self.events, self.excluded),
                         daemon=True, name="Dolly startup update").start()
        self.root.after(100, self.poll)
        self.root.mainloop()
        return self.outcome


def main(argv=None, *, target=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--plan" in args:
        return update_worker.main(args)
    if "--updater-self-test" in args:
        return update_worker.main(["--self-test", *args[args.index("--updater-self-test")+1:]])
    target = Path(sys.executable).resolve().parent if target is None else Path(target).resolve()
    # Installation health checks run while a journal is intentionally pending.
    if any(flag in args for flag in ("--self-test", "--cleanup-session", "--help")):
        return open_editor(target, args, wait=True)
    pending = update_worker.pending_work(target)
    if pending is not None:
        ids = launcher_process_ids()
        update_worker.check_processes(target, exclude_pid=ids)
        update_worker.launch_worker(target, pending, recover=True, parent_pid=ids[-1])
        return 0
    try:
        automatic = load_settings().auto_updates
    except (OSError, ValueError):
        automatic = True
    if not args and automatic and (target / updater.MANIFEST).is_file():
        if StartupWindow(target).run() == "updating":
            return 0
    open_editor(target, args)
    return 0
