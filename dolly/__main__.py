from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import traceback
from .runtime import application_root


def main():
    root = application_root(Path(__file__).resolve().parents[1])
    logs = root / "logs"
    logger = logging.getLogger("dolly")
    try:
        logs.mkdir(parents=True, exist_ok=True)
        logger.setLevel(logging.DEBUG)
        handler = RotatingFileHandler(logs / "Dolly.log", maxBytes=4_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        logger.addHandler(handler)
        from .branding import set_taskbar_identity
        set_taskbar_identity()
        from .gui import main as gui_main
        gui_main()
    except Exception:
        logger.exception("Dolly failed")
        traceback.print_exc()
        print("Startup failed. Log:", logs / "Dolly.log", file=sys.stderr)
        if os.name == "nt":
            # Normal startup has no console window. Keep failures visible even
            # when creating the Tk root itself failed.
            try:
                import ctypes
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                message = user32.MessageBoxW
                message.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                    ctypes.c_wchar_p, ctypes.c_uint]
                message.restype = ctypes.c_int
                message(None, "Dolly could not start. Please send these logs:\n\n"
                        + str(logs / "Dolly_startup.log") + "\n" + str(logs / "Dolly.log"),
                        "Deadlock Dolly — startup error", 0x10)
            except (OSError, AttributeError):
                logger.exception("Could not show the startup error dialog")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
