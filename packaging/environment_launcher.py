"""Dedicated portable launcher, without the regular Dolly update channel."""
from dolly.edition import configure_environment
configure_environment()
from dolly.release_launcher import main

if __name__ == '__main__':
    raise SystemExit(main())
