"""Metadata-only discovery and explicit, version-bound update consent."""
from __future__ import annotations
import logging
import os
import queue
import threading
import webbrowser
from . import __version__, definitions, updater, edition
from .runtime import application_root, is_frozen
from .ui.update_notice import UpdateNotice
from .video_export import ACTIVE_STATES
from .update_worker import check_processes, pending_work, launch_worker

LOG = logging.getLogger("dolly")


class UpdateUI:
    error = False           # the notice's detail is an error (Figma [design reference]: the updater could not start)

    def __init__(self, app, *, skip_startup=False):
        self.app = app
        self.events = queue.Queue()
        self.running = False
        self.available = None
        self.ready = None
        self.ready_release = None
        self.approved = None
        self.restarting = False
        self.dismissed = False
        self.request_id = 0
        self.message = ""
        self.notice = UpdateNotice(app.update_notice_parent, update=self.update_now,
                                   notes=self.open_notes, full_notes=self.open_full_notes,
                                   later=self.later, reopen=self.reopen, height_limit=self._notes_height_limit)
        if getattr(app, "notebook", None) is not None:
            app.notebook.bind("<Configure>", lambda _event: self.notice.fit_height(), add="+")
        self.enabled = (edition.allow_standard_updates() and is_frozen()
                        and (application_root() / updater.MANIFEST).is_file())
        if not self.enabled:
            app.update_status.set("Update installation is available in the packaged Windows build.")
        app.root.after(250, self.poll)
        if self.enabled and app.app_settings.auto_updates and not skip_startup:
            app.root.after(2000, self.check)
        if edition.allow_standard_updates() and app.app_settings.auto_updates and not skip_startup:
            # Build definitions are small and useful to source builds too, so
            # this runs even when the packaged updater is unavailable.
            app.root.after(2400, self.refresh_definitions)

    def refresh_definitions(self):
        """Adopt published build definitions for this and later launches.

        Failure is silent by design: the bundled reviewed table stays active
        and the user is never blocked or interrupted by a network problem.
        """
        if self.app.closed:
            return

        def run():
            try:
                document = definitions.refresh()
                LOG.info("Published build definitions: %s", document.describe())
            except definitions.DefinitionsError as exc:
                LOG.info("Published build definitions were not used: %s", exc)
            except Exception:  # never let background network work surface a dialog
                LOG.info("Published build definitions were not used.")
        threading.Thread(target=run, name="Dolly build definitions", daemon=True).start()

    @staticmethod
    def release_key(release):
        return tuple(release[name] for name in ("version", "url", "sha256", "size"))

    def _notes_height_limit(self):
        """Room for the open release highlights: the page area below the banner.

        Kara's pane (Figma [design reference]) shows every note in full and scrolls only when it is
        taller than the page, so the highlights may push the whole page down (the page then
        scrolls, its fallback). Their own height is added back while they show, so the budget
        stays the same as they displace the page.
        """
        workspace = getattr(self.app, "notebook", None)
        if workspace is None:
            return self.notice.MAX_NOTES_HEIGHT * self.notice.scale
        current = self.notice.notes_clip.winfo_height() if self.notice.notes_clip.winfo_ismapped() else 0
        return max(1, workspace.winfo_height() + current)

    def _status(self, text, *, error=False):
        self.message = text
        self.error = error
        self.app.update_status.set(text)
        self._render()

    def _render(self):
        if self.available is None:
            self.notice.hide()
        else:
            self.notice.set_notes(self.available.get("notes", []), release=self.release_key(self.available))
            if self.dismissed:
                self.notice.collapse(self.available["version"])
            else:
                self.notice.show(self.available["version"], self.message, busy=self.running, error=self.error)

    def reopen(self):
        self.dismissed = False
        self._render()

    def later(self):
        # Revoking consent also covers a download that completes after dismissal.
        self.approved = None
        self.dismissed = True
        self._render()

    def open_notes(self):
        if self.available is not None and not self.app.closed:
            self.notice.toggle_notes()

    def open_full_notes(self):
        if self.available is not None and not self.app.closed:
            try:
                webbrowser.open(self.available["notes_url"])
            except OSError:
                self._status("Could not open release notes. Your installed Dolly is unchanged.")

    def check(self):
        if self.restarting or self.app.closed:
            return
        if not edition.allow_standard_updates():
            self.app.update_status.set(
                "Environment Capture updates are supplied as separate ZIPs.")
            return
        if self.available is not None:
            self.reopen()
            return
        if self.running:
            return
        if not self.enabled:
            self.app.update_status.set("Use a packaged Windows build to check for updates.")
            return
        self.running = True
        self.request_id += 1
        request = self.request_id
        self.app.update_status.set("Checking your publisher's Latest release...")

        def run():
            try:
                release = updater.check_latest(__version__)
                self.events.put((request, "available", release))
            except Exception as error:
                self.events.put((request, "error", "Update check deferred: " + str(error)))
            finally:
                self.events.put((request, "done", None))
        threading.Thread(target=run, name="Dolly update check", daemon=True).start()

    def safe_to_restart(self):
        app = self.app
        status = app.controller.status()
        return not (app.busy or app.playing or app.dirty or status.get("game_running")
                    or app.root.grab_current() is not None
                    or app.video_export.status().get("state") in ACTIVE_STATES
                    or app.startup_cancel is not None)

    def _can_update(self):
        try:
            if self.safe_to_restart():
                check_processes(application_root().resolve(), exclude_pid=os.getpid())
                return True
        except (RuntimeError, OSError):
            pass
        self.approved = None
        self._status("Save your shot and close Deadlock, then click Update now. Dolly will stay open.")
        return False

    def update_now(self):
        if self.running or self.restarting or self.app.closed or self.available is None:
            return
        self.dismissed = False
        if not self._can_update():
            return
        release = dict(self.available)
        self.approved = self.release_key(release)
        if self.ready is not None and self.ready_release == self.approved:
            self.restart()
            return
        self.running = True
        self.request_id += 1
        request = self.request_id
        self._status(f"Downloading Dolly {release['version']}...")

        def run():
            try:
                work = updater.download_update(release, application_root(),
                    lambda message: self.events.put((request, "status", message)))
                self.events.put((request, "ready", (work, release)))
            except Exception as error:
                self.events.put((request, "error", "Update deferred: " + str(error)))
            finally:
                self.events.put((request, "done", None))
        threading.Thread(target=run, name="Dolly approved update download", daemon=True).start()

    def restart(self):
        if self.restarting or self.app.closed or self.available is None or self.ready is None:
            return
        key = self.release_key(self.available)
        if self.approved != key or self.ready_release != key:
            return
        # Every attempt consumes consent. Unsafe or failed handoffs require a new click.
        self.approved = None
        if not self._can_update():
            return
        work, version = self.ready
        try:
            launch_worker(application_root().resolve(), work)
        except (OSError, RuntimeError, ValueError) as error:
            self._status("Could not start updater: " + str(error) + ". Click Update now to retry.", error=True)
            return
        self.restarting = True
        self.app.update_status.set("Installing Dolly " + version + "; restarting...")
        self.app._on_close()

    def poll(self):
        if self.app.closed:
            self.approved = None
            return
        try:
            while True:
                request, kind, value = self.events.get_nowait()
                if request != self.request_id or self.restarting:
                    continue
                if kind == "available":
                    self.running = False
                    self.available = dict(value) if value is not None else None
                    self.approved = None
                    self.ready = self.ready_release = None
                    if value is None:
                        self._status("Dolly is up to date with the published Latest release.")
                    else:
                        self.app.update_check_button.configure(text="View update")
                        self._status(f"Installed: {__version__}. Download and install only when you click Update now.")
                elif kind == "status":
                    self._status(value)
                elif kind == "ready":
                    self.running = False
                    work, release = value
                    key = self.release_key(release)
                    if self.available is None or key != self.release_key(self.available):
                        continue
                    self.ready = (work, release["version"])
                    self.ready_release = key
                    self._status("Download verified. Click Update now when you are ready to restart Dolly.")
                    if self.approved == key and not self.dismissed:
                        self.restart()
                elif kind == "error":
                    self.running = False
                    self.approved = None
                    self._status(value)
                elif kind == "done":
                    self.running = False
                    self._render()
        except queue.Empty:
            pass
        self.app.root.after(250, self.poll)


def recover_pending():
    """Finish interrupted rollback before opening a partially replaced runtime."""
    if edition.is_environment():
        return False
    if not is_frozen():
        return False
    target = application_root().resolve()
    work = pending_work(target)
    if work is None:
        return False
    launch_worker(target, work, recover=True)
    return True
