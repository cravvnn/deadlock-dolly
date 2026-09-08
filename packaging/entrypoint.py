"""PyInstaller's windowed executable starts directly, without a BAT or child Python."""
from dolly.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
