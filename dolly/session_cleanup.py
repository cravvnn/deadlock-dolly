"""Remove only Dolly's generated mounts, after the launched game has exited."""
from __future__ import annotations

import ctypes
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


OWNER = "Deadlock Dolly"
PREFIX = "citadel_dolly_"
GENERATED_FILES = frozenset({
    ".dolly-session.json", "cvar_unlocker/bin/win64/server.dll",
    "cvar_unlocker/bin/win64/dolly_cvar_unlocker.dll",
    "cvar_unlocker/bin/win64/dolly_native.cfg",
})
GENERATED_DIRS = frozenset({"cvar_unlocker", "cvar_unlocker/bin", "cvar_unlocker/bin/win64"})


def _plain_path(path: Path) -> bool:
    """Do not traverse symbolic links, Windows junctions or other reparse points."""
    info = path.lstat()
    return not stat.S_ISLNK(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_ancestors(path: Path) -> bool:
    return all(_plain_path(parent) for parent in (path, *path.parents))


def _marker(overlay: Path, paths, session_dir: Path | None = None) -> dict:
    from .launcher import LaunchError
    try:
        if (not overlay.is_absolute() or overlay.parent.resolve() != paths.game_dir.resolve()
                or re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay.name) is None
                or not _plain_ancestors(overlay)):
            raise ValueError("unexpected or linked plugin directory")
        marker = overlay / ".dolly-session.json"
        if not _plain_path(marker) or not marker.is_file():
            raise ValueError("linked or missing session marker")
        data = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("owner") != OWNER:
            raise ValueError("unrecognized owner")
        recorded_session = Path(data["session_dir"])
        if (not recorded_session.is_absolute() or recorded_session.parent.name != "logs"
                or recorded_session.name != overlay.name[len(PREFIX):]):
            raise ValueError("unexpected session directory")
        if session_dir is not None and recorded_session.resolve() != session_dir.resolve():
            raise ValueError("session directory mismatch")
        if (not isinstance(data.get("original_sha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", data["original_sha256"]) is None
                or Path(data["original_gameinfo"]).resolve() != paths.gameinfo.resolve()):
            raise ValueError("unexpected game configuration")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LaunchError(f"Temporary plugin directory left untouched: {overlay}: {exc}") from exc


def _mount_is_referenced(paths, overlay: Path) -> bool:
    """Read actual SearchPaths, including conditions and wildcard mounts."""
    from .launcher import LaunchError, _parse, _named
    if not _plain_path(paths.gameinfo):
        raise LaunchError("Linked gameinfo.gi was left untouched during temporary-file cleanup.")
    roots = _named(_parse(paths.gameinfo.read_text(encoding="utf-8-sig")), "GameInfo")
    if len(roots) != 1:
        raise LaunchError("Could not verify game search paths before temporary-file cleanup.")
    filesystems = _named(roots[0].children or [], "FileSystem")
    searches = _named(filesystems[0].children or [], "SearchPaths") if len(filesystems) == 1 else []
    if len(searches) != 1 or searches[0].children is None:
        raise LaunchError("Could not verify game search paths before temporary-file cleanup.")
    name = overlay.name.casefold()
    for entry in searches[0].children:
        if entry.value is None:
            raise LaunchError("Could not verify a nested game search path before cleanup.")
        raw = entry.value.value.replace("\\", "/").casefold()
        if name in raw or any(fnmatch.fnmatchcase(name, part) for part in raw.split("/") if part):
            return True
    return False


def remove_overlay(overlay: Path, paths, session_dir: Path | None = None) -> bool:
    """Caller must first establish game exit. Unknown contents are never deleted."""
    from .launcher import LaunchError
    if not overlay.exists() and not overlay.is_symlink():
        return False
    try:
        _marker(overlay, paths, session_dir)
    except LaunchError:
        if not overlay.exists():
            return False  # The editor's exit thread may have finished first.
        raise
    if _mount_is_referenced(paths, overlay):
        raise LaunchError(f"The game still references {overlay.name}; its temporary files were left intact. "
                          "Recover the game configuration before starting Deadlock normally.")
    # Inspect every entry before deleting anything. In particular, rmtree must
    # not follow a replaced cvar_unlocker directory or remove extra user files.
    files: list[Path] = []
    directories: list[Path] = []
    pending = [overlay]
    while pending:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except FileNotFoundError:
            continue
        for child in children:
            relative = child.relative_to(overlay).as_posix()
            try:
                plain = _plain_path(child)
            except FileNotFoundError:
                continue
            if not plain:
                raise LaunchError(f"Temporary plugin directory contains a link; left untouched: {child}")
            if child.is_dir() and relative in GENERATED_DIRS:
                directories.append(child)
                pending.append(child)
            elif child.is_file() and relative in GENERATED_FILES:
                files.append(child)
            elif not child.exists():
                continue
            else:
                raise LaunchError(f"Temporary plugin directory contains an extra file or folder; left untouched: {child}")
    # Retain the ownership marker until all binaries have been removed so a
    # locked DLL can be retried on the next launch.
    marker = overlay / ".dolly-session.json"
    for child in files:
        if child != marker:
            child.unlink(missing_ok=True)
    for child in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            child.rmdir()
        except FileNotFoundError:
            pass
    marker.unlink(missing_ok=True)
    try:
        overlay.rmdir()
    except FileNotFoundError:
        pass
    return True


def recover_orphans(paths) -> list[str]:
    """Find mounts made by an older/moved copy of Dolly in this installation."""
    from .launcher import LaunchError, _load_record, _restore_record
    recovered = []
    for overlay in sorted(paths.game_dir.glob(PREFIX + "*")):
        try:
            marker = _marker(overlay, paths)
        except LaunchError:
            continue  # A name alone is not proof of ownership.
        session = Path(marker["session_dir"])
        journal = session / "session.json"
        if journal.is_file():
            if not _plain_ancestors(session) or not _plain_path(journal):
                continue
            record = _load_record(session)
            if (Path(record["overlay_dir"]).resolve() != overlay.resolve()
                    or record["original_sha256"] != marker["original_sha256"]):
                continue
            _restore_record(session)
        # Even if its old portable folder was deleted, a marked mount can be
        # removed once the current gameinfo demonstrably does not reference it.
        if remove_overlay(overlay, paths, session):
            recovered.append(str(session))
    return recovered


def _append_log(session_dir: Path, message: str) -> None:
    try:
        with (session_dir / "launch.log").open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + message + "\n")
    except OSError:
        pass


def start_waiter(session) -> None:
    """Hand an exact, wait-only process handle to a child that survives UI exit."""
    from .runtime import is_frozen
    if sys.platform != "win32":
        raise OSError("Detached game cleanup is available on Windows only.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.argtypes = []
    api.GetCurrentProcess.restype = ctypes.c_void_p
    api.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    api.DuplicateHandle.restype = ctypes.c_int
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    duplicate = ctypes.c_void_p()
    own_process = api.GetCurrentProcess()
    # Popen owns this actual process object; a PID lookup could instead select
    # an unrelated process after Windows reused an exited game's PID.
    if not api.DuplicateHandle(own_process, int(session.process._handle), own_process,
                               ctypes.byref(duplicate), 0x00100000, True, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        command = [sys.executable]
        if is_frozen():
            command += ["--cleanup-session"]
        else:
            command += ["-m", "dolly.session_cleanup"]
        command += [str(session.session_dir.resolve()), str(duplicate.value)]
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [duplicate.value]}
        environment = dict(os.environ)
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        with (session.session_dir / "launch.log").open("ab") as output:
            subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
                             stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             close_fds=True, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
                             env=environment)
    finally:
        api.CloseHandle(duplicate)


def wait_and_cleanup(session_dir: Path, handle: int) -> int:
    """Internal helper entry; no injection, console connection, or GUI startup."""
    from .launcher import LaunchError, _load_record, _restore_record, validate_game, running_processes, _game_is_running
    try:
        if sys.platform != "win32" or handle <= 0:
            raise LaunchError("Invalid detached cleanup process handle.")
        _load_record(session_dir)  # Validate before opening a log or waiting.
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        api.WaitForSingleObject.restype = ctypes.c_uint
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        try:
            result = api.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            api.CloseHandle(handle)
        if result != 0:
            raise LaunchError("Could not wait for the launched game; cleanup will retry at next launch.")
        if _game_is_running(running_processes()):
            _append_log(session_dir, "Another Deadlock process is running; temporary-file cleanup deferred until next launch.")
            return 0
        _restore_record(session_dir)
        record = _load_record(session_dir)
        paths = validate_game(Path(record["original_gameinfo"]).parent)
        overlay = Path(record["overlay_dir"])
        # Windows may release loaded DLL files just after the process signal.
        for attempt in range(6):
            try:
                if remove_overlay(overlay, paths, session_dir):
                    _append_log(session_dir, "Removed the temporary plugin directory after Dolly closed and the game exited.")
                return 0
            except OSError:
                if attempt == 5:
                    raise
                time.sleep(0.5)
        return 0
    except (OSError, ValueError, LaunchError) as exc:
        _append_log(session_dir, f"Temporary-file cleanup deferred: {exc}")
        return 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wait for a Dolly editing session to exit and remove its temporary files.")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("handle", type=int)
    options = parser.parse_args()
    raise SystemExit(wait_and_cleanup(options.session_dir, options.handle))
