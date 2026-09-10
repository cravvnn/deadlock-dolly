# Deadlock Dolly — user guide

**0.4.0-alpha adds Stage 1 connected editing:** a cleaner launcher and replay
browser, configurable controls, native paused flight with mouse look, and a
DirectX 11 in-game panel. The desktop editor and in-game panel operate on the
same camera project. Existing framing/effect editing remains on the desktop.

The new Windows/game integration and ReShade coexistence still need live
validation. Follow [STAGE1_TESTING.md](STAGE1_TESTING.md) before public release.
Automated tests and a Windows package startup check are separate from a
successful Deadlock session. This is an alpha test candidate.

## Start here

1. Close Deadlock. If an older Dolly has pending recovery, use its **File →
   Recover game configuration** before replacing it. Keep old logs and shots;
   never discard a pending gameinfo backup.
2. Extract the **whole Windows ZIP** into a fresh writable folder. Keep
   `Dolly.exe` and `_internal` together. The portable package includes Python.
   Source users need Python 3.10+ with Tcl/Tk and a built native helper; see
   [BUILDING.md](BUILDING.md). Do not mix ABI 3 with earlier helper/editor files.
3. Open Steam and sign in. Use **DirectX 11** in Deadlock's graphics settings.
4. Open **Dolly.exe**. On **Home**, choose `game/bin/win64/deadlock.exe` and a
   local, decompressed `.dem`. Older `citadel.exe` installations and Steam
   libraries on another drive are accepted; do not rename game files.
5. Alternatively, open **Replays**, choose/refresh a replay folder and select
   a file. It lists local `.dem` files; it does not download or decompress demos.
   Review **Keybinds** before launch if you want a mouse-button capture key.
6. Click **Play replay**. Dolly connects to the game process it launched,
   waits for rendered pre-replay scene and unlocker-registration evidence,
   executes `cvar_unhide` once, and requires both completion summaries.
7. Only after confirmation, Dolly loads the selected demo, checks its identity
   and camera support, pauses it, closes the console explicitly and enables
   native flight. Startup progress appears on Home. Do not load a demo manually
   while waiting for this sequence.
8. Frame a view with WASD and mouse look, then capture it. Fly to the next
   camera position and capture again. **F8** opens the in-game Dolly panel.

**Cancel startup** stops the automatic sequence; it does not kill Deadlock.
The launched process remains open. Close it before launching another session.
A timeout or failed unlocker confirmation does not automatically load the demo.

**Home → Troubleshooting…** retains manual Launch hideout, Connect, Initialize
unlocker, Load replay and Check camera support. Use the manual initialization
only when the hideout has finished loading. Probe does not rerun the unlocker
inside a demo. Netconsole remains the default; VConsole is a troubleshooting
alternative selected before a new launch.

The launcher keeps `-dev -insecure -console`. **Launch options…** accepts
restricted additional display options such as `-windowed -w 1280 -h 720`.
Replay/map commands, `-secure`, Vulkan and managed game/console arguments cannot
override the editing configuration. Close this editing session before opening
Deadlock normally.

The native helper runs inside the game's user-mode process; it is **not a
kernel driver**. Native launch checks reviewed `client.dll`, `engine2.dll` and
`tier0.dll` fingerprints. Unknown updates are blocked. See
[GAME_UPDATES.md](GAME_UPDATES.md); do not replace game DLLs or edit fingerprints
to force compatibility. **Console (legacy)** in Troubleshooting preserves the
older workflow without the Stage 1 DX11 panel/native flight.

## In-game input and default keys

| Shortcut | Action |
| --- | --- |
| Ctrl+Alt+K | Capture camera; starts an empty shot or appends a view |
| Ctrl+Alt+R | Replace selected camera |
| P | Pause/resume replay time |
| F5 | Play the authored camera shot from its start |
| F6 | Stop and restore |
| PageUp / PageDown | Previous / next camera at the current paused moment |
| Comma / Period | Seek backward / forward one second |
| F7 | Open/close console, reserved |
| F8 | Dolly in-game panel |
| F9 | Original game replay UI / hero selection |
| F10 | Paused flight |
| WASD | Move relative to the camera |
| Space / Ctrl | Rise / descend in world height |
| Mouse or arrow keys | Look |
| Shift / Alt | Faster / slower movement |
| Q / E | Roll the camera |

During **flight**, Dolly owns movement and mouse look. **F8** releases mouse
look for clicking its panel. The panel provides capture/replace, saved-camera
selection, replay/path playback and movement-speed controls. Change movement speed and sensitivity from desktop
**Keybinds**; the in-game speed control updates the same saved setting.

**F7** gives the console input ownership before the open command is dispatched,
so typing cannot create keys or move the camera. F7 closes it again; Escape
also requests console closure. **F9** returns the camera and mouse to Deadlock's
original replay UI for selecting a hero. Return through F8 for the panel or F9
for flight; Dolly seeds from the currently displayed view rather than a stale
camera from before hero selection.

Changing focus clears native held inputs and accumulated mouse movement.
Release controls after returning from another window before pressing again.
If a UI transition fails, inspect the status/diagnostics and use the game UI
recovery path instead of repeatedly issuing camera actions.

## Customize bindings

Open **Keybinds**, select an action and choose a keyboard key, Mouse4, Mouse5
or MiddleMouse. You can use **Press a key / mouse button…**, optional modifiers,
or **Unbound**. Click **Save binding**. Exact duplicate bindings are rejected;
F7 is always reserved for console access. Movement modifier keys can be assigned
in their movement context. Mouse software that translates a side button to a
keyboard key should use that resulting key in Dolly.

Bindings, movement speed and sensitivity are saved in
`%APPDATA%/DeadlockDolly/settings.json`. No manual configuration editing is
needed. Existing capture preferences are migrated. If an old capture binding
conflicts with a newly introduced default, Dolly keeps the capture choice and
asks you to reassign the conflicting action. Invalid settings are reported and
preserved until you explicitly save replacement preferences.

The native editor handles input inside the launched game while its editor
mode is active. It suspends editor shortcuts for console typing and ordinary
game interaction. The external capture/keyboard listener remains only for the
legacy Console workflow; it is disabled during native editing.

## Switch and move cameras while paused

After **Play replay**, native flight starts from the rendered camera pose.
It updates position and mouse/keyboard rotation in the main-view callback
using elapsed real time. It does not move the game spectator with repeated
`spec_goto`, and entering flight does not perform the legacy adjacent-tick
height calibration. Moving the camera does not advance the paused demo.

Capture stores the view sampled with the native capture event. The camera can
continue flying after a capture without entering coordinates. Choose a saved
camera in the panel, or use PageUp/PageDown, to apply its position, rotation,
aspect and supported DOF at the **current replay moment**. This does not seek
to that key's arrival time. F10 continues flight from the displayed view.

P controls replay time. If replay time advances while manual flight is active,
the current manual camera is held; pause again to continue paused editing.
P during a normal authored native shot pauses/resumes replay time without
restarting the path. Use F5 to restart the authored shot. Stop a frozen preview
before trying to resume replay time. Comma/Period uses the project's ticks per
second; check that value in **More → Coordinates / timing…** before timing work.

**Console legacy movement:** the older paused-camera dialog remains available
when launching the Console driver. It uses a bounded camera calibration and
may briefly seek to an adjacent tick and return, then verifies XYZ movement.
Release movement keys during preparation. It uses external keyboard input and
arrow-key look, not native mouse look. Stop that legacy flight before typing
in the game console. The native input behavior above does not apply to it.

## Make a first shot

1. **Replay timing** is selected by default on Cameras. Camera arrival times
   follow the replay moments at which each view is captured.
2. Frame the first view and use Ctrl+Alt+K or **Start path here**. Dolly records
   the first camera at time zero and sets the shot's replay start tick.
3. Advance the replay, move to another position and capture again. Edit a
   selected camera's arrival time to retime an existing key. **Timed shot**
   remains available for several views at one paused tick; its spacing starts
   at three seconds and can be changed for future captures.
4. Leave **Frozen preview** off and **Hide HUD** on. F5 or **Play shot** seeks
   to the shot start, applies the starting camera/lens/DOF, then plays the demo
   and path together. Release movement keys while it prepares the shot.
5. Select a camera and use **Replace selected** to recapture its view at the
   same authored time. Edit aspect, bank and arrival time on Cameras; use
   **Preview** to check a changed view. Graph edits alone do not go live.
6. Save from the desktop File menu. Projects remain editable JSON files.

**Replay timing** assigns keys from their actual replay ticks: capture a view,
advance the demo, and capture the next. Capturing twice at the same tick cannot
create distinct arrival times; advance, replace the key, or use Timed shot.
The project's default is **64 ticks/second**, not automatic tick-rate detection.
Normal playback and relative seek require a readable current replay tick.

At 0.1 speed, a four-second shot takes about forty real seconds. Smooth spline
geometry does not guarantee constant speed; spacing and arrival times determine
speed through each section. Frozen preview deliberately moves along the whole
path while its scene stays paused.

Native capture reads rendered position, height, bank and framing. Console
capture reads `spec_pos`, which has no roll field, and retains Dolly's last
bank value. Existing shots need no guessed height adjustment. Native saved-view
preview and flight avoid legacy spectator calibration; the authored Play shot
startup retains its existing verified positioning/seek sequence.

Rotation still uses Euler angle curves. `shortest` crosses yaw/roll wrap
boundaries by the shorter route; `unwrapped` permits an intentional 0 → 360
turn. This stage does not replace the established path clock or spline math.

The desktop XY path overview is not an in-world overlay. In-game camera
markers, spline visualization and expanded position/rotation/aspect curve
editing remain Stage 2. Video export and depth/world/hero/effect passes remain
Stage 3. ReShade coexistence is unverified; test it separately after clean DX11.

## Framing curve

Use **FRAMING CURVE** on **Cameras** for the zoom-like effect. Its horizontal axis
is shot time and its vertical axis is the aspect-ratio framing value. Console preview/playback use `r_aspectratio`; native preview/playback apply the
framing directly to each main view. The graph
shows the entire **0.5–4.0** editing range and a line for the shot's **Normal**
aspect ratio. These are Dolly's editing limits, not verified native cvar bounds.
The values are ratios, not FOV degrees; preview them in your scene to judge the
framing. Dolly does not convert between the two or claim an identical projection.

1. Select a camera in the list or click its point in the graph.
2. Drag the point up or down to change that camera's aspect ratio. This updates
   the saved camera value without changing its arrival time or position. With
   the graph focused, Up/Down changes it by 0.01; Shift+Up/Down changes it by 0.1.
   You can also enter an exact **Aspect ratio** and click **Update camera**.
3. Leave the graph's interpolation on **smooth** for a continuous curve that
   does not overshoot between values. **linear** connects values directly;
   **step** holds each value until the next key. This selection is independent
   of the camera's position spline.
4. Use **Preview** for the selected camera, or **Play shot** to run the curve
   with the moving camera and replay. Graph edits alone do not send commands
   to the game.

**Normal** starts at 16:9 (about 1.77778), matching the supplied screenshots.
Choose 16:10, 21:9 or 4:3, or type a custom ratio/decimal and press Enter for a different standard.
Use **Reset** to set the selected camera's framing to Normal. Changing Normal
changes the reference value; it does not replace every existing framing key.

Native capture stores the rendered positive aspect ratio. Console capture
retains an explicit positive `r_aspectratio` value. If the game reports
its automatic value **0**, Dolly resolves it from the launched game's largest
visible client area on Windows, falling back to the shot's Normal value when
that area cannot be read. The client area does not detect internal letterboxing
or a custom render viewport: set Normal appropriately if that applies. Diagnostics
record the raw value, resolved ratio and source. Zero is never interpolated as a
framing key. **Stop / restore** returns the original cvar value exactly, including
0 for automatic mode. Pause and the end of a shot keep the current framing.

**Opening an older shot:** version-1 projects retain their camera coordinates,
angles, arrival times, replay settings and other cvar tracks. Their old FOV
numbers are retained as inactive file metadata. Framing starts at 16:9 for all
migrated cameras because the old FOV settings have no verified aspect-ratio
conversion. Rebuild only the framing curve; the camera path needs no recapture.
Saving writes version 2, which older Dolly releases cannot open. Custom tracks or
fixed values that target an old FOV control or `r_aspectratio` must be removed;
Dolly reports the conflict so that only the framing curve controls this setting.

## Program icon

Dolly uses the exact reattached, white-backed film-reel logo as its window and
Windows taskbar icon. Only size conversion is applied to the artwork. The source
file is preserved byte-for-byte as `assets/logo-original.png`.

Version 0.2.2 corrects an icon-format incompatibility with older Tk 8.6 Windows
readers: every ICO image now uses an uncompressed 32-bit bitmap with a complete
transparency mask. Dolly applies it explicitly to its main window and future
dialogs; PNG is used only if ICO loading fails. Missing icon files are logged
without preventing startup.

The desktop launcher starts the editor without retaining a Python console
window or its separate taskbar button. Dolly keeps its dedicated Windows
application identity. The launcher `.bat` file itself retains its normal File
Explorer file-type icon. Close the previous Dolly instance before launching
this version so you can distinguish the current window from an old one.

## Editor layout

**Home** contains one-click startup and progress; **Replays** lists local demo
files; **Keybinds** configures input and movement. **Cameras** keeps the list,
framing graph, selected-camera controls and XY path overview together.
**Effects** contains DOF and numeric camera-variable tracks. Coordinate entry
and advanced timing are in **More → Coordinates / timing…**.

Playback controls appear on the camera/effect editing pages. Home stays focused
on launching and choosing a replay. Tables scroll inside their own panels.
**Log** opens a separate resizable activity window, closed by default.

The DX11 in-game panel provides the Stage 1 editing controls. Save/load and
full desktop graph editing remain in the desktop application. It is one shared
project, not a second copy that requires import/export to synchronize changes.

**Frozen preview** is off by default. It moves along shot seconds through the
currently paused scene rather than resuming or seeking to the shot's start.
Choose the scene first. Capture timing determines authored key timestamps;
frozen preview determines whether replay time advances during playback.

## Depth of field and other camera variables

On **Effects**, use **+ Depth-of-field preset** as a starting point. The preset
enables the native Citadel DOF controls and creates focus and aperture tracks.
Select each track and edit its time/value keys. Increase the preset's aperture
only as much as the shot needs; the effect must be checked at your game settings.

| Control | Meaning | Reference range |
| --- | --- | --- |
| `r_citadel_depthoffield_enable` | Enable Citadel DOF | 0 or 1 |
| `r_depth_of_field` | Enable depth-of-field rendering | 0 or 1 |
| `r_citadel_depthoffield_focus_distance` | Focus distance in inches | 0–10000 |
| `r_citadel_depthoffield_aperture_diameter` | Aperture diameter in inches | 0–3 |
| `r_citadel_depthoffield_sensor_size` | Sensor size in inches | 0.5–3 |
| `r_citadel_depthoffield_mode` | Normal / near only / far only | 0 / 1 / 2 |
| `r_citadel_depthoffield_debug` | DOF debug view | 0 or 1 |

Use **Step** interpolation for toggles and mode values. Use Linear or Smooth for
continuous focus/aperture changes. **Fixed values…** opens a separate dialog on
**Effects**. These values are applied for the shot; a track with the
same name overrides the fixed value while the shot runs.

Custom tracks accept numeric camera cvars, including `citadel_camera_*`, `cam_*`,
`r_citadel_depthoffield_*`, `r_dof_*` and several HUD/lens controls. These are cvar
names, not arbitrary console command lines. Being accepted by the editor does
not mean the cvar exists or works in your current game. Dolly first queries its
current numeric value and stops with a diagnostic if it cannot read it.

To animate or fix `citadel_hud_visible` yourself, turn **Hide HUD**
off; Dolly rejects combining that automatic option with a project value or track
for the same cvar.

The supplied unlocker removes hidden/development/defensive flags. It does not
remove cheat flags. Start with the native Citadel DOF family above; generic
`r_dof_override*` controls may remain unavailable.

Leave a track's **Restore value** blank to restore the value read before the
shot. Filling it in deliberately overrides that restoration value.

With Native playback, supported DOF curves use the camera's frame phase and
native typed setters with readback. **Updates / s** affects editor monitoring
only. See [native DOF support](NATIVE_EFFECTS.md) for the exact controls and
restoration behavior. Other camera cvars require Console mode.

## Playback behavior

- Smooth or Linear camera paths; Linear, Smooth or Step cvar tracks. A smooth
  spatial spline does not automatically give constant travel speed. The distance
  between views and their arrival times determine how fast each section moves.
- **Play shot** starts from zero, seeks the shot's starting replay tick, applies
  the initial camera and fixed/animated cvars, verifies the camera position,
  then resumes. The replay and path
  use the same speed. At **0.1×**, a four-second shot takes about **40 real
  seconds**; at **1×**, it takes about four.
- **Native playback** publishes the complete shot before the replay resumes.
  The helper evaluates position, rotation and aspect for each main rendered
  view using the game's time. The Console smoothing filter is disabled for
  this driver. Native manual flight and saved-view capture now share the native
  camera, retaining the existing project format.
- **Console playback** timing follows acknowledged replay ticks. New integer ticks
  adjust the clock gradually rather than snapping the camera's fractional
  position. Position, rotation, aspect and cvar curves share that clock. Its
  underlying estimate can briefly lag a tick, leads by at most one tick, and holds at that
  cap when observations stop. Optional smoothing adds the delay described below.
  Completion waits for both the replay and camera
  to arrive, then explicitly applies the final key. A small final clock lag gets
  a bounded finishing period instead of an immediate jump to the endpoint.
- Legacy positioning startup checks require approximately one-to-one XYZ movement and a verified
  return. During Console movement, occasional position readbacks check for persistent
  divergence from recent commands. A large sustained mismatch stops movement;
  it does not train a new correction from unreliable paused-camera responses.
  Diagnostics retain separate startup measurements and bounded frame traces.
- Replay file metadata establishes which demo is open. Its `playback_ticks`
  field is the total length, never the current position. Dolly queries bare
  `demo_goto` for the live tick; it supplies no seek arguments for that query.
  Timed capture and frozen playback also work when only metadata is readable.
- With **Hide HUD** enabled, Dolly sends
  `citadel_hud_visible 0` before resuming, then `citadel_hud_visible 1` at the
  end of the path or on Pause, Stop, or a playback error. If supported, it also
  sets `citadel_hide_replay_hud 1` during playback and restores that control's
  previous value afterward. Restoration requires a working game connection.
- If supported, Dolly temporarily sets `engine_no_focus_sleep 0` during playback
  and restores its previous value afterward. This avoids that source of extra
  delay while the editor has focus.
- **Pause** holds the current camera and lens/DOF values and pauses the replay,
  while restoring the HUD and the temporary playback controls above. At the end
  of a path the final view also remains in place with the replay paused.
- **Native camera handoff:** completed camera/DOF can remain held. Play shot and
  Stop release the old override before any new seek. They do not require you
  to cycle heroes to restart. Native flight can seed from the displayed held
  view. Returning to the original game UI explicitly releases native ownership;
  the game's underlying spectator may be in a different place.
- **Stop / restore** restores the captured cvar baseline (including the exact original `r_aspectratio`) and returns replay
  speed to **1×** if Dolly changed it. It leaves replay time paused and returns
  native camera ownership to the game; the underlying spectator view can differ. Previous replay speed is not queried.
- Playback speed is 0.05×–4×. Updates / s choices are 30/60/120. They control
  camera commands with Console, and editor monitoring with Native. They do
  not set the game's render or recording frame rate.
- On supported Windows versions, frame pacing uses a dedicated high-resolution
  waitable timer. An unavailable timer falls back to normal event waits and is
  reported in diagnostics. No system-wide timer setting is changed.

The new native input/render integration is restricted to reviewed builds and
still needs a Windows/Deadlock run. Console remains available for comparison.
Neither driver records video in Stage 1; use existing capture software. Complete
[STAGE1_TESTING.md](STAGE1_TESTING.md) before publishing the alpha to users.

## Experimental playback smoothing

**These settings apply only to Console playback.** Native disables and ignores
the filter. For Console, choose **Smoothing** under Shot playback before pressing **Play shot**. It
averages the shared playback time over a short real-time window, then evaluates
position, rotation, aspect and camera-variable tracks together at that time.
The authored path geometry stays intact; Step cvar tracks still change
discretely. This applies to normal path playback and Frozen preview, not manual
paused-camera flight, saved-view previews or capture.

| Mode | Real-time filter window | Added camera delay during steady movement |
| --- | --- | --- |
| Off | 0 ms | 0 ms |
| Light | 80 ms | About 40 ms |
| Balanced | 160 ms | About 80 ms |
| Strong | 280 ms | About 140 ms |

**Balanced** is the initial Console smoothing choice. It remains for this
editor session and is not saved in settings or project files. Changing it
during a shot affects the next Play shot. **Off** preserves the 0.3.6 command
sequence, including its smooth endpoint completion.

Stronger settings spread small timing changes over a longer window, at the
cost of more camera delay relative to the replay action. The values above are
real-time delays, including at 0.1 replay speed. At the end, Dolly lets the
filtered camera finish before restoring the HUD; it does not snap the filter
to the last key. A discrete Step track remains discrete, with its timing
following the same delayed camera clock.

For a useful comparison, keep the same shot, recording setup, **Speed 0.1**
and **Updates / s 120**. Play once with Off, once with Balanced, then once with
Strong. Compare the diagonal pan separately from the last turn into the final
camera. Export diagnostics after the setting you record. The filter has
automated regression coverage, but an in-game improvement is not yet verified.
It cannot replace missed console updates or smooth frames inside Deadlock's
renderer, so it does not establish that the build is ready for public release.

## How the unlocker is loaded and recovered

Dolly bundles the **unmodified official cvar unlocker v0.5.2 DLL** and checks its
pinned SHA-256 before launching. It never replaces the real game `server.dll`.

For the upstream SearchPaths loading method, Dolly makes a temporary edit to
the existing `game/citadel/gameinfo.gi`. It first writes an exact original backup
and recovery journal to `logs/<session>/`, then atomically adds a unique plugin
mount. The normal Citadel game directory is retained so an alternate game name
does not introduce replay game-directory mismatch errors.

The original file is restored after automatic or manual hideout initialization
confirms unlocker activation, when the game exits while Dolly is open, or when Dolly closes.
The already loaded unlocker remains in that running development process.
Launch failure also triggers recovery. Before the next launch, unfinished
transactions are checked and recovered while Deadlock is closed.

After an interrupted launch or forced shutdown, close Deadlock and run
**Recover_Game_Config.bat** from the same extracted folder before starting the
game normally. Keep that folder and its logs until recovery succeeds. Recovery
will preserve a newer Steam/user edit instead of replacing it; an error will
identify the backup to compare. Existing standard `citadel/cvar_unlocker`
mounts are temporarily replaced in the session and restored with the original
file. Steam launch settings and pre-existing mod files are not changed.

## If something fails

Use **Export diagnostics** in Dolly and send the resulting ZIP with a short
description of the failed step. It includes the active shot and playback
settings, measured playback update rates, console responses, capability results
and launch logs, including local file paths. Preview diagnostics include the
complete requested frame, its aspect ratio, and the action's result or error.
Camera-position checks include requested positions, readbacks and measured
corrections. This helps distinguish a timing
choice in the shot from console throughput or a stalled replay clock. Failed
launch attempts also record your selected executable/replay paths and the error, even if startup
failed before a game session existed. This version adds a bounded raw console
history and logs from up to eight recent sessions, so a successful retry does
not hide a preceding crashed launch. New session journals retain exit code/time.
Native runs also retain sampled original/applied view poses, callback counts,
native timing and handoff checks. These are diagnostic samples, not a complete
record of every rendered frame. The export does not include the replay itself
or game assets. The support check verifies commands and native readiness, not visual
camera effects: mention separately whether position, rotation, aspect-ratio framing or DOF failed.

For a legacy/authored-start positioning check failure, make sure the replay is in roaming/freecam,
release all movement keys, and retry the selected camera's Preview once. Wait for the
check to finish before moving the camera. If it still fails, export diagnostics;
do not manually adjust every key's height. A fixed or unstable position response
cannot be corrected reliably with a guessed height offset.

If initialization returns empty console output, stay in the hideout and retry
once. If it persists, export diagnostics there. An empty response is an unknown
result, not proof that the DLL is absent; different logging channels and delayed
engine output need to be checked against the raw history. Dolly does not skip
initialization or automatically load the replay after a failed confirmation.

If the editor never opens, send `logs/Dolly_startup.log`. The batch launcher keeps
the error visible. Runtime details are also in `logs/Dolly.log`.

If the game launch changes or breaks after a Deadlock update, the pinned unlocker
may need a compatible release. Dolly will not silently replace it with a new
binary. Run recovery as described above if a configuration edit remains pending.

## Package and development

- `dolly/`: complete Python editor, controller, path engine, launcher and console source.
- `tests/`: automated path, transport, controller and launcher/recovery checks.
- `native/`: native camera source, exact-build profile, C++ tests and vendored dependency.
- `docs/NATIVE_CAMERA.md`: native driver scope, compatibility and first test.
- `examples/demo_shot.dolly.json`: a **synthetic** example of file structure.
  Its coordinates are not a verified location in your replay; capture your own.
- `docs/CONSOLE_RESEARCH.md`: source evidence and remaining integration questions.
- `docs/VALIDATION.md`: what was and was not tested for this release.
- `third_party/`: official unlocker binary, provenance and third-party licenses.

Run `python -m unittest discover -s tests -v` from this folder to run the tests.
Run `python -m dolly` to open the editor without the batch wrapper.

HLAE is the workflow reference, not a transplanted binary hook. Its CS2 view
offsets and the supplied CS2 animation offsets have not been treated as Deadlock
offsets. Source references: [HLAE](https://github.com/advancedfx/advancedfx),
[cvar unlocker](https://github.com/Artemon121/cvar-unhide-s2-citadel), and
[extracted Deadlock command data](https://github.com/SteamDatabase/GameTracking-Deadlock).
