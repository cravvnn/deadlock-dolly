"""Nonblocking desktop update checks; Tk is only touched on the UI thread."""
from __future__ import annotations
import os
from pathlib import Path
import queue
import threading
import time
from tkinter import ttk
from . import __version__
from .runtime import application_root, is_frozen
from . import updater
from .video_export import ACTIVE_STATES
from .update_worker import check_processes, pending_work, launch_worker


class UpdateUI:
    def __init__(self, app, *, skip_startup=False):
        self.app = app
        self.events = queue.Queue()
        self.running = False
        self.ready = None
        self.restarting = False
        self.ready_after = 0
        self.notice = ttk.Button(app.sidebar, text="Updates", command=self.check, width=12)
        self.enabled = is_frozen() and (application_root() / updater.MANIFEST).is_file()
        if not self.enabled:
            app.update_status.set("Automatic installation is available in the packaged Windows build.")
        app.root.after(250, self.poll)
        if self.enabled and app.app_settings.auto_updates and not skip_startup:
            app.root.after(2000, self.check)

    def check(self):
        if self.running or self.restarting:
            return
        if not self.enabled:
            self.app.update_status.set("Use a packaged Windows build for automatic updates.")
            return
        if self.ready:
            self.restart()
            return
        self.running = True
        self.notice.configure(text="Checking...")
        self.notice.pack(side="bottom", fill="x", pady=(12, 0))
        self.app.update_status.set("Checking your publisher's Latest release...")
        def run():
            try:
                release = updater.check_latest(__version__)
                if release is None:
                    self.events.put(("status", "Dolly is up to date with the published Latest release."))
                else:
                    work = updater.download_update(release, application_root(),
                        lambda message: self.events.put(("status", message)))
                    self.events.put(("ready", (work, release["version"])))
            except Exception as error:
                self.events.put(("status", "Update check deferred: " + str(error)))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=run, name="Dolly update download", daemon=True).start()

    def safe_to_restart(self):
        app = self.app
        status = app.controller.status()
        return not (app.busy or app.playing or app.dirty or status.get("game_running")
                    or app.root.grab_current() is not None
                    or app.video_export.status().get("state") in ACTIVE_STATES
                    or app.startup_cancel is not None)

    def restart(self):
        if not self.ready or self.restarting:
            return
        try:
            safe = self.safe_to_restart()
            if safe:
                check_processes(application_root().resolve(), exclude_pid=os.getpid())
        except (RuntimeError, OSError):
            safe = False
        if not safe:
            self.app.update_status.set("Update ready. Save your shot and close the game, then restart to update.")
            return
        work, version = self.ready
        target = application_root().resolve()
        try:
            launch_worker(target, work)
        except OSError as error:
            self.app.update_status.set("Could not start updater: " + str(error))
            return
        self.restarting = True
        self.app.update_status.set("Installing Dolly " + version + "; restarting...")
        self.app._on_close()

    def poll(self):
        if self.app.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.app.update_status.set(value)
                    if value.startswith("Downloading Dolly"):
                        self.notice.configure(text="Update: " + value.rsplit(" ", 1)[-1])
                elif kind == "ready":
                    self.ready = value
                    self.ready_after = time.monotonic() + 10
                    self.notice.configure(text="Restart update", style="Primary.TButton")
                    self.app.update_status.set("Dolly " + value[1] + " downloaded. Restarting when idle...")
                    self.app.update_check_button.configure(text="Restart to update")
                elif kind == "done":
                    self.running = False
                    if not self.ready:
                        self.notice.pack_forget()
        except queue.Empty:
            pass
        if self.ready and self.app.app_settings.auto_updates and not self.restarting and time.monotonic() >= self.ready_after:
            self.restart()
        self.app.root.after(500, self.poll)


def recover_pending():
    """Normal launches resume an interrupted rollback before opening the editor."""
    if not is_frozen():
        return False
    target = application_root().resolve()
    work = pending_work(target)
    if work is None:
        return False
    launch_worker(target, work, recover=True)
    return True
