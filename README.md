<img src="assets/dolly.png" width="96" alt="Deadlock Dolly logo">

# Deadlock Dolly

A camera-path editor for local Deadlock replays. Capture the free camera,
shape a shot and play it back with animated framing and camera variables.

**Current source: 0.6.15-alpha.** The portable Windows build opens through
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

- In-game camera list: click to select, double-click to view, and delete the selected camera.
- Shared shot Undo/Redo in the desktop and in-game editors.
- Game Follow with hero selection, adjustable distance/shoulder/height, slider resets and optional game HUD.
- Bone-camera preview and attachment, including direct transfer from paused Follow.
- Captured lens metadata preserved through replay reloads, playback, save/reopen and history.
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
- High-resolution screenshots: plate, 16-bit players-only matte, cut-out hero and depth from one paused view, up to 8192 × 8192 (see [High-resolution screenshots](docs/SCREENSHOTS.md)).
- Video recording at the game resolution: real-time or fixed-step, 30 to 600 FPS, hardware or software H.264/HEVC encoders, or lossless FFV1.
- Paired depth master as a 10-bit ProRes `.mov`, with an optional float EXR sequence and a normalized preview video.
- Isolated world, players and effects layer takes; players and effects get a real alpha channel from black and white matte passes.
- Optional ReShade color effects, its in-game menu on a configurable F11 key, and the verified scene depth published to ReShade for depth-based effects.
- Startup update check with manual checks in Settings.
- Optional game-only audio for real-time video, plus a separate advanced reconstructed-audio workflow.

### 0.6.14 highlights

Maintenance across the desktop and in-game editors: shared controls and theme
setup, clearer page and operation ownership, separated native editor publication
and panel rendering. Existing layouts, shortcuts, camera/export features and
settings remain intact, including the 0.6.13 mod-loading repair. Clean source
builds use a refreshed, checksum-pinned LGPL FFmpeg runtime. See the
[release notes](docs/RELEASE_NOTES_0.6.14-alpha.md) for changes and offline validation limits.

### 0.6.13 fixes

Mods load again while Dolly drives the game. Dolly carries your installed addon
mounts (Deadlock Mod Manager, Grimoire or a manual setup) into its temporary
game configuration and still restores your original file exactly on exit.
Uncompiled Panorama files inside mounted mod folders are still refused with
their exact path. Existing camera, graphics-profile and export features are
retained. See the [release notes](docs/RELEASE_NOTES_0.6.13-alpha.md).

### 0.6.4 highlights

Reviewed compatibility for **Deadlock build 6731 / Steam build 25658155**:
native camera, automatic replay loading and the cvar unlocker are updated.
The Bone Picker and Game Follow cameras read player IDs again, and pressing F9
during playback brings back Deadlock's replay UI and timeline for scrubbing.

### 0.6.3 highlights

Reviewed compatibility for **Deadlock build 6728 / Steam build 25658155**:
native camera, automatic replay loading and the cvar unlocker are updated.
Dolly now steps over Deadlock's own assertion dialogs instead of appearing
frozen, checks native depth-of-field shader support and warns when the game
cannot compile the effect (the magenta/black checkerboard), and the editing
configuration tolerates a read-only `gameinfo.gi`.

### 0.6.1 fixes

Saved-camera markers now keep their anchored size and a consistent shape at any
framing or aspect, including while previewing a camera path. If Deadlock never
runs its hideout intro or starts the map/shader preload, Dolly stops automatic
startup early and offers to load the selected replay without that check instead
of waiting for a state that cannot change.

### 0.6.0 highlights

This release updates reviewed compatibility for **Deadlock build 6726 / Steam
build 25639407**, restores player discovery in local `tv_record` demos, and
adds the in-game camera list and shared Undo/Redo. It also fixes captured-lens
framing, floating health-bar controls, real-time recording gaps caused by
repeated replay-clock values, and final-frame loss when adding audio.

See the [changelog](docs/CHANGELOG.md) for details. Older custom-lens shots that
lack lens metadata may need recapture; new captures preserve their framing.

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

Returning from hero selection to Dolly's native camera keeps the chosen view
and switches the underlying game camera to Free Cam. The native camera also
filters death desaturation and hides the game's player screen-particle layer
(including damage borders and low-health pulses) while it owns the view.
F9 restores the previous spectator mode and its effects; the separate
Player POV export keeps the game's selected-player view and effects.

In the F8 panel, use **Playback speed**, **Updates / s** and **Show path guides**.
Guides appear in paused flight and hide during playback. Native camera and
supported effects follow each rendered frame; Updates / s controls monitoring.

Enable the desktop **Full editor** switch for Cameras, Effects and the shot
timeline. The switch preserves the current shot and remembers the layout.
In-game, the tabs are **CAMERA, FOLLOW, BONE PICKER, LOOK and EXPORT**.

On **CAMERA**, one click selects a saved camera without moving the view;
double-click jumps to it. Delete removes only that camera. Undo/Redo restore
shot edits through the desktop or in-game controls. With the Dolly panel open,
use **Ctrl+Z** to undo and **Ctrl+Y / Ctrl+Shift+Z** to redo. These controls do
not rewind the replay or undo external game settings, and are guarded during
playback, recording and active pickers.

On **FOLLOW**, choose a hero to follow their aim with the game's camera.
Adjust distance, shoulder and height; right-click a slider to reset its normal
value. The HUD option lets you retain or hide the game UI. Follow is separate
from a bone attachment. You can open **BONE PICKER** directly from paused Follow.

Regular camera paths hold their final view when playback finishes. Explicit
**F9** or **F6 / Stop–restore** returns control through the spectator handoff.

On the desktop Effects tab, **+ Range DOF** creates a four-value range track.
Its value order is near blurry, near crisp, far crisp, far blurry. See the
[supported camera cvars](docs/SUPPORTED_CAMERA_CVARS.md) for values and examples.

In the in-game **LOOK** tab, right-click a DOF value to restore Dolly's default.
Enabling Citadel DOF switches off Native range DOF and supplies a usable aperture
when none is authored. Switching back retains your range settings and animation.

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
optional game-only audio for real-time capture, and output resolution follows
the game. Fixed-step bundled audio is rejected; export silent layers separately.
Reconstructed audio is an advanced workflow with additional tools.
Recording continues through
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
native preview holds the current scene without seeking. The scripted intro
remains visible during shot preparation until a true skip is verified. Some console position-
calibration recoveries still require exact ticks and can fail on sparse recordings.

Console camera preparation first checks the current view without seeking. It
uses a bounded refresh only when the measured camera response fails. If
**Stop / restore** leaves the health panel pending, the restore guide opens:
choose **Open replay controls**, select a hero and wait for its camera, then
return to Dolly and choose **Restore after selection**. The panel stays hidden
until that handoff is verified.

## Compatibility

Native mode supports reviewed builds of `client.dll`, `engine2.dll` and
`tier0.dll`. Every Dolly launch hashes the installed modules against the
bundled compatibility manifest (`native/profiles/manifest.json`) and reports an
unrecognized build instead of injecting. **Settings → Troubleshooting & recovery → Startup controls** has a
**Check game build** action, and **Console (legacy)** remains available when
Native is unavailable. See [game updates](docs/internal/GAME_UPDATES.md) for the
manifest, signature scanning and profile-generation workflow.
Console avoids the native camera/capture bridge, but its unlocker and automatic
startup-readiness checks still need support for the installed game build.

Native camera startup checks its essential view and gameplay-effect hooks
separately from the optional Follow correction. A rejected Follow correction
disables Game Follow while keeping a verified camera usable. Diagnostics record
which prerequisite failed; malformed optional diagnostic blocks no longer hide
the main camera status. Core protocol and process-identity checks remain strict.
This separation reduces the scope of some update failures; it does not approve
unknown game builds or establish support for Depth or Players output.

Current startup, Follow and attachment definitions share reviewed inputs across
Python and native code. One offline generator reproduces these definitions and
the camera/sound compatibility tables; native builds reject stale generated
data. The [maintainer workflow](docs/internal/GAME_UPDATES.md#shared-reviewed-contracts-unreleased-hardening)
records how to review and regenerate them after a game update.

0.6.0-alpha was checked against build 6726. Current-build checks covered camera
paths and mode transitions, camera-list/history controls, health/HUD restoration,
DOF with Confetti, short layer exports, and a three-second 180-frame Color/audio
take with zero missed slots and user-confirmed smooth, aligned playback.
These bounded checks do not certify every GPU, long take or 4K workload.

Floating health bars and the selected hero's health/ability HUD use separate
controls. Glow remains subject to the game's eligibility rules: enabling it
does not force every hero to glow, and its visual effect was not conclusively
verified. A reported particle checkerboard was not reproduced locally across
the tested presets and quality settings; do not assume every installation is fixed.

Real-time recording now retains rendered frames when replay time briefly repeats.
Genuine missed capture slots are still reported; high export FPS is not a promise
that the game or encoder can sustain that rate. See [validation notes](docs/internal/VALIDATION.md)
and [Video and ReShade](docs/VIDEO_AND_RESHADE.md) for workflow limits.

## Session files

Each editing launch creates a temporary `game/citadel_dolly_…` folder for its
plugins. The original `gameinfo.gi` is restored after unlocker initialization.
A read-only `gameinfo.gi` is supported and keeps its attribute. Uncompiled
Panorama files left in `game/citadel/panorama` are listed and refused before
launch, because development mode would load them before the packaged UI.
Deadlock's development build can show its own assertion dialogs; Dolly chooses
the ignore action for the game process it launched and records it in the
session, so a game-side failure cannot freeze editing.
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
