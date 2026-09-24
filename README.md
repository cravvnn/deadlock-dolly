<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

**Current source: 0.5.34 alpha.** The portable Windows build opens through
`Dolly.exe`. Python and Tcl/Tk are bundled; no separate installation is needed.

**THIS MOD INJECTS CODE INTO DEADLOCK — USE AT YOUR OWN RISK.**

The Native driver loads a DLL into the game to apply camera movement during
each main rendered view. It runs in the game's user-mode process; there is no
kernel driver.

**KEEP `-insecure` IN DEADLOCK'S LAUNCH OPTIONS WHILE USING DOLLY.**

Dolly refuses to launch or connect unless it started the game process itself,
and it always adds `-dev -insecure -console` to that process. The launch-option
setting is a safety belt for the brief window before Dolly restores
`gameinfo.gi`: if Deadlock is started outside Dolly while a session is open,
exit that game before continuing. Close the editing session before launching
Deadlock normally.

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
- Video recording at the game resolution: real-time or fixed-step, 30 to 600 FPS, hardware or software H.264/HEVC encoders, or lossless FFV1.
- Paired depth master as a 10-bit ProRes `.mov`, with an optional float EXR sequence and a normalized preview video.
- Isolated world, players and effects layer takes; players and effects get a real alpha channel from black and white matte passes.
- Optional ReShade color effects, its in-game menu on a configurable F11 key, and the verified scene depth published to ReShade for depth-based effects.
- Startup update check with manual checks in Settings.

## Using the Windows app

Download the **Windows x64** ZIP from [Releases](https://github.com/cravvnn/deadlock-dolly/releases).
Extract it completely and double-click **Dolly.exe**. Keep `_internal` beside
the EXE; a desktop shortcut can point to it.

Open Steam and close any running Deadlock. Dolly selects **DirectX 11** for
Native sessions without changing your saved graphics settings. In Dolly,
choose the game executable and a local `.dem` replay, or select a file from
**Library**. Click **Open replay in Dolly** to open the game, initialize the unlocker
in the hideout, load the replay and pause it for editing.

Move to a view and press **Ctrl+Alt+K** to capture it. **Replay timing** is the
default: advance the replay, frame the next view and capture again.
**F8** opens the in-game panel, **F7** opens the console, and
**F9** switches to Deadlock's replay UI for hero selection. Bindings, movement
speed and mouse sensitivity are saved from **Settings → Controls & keybinds**. Capture also works
while the replay is playing and leaves it paused.

In the F8 panel, use **Playback speed**, **Updates / s** and **Show path guides**.
Guides appear in paused flight and hide during playback. Native camera and
supported effects follow each rendered frame; Updates / s controls monitoring.

Enable the desktop **Full editor** switch for Cameras, Effects and the shot
timeline. The switch preserves the current shot and remembers the layout.
In-game, **Camera**, **Lens** and **Export** divide the floating panel.

On the desktop Effects tab, **+ Range DOF** creates a four-value range track.
Its value order is near blurry, near crisp, far crisp, far blurry. See the
[supported camera cvars](docs/SUPPORTED_CAMERA_CVARS.md) for values and examples.

See `Start_Here.txt` and the [user guide](docs/USER_GUIDE.md) for the full controls.
The GitHub **Source code** download and source ZIP contain the source and build
files. Windows EXE build instructions are in [BUILDING.md](docs/BUILDING.md).

## Application updates

Starting with 0.5.6-alpha, opening Dolly.exe shows a startup update check against
the published GitHub Latest release and updates automatically when safe. The
updater is built into Dolly.exe; the editor runtime stays under _internal. Settings, shots and external tool
paths are preserved. Use Settings / Updates for manual checks or to turn off
automatic installation. See [Updating Dolly](docs/internal/UPDATING.md) for release
publishing and interrupted-update recovery.

## Video and ReShade

Choose an output path, FPS and encoder on **Export**, then use **F8 → Export → Record video**
and **Finish recording** in the game. Video capture excludes Dolly controls
and path guides. Real-time capture follows the game; fixed-step export advances
the simulation one frame at a time so the output stays deterministic. There is
no audio, and output resolution follows the game. Recording continues through
camera handoffs and desktop controls; use Finish recording to save.

The **Depth master** option writes a paired depth `.mov` (and optional EXR
sequence) beside the color video. **World**, **Players** and **Effects** record
isolated layer takes; players and effects also get an alpha master built from
black and white matte passes. Depth and layer takes need a verified scene
sample for every frame and stop with an error instead of writing unpaired
data. Depth exports temporarily use 100% render scale for the color/depth take
and its selected extra passes, then restore the previous scale. This avoids
unverified depth from the game's spatial upscaling pass and can make exports
slower on GPUs that normally use reduced render scale. See [Layer export](docs/internal/LAYER_EXPORT.md) for details.

Select a compatible ReShade64.dll in **Settings → ReShade** to enable ReShade color effects.
**F11** opens its own menu; **Settings → Controls & keybinds** changes that shortcut. ReShade is an
optional separate download. Dolly bundles the crosire/prod80 shader library and
publishes its verified scene depth to ReShade, so depth-based effects such as
MXAO can use it. See [Video and ReShade](docs/VIDEO_AND_RESHADE.md) for setup
and limits.

## Recorded demos

Local tv_record .dem files can be selected like other replays. If a native shot
starts between recorded packets, Dolly starts at the next verified packet and
reports the skipped fraction. Camera/effect key times stay unchanged. Frozen
native preview holds the current scene without seeking. Some console position-
calibration recoveries still require exact ticks and can fail on sparse recordings.

## Compatibility

Native mode supports reviewed builds of `client.dll`, `engine2.dll` and
`tier0.dll`. Every Dolly launch hashes the installed modules against the
bundled compatibility manifest (`native/profiles/manifest.json`) and reports an
unrecognized build instead of injecting. **Settings → Troubleshooting & recovery → Startup controls** has a
**Check game build** action, and **Console (legacy)** remains available when
Native is unavailable. See [game updates](docs/internal/GAME_UPDATES.md) for the
manifest, signature scanning and profile-generation workflow.

0.5.24 is an alpha. Camera capture and native playback build on the
0.4.x baseline, with rotation curves, attachment and in-between shot seeking. The 0.5.x line adds
the compatibility scanner and AOB fallback, real-time and fixed-step recording,
the paired depth master and layer takes, the ReShade runtime with a bundled
shader library and depth publication, the Citadel glow / health-bar / DOF
controls, the live replay speed control, in-folder update staging, and the
startup update check. Earlier
renderer slowdowns do not have a confirmed general fix; build and release checks
live in [VALIDATION.md](docs/internal/VALIDATION.md). Audio, expanded in-game curve
editing and arbitrary output resizing remain planned.

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
| `docs/` | User guide and build instructions (`docs/internal/` keeps maintainer research notes) |

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

**0.5.22:** recording a World layer before the Players layer no longer hides the characters from it, and Stop / restore always brings every scene class back.

**0.5.21:** the Players layer records real players and their equipment with no NPCs, scenery occlusion kept and real alpha, on the current game build.

**0.5.20:** supports the September 17 Deadlock client builds (including the 25379260 hotfix) and re-verifies capture and playback on them. The players-only layer export is built in but not available yet: the update moved the render-side draw records behind its selection, so the **Players layer** option stays disabled until the gates are re-derived and verified live.

**0.5.18:** reads each replay's own tick rate and uses it for replay-timed
cameras, so 32-tick replays line up instead of running the shot at double
speed. New shots adopt the detected rate, saved shots are offered a one-step
retime, and a capture that would mix two clocks is refused with instructions.

**0.5.17:** skips unverifiable transition frames at the start of a depth take
(for example a full-viewport `ALWAYS` depth write before the world pass)
instead of failing the take, and reports the skip count in zero-frame
diagnostics. Any scene failure after the first captured frame still stops the
take.

**0.5.16:** keeps the verified scene depth when a full-viewport `EQUAL` depth
write touches the chosen scene texture instead of failing the take
(`why=depth function=EQUAL`), names the failing FFmpeg pipe and appends its
stderr tail to a stopped take's error, and re-applies a disabled Citadel glow
after the replay reset that recording preparation performs.

**0.5.15:** supports the September 16, 2026 Deadlock client build
(`client.dll` `472dad57…`). The reviewed compatibility profile, bundled manifest
and generated native profile table list the new build and the native helper was
rebuilt against it; the camera symbols and all view and field offsets were
re-verified, and `scenesystem.dll`, `rendersystemdx11.dll`, `tier0.dll` and
`engine2.dll` are unchanged by this game update. The new client keeps the
reviewed `globals+0x30` clock fallback until a render-fraction observation is
recorded for it.

**0.5.14:** builds the Windows package from an explicit file list so a Dolly
folder that was run in place can no longer leak its session journals or staged
updates into a shared ZIP, and names the observed depth comparison (for example
`why=depth function=LESS`) when the scene-depth guard rejects a frame, so a
failed depth take identifies the offending pass from its own message.

**0.5.13:** fixes the health-bar toggle hang: it no longer writes the
`citadel_unit_status_enabled` or `citadel_hud_objective_health_enabled` master
switches, which could hang the game with a DX11 device error while a replay
rendered. The toggle uses the two live-verified switches again, written one
command at a time, and still restores the exact prior values on the next press.

**0.5.12:** keeps update downloads, staging and backups inside a
`.dolly-update-*` folder in the Dolly folder and removes the folder once the
update completes or rolls back safely, instead of leaving folders beside Dolly.
A deferred or retried update reuses its verified download, and a locally
modified managed file (for example a hand-built native DLL) is backed up and
replaced rather than blocking the update.

**0.5.11:** makes **Playback speed** apply immediately through the replay's
demo timescale, so a playing or paused replay slows or speeds up without a
restart (Stop / restore still returns a Dolly-owned speed to 1×), and turns the
health-bar button into **Toggle health bars**, a master hide/restore for unit,
HUD and objective bars that snapshots and restores their exact prior values.
Bar glow stays with Toggle Citadel glow.

**0.5.10:** adds in-game Camera-tab buttons for Citadel glow, health bars and
the near-player opacity fix, and a **Citadel Depth of Field** card in both UIs
with an Enable DOF switch plus log-scale sensor-size and focus-distance
sliders. The native DOF card is renamed to **Native Depth of Field** so the
two systems cannot be confused. ReShade depth effects now keep working in
online sessions: Dolly publishes its own verified scene depth from its Present
path, which ReShade's network-traffic pause does not cover.

**0.5.9:** states the export take order in the UI — the color video records
first and ticked passes follow automatically — and announces that handoff in
the status line, so Finish recording no longer looks like it starts a surprise
take. The **In-game capture** switch now also sits on the Library page, so it
is reachable without the Full editor. Includes the current user guide and
start-here wording.

**0.5.8:** fixes layered export take paths nesting inside the previous take,
replaces stale bundled ReShade search paths instead of listing each effect
several times, publishes the verified scene depth to ReShade for depth-based
effects, and stops depth takes failing on the scene tracker's observation
budget or on a later scene pass without per-view constants. Diagnostics now
include the newest game crash dumps and name the depth rejection reason.

**0.5.7:** matches the desktop and in-game panels to the wireframe layout and
keeps Windows builds manual.

**0.5.6:** embeds the startup update check in Dolly.exe and moves the desktop
to the launcher-first layout with the Camera, Lens and Export panels.

**0.5.5:** adds verified public-release updates that preserve tool paths, the
paired depth master, isolated layer takes with black and white matte alpha,
scroll-wheel framing fixes and a recording-failure dialog.

**0.5.4:** adds recorded-shot sidecar metadata (`<video>.shot.json`) with the
exact first/last shot frame and replay time, waits for a live recorder before
playing a prepared shot, and bundles a patched cvar unlocker whose Disconnect
cleanup removes the normal-quit access violation. Internal live-depth work is
default-off.

**0.5.3:** adds the bundled compatibility manifest and per-launch build scanner,
a native AOB fallback for game updates whose camera code is byte-identical
modulo relocated addresses, reviewed support for the September 11 client, and
`tools/generate_profile.py` to generate a new profile, manifest and native
header from a real install.

**0.5.2:** adds 120 FPS recording. Layer export requirements are documented; depth, hero-only and world-only passes are not included yet.

**0.5.1:** keeps recording active through camera handoffs and desktop controls.

**0.5.0:** adds real-time MP4 recording, optional ReShade color effects/menu,
configurable F11, and the REPLAY home heading.

**0.4.8:** handles native shot starts between recorded packets, preserves the
current scene for frozen native previews, and records bounded view history for
slowdown diagnosis. The native DLL is unchanged. ReShade and layer-export
research is documented in [STAGE3_PLAN.md](docs/internal/STAGE3_PLAN.md); those features
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
