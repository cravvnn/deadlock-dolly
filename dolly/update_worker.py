"""Embedded update process: backed-up file replacement and recoverable rollback."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from .updater import MANIFEST, digest_file, read_manifest, version_key, owned_name

PENDING = "DOLLY_UPDATE_PENDING.json"
# The update helper keeps one branded window on screen for the whole handoff so
# users never see the editor close and reopen with nothing in between. Even a
# fast install stays visible for at least this long so it never just flashes.
MIN_PROGRESS_SECONDS = 2.0


def atomic_json(path, value):
    path = Path(path)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dolly-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write((json.dumps(value, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        # A serialization or write failure must not leave .dolly-* temporaries
        # behind in the install root; a successful replace already moved it.
        temporary.unlink(missing_ok=True)


def checked_file(root, name):
    if name != MANIFEST and not owned_name(name):
        raise ValueError("Unmanaged update target")
    root = Path(root).resolve()
    path = root / name
    if not path.resolve().is_relative_to(root):
        raise ValueError("Update path escapes the installation")
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError("Linked application files cannot be updated")
    if path.exists() and not path.is_file():
        raise ValueError("Application file is occupied by a directory")
    return path


def _clear_readonly(path):
    """An executable extracted from a ZIP can carry the read-only attribute.

    os.replace onto a read-only file fails with WinError 5 (access denied) even
    though nothing holds it open, so clear the flag before moving the new file in.
    """
    try:
        if path.exists() and not os.access(path, os.W_OK):
            os.chmod(path, 0o666)
    except OSError:
        pass


def _replace_retrying(temporary, target, attempts=6, delay=0.25):
    """Move a staged file into place, tolerating transient Windows locks.

    Real-time antivirus scanning routinely holds a just-written executable for a
    moment, so os.replace can fail with WinError 5 (access denied) or 32 (sharing
    violation) with no Dolly process running. Clear read-only and retry briefly;
    a genuine lock (for example a protected folder) still fails closed.
    """
    _clear_readonly(target)
    for attempt in range(attempts):
        try:
            os.replace(temporary, target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".dolly-new-", delete=False) as out:
        temporary = Path(out.name)
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("ab") as durable:
            durable.flush()
            os.fsync(durable.fileno())
        _replace_retrying(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def rollback(plan):
    work, target = Path(plan["work"]).resolve(), Path(plan["target"]).resolve()
    state = json.loads((work / "journal.json").read_text(encoding="utf-8"))
    if state["target"] != str(target):
        raise ValueError("Rollback target mismatch")
    # Backups are complete before any installed file is touched. Repeating this
    # operation after interruption is safe; retain backups until recovery passes.
    for name, old_digest in state["before"].items():
        path = checked_file(target, name)
        if old_digest is None:
            path.unlink(missing_ok=True)
        else:
            if path.is_file() and digest_file(path) == old_digest:
                continue
            backup = checked_file(work / "backup", name)
            if digest_file(backup) != old_digest:
                raise ValueError("Update backup is damaged")
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, path)
    state["state"] = "rolled_back"
    atomic_json(work / "journal.json", state)
    (target / PENDING).unlink(missing_ok=True)


def install(plan, health_check=lambda target: None, replace=replace_file):
    work, target = Path(plan["work"]).resolve(), Path(plan["target"]).resolve()
    payload = work / "payload"
    old, new = read_manifest(target), read_manifest(payload)
    if version_key(new["version"]) <= version_key(old["version"]):
        raise ValueError("Refusing a same-version update or downgrade")
    if (target / PENDING).exists() or (work / "journal.json").exists():
        raise ValueError("An unfinished update must be recovered first")
    names = sorted(set(old["files"]) | set(new["files"]) | {MANIFEST})
    before = {}
    modified = set()
    for name in names:
        path = checked_file(target, name)
        if name != MANIFEST:
            if name in new["files"] and digest_file(checked_file(payload, name)) != new["files"][name]:
                raise ValueError("Staged update changed")
            if path.exists() and name not in old["files"]:
                raise ValueError("Update would overwrite an unowned file: " + name)
            if name in old["files"] and path.is_file() and digest_file(path) != old["files"][name]:
                # A locally modified application file (for example a hand-built
                # DLL) is backed up like any other before the new version wins.
                modified.add(name)
        before[name] = digest_file(path) if path.is_file() else None
        if path.is_file():
            backup = checked_file(work / "backup", name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, backup)
            with backup.open("ab") as durable:
                durable.flush()
                os.fsync(durable.fileno())
            if digest_file(backup) != before[name]:
                raise ValueError("Update backup verification failed")
    state = {"state": "applying", "target": str(target), "before": before, "modified": sorted(modified)}
    atomic_json(work / "journal.json", state)
    atomic_json(target / PENDING, plan)
    try:
        # Manifest is last so it describes a fully installed set.
        for name in [n for n in names if n != MANIFEST] + [MANIFEST]:
            path = checked_file(target, name)
            if name == MANIFEST or name in new["files"]:
                replace(checked_file(payload, name), path)
            else:
                path.unlink(missing_ok=True)
        health_check(target)
        state["state"] = "complete"
        atomic_json(work / "journal.json", state)
        (target / PENDING).unlink()
    except BaseException:
        rollback(plan)
        raise


@contextmanager
def install_lock(target):
    import msvcrt
    path = Path(target) / ".dolly-update.lock"
    with path.open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def wait_parent(pid):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    api.WaitForSingleObject.restype = ctypes.c_uint
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return  # Parent already exited.
        raise OSError("Cannot verify that Dolly closed")
    try:
        if api.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError("Dolly did not close; update was not installed")
    finally:
        api.CloseHandle(handle)


def smoke(target, work):
    report = work / "installed-smoke.json"
    report.unlink(missing_ok=True)
    # Hidden: the check must not paint a window over the update progress window.
    result = subprocess.run([str(target / "Dolly.exe"), "--self-test-hidden", str(report)], cwd=work, timeout=60)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    if result.returncode or data.get("passed") is not True or data.get("frozen") is not True or data.get("game_launched") is not False:
        raise RuntimeError("Updated Dolly failed its startup check")


def notify(text):
    from .ui.scale import enable_dpi_awareness
    enable_dpi_awareness()  # a sharp message box on scaled displays
    ctypes.windll.user32.MessageBoxW(None, text, "Dolly update", 0x40)


class UpdateWindow:
    """One branded window shown while an update installs, verifies and relaunches.

    The helper copies Dolly.exe and re-runs it as DollyUpdater.exe; without this
    window the editor closes, files are replaced with no visible owner, and the
    relaunch smoke test briefly paints a window, which reads as random flashing.
    This keeps a single steady window on screen across the whole handoff.
    """
    scale = 1.0  # display scale; sizes below are design px

    def __init__(self):
        from . import ui_theme
        from .ui.scale import enable_dpi_awareness, ui_scale
        # Before the window exists, like the editor: drawn sharp at the display's DPI.
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.scale = ui_scale(self.root)
        dp = self._dp
        self.root.title("Deadlock Dolly - Updating")
        self.root.resizable(False, False)
        self.root.configure(background=ui_theme.TOKENS["bg"])
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets/dolly.ico"
        if icon.is_file():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Update.TFrame", background=ui_theme.TOKENS["bg"])
        # FONT_FAMILY stays the Segoe UI fallback here on purpose: this process replaces Dolly's
        # files, and a privately registered font file (Inter, dolly.ui.fonts) stays open until exit.
        style.configure("Update.TLabel", background=ui_theme.TOKENS["bg"],
                        foreground=ui_theme.TOKENS["text"], font=(ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]))
        style.configure("Title.Update.TLabel", font=(ui_theme.FONT_FAMILY, 16, "bold"))
        # clam draws the bar -arrowsize thick (14 px at 100 %, in screen pixels at any DPI).
        style.configure("Update.Horizontal.TProgressbar", background=ui_theme.TOKENS["accent"],
                        troughcolor=ui_theme.TOKENS["button"], bordercolor=ui_theme.TOKENS["button"],
                        lightcolor=ui_theme.TOKENS["accent"], darkcolor=ui_theme.TOKENS["accent"], borderwidth=0,
                        arrowsize=dp(14))
        style.configure("Update.TButton", padding=dp((12, 7)), background=ui_theme.TOKENS["button"],
                        foreground=ui_theme.TOKENS["text"], font=(ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"]),
                        borderwidth=0)
        body = ttk.Frame(self.root, style="Update.TFrame", padding=dp(24))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="DEADLOCK DOLLY", style="Title.Update.TLabel").pack(anchor="w")
        self.status = tk.StringVar(value="Updating Dolly...")
        self.message = ttk.Label(body, textvariable=self.status, style="Update.TLabel", wraplength=dp(390))
        self.message.pack(anchor="w", pady=dp((18, 14)))
        self.progress = ttk.Progressbar(body, mode="indeterminate", length=dp(390), style="Update.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.progress.start(15)
        self.close_button = ttk.Button(body, text="Close", style="Update.TButton", command=self._close)
        # The window is not cancellable during install; the Close button appears
        # only if the update fails, so a stuck window can still be dismissed.
        self.closed = False
        self._close_allowed = False
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        self.root.geometry(f"+{max(0, (self.root.winfo_screenwidth()-width)//2)}"
                           f"+{max(0, (self.root.winfo_screenheight()-height)//2)}")

    def _dp(self, value):
        from .ui.scale import px
        return px(self.scale, value)

    def set_status(self, text):
        self.status.set(str(text))

    def show_failure(self, text):
        self.progress.stop()
        self.progress.pack_forget()
        self.status.set(str(text))
        self._close_allowed = True
        self.close_button.pack(anchor="e", pady=self._dp((20, 0)))

    def _request_close(self):
        """Ignore a user close while the transaction is running.

        The worker is a daemon thread, so destroying its window can terminate
        the helper in the middle of replacing files. The failure screen is
        dismissible; successful installs close themselves after the hold.
        """
        if self._close_allowed:
            self._close()

    def _close(self):
        if not self.closed:
            self.closed = True
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def _progress_helper_window(work, target, *, recover, parent_pid):
    """Run the install/verify/relaunch on a worker while one window stays up.

    Returns the same exit semantics as the non-GUI path. Worker results arrive
    through a queue drained on the UI thread (Tk is not touched off it). Any
    failure is shown in the window instead of a separate message box, and the
    window stays until the user closes it so the reason is never lost in a flash.
    """
    import queue
    window = UpdateWindow()
    events = queue.Queue()
    result = {}
    started = time.monotonic()

    def worker():
        try:
            if parent_pid:
                wait_parent(parent_pid)
            _apply_plan({"work": str(work), "target": str(target)}, work, target, recover,
                        on_status=lambda text: events.put(("status", text)))
            result["passed"] = True
            events.put(("passed", None))
        except Exception as error:
            result["passed"] = False
            result["error"] = error
            try:
                atomic_json(work / "result.json", {"passed": False, "error": str(error)})
            except OSError:
                pass
            events.put(("failed", _install_error_message(error, work)))

    def poll():
        if window.closed:
            return
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "status":
                    window.set_status(value)
                elif kind == "failed":
                    window.show_failure(value)
                elif kind == "passed":
                    window.set_status("Update installed. Opening Dolly...")
                    try:
                        launch_program([str(target / "Dolly.exe"), "--updated"], target)
                    except (OSError, RuntimeError) as error:
                        # The transaction already succeeded. Don't report an
                        # install failure or leave an undismissable progress
                        # window when only the subsequent launch failed.
                        result["passed"] = False
                        window.show_failure(
                            "Dolly was updated, but could not reopen automatically. "
                            "Close this window and open Dolly.exe from its folder.\n\n"
                            + str(error))
                    else:
                        hold_then_close()
        except queue.Empty:
            pass
        window.root.after(100, poll)

    def hold_then_close():
        # Keep the window at least MIN_PROGRESS_SECONDS so a fast update is still
        # visible rather than a flash.
        remaining = MIN_PROGRESS_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            window.root.after(int(remaining * 1000), window._close)
        else:
            window._close()

    threading.Thread(target=worker, name="Dolly update install", daemon=True).start()
    window.root.after(100, poll)
    window.root.mainloop()
    return 0 if result.get("passed") else 1


def check_processes(target, exclude_pid=0):
    """Conservative last-moment guard, independent of desktop telemetry."""
    excluded = {exclude_pid} if isinstance(exclude_pid, int) else set(exclude_pid)
    target = Path(target).resolve()
    from ctypes import wintypes as wt
    class Entry(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                    ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD), ("name", wt.WCHAR * 260)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    api.CreateToolhelp32Snapshot.restype = wt.HANDLE
    api.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
    api.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    api.OpenProcess.restype = wt.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    api.CloseHandle.argtypes = [wt.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("Cannot check for active game processes")
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        more = api.Process32FirstW(snapshot, ctypes.byref(entry))
        if not more:
            raise OSError("Cannot enumerate active processes")
        while more:
            if entry.name.lower() == "deadlock.exe":
                raise RuntimeError("Close Deadlock before installing the update")
            if entry.name.lower() in ("dolly.exe", "dollyapp.exe") and entry.pid not in excluded:
                handle = api.OpenProcess(0x1000, False, entry.pid)
                if not handle:
                    raise RuntimeError("Cannot verify another Dolly process; close it before updating")
                try:
                    buffer = ctypes.create_unicode_buffer(32768); length = wt.DWORD(len(buffer))
                    if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        raise OSError("Cannot identify another Dolly process")
                    if Path(buffer.value).resolve() in (target / "Dolly.exe", target / "_internal/DollyApp.exe"):
                        raise RuntimeError("Another instance of this Dolly installation is still open")
                finally:
                    api.CloseHandle(handle)
            more = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)


def launch_program(command, cwd, *, wait=False):
    from .runtime import external_program_environment
    with external_program_environment() as environment:
        environment = dict(os.environ) if environment is None else environment
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        process = subprocess.Popen(command, cwd=cwd, env=environment,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return process.wait() if wait else process


def launch_worker(target, work, *, recover=False, parent_pid=None):
    target, work = Path(target).resolve(), Path(work).resolve()
    if work.parent != target or not work.name.startswith(".dolly-update-"):
        raise ValueError("Unexpected update workspace")
    # A private copy can replace Dolly.exe after both onefile processes close.
    helper = work / "DollyUpdater.exe"
    if not recover:
        shutil.copyfile(target / "Dolly.exe", helper)
        atomic_json(work / "plan.json", {"work": str(work), "target": str(target)})
    elif not helper.is_file():
        shutil.copyfile(target / "Dolly.exe", helper)
    command = [str(helper), "--plan", str(work / "plan.json"), "--parent",
               str(os.getpid() if parent_pid is None else parent_pid)]
    if recover:
        command.append("--recover")
    # Show the update window for a normal install so the user sees progress
    # instead of the editor closing and file replacement happening invisibly.
    if not recover:
        command.append("--progress")
    return launch_program(command, work)


def pending_work(target):
    target = Path(target).resolve()
    marker = target / PENDING
    if not marker.exists():
        return None
    plan = json.loads(marker.read_text(encoding="utf-8"))
    work = Path(plan["work"]).resolve()
    if Path(plan["target"]).resolve() != target or work.parent != target or not work.name.startswith(".dolly-update-"):
        raise ValueError("Invalid pending update recovery location")
    saved = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    if saved != plan:
        raise ValueError("Pending update plan disagrees with recovery journal")
    return work


def _workspace_finished(work, keep):
    """Decide whether a workspace is safe to delete or still in use."""
    if keep is not None and work.resolve() == keep:
        return False
    journal = work / "journal.json"
    if journal.exists():
        try:
            state = json.loads(journal.read_text(encoding="utf-8")).get("state")
        except (OSError, ValueError):
            return False
        return state != "applying"
    if (work / "result.json").exists():
        return True
    try:
        # No result yet: another instance may still be downloading.
        return time.time() - work.stat().st_mtime >= 3600
    except OSError:
        return False


def _workspace_records_target(work, target):
    """Only claim a legacy sidecar workspace that names this installation."""
    recorded = None
    for name in ("plan.json", "journal.json"):
        try:
            data = json.loads((work / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data.get("target"), str):
            recorded = data["target"]
    if recorded is None:
        return False
    try:
        return Path(recorded).resolve() == target
    except OSError:
        return False


def cleanup_workspaces(target, keep=None):
    """Remove update workspaces that no recovery depends on.

    The public launcher calls this before opening the editor, and after a
    successful update the relaunched launcher removes the folder it was
    installed from. Workspaces are kept while a recovery is pending or a
    transaction is mid-apply. A recently touched folder without a result may be
    an active download in another instance and is left alone for an hour.
    Releases before the in-folder change staged beside the install; those
    legacy folders are removed too when they record this installation.
    """
    target = Path(target).resolve()
    keep = Path(keep).resolve() if keep is not None else None
    if (target / PENDING).exists():
        return
    candidates = {w.resolve() for w in target.glob(".dolly-update-*") if w.is_dir()}
    candidates.update(w.resolve() for w in target.parent.glob(".dolly-update-*")
                      if w.is_dir() and _workspace_records_target(w, target))
    for work in sorted(candidates):
        if _workspace_finished(work, keep):
            try:
                shutil.rmtree(work)
            except OSError:
                pass


def self_test(report):
    from .updater import write_manifest
    result = {"passed": False, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        with tempfile.TemporaryDirectory(prefix="Dolly updater test ") as directory:
            root = Path(directory); target = root / "installed"; work = root / ".dolly-update-test"
            for folder, version in ((target, "1.0.0"), (work / "payload", "1.0.1")):
                for name in ("Dolly.exe", "_internal/DollyApp.exe", "_internal/native/bin/win64/DollyNative.dll",
                             "_internal/third_party/ffmpeg/bin/ffmpeg.exe"):
                    path = folder / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(version)
                write_manifest(folder, version)
            (target / "my-shot.json").write_text("keep this shot")
            plan = {"work": str(work), "target": str(target)}
            def failed_health(_target):
                raise RuntimeError("simulated startup failure")
            try:
                install(plan, failed_health)
            except RuntimeError as error:
                assert str(error) == "simulated startup failure"
            assert (target / "Dolly.exe").read_text() == "1.0.0"
            assert (target / "my-shot.json").read_text() == "keep this shot"
            assert not (target / PENDING).exists()
            # Exercise a fresh transaction after recovery.
            (work / "journal.json").unlink()
            install(plan)
            assert (target / "Dolly.exe").read_text() == "1.0.1"
            assert (target / "my-shot.json").read_text() == "keep this shot"
            result.update(passed=True, rollback=True, install=True, user_files_preserved=True)
    except Exception as error:
        result["error"] = str(error)
    atomic_json(report, result)
    return 0 if result["passed"] else 1


def _install_error_message(error, work):
    message = ("Dolly could not finish updating. Your previous files were retained or restored."
               "\n\n" + str(error))
    if isinstance(error, PermissionError) or getattr(error, "winerror", None) in (5, 32):
        message += ("\n\nWindows blocked writing to the Dolly folder. Close any other Dolly "
                    "window, and if a security tool or a protected location (for example "
                    "Downloads with Controlled Folder Access) is holding the files, move Dolly "
                    "to another folder or allow it there, then open Dolly.exe again to finish "
                    "the update.")
    return message + "\n\nUpdate details: " + str(work)


def _apply_plan(plan, work, target, recover, *, on_status=None):
    """Shared install/recover body for the headless and windowed paths.

    Returns the exit code; raises on failure so the caller can report it. A
    failure before the parent exits must not launch a duplicate instance, so an
    incomplete-pending failure returns 1.
    """
    if on_status:
        on_status("Restoring your previous Dolly files..." if recover
                  else "Replacing application files...")
    with install_lock(target):
        check_processes(target)
        if recover:
            if (target / PENDING).exists():
                rollback(plan)
        else:
            install(plan, lambda path: smoke(path, work))
    atomic_json(work / "result.json", {"passed": True, "recovered": recover})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--parent", type=int, default=0)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--progress", action="store_true",
                        help="Keep one window on screen through install and relaunch")
    parser.add_argument("--no-relaunch", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test(args.self_test)
    if args.plan is None:
        # Double-click recovery remains usable even if the main Python bundle
        # was interrupted mid-replacement; this executable is self-contained.
        target = Path(sys.executable).resolve().parent
        marker = target / PENDING
        if not marker.is_file():
            notify("No unfinished Dolly update was found. Open Dolly.exe to check for updates.")
            return 0
        pending = json.loads(marker.read_text(encoding="utf-8"))
        args.plan = Path(pending["work"]) / "plan.json"
        args.recover = True
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    work, target = Path(plan["work"]).resolve(), Path(plan["target"]).resolve()
    if args.plan.resolve().parent != work or not work.name.startswith(".dolly-update-") or work.parent != target:
        raise ValueError("Unexpected update workspace")
    if args.progress and not args.no_relaunch:
        return _progress_helper_window(work, target, recover=args.recover, parent_pid=args.parent)
    try:
        if args.parent:
            wait_parent(args.parent)
        _apply_plan(plan, work, target, args.recover)
    except Exception as error:
        atomic_json(work / "result.json", {"passed": False, "error": str(error)})
        notify(_install_error_message(error, work))
        # A failure before the parent exits must not launch a duplicate instance.
        if (target / PENDING).exists():
            return 1
        try:
            check_processes(target)
        except Exception:
            return 1
    if not args.no_relaunch:
        launch_program([str(target / "Dolly.exe"), "--updated"], target)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        notify("Dolly update recovery could not start: " + str(error))
        raise SystemExit(1)
