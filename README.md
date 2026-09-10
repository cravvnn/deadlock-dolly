<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

**Current source: 0.5.1 alpha.** The portable Windows build opens through
`Dolly.exe`. Python and Tcl/Tk are bundled; no separate installation is needed.

**THIS MOD INJECTS CODE INTO DEADLOCK — USE AT YOUR OWN RISK.**

The Native driver loads a DLL into the game to apply camera movement during
each main rendered view. It runs in the game's user-mode process; there is no
kernel driver.

**KEEP `-insecure` IN DEADLOCK'S LAUNCH OPTIONS WHILE USING DOLLY.**

Dolly also adds `-dev -insecure -console` when starting an editing session.
Close that session before launching Deadlock normally.

## Features

- Capture cameras from the game with configurable keyboard or mouse bindings.
- Smooth position paths, rotation and camera bank.
- Native camera playback evaluated for each main rendered view.
- Native paused-camera movement with WASD and mouse look.
- In-game panel for capture, saved views, replay controls and movement speed.
- Replay browser and automatic startup, with the unlocker initialized before the demo loads.
- Animated `r_aspectratio` framing with an editable desktop curve.
- Fourteen supported DOF controls synchronized with the native camera, including four-value range tracks.
- Numbered in-game camera guides and spline preview during paused editing.
- In-game playback speed and monitoring rate shared with desktop controls.
- Replay playback with HUD handling, settings restoration and diagnostics.
- Console fallback with Off, Light, Balanced and Strong smoothing choices.
- Real-time H.264 MP4 video recording at the game resolution, with 30/60 FPS capture.
- Optional ReShade color effects and its in-game menu on a configurable F11 key.

## Using the Windows app

Download the **Windows x64** ZIP from [Releases](https://github.com/cravvnn/deadlock-dolly/releases).
Extract it completely and double-click **Dolly.exe**. Keep `_internal` beside
the EXE; a desktop shortcut can point to it.

Open Steam, close any running Deadlock, and use **DirectX 11**. In Dolly,
choose the game executable and a local `.dem` replay, or select a file from
**Replays**. Click **Play replay** to open the game, initialize the unlocker
in the hideout, load the replay and pause it for editing.

Move to a view and press **Ctrl+Alt+K** to capture it. **Replay timing** is the
default: advance the replay, frame the next view and capture again.
**F8** opens the in-game panel, **F7** opens the console, and
**F9** switches to Deadlock's replay UI for hero selection. Bindings, movement
speed and mouse sensitivity are saved from **Keybinds**. Capture also works
while the replay is playing and leaves it paused.

In the F8 panel, use **Playback speed**, **Updates / s** and **Show path guides**.
Guides appear in paused flight and hide during playback. Native camera and
supported effects follow each rendered frame; Updates / s controls monitoring.

On the desktop Effects tab, **+ Range DOF** creates a four-value range track.
Its value order is near blurry, near crisp, far crisp, far blurry. See the
[supported camera cvars](docs/SUPPORTED_CAMERA_CVARS.md) for values and examples.

See `Start_Here.txt` and the [user guide](docs/USER_GUIDE.md) for the full controls.
The GitHub **Source code** download and source ZIP contain the source and build
files. Windows EXE build instructions are in [BUILDING.md](docs/BUILDING.md).

## Video and ReShade

Choose an MP4 output path and FPS on **Export**, then use **F8 → Record video**
and **Finish recording** in the game. Video capture excludes Dolly controls
and path guides. It records in real time without audio; output resolution
follows the game. Recording continues through camera handoffs and desktop controls; use Finish recording to save.

Select a compatible ReShade64.dll on **Export** to enable ReShade color effects.
**F11** opens its own menu; **Keybinds** changes that shortcut. ReShade is an
optional separate download. Depth-dependent ReShade shaders are not supported
yet. See [Video and ReShade](docs/VIDEO_AND_RESHADE.md) for setup and limits.

## Recorded demos

Local tv_record .dem files can be selected like other replays. If a native shot
starts between recorded packets, Dolly starts at the next verified packet and
reports the skipped fraction. Camera/effect key times stay unchanged. Frozen
native preview holds the current scene without seeking. Some console position-
calibration recoveries still require exact ticks and can fail on sparse recordings.

## Compatibility

Native mode supports reviewed builds of `client.dll`, `engine2.dll` and
`tier0.dll`. A game update can require a Dolly update. **Console (legacy)**
is available under **Home → Troubleshooting** when Native is unavailable.
See [game updates](docs/GAME_UPDATES.md) for compatibility details.

0.5.1 is an alpha. It keeps the camera hook and interpolation from the working
0.4.7 baseline, plus the recorded-demo handling from 0.4.8. Earlier renderer
slowdowns do not have a confirmed general fix. The new video and ReShade paths
need testing in Deadlock; build checks are in [VALIDATION.md](docs/VALIDATION.md).
Fixed-step rendering, audio, separate render layers and expanded in-game curve
editing remain planned.

## Session files

Each editing launch creates a temporary `game/citadel_dolly_…` folder for its
plugins. The original `gameinfo.gi` is restored after unlocker initialization.
The temporary folder is removed when the game exits. If Dolly closes first,
a background helper waits for that game process and then removes its files.
It exits afterward and does not start another game.

Older marked folders are checked on the next Dolly launch or through
**File → Recover game configuration** with Deadlock closed. Referenced mounts,
configuration conflicts and unrecognized files are left intact. Logs and
original configuration backups remain beside Dolly for diagnostics/recovery.

## Development

Run from source with Python 3.10+ and Tcl/Tk:

```console
python -m dolly
python -m unittest discover -s tests -q
```

The source editor needs no pip dependencies. Native features also require the
compiled Windows helper. EXE builds use the pinned dependencies in
`requirements-build.txt`, Python 3.12 x64, Visual Studio 2022 C++ tools and CMake.

| Location | Contents |
| --- | --- |
| `dolly/` | Desktop editor, settings and replay controller |
| `native/` | Native camera, input, DX11 panel, tests and dependencies |
| `assets/` | Logo and Windows icon |
| `tests/` | Regression tests |
| `packaging/`, `tools/` | Executable build and packaging |
| `examples/` | Example shot |
| `third_party/` | Bundled unlocker and component notices |
| `docs/` | User guide, build instructions and technical reference |

Logs, replays, personal shots and build outputs are excluded from Git.
`SOURCE_FILES.txt` lists the source archive contents.

## Support

Contact **@Cravvnn on Discord** or [submit a GitHub issue](https://github.com/cravvnn/deadlock-dolly/issues)
if something is not working as intended. Include the Dolly version and the
ZIP from **Export diagnostics**. If the app does not open, include
`logs/Dolly_startup.log` and `logs/Dolly.log` when available.

## License

Dolly source uses the [MIT license](LICENSE.txt). Bundled components retain
their own notices under `third_party/` and `native/vendor/`. Artwork has
separate terms in [assets/README.md](assets/README.md).

## Updates

**0.5.1:** keeps recording active through camera handoffs and desktop controls.

**0.5.0:** adds real-time MP4 recording, optional ReShade color effects/menu,
configurable F11, and the REPLAY home heading.

**0.4.8:** handles native shot starts between recorded packets, preserves the
current scene for frozen native previews, and records bounded view history for
slowdown diagnosis. The native DLL is unchanged. ReShade and layer-export
research is documented in [STAGE3_PLAN.md](docs/STAGE3_PLAN.md); those features
are not implemented in this update.

**0.4.7:** restores paused flight when closing F8 from a held camera, waits for
rendered pause acknowledgement before spectator handoff, and accepts dotted
custom replay names consistently. Input and graphics diagnostics share observation
timestamps. The reported renderer slowdown and overflow remain unresolved.

**0.4.6:** corrects the initial flight HUD/cursor handoff and delayed camera
readiness, adds mouse and expanded graphics diagnostics, and consistently
formats owned C++ sources. The reported renderer overflow remains unresolved.

**0.4.5:** adds paused in-game camera/path guides, shared playback controls,
four-component range DOF and a portable supported-cvar list.

**0.4.4:** cleans temporary session folders after game exit, recovers older
leftovers and retains read-only DX11 diagnostics after a crash. The reported
vertex-buffer overflow is not yet fixed.

**0.4.3:** hides the full game HUD when returning from F9 and corrects overlay
mouse handling during UI transitions.

**0.4.2:** fixes capture during replay playback and restarting shots at the
replay's first available tick. Saved keyframes retain their authored timing.

**0.4.0:** adds the replay browser, automatic startup, configurable editor
bindings, native paused movement and the DX11 in-game panel.

**0.3.13:** compatibility update for the reviewed September 9 client, engine2
and tier0 builds. Previous build support remains.

Earlier changes are in [CHANGELOG.md](docs/CHANGELOG.md).
