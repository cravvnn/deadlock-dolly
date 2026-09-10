# Contributing

Keep documentation concise and focused on setup, controls and troubleshooting.
Detailed build procedures and test results belong in the guides under docs/.

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

## C++ formatting

Owned files under native/include, native/src and native/tests use clang-format
18.1.8. The formatter is a contributor tool; it is not needed to run Dolly or
build the Windows EXE. Vendor sources keep their upstream formatting.

```console
python -m pip install clang-format==18.1.8
python tools/format_cpp.py --check
python tools/format_cpp.py --write
```

The helper checks protected tokens, literals, preprocessing boundaries and a
second formatting pass before writing. Keep formatting changes separate from
behavior changes so both are reviewable.
