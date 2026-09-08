# Validation — 0.3.3 alpha, build fix 1

Date: 8 September 2026.

The supplied first GitHub Windows run installed the pinned build dependencies
successfully, then stopped at the source-test gate: 460 tests in 46.221 seconds,
six failures and one error. No executable was produced by that run.

Build fix 1 corrects the four affected test files and updates this report and
the changelog. Production code, build scripts, workflow, logo and unlocker are
byte-identical to the originally delivered 0.3.3 source archive. No test is
skipped and no build gate is disabled.

**No Windows EXE was compiled or launched in this Linux workspace.** A fresh
GitHub Windows build must run after uploading these changes. Neither these
checks nor the future bundle smoke test establish native Deadlock camera
compatibility.

## Reported failure and repair

- Six assertions compared Windows short-name temporary paths (`RUNNER~1`) to
  canonical long paths (`runneradmin`). Expected paths now resolve the same
  location while still checking the full startup-log paths and replay command.
- One separator test tried to create a newline-containing filename, which the
  Windows filesystem rejected before the application's validator could run.
  Real files still exercise semicolon and plus-sign rejection through the
  command builder. Only filesystem lookup is simulated for quotes/control
  characters, with the real validator required to report console separators.
- An independent Linux harness used a directory symlink to reproduce path
  aliases and rejected writes of Windows-invalid filenames. The seven original
  cases reproduced **six failures and one error**; all seven corrected cases
  passed. This emulates the two reported assumptions, not Windows APIs.

## Completed local checks

`python3 -m unittest discover -s tests -q`: **460 tests passed** on Linux,
Python **3.12.13**, in **37.848 seconds** after the build fix.

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

The original packaging validation included an actual **Linux Tk 8.6.14** smoke
run using the new desktop entry point. It
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

## Windows build gates awaiting a fresh run

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
No GitHub repository was modified by this repair. The small update ZIP contains
the four corrected tests and these two documentation files at their original
repository paths. The full source ZIP includes the same corrections.

Build and upload instructions are in [BUILDING.md](BUILDING.md). The retained
camera investigations, tests and previous visual checks are in
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md).
