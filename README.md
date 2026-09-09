<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

THIS IS AN "INJECTION" TO THE GAME - PLEASE USE AT YOUR OWN RISK

This is due to the mod using source2's native camera to render the dolly paths. Without this, we would not have dolly's

PLEASE STILL LAUNCH GAME WITH `-insecure` IN THE GAMES LAUNCH SETTINGS, TO BE SAFE.


**Current source: 0.3.10 alpha.** Python and Tcl/Tk are bundled; end users do not need to install them.


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

In 0.3.9, **Camera driver → Native (experimental)** is selected by default
before launch. It applies position, rotation and aspect-ratio framing during
each main-view callback. It supports only the exact `client.dll` and
`engine2.dll` build inspected for this release. If your installation differs,
choose **Console (legacy)** before launching; Dolly does not guess new offsets.

The GitHub **Source code** download and the source ZIP contain the source and
build recipe. They do not contain an already compiled Windows executable.


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
paused flight retain the console implementation. The user reports smooth native panning in 0.3.9;
this restart correction still needs an in-game check. Windows packaging and game behavior are separate
validation steps. Consult
[VALIDATION.md](docs/VALIDATION.md) and the generated Windows `BUILD_INFO.json`
for the checks that have actually run.

Dolly source uses the [MIT license](LICENSE.txt). The bundled official unlocker
and other components retain their own notices under `third_party/`. Artwork
is separate from the source-code license; see [assets/README.md](assets/README.md).

0.3.10 fixes restarting after a native shot holds its last view. Play releases
the old override before the normal seek and calibration. Stop / restore returns
control to the game; its spectator view may differ from the final shot. Native
rendering and paused movement are unchanged. Rebuild the complete Windows package.
