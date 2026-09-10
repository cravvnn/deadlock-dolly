"""Direct EXE entry point, startup logging and no-game bundle smoke check."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import sys
import traceback

from . import __version__
from .runtime import application_root, resource_root, is_frozen


def _message(text: str, *, error: bool = False) -> None:
    if sys.platform == "win32":
        try:
            api = ctypes.WinDLL("user32", use_last_error=True)
            function = api.MessageBoxW
            function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            function(None, text, "Deadlock Dolly", 0x10 if error else 0x40)
            return
        except (OSError, AttributeError):
            pass
    if sys.stderr is not None:
        print(text, file=sys.stderr)


def bundle_self_test(report_path: Path) -> int:
    """Exercise the actual frozen GUI/runtime without starting or editing a game."""
    report = {"version": __version__, "frozen": is_frozen(), "platform": sys.platform,
              "pointer_bits": struct.calcsize("P") * 8, "checks": {}, "game_launched": False}
    root = None
    try:
        from . import launcher, branding, controller, gui
        import tkinter as tk
        app = application_root()
        resources = resource_root()
        report["checks"]["paths"] = {
            "app": str(app), "resources": str(resources), "logs": str(controller.ROOT / "logs")}
        assert controller.ROOT == launcher.PACKAGE_ROOT == app, "Log/recovery roots disagree"
        assert branding.ASSETS == resources / "assets", "Icon resource root mismatch"
        if is_frozen():
            assert app != resources, "Portable data must be outside the bundled runtime"
        if sys.platform == "win32":
            assert struct.calcsize("P") == 8, "The Windows bundle must be x64"
        dll = launcher._verified_unlocker()
        report["checks"]["unlocker_sha256"] = hashlib.sha256(dll.read_bytes()).hexdigest()
        # Use the real application, not just a mock Tk root. This imports the
        # camera editor, creates its widgets, exercises its icon and closes it.
        branding.set_taskbar_identity()
        root = tk.Tk()
        app_ui = gui.DollyApp(root)
        root.update_idletasks()
        root.update()
        # Exercise the new export controls at the supported minimum size.
        # This runs on the actual frozen Windows GUI in CI without a game.
        root.geometry(app_ui._window_size(1000, 700))
        app_ui.notebook.select(app_ui.export_tab)
        root.update_idletasks()
        for name in ("video_path_entry", "video_start_button", "video_stop_button",
                     "video_cancel_button", "reshade_path_entry", "reshade_configure_button",
                     "reshade_disable_button", "reshade_forget_button"):
            widget = getattr(app_ui, name)
            x = widget.winfo_rootx() - root.winfo_rootx()
            y = widget.winfo_rooty() - root.winfo_rooty()
            if (not widget.winfo_ismapped() or x < 0 or y < 0
                    or x + widget.winfo_width() > root.winfo_width()
                    or y + widget.winfo_height() > root.winfo_height()):
                raise RuntimeError(f"Export control is clipped at the minimum window size: {name}")
        report["checks"]["export_layout"] = "minimum_window_controls_visible"
        if sys.platform == "win32":
            root.iconbitmap(str(branding.ASSETS / "dolly.ico"))  # No silent PNG fallback in this gate.
        else:
            branding.apply_window_icon(root)
        report["checks"]["tk"] = str(root.tk.call("info", "patchlevel"))
        report["checks"]["gui"] = "created_and_updated"
        app_ui._destroy()
        root = None
        report["passed"] = True
    except Exception:
        report.update(passed=False, error=traceback.format_exc())
    finally:
        if root is not None:
            root.destroy()
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Deadlock Dolly portable desktop editor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--recover", action="store_true", help="Restore pending game configuration; close Deadlock first")
    group.add_argument("--self-test", metavar="REPORT_JSON", type=Path, help="Check this bundle without launching a game")
    group.add_argument("--cleanup-session", nargs=2, metavar=("SESSION", "HANDLE"), help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if options.cleanup_session:
        from .session_cleanup import wait_and_cleanup
        directory, handle = options.cleanup_session
        return wait_and_cleanup(Path(directory), int(handle))
    if options.self_test:
        return bundle_self_test(options.self_test)
    if options.recover:
        from .launcher import recover_pending
        restored = recover_pending()
        _message(f"Recovered or cleaned {len(restored)} session(s)." if restored else
                 "No pending recovery or temporary-folder cleanup is needed.")
        return 0
    from .__main__ import main as gui_main
    return gui_main()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    log = application_root() / "logs" / "Dolly_startup.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8", buffering=1) as output:
            # PyInstaller --windowed sets stdout/stderr to None. Own the log
            # inside this process instead of relaunching the EXE as Python.
            with redirect_stdout(output), redirect_stderr(output):
                print(f"Deadlock Dolly {__version__}; frozen={is_frozen()}")
                try:
                    return _run(arguments)
                except Exception:
                    traceback.print_exc()
                    raise
    except Exception as exc:
        _message(f"Dolly could not start: {exc}\n\nStartup log: {log}\n"
                 "Extract the entire package to a writable folder.", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
