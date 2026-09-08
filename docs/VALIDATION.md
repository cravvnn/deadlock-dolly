# Validation — 0.3.3 alpha

Date: 8 September 2026.

This revision prepares the Windows executable and a clean GitHub source
package. **No Windows EXE was compiled or launched in this Linux workspace.**
The included Windows build workflow must complete before a portable Windows
release is available. Neither the local checks nor the future bundle smoke
test establish native Deadlock camera compatibility.

## Completed local checks

`python -m unittest discover -s tests -q`: **460 tests passed** on Python 3.12
in **37.799 seconds**.

This includes the previous 441 camera/editor regressions and 19 packaging checks:

| Area | Checks |
| --- | --- |
| Portable startup and paths — 13 tests | EXE/resource roots, windowless log streams, direct startup without a child Python process, visible failures, File/CLI recovery, and prevention of recursive bootstrap |
| External game environment | Native DLL-directory setup and restoration on successful/failed spawn, PATH filtering and unchanged source launch behavior; Windows API boundary simulated |
| Release packaging — 6 tests | Explicit source exports, excluded personal/runtime data, invalid paths, missing files, x64 GUI PE and nine embedded icon frames through a simulated PE resource tree |
| Retained application — 441 tests | Camera interpolation, aspect curves, controller transitions, paused movement, input, timing, startup, unlocker preparation and restoration |

All **47 Python files plus the PyInstaller spec** parse with Python 3.10 syntax
rules. The build itself deliberately requires Windows x64 and Python 3.12.
The existing source BAT files retain their Windows CRLF line endings.

An actual **Linux Tk 8.6.14** smoke run used the new desktop entry point. It
created and updated the real Dolly editor, loaded its PNG icon fallback, read
and verified the bundled unlocker, checked data locations and closed. This
check ran from source (`frozen: false`), started no game and changed no game
configuration. It is not evidence of native Windows EXE or ICO loading.

The playback clock, path model, movement/input, hotkey, preferences, framing
graph and console modules are byte-identical to 0.3.2. Controller changes in
this revision locate portable logs; game-launch changes handle bundled resource
paths and external DLL lookup. Existing launch/recovery regressions pass.

The three logo assets and official cvar unlocker are byte-identical to 0.3.2.
The unlocker SHA-256 remains
`e86f270b1dedc81fd54a230f0080eee568a4f2bd39e1f41080dcf71d833267ba`.

## Windows checks configured, not yet executed here

The builder and GitHub Actions workflow will:

1. Run the source regressions on Windows x64 with Python 3.12.
2. Build a windowed `Dolly.exe` with the existing ICO and version information.
3. Copy the official unlocker afterward and verify its pinned hash.
4. Inspect the actual PE architecture, GUI subsystem and all nine icon frames.
5. Relocate the complete folder to a path with spaces and start it from a
   different working directory. Create the actual editor, load its Windows
   icon and check the bundled runtime/resources without launching Deadlock.
6. Produce Windows/source ZIPs, checksums and `BUILD_INFO.json` only after those
   checks pass. The workflow creates downloadable artifacts; it does not publish
   a GitHub Release.

The reference PyInstaller Windows wheel was checked for the COPYING file used
by the license-copy step. Build dependencies are pinned in requirements-build.txt.
The icon, PE and API mock checks cannot substitute for executing this Windows gate.

## Source delivery

`SOURCE_FILES.txt` lists **79 files** for the GitHub source ZIP, including the
workflow, tests, logo and licensed plugin. Logs, diagnostic archives, replays,
recordings, private settings, virtual environments and build output are excluded.
No GitHub repository was created or modified for this delivery.

Build and upload instructions are in [BUILDING.md](BUILDING.md). The retained
camera investigations, tests and previous visual checks are in
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md).
