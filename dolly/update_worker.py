"""Separate update process: backed-up file replacement and recoverable rollback."""
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
import time
from .updater import MANIFEST, digest_file, read_manifest, version_key, owned_name

PENDING = "DOLLY_UPDATE_PENDING.json"


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dolly-", delete=False) as f:
        f.write((json.dumps(value, indent=2) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
        temporary = Path(f.name)
    os.replace(temporary, path)


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


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".dolly-new-", delete=False) as out:
        temporary = Path(out.name)
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("ab") as durable:
            durable.flush()
            os.fsync(durable.fileno())
        os.replace(temporary, target)
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
    for name in names:
        path = checked_file(target, name)
        if name != MANIFEST:
            if name in new["files"] and digest_file(checked_file(payload, name)) != new["files"][name]:
                raise ValueError("Staged update changed")
            if path.exists() and name not in old["files"]:
                raise ValueError("Update would overwrite an unowned file: " + name)
            if name in old["files"] and (not path.is_file() or digest_file(path) != old["files"][name]):
                raise ValueError("Application file was modified; preserve it before updating: " + name)
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
    state = {"state": "applying", "target": str(target), "before": before}
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
    result = subprocess.run([str(target / "Dolly.exe"), "--self-test", str(report)], cwd=work, timeout=60)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    if result.returncode or data.get("passed") is not True or data.get("frozen") is not True or data.get("game_launched") is not False:
        raise RuntimeError("Updated Dolly failed its startup check")


def notify(text):
    ctypes.windll.user32.MessageBoxW(None, text, "Dolly update", 0x40)


def check_processes(target, exclude_pid=0):
    """Conservative last-moment guard, independent of desktop telemetry."""
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
            if entry.name.lower() == "dolly.exe" and entry.pid != exclude_pid:
                handle = api.OpenProcess(0x1000, False, entry.pid)
                if not handle:
                    raise RuntimeError("Cannot verify another Dolly process; close it before updating")
                try:
                    buffer = ctypes.create_unicode_buffer(32768); length = wt.DWORD(len(buffer))
                    if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                        raise OSError("Cannot identify another Dolly process")
                    if Path(buffer.value).resolve() == target / "Dolly.exe":
                        raise RuntimeError("Another instance of this Dolly installation is still open")
                finally:
                    api.CloseHandle(handle)
            more = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)


def self_test(report):
    from .updater import write_manifest
    result = {"passed": False, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        with tempfile.TemporaryDirectory(prefix="Dolly updater test ") as directory:
            root = Path(directory); target = root / "installed"; work = root / ".dolly-update-test"
            for folder, version in ((target, "1.0.0"), (work / "payload", "1.0.1")):
                for name in ("Dolly.exe", "DollyUpdater.exe", "_internal/native/bin/win64/DollyNative.dll",
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--parent", type=int, default=0)
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--self-test", type=Path)
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
    if args.plan.resolve().parent != work or not work.name.startswith(".dolly-update-") or work.parent != target.parent:
        raise ValueError("Unexpected update workspace")
    try:
        if args.parent:
            wait_parent(args.parent)
        with install_lock(target):
            check_processes(target)
            if args.recover:
                if (target / PENDING).exists():
                    rollback(plan)
            else:
                install(plan, lambda path: smoke(path, work))
    except Exception as error:
        atomic_json(work / "result.json", {"passed": False, "error": str(error)})
        notify("Dolly could not finish updating. Your previous files were retained or restored.\n\n" + str(error) + "\n\nUpdate details: " + str(work))
        # A failure before the parent exits must not launch a duplicate instance.
        if (target / PENDING).exists():
            return 1
        try:
            check_processes(target)
        except Exception:
            return 1
    else:
        atomic_json(work / "result.json", {"passed": True, "recovered": args.recover})
    if not args.no_relaunch:
        subprocess.Popen([str(target / "Dolly.exe"), "--updated"], cwd=target)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        notify("Dolly update recovery could not start: " + str(error))
        raise SystemExit(1)
