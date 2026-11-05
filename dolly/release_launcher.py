"""Self-contained public Dolly.exe: open the editor and recover interrupted updates.

The editor runtime lives under _internal. This launcher bundles its own Python
and Tk so recovery can start even when the editor runtime was partly replaced.
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import sys
from . import update_worker


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
    return update_worker.launch_program(command, target, wait=wait)


def main(argv=None, *, target=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--plan" in args:
        return update_worker.main(args)
    if "--updater-self-test" in args:
        return update_worker.main(["--self-test", *args[args.index("--updater-self-test")+1:]])
    target = Path(sys.executable).resolve().parent if target is None else Path(target).resolve()
    # Installation health checks run while a journal is intentionally pending.
    if any(flag in args for flag in ("--self-test", "--self-test-hidden", "--cleanup-session", "--help")):
        return open_editor(target, args, wait=True)
    try:
        with update_worker.install_lock(target):
            pending = update_worker.pending_work(target)
            if pending is not None:
                ids = launcher_process_ids()
                update_worker.check_processes(target, exclude_pid=ids)
                update_worker.launch_worker(target, pending, recover=True, parent_pid=ids[-1])
                return 0
            update_worker.cleanup_workspaces(target)
            # Release discovery belongs to the already-open desktop editor. The public
            # launcher never downloads or installs a new release on an ordinary open.
            open_editor(target, args)
            return 0
    except OSError as error:
        update_worker.notify("Dolly could not open safely. Another update may be in progress. "
                             "Wait for it to finish, then open Dolly again.\n\n" + str(error))
        return 1
