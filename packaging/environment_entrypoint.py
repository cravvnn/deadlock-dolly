"""Dedicated editor entry point; regular Dolly settings/updater stay separate."""
from dolly.edition import configure_environment
configure_environment()
from dolly.desktop import main

if __name__ == '__main__':
    raise SystemExit(main())
