<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

**Current source: 0.3.5 alpha.** The portable Windows build opens through
`Dolly.exe`, with the supplied logo embedded in the executable. Python and
Tcl/Tk are bundled; end users do not need to install them.

## Features

- Capture camera keys from the game, including configurable keyboard and mouse bindings.
- Smooth position paths, rotation and camera bank.
- Animated `r_aspectratio` framing with an editable curve.
- Depth-of-field and other numeric camera-variable tracks.
- Paused-camera movement and switching between saved views.
- Replay playback with HUD handling, settings restoration and diagnostics.
- Development launcher with `-dev -insecure` and unlocker initialization before replay loading.

## Using the Windows app

Download a **Windows x64** ZIP from this repository's Releases when one has
been published. Extract it completely to a writable folder and double-click
**Dolly.exe**. Keep its `_internal` folder beside it; a desktop shortcut can
point to the EXE. See the included `Start_Here.txt` and the
[user guide](docs/USER_GUIDE.md) for the replay workflow.

The GitHub **Source code** download and the source ZIP contain the source and
build recipe. They do not contain an already compiled Windows executable.

## Building and publishing your own copy

The included [Build Windows app workflow](.github/workflows/windows.yml) runs
on GitHub's Windows runner. Open **Actions → Build Windows app → Run workflow**
after putting these source files in your repository. A successful run supplies
a Windows ZIP, source ZIP and checksums as a downloadable artifact.

It checks the tests, executable architecture, embedded logo and an actual
editor startup before packaging. It does not launch Deadlock, create a release,
or publish anything automatically. You can attach the Windows ZIP to your own
GitHub Release after testing it. See [BUILDING.md](docs/BUILDING.md) for local
Windows build commands, repository setup and the release layout.

## Development

Run from source with Python 3.10+ and Tcl/Tk:

```console
python -m dolly
python -m unittest discover -s tests -q
```

Source operation needs no pip dependencies. Executable builds use the pinned
dependencies in `requirements-build.txt` and 64-bit Python 3.12 on Windows.

| Location | Contents |
| --- | --- |
| `dolly/` | Application and replay controller |
| `assets/` | Supplied logo and prepared Windows icon |
| `tests/` | Regression tests |
| `packaging/`, `tools/` | Executable recipe and release checks |
| `examples/` | Example shot |
| `third_party/` | Pinned official unlocker and component notices |
| `docs/` | User guide, build instructions, changes and validation evidence |

Logs, replays, personal shots, virtual environments and build outputs are
excluded from Git. `SOURCE_FILES.txt` is the explicit source-archive list.

## Status and license

Camera control uses the console; it is not an HLAE render-time camera hook.
Windows packaging and game behavior are separate validation steps. Consult
[VALIDATION.md](docs/VALIDATION.md) and the generated Windows `BUILD_INFO.json`
for the checks that have actually run.

Dolly source uses the [MIT license](LICENSE.txt). The bundled official unlocker
and other components retain their own notices under `third_party/`. Artwork
is separate from the source-code license; see [assets/README.md](assets/README.md).
