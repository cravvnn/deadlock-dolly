# Deadlock Dolly — user guide

A desktop camera-path editor for local Deadlock replays, with an integrated
development launcher and the official cvar unlocker. It follows the HLAE
keyframe workflow: capture views, edit their timing and framing, then play the
camera along the resulting path.

**0.3.8 adds experimental native camera playback.** In Session,
**Camera driver → Native (experimental)** is selected by default before launch.
It evaluates and applies position, rotation and aspect-ratio framing during
each main rendered view. It supports only the exact game `client.dll` and
`engine2.dll` build inspected for this release. If Dolly reports a mismatch,
choose **Console (legacy)** before launch. The editor and saved shots work with
either driver. See the [native camera guide and first test](NATIVE_CAMERA.md).

Native playback ignores the earlier temporal smoothing filter. DOF and other
camera cvars still follow sampled native time through console updates; they are
not synchronized to every rendered frame. The [smoothing modes](#experimental-playback-smoothing)
remain available with Console playback.

**The 0.3.6 final-key fix remains in Console playback.** When the replay reaches
the shot end slightly before the continuous camera, Dolly lets that final
fraction finish before pausing and restoring the HUD. Completion can therefore
be slightly later than the first end-tick report. A bounded wait prevents a
stalled camera clock from keeping the shot running indefinitely.

**The working 0.3.5 paused-camera controls are preserved.**
Preparing a paused camera briefly seeks one tick away and returns to the
original tick before restoring the chosen view and verifying its movement.
If the return settles one or two ticks late, Dolly pauses and retries the exact
target once from that later tick. Controls only start after the original tick,
chosen view and direct movement are verified. Camera movement retains the
high-resolution clock introduced in 0.3.4, including at 0.1 replay speed.
Capture after an external seek measures the new view on the first attempt;
start paused controls again before continuing manual movement.

**Paused camera controls.** Switch between saved cameras at the current
replay moment, or move and aim the camera while the replay remains paused.
Capture the resulting views into the same spline-path workflow. Framing uses
the existing `r_aspectratio` curve, starting at **16:9**, with other ratios and
a custom standard available.

**This is an alpha.** Existing Windows feedback confirms Netconsole connectivity,
unlocker initialization before replay loading, a readable replay clock and visible
camera travel, including manual movement while paused, and EXE startup. The
native driver changes how path playback reaches the game. Automated tests and
static inspection do not prove its in-game result: **native smoothness, startup
and final camera handoff still need testing in Deadlock.** This release does
not claim the positioning jitter is fixed.

## Start here

1. Extract the **entire Windows ZIP** into a writable folder. When updating an existing
   Dolly installation, first close Deadlock and use Dolly's **File → Recover
   game configuration** if a recovery is pending, then close Dolly. A source
   installation can use **Recover_Game_Config.bat**. Replace package files in that same
   folder, keeping `logs` and saved shots. Do not discard a folder containing a
   pending gameinfo recovery journal.
2. Windows portable builds include Python and Tcl/Tk. Keep `Dolly.exe` and
   `_internal` together in a writable folder. A source installation instead
   needs 64-bit Python 3.10 or newer with Tcl/Tk. Native playback also requires
   the compiled Windows helper; see [BUILDING.md](BUILDING.md). Source users can
   choose Console before launch if that helper has not been built.
3. Open Steam and sign in. Close any running Deadlock session.
4. Double-click **Dolly.exe**. It opens the editor directly, with your program
   icon and no console window. Source installations can still use
   **Launch_Dolly.bat**. Startup output is in `logs/Dolly_startup.log`.
5. In **Session**, select `game/bin/win64/deadlock.exe` in your Deadlock installation
   and an existing, decompressed **.dem** replay. Steam installations are also
   detected automatically where possible. Older installations using `citadel.exe`
   are supported too. Keep the executable's original filename; other Steam
   library drives such as B: are supported.
6. Leave **Link** set to **Netconsole**. Choose the **Camera driver** now;
   Native is the experimental default, and Console retains the previous driver.
   The choice is fixed for this game session. Click **Launch hideout**. No replay
   is loaded at launch. Wait until the pre-lobby/hideout is fully loaded.
7. Click **Connect**, then **Initialize unlocker**. Dolly executes
   `cvar_unhide` now and requires both completion summaries before allowing replay
   loading. This check waits up to 12 seconds for those messages, including ones
   arriving after the console echo. The main menu alone may not have initialized
   the local server plugin.
8. Once initialization succeeds, click **Load replay**. Wait until the replay
   is visible in the game, then click **Check camera support**.

Do not manually load a replay before unlocker initialization succeeds. A recognized active demo
is rejected during initialization. The UI action explicitly selects hideout
readiness; this alpha does not infer a hideout map name from an unverified status
format. Empty `demo_info` output is recorded as unknown. Empty unlocker output
never unlocks the Load replay button: both command and cvar completion summaries
are required. Check camera support never reruns `cvar_unhide` inside the replay.

The launcher always supplies `-dev -insecure`. It does not accept custom launch
arguments that could remove them. It connects only to the console port owned by
the game process it launched and checks the selected demo before controlling it.
Close this editing session before opening Deadlock normally.

Native launch checks the installed game DLLs against this release's supported
build. If a game update changes either file, native launch stops with an
explanation. Choose Console before launching to continue with the existing
camera commands. Do not replace the game's DLLs or edit the build fingerprint
to force compatibility.

The default **Link** is Netconsole, matching the successful local connection.
VConsole remains available as an alternative for testing. Changing the selector
does not change an already running game's launch options. A successful echo
checks connectivity; it does not establish visibility of every engine logging
channel or successful unlocker initialization.

## Switch and move cameras while paused

After the usual launch, unlocker initialization and camera-support check, enter
replay freecam and open **Paused camera…** on the **Cameras** tab. This is a
separate, nonmodal panel; you can return focus to the game and use your capture
binding while it remains open.

- Use **Previous / Next** to cycle saved camera views, or apply the selected
  camera. These actions pause at the current replay moment and apply the view's
  position, rotation, aspect and camera-variable values. They do **not** seek to
  that view's arrival time. The completed operation leaves the replay at its
  original tick.
- Preparing or switching a paused camera briefly seeks one tick backward and
  returns to the original tick to refresh stale spectator state. At tick zero,
  it uses the next available tick instead. A return that settles one or two ticks
  late gets one correction back to the exact target. Dolly then applies the
  requested view and verifies a small XYZ movement and return. If the game still applies only part of the
  command, Dolly reports the failed check and leaves continuous controls off.
  Release movement keys and allow preparation to finish. Cancelling midway
  stops further commands, so the replay can remain at the neighbouring or
  overshot tick.
- Start camera controls to move from the current freecam position. Release
  movement keys during the initial camera-position check. Use the panel's
  movement buttons, or focus its movement pad for keyboard controls.
- For controls while Deadlock has focus, enable **Enable keyboard flight while Deadlock is focused**.
  **WASD** moves relative to the viewing direction; **Space / Ctrl** moves up /
  down in world height; **arrow keys** look around. **Shift** makes translation
  four times faster. Move and turn speeds can be adjusted in the panel.
- Capture the view with the panel's capture button, the existing camera-toolbar
  actions, or your enabled in-game capture binding. Use **Timed shot** spacing
  when building multiple cameras at one paused replay moment. **Replay timing**
  still requires advancing the replay between different arrival times.
- Stop camera controls, close the panel, or press **Escape** during flight to
  end manual movement. The replay stays paused at the current view. The main
  **Stop / restore** button restores the camera cvars captured before editing.

Keyboard flight is off by default and observes only the game Dolly launched
while that game has foreground focus. Switching away stops movement; release
held controls after returning before pressing them again. Flight stops if the
replay tick changes or its status cannot be read. This release uses keyboard
look and console camera commands. It does not take ownership of the mouse or
detect an open in-game console, so stop flight before typing into that console.
Keys retain their existing game actions. The camera moves in real time while
the scene remains paused, with a bounded step after a slow console response.

Saved-camera switching and manual flight do not change authored path keys until
you capture or replace a view. **Frozen preview** remains available to travel
along the whole authored path through the current frozen scene. **Play shot**
retains the normal behavior: seek to the shot start, then run replay and path
together with HUD handling.

HLAE provides an independent camera input mode through
[`mirv_input`](https://github.com/advancedfx/advancedfx/wiki/Source:mirv_input).
Its Source 2 implementation overrides camera state using a frame-time input
update. Dolly implements manual paused movement through its existing console
connection. It does not add native mouse-look controls. See
`docs/CONSOLE_RESEARCH.md` for the source references and `docs/VALIDATION.md` for
what was tested locally. The 0.3.8 native driver applies to authored path
playback; these manual paused-camera controls retain their existing implementation.

## Make a first shot

Start with two nearby views. No coordinate entry is needed. Confirm each camera
effect before spending time on a longer shot.

1. Enter the replay's roaming/free camera and frame your first view.
2. On **Cameras**, leave **Capture timing** on **Timed shot** and click
   **Start path here**. Dolly pauses the replay and captures the freecam's
   position, pitch/yaw and current aspect-ratio framing. The first view is at zero seconds.
3. Fly to another position, aim the camera, and click **+ Capture camera**.
   Repeat as needed. Views are three seconds apart by default; change
   **Spacing (s)** to set the spacing for future captures. You can
   capture several views at the same paused replay moment using **Paused camera…**.
4. Leave **Frozen preview** off and **Hide HUD** on, then click
   **Play shot**. Dolly seeks to the first captured replay tick, applies the
   first camera view and your lens/DOF settings, hides the HUD, and checks the
   camera position while paused before resuming the replay with the camera path.
   Release movement keys during that check; it can briefly move the camera to
   verify movement in all three axes. Play shot always starts at zero, regardless
   of the selected view or preview cursor. Return focus to the game to watch
   or record it. The HUD comes back when the shot finishes.
5. To revise a view, select its row, reframe in freecam and use
   **Replace selected**. To change its aspect ratio, camera bank (roll), or
   arrival time, edit the selected camera and click **Update camera**. Use
   **Preview** to check it. Position and angle fields remain
   available through **More → Coordinates / timing…**.
6. Save the shot from the File menu. Projects are editable JSON files.

**Capture without switching windows:** open **Capture binding…** on Cameras
(or from File). Choose **Mouse4** or **Mouse5** for a side button, **MiddleMouse**
for the wheel click, or a keyboard key. Ctrl, Alt and Shift are optional required
modifiers. Use Save to apply and remember the choice, then enable **In-game
capture** and return to Deadlock. The checkbox shows your current binding;
Ctrl+Alt+K remains the default.

The binding starts an empty path or adds the next view. It only captures while
the Dolly-launched game's window has focus and Dolly is idle. Capturing from a
playing demo pauses it; use the replay controls to advance afterward. Each
press captures once; holding the button does not repeat. A bare side-button
binding also works while Ctrl or Shift is held for freecam movement. Changing
modifiers while the main key is already held does not trigger another capture.

Your choice is saved automatically in
`%APPDATA%/DeadlockDolly/settings.json`, outside the extracted program folder,
so a future Dolly update keeps it.
The enable checkbox remains off each time Dolly opens. Invalid settings fall
back to Ctrl+Alt+K with an explanation in the log; they are preserved until you
explicitly save a replacement, which retains an `.invalid` backup. No manual
configuration editing is needed.

Dolly observes the selected input; it does not consume the game's button action
or change Deadlock's bindings. Choose a key that does not conflict with another
replay control. Mouse4/Mouse5 follow the buttons reported by Windows. If your
mouse software remaps a side button to a keyboard key, select that key in Dolly.
Primary left/right clicks and modifier-only bindings are intentionally omitted.

For reference, a saved bare Mouse4 binding is:

```json
{
  "version": 1,
  "capture_binding": {"key": "Mouse4", "ctrl": false, "alt": false, "shift": false}
}
```

**Follow replay action:** choose **Replay timing** before starting a new path.
Capture the first view, advance the replay, then move and capture the next view.
Dolly sets the first view to zero and calculates subsequent times from the actual
replay tick. Leave **Frozen preview** off to play the action. Capturing twice
at the same tick is rejected with an instruction to advance or replace a view.
The tick rate is editable under **More → Coordinates / timing…**; its default
is **64 ticks/second**, not automatically detected. Normal playback, this capture
mode, and **Seek replay** require a readable live tick. If the support check
reports that it is unavailable, export diagnostics. You can explicitly select
**Frozen preview** to move through the currently paused scene in the meantime;
Dolly no longer selects it automatically after a capture.

**Timed shot** also plays moving replay action. Its keyframe times are chosen by
you, independently of when you captured each view: views at 0, 3 and 6 seconds
make a six-second shot beginning at the first capture's replay tick. **Replay
timing** instead assigns each view to the replay moment where it was captured.
Changing **Spacing (s)** affects future captures; edit a saved view's
**Arrive · seconds** value to retime it.

The editor's path view is an XY overview, not a 3D map or an in-game path overlay.
The timeline by itself previews the path in the editor. Use Preview frame or Seek replay
to send that time to the game.

**Camera height and position:** captures store the position reported by the
freecam, including height. Before Preview, Seek replay, or Play shot, Dolly
pauses the replay, applies the selected lens settings, and compares the requested
view with fresh `spec_pos` readbacks. It measures the position offset, corrects
outgoing coordinates, and gives a converging response time to settle within a
bounded check. It verifies the result before playback can resume. A brief height
movement also checks that the camera responds, followed by a verified return to
the requested view. The check does not require that this temporary movement
travel the exact commanded distance.
Keep movement keys released until this check finishes. Existing saved shots
remain usable: there is no need to subtract a height offset or recapture keys.
The correction is checked again for each preview, seek, or play action; it is
not added to saved keyframes or to new captures. If a check fails, Dolly retains
the last successful correction as a starting estimate for the next attempt,
which must still be verified. If the position cannot be verified, Dolly stops
before resuming and records the readbacks in diagnostics.

**Rotation:** the default `shortest` mode takes the shorter route across yaw/roll
wrap boundaries. Playback keeps the emitted yaw/roll values continuous across
the numerical −180°/180° seam to avoid a sudden command jump. Choose `unwrapped`
and enter values such as 0 → 360 for a full intentional turn. Pitch, yaw and roll
are interpolated as Euler angles. The game's `spec_pos` output does not include
roll, so capture keeps Dolly's last
applied roll, or zero in a new session; edit roll explicitly when needed.

## Framing curve

Use **FRAMING CURVE** on **Cameras** for the zoom-like effect. Its horizontal axis
is shot time and its vertical axis is the aspect-ratio framing value. Preview
and Console playback use `r_aspectratio`; Native playback applies the framing
directly to each main view. The graph
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

Capture retains an explicit positive `r_aspectratio` value. If the game reports
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

**Session** contains the ordered startup controls. **Cameras** keeps the camera
list, framing graph, selected-camera controls and top-down path overview together.
**Camera variables** contains depth-of-field and other numeric tracks. Playback
controls stay below the active tab. Coordinate entry and advanced timing are in
**More → Coordinates / timing…**, keeping them out of the normal capture workflow.

At 100% Windows scaling (96 DPI), the window opens at 1180×800 with a minimum
of 1000×700. Window and dialog sizes scale with DPI to keep the controls readable;
the minimum is about 1500×1050 at 150% scaling. Panels resize with the window.
The main pages do not scroll; long tables scroll vertically within their own panel.
**Log** opens a separate resizable activity window, closed by default.
The path overview is an XY diagram, not an in-game overlay.

**Frozen preview:** this optional effect is off by default. It moves the camera
using shot seconds through the scene currently on screen. Preparation checks
the current tick and can use the adjacent-tick refresh described above; it never
resumes or jumps to the shot's start.
Choose that scene with the replay controls first, or use
**Seek replay** if live ticks are available. **Capture timing** controls keyframe
timestamps; **Frozen preview** independently controls whether the scene stays
paused during path playback. Capture timing, frozen preview, HUD hiding and the
capture enable switch are session options; the binding is a saved user preference.
Camera keyframes and their timestamps are saved in the shot.

## Depth of field and other camera variables

On **Camera variables**, use **+ Depth-of-field preset** as a starting point. The preset
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

Use **Step** interpolation for toggles and mode values. Use Linear or Smooth for
continuous focus/aperture changes. **Fixed values…** opens a separate dialog on
**Camera variables**. These values are applied for the shot; a track with the
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

With Native playback, DOF and other cvars use the same sampled shot time as the
camera, but their updates still travel through the console. They are best
effort and are not synchronized to every rendered frame. **Updates / s** controls
these effect updates; it does not cap the native camera's view callbacks.

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
  this driver. Manual paused flight and saved-view capture keep their existing
  controls and project format.
- **Console playback** timing follows acknowledged replay ticks. New integer ticks
  adjust the clock gradually rather than snapping the camera's fractional
  position. Position, rotation, aspect and cvar curves share that clock. Its
  underlying estimate can briefly lag a tick, leads by at most one tick, and holds at that
  cap when observations stop. Optional smoothing adds the delay described below.
  Completion waits for both the replay and camera
  to arrive, then explicitly applies the final key. A small final clock lag gets
  a bounded finishing period instead of an immediate jump to the endpoint.
- Startup checks require approximately one-to-one XYZ movement and a verified
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
- **Native camera handoff:** on completion, Pause or Stop, Dolly holds the
  native view while positioning the underlying free camera. It checks fresh
  unmodified view samples before releasing the override. If that camera cannot
  settle within the bounded check, the view remains held and other camera
  writers stay blocked. Keep the replay paused and use **Stop / restore** to
  retry, then export diagnostics if it persists.
- **Stop / restore** restores the captured cvar baseline (including the exact original `r_aspectratio`) and returns replay
  speed to **1×** if Dolly changed it. It leaves the camera position/orientation
  and the paused replay where they are. Previous replay speed is not queried.
- Playback speed is 0.05×–4×. Updates / s choices are 30/60/120. They control
  camera commands with Console, and effect-cvar updates with Native. They do
  not set the game's render or recording frame rate.
- On supported Windows versions, frame pacing uses a dedicated high-resolution
  waitable timer. An unavailable timer falls back to normal event waits and is
  reported in diagnostics. No system-wide timer setting is changed.

Native playback is the experimental render-time implementation in this release.
It is restricted to the inspected game build and has not yet been tested inside
Deadlock. Console remains available for comparison and other game builds.
Neither driver records video; use your existing capture software. Follow the
[first native camera test](NATIVE_CAMERA.md#first-in-game-test) before deciding
whether this build is ready to publish.

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

The original file is restored after **Initialize unlocker in hideout** confirms unlocker
activation, when the game exits while Dolly is open, or when Dolly closes.
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

If the camera-position check fails, make sure the replay is in roaming/freecam,
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
