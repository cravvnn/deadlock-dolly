"""Dedicated editor entry point; regular Dolly settings/updater stay separate."""
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dolly.edition import configure_environment
configure_environment()
from dolly.desktop import main

if __name__ == '__main__':
    raise SystemExit(main())
