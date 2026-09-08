# Contributing

Use Python 3.10+ with Tcl/Tk to run from source. The reference Windows build
uses Python 3.12 x64; see [BUILDING.md](docs/BUILDING.md).

Run `python -m unittest discover -s tests -q` before proposing a change. For
camera or transport changes, describe the tested replay state and separate
simulated checks from observations in the native game. Do not commit personal
replays, diagnostic exports, recovery journals, credentials or build outputs.

Keep the launcher restricted to its own development/insecure process and
retain journaled gameinfo recovery. The bundled official unlocker has pinned
provenance; do not replace it silently. Update `SOURCE_FILES.txt` when adding
files that should be part of a source release.

For bug reports, include the Dolly version, the action that failed and the
relevant error text. Review diagnostics before posting them publicly: a local
diagnostic export can contain installation paths and replay names.
