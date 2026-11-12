# Contributing

## Contributors and credits

Thanks to [EsteemFPS](https://www.youtube.com/@EsteemFPS) for creating a
[Dolly tutorial](https://www.youtube.com/watch?v=6l0pWXf1sVo) and helping
more players get started with the editor.

[@paralin](https://github.com/paralin) contributed
Linux/Proton Steam discovery, Wine mount-point handling and interrupted
plugin-cleanup recovery. The full [credits](CREDITS.md) record Dolly's creator,
contributors, project references and supporting tools.

When contributing, include your preferred credit name or handle and a short
description of the work. Identify any upstream code, license and provenance
that should be retained alongside it.

## Development

Keep documentation concise and focused on setup, controls and troubleshooting.
Detailed build procedures and test results belong in the guides under docs/.

Use Python 3.10+ with Tcl/Tk to run from source. On Windows, use a 64-bit
interpreter and run `Launch_Dolly.bat` or `python -m dolly` from the repository
root. The basic source editor needs no pip packages; optional audio and build
tools have additional dependencies. Native features require the matching native
helper, assets and build metadata from the checkout. The Windows package bundles
its own runtime; its build uses Python 3.12 x64 and the pinned
`requirements-build.txt`. See [BUILDING.md](docs/BUILDING.md).

Run the core headless workflows and focused owner regressions from the repository root with
`python -m unittest discover -s tests -q`. See the [testing guide](docs/internal/TESTING.md)
for release gates and targeted manual checks. These tests do not qualify Windows,
the game, or rendered output. For camera or transport changes, describe the
tested replay state and distinguish simulated checks from observations in the
native game. Do not commit personal replays, diagnostic exports, recovery
journals, credentials or build outputs.

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
