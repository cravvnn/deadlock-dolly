<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

**Current source: 0.3.8 alpha.** The portable Windows build opens through
`Dolly.exe`, with the supplied logo embedded in the executable. Python and
Tcl/Tk are bundled; end users do not need to install them.

## Features

- Capture camera keys from the game, including configurable keyboard and mouse bindings.
- Smooth position paths, rotation and camera bank.
- Experimental native camera playback evaluated for each main rendered view.
- Console fallback with Off, Light, Balanced and Strong smoothing choices.
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

In 0.3.8, **Camera driver → Native (experimental)** is selected by default
before launch. It applies position, rotation and aspect-ratio framing during
each main-view callback. It supports only the exact `client.dll` and
`engine2.dll` build inspected for this release. If your installation differs,
choose **Console (legacy)** before launching; Dolly does not guess new offsets.

The working manual paused-camera controls are unchanged. DOF and other camera
variables still use sampled console updates and are not synchronized to every
rendered frame. **In-game smoothness is not yet verified for the native driver.**
Use the short [native camera test](docs/NATIVE_CAMERA.md) before publishing.

The GitHub **Source code** download and the source ZIP contain the source and
build recipe. They do not contain an already compiled Windows executable.

## Building and publishing your own copy

The included [Build Windows app workflow](.github/workflows/windows.yml) runs
on GitHub's Windows runner. Open **Actions → Build Windows app → Run workflow**
after putting these source files in your repository. A successful run supplies
a Windows ZIP, source ZIP and checksums as a downloadable artifact.

It builds the native helper and executable, runs their automated checks, and
checks the embedded logo and an actual editor startup before packaging.
It does not launch Deadlock, create a release,
or publish anything automatically. You can attach the Windows ZIP to your own
GitHub Release after testing it. See [BUILDING.md](docs/BUILDING.md) for local
Windows build commands, repository setup and the release layout.

## Development

Run from source with Python 3.10+ and Tcl/Tk:

```console
python -m dolly
python -m unittest discover -s tests -q
```

The editor's source runtime needs no pip dependencies. Native playback also
needs the Windows-built helper. Choose Console to use source without that
helper. Executable builds use the pinned dependencies in `requirements-build.txt`,
64-bit Python 3.12, Visual Studio 2022 C++ tools and CMake on Windows.

| Location | Contents |
| --- | --- |
| `dolly/` | Application and replay controller |
| `native/` | Native camera source, build profiles, tests and vendored dependency |
| `assets/` | Supplied logo and prepared Windows icon |
| `tests/` | Regression tests |
| `packaging/`, `tools/` | Executable recipe and release checks |
| `examples/` | Example shot |
| `third_party/` | Pinned official unlocker and component notices |
| `docs/` | User guide, build instructions, changes and validation evidence |

Logs, replays, personal shots, virtual environments and build outputs are
excluded from Git. `SOURCE_FILES.txt` is the explicit source-archive list.

## Status and license

Native playback uses a Deadlock-specific view hook; Console playback and manual
paused flight retain the console implementation. This alpha has not yet passed
a live Deadlock rendering test. Windows packaging and game behavior are separate
validation steps. Consult
[VALIDATION.md](docs/VALIDATION.md) and the generated Windows `BUILD_INFO.json`
for the checks that have actually run.

Dolly source uses the [MIT license](LICENSE.txt). The bundled official unlocker
and other components retain their own notices under `third_party/`. Artwork
is separate from the source-code license; see [assets/README.md](assets/README.md).
