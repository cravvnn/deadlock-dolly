# Deadlock Dolly — user guide

[Documentation](README.md)

Dolly connects its desktop editor and in-game panel to the same camera project.
Capture views, edit their position, rotation and framing, then play or export
the shot. Paired depth/EXR takes include per-frame camera metadata; normal
reconstruction remains internal groundwork, not an export option.
The [in-game editor guide](UI_REWORK.md) covers curve editing, player selection
and saved layouts, building on the UI rework contributed by
[@tbhtho](https://github.com/tbhtho).

[@paralin](https://github.com/paralin) contributed
Linux/Proton discovery and compatibility fixes. See the full
[credits](../CREDITS.md) for contributors, libraries and project references.

Native camera playback, Game Follow, Bone Picker, real-time game audio,
fixed-step silent exports and optional ReShade are available. Players/Depth
exports retain their documented alpha limitations. See the current
[compatibility notes](../README.md#compatibility); a Deadlock update can require
a matching Dolly update.

[Video and ReShade setup](VIDEO_AND_RESHADE.md) covers output settings, F11,
installation and current limits. [Export the camera](VIDEO_AND_RESHADE.md#export-the-camera)
covers After Effects and USD camera exports. See the [guide index](README.md) for related
workflows and the [changelog](CHANGELOG.md) for release history.

## Start here

1. Close Deadlock. If an older Dolly has pending recovery, use its **File →
   Recover game configuration** before replacing it. Keep old logs and shots;
   never discard a pending gameinfo backup.
2. Extract the **whole Windows ZIP** into a fresh writable folder. Keep
   `Dolly.exe` and `_internal` together. The portable package includes Python.
   Source users need Python 3.10+ with Tcl/Tk and a built native helper; see
   [BUILDING.md](BUILDING.md). The current native helper uses ABI 4; keep it with
   the matching editor files from the same build.
3. Open Steam and sign in. Dolly adds `-dev -insecure` directly to the process it
   launches; Steam's saved launch options stay untouched. Native sessions use
   **DirectX 11**. Dolly keeps normal-play configs aside by using stock defaults
   and a separate session settings folder; its own saved Controls remain in use.
4. Open **Dolly.exe**. In **Replay → Launch setup**, choose `game/bin/win64/deadlock.exe` and a
   local, decompressed `.dem`. Older `citadel.exe` installations and Steam
   libraries on another drive are accepted; do not rename game files.
5. Alternatively, open **Replay**, refresh the replay list and select
   a file. It lists local `.dem` files; it does not download or decompress demos.
   Review **Controls** before launch if you want a mouse-button capture key.
6. Click **Open replay in Dolly**. Dolly connects to the game process it launched,
   waits for rendered pre-replay scene and unlocker-registration evidence,
   executes `cvar_unhide` once, and requires both completion summaries.
7. Dolly skips the early-development notice inside its own session and
   automatically advances the verified hideout **Press any key to Start** intro.
   Hero announcements and other prompts remain available; dismiss those in
   Deadlock. Dolly waits for
   verified map and shader preload completion, then automatically loads the
   selected demo, checks its identity
   and camera support, pauses it, closes the console explicitly and enables
   native flight. Startup progress appears in Replay. Do not load a demo manually
   while waiting for this sequence.
   If your HUD is hidden, Dolly temporarily shows it so the intro can advance,
   then restores your HUD setting before loading the replay.
   A Deadlock update rebuilds the game's shader cache, so the first hideout load
   afterwards can spend minutes preloading. Dolly keeps verifying for as long as
   Deadlock reports real progress, and only stops on a preload that has gone
   quiet. If it does stop, it offers to load the replay anyway, and the manual
   **Connect → Initialize unlocker → Load replay** order skips that check
   entirely.
8. Frame a view with WASD and mouse look, then capture it with **Ctrl+Alt+K**.
   With the default **Replay timing**, advance the replay before framing and
   capturing the next view. **F8** opens the in-game Dolly panel.

**Cancel startup** stops the automatic sequence; it does not kill Deadlock.
The launched process remains open. Close it before launching another session.

**WinError 740 / administrator permission:** Windows is requiring elevation for
`deadlock.exe`. Open that executable's **Properties → Compatibility**, including
**Change settings for all users**, and disable **Run this program as an
administrator** if it was enabled unnecessarily. If your computer requires that
permission, close Dolly and use **Run as administrator** on `Dolly.exe`, then retry.
Re-extracting the ZIP does not resolve this Windows permission requirement.
A timeout, failed unlocker confirmation, or unverified preload does not
automatically load the demo. If the hideout intro never starts, Dolly asks you
to check the game for a hero announcement or another prompt. Dismiss that screen and
choose **Check startup again** to repeat the normal checks in the same session.
The game stays open and the selected replay remains unloaded until they pass.
Preload verification supports reviewed game builds; after a game update, Dolly
may need an update before automatic startup can proceed.

Dolly starts with its reviewed editing `gameinfo.gi`, so competitive presets do
not carry their framing and rendering overrides into the editing session. Your
original file is backed up byte-for-byte and restored on disk after unlocker
initialization, while the game keeps its loaded settings. Exit and failed-launch
recovery use the same backup. Newer external changes are left untouched for manual
recovery. The editing configuration is checked against the installed game build;
if it no longer matches after a game update, update Dolly before launching.
This does not reset separate autoexec files or saved video settings.

**Settings → Troubleshooting & recovery → Startup controls…** retains manual Launch hideout, Connect, Initialize
unlocker, Load replay and Check camera support. Use the manual initialization
only when the hideout has finished loading. Manual **Load replay** bypasses the
automatic preload check; wait for the game's preload to finish yourself.
**Check camera support** does not rerun the unlocker
inside a demo. Netconsole remains the default; VConsole is a troubleshooting
alternative selected before a new launch.

The launcher keeps `-dev -insecure -console` and refuses to launch or connect
unless it started the game process itself. **Launch options…** accepts
restricted additional display options such as `-windowed -w 1280 -h 720`.
Replay/map commands, `-secure`, Vulkan and managed game/console arguments cannot
override the editing configuration. Close this editing session before opening
Deadlock normally.

The native helper runs inside the game's user-mode process; it is **not a
kernel driver**. Native launch checks reviewed `client.dll`, `engine2.dll` and
`tier0.dll` fingerprints. Unknown updates are blocked. See
[the maintainer compatibility reference](internal/GAME_UPDATES.md); do not replace game DLLs or edit fingerprints
to force compatibility. Choosing **Console (legacy)** and **Play replay** opens
the manual Startup controls: wait for the hideout, then use **Connect → Initialize
unlocker → Load replay → Check camera support**. This uses console camera controls
without native flight, the in-game panel, native recording or layers. Its separate
configuration and server-unlocker checks must still pass; a client-only update
does not itself disable the manual route, but every patch is not guaranteed.

### Linux (Proton)

Run the Windows package inside Deadlock's own Proton prefix with the same
Proton version Deadlock uses. Dolly reads the game's process, memory and console
through Windows APIs, so it and the game must share one Wine server; a separate
prefix, such as a non-Steam shortcut's, cannot see the game.

`tools/run_dolly_proton.sh` finds the Steam library, the Proton version Deadlock
uses and the matching Steam Linux Runtime, then starts Dolly in that prefix:

```sh
bash tools/run_dolly_proton.sh --dry-run   # show detected paths, start nothing
bash tools/run_dolly_proton.sh             # start Dolly, then launch from Dolly
```

To start it by hand, match the runtime to the Proton version. Proton 8-10 use
`SteamLinuxRuntime_sniper`; Proton 11 and newer use `SteamLinuxRuntime_4`
(`require_tool_appid` in the Proton folder's `toolmanifest.vdf`: `1628350` is
sniper, `4183110` is Steam Linux Runtime 4). With `lib` set to the Steam library
that holds Deadlock:

```sh
lib=~/.local/share/Steam/steamapps
export STEAM_COMPAT_DATA_PATH="$lib/compatdata/1422450"
export STEAM_COMPAT_CLIENT_INSTALL_PATH=~/.local/share/Steam
export SteamAppId=1422450 SteamGameId=1422450
"$lib/common/SteamLinuxRuntime_4/_v2-entry-point" --verb=waitforexitandrun -- \
  "$lib/common/Proton - Experimental/proton" waitforexitandrun "$PWD/Dolly.exe"
```

Replace `Proton - Experimental` and the runtime with the versions selected for
Deadlock. Recording uses the bundled FFmpeg, whose hardware encoders need driver
DLLs a Proton prefix does not have; choose **Lossless FFV1 (.mkv)** until
software H.264 is available. Dolly finds the game in any Steam library, launches
it and restores `gameinfo.gi` as on Windows. See
[LINUX_PROTON.md](LINUX_PROTON.md) for the current test checklist.

## In-game input and default keys

| Shortcut | Action |
| --- | --- |
| Ctrl+Alt+K | Capture camera; starts an empty shot or appends a view |
| Ctrl+Alt+R | Replace selected camera |
| P | Pause/resume replay time |
| F5 | Play the authored camera shot from its start |
| Backspace | Stop and restore |
| PageUp / PageDown | Previous / next camera at the current paused moment |
| Comma / Period | Seek backward / forward one second |
| F7 | Open/close console, reserved |
| F8 | Dolly in-game panel |
| Ctrl+Z | Undo a shot edit while the Dolly panel is open |
| Ctrl+Y / Ctrl+Shift+Z | Redo a shot edit while the Dolly panel is open |
| F9 | Original game replay UI / hero selection |
| F10 | Paused flight |
| WASD | Move relative to the camera |
| Space / Ctrl | Rise / descend in world height |
| Mouse or arrow keys | Look |
| Shift / Alt | Faster / slower movement |
| Q / E | Roll the camera |
| R | Level the live paused view and restore normal framing |
| Alt+G | Toggle the framing guide |
| F11 | ReShade menu, when configured |

These are the defaults for new preferences. Existing saved shortcuts can differ;
check **Controls** for the bindings in your installation. F7 is reserved.

During **flight**, Dolly owns movement and mouse look. **F8** releases mouse
look for clicking its panel. The panel provides capture/replace, saved-camera
selection, replay/path playback and movement-speed controls. Change movement speed and sensitivity from desktop
**Controls**; the in-game speed control updates the same saved setting.

**F7** gives the console input ownership before the open command is dispatched,
so typing cannot create keys or move the camera. F7 or Escape closes it and
returns to the previous UI; F8 closes it and opens the Dolly panel. **F9** returns the camera and mouse to Deadlock's
original replay UI for selecting a hero. Return through F8 for the panel or F9
for flight; Dolly seeds from the currently displayed view rather than a stale
camera from before hero selection.

Changing focus clears native held inputs and accumulated mouse movement.
Release controls after returning from another window before pressing again.
If a UI transition fails, inspect the status/diagnostics and use the game UI
recovery path instead of repeatedly issuing camera actions.

## Customize bindings

Open **Controls** in the desktop sidebar. Search by action or shortcut, click its shortcut button,
and press a key or mouse button with any modifiers. Review the result and choose **Save shortcut**.
Conflicts name the other action and leave both shortcuts unchanged. **Clear** unbinds one action;
**Reset** restores its default. **Reset all shortcuts** restores the defaults after confirmation.
Escape cancels recording; **Use Escape as the shortcut** lets you assign Escape itself
(hold any modifiers when clicking it). Movement actions also accept a modifier key by itself.
Mouse 4 and Mouse 5 are captured while this window has focus. F7 stays reserved for the console.
Mouse software that translates a side button to a keyboard key should use that resulting key in Dolly.

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

The **In-game capture** switch is on **Replay** beside **Open replay in Dolly**;
the same switch is also on the Camera toolbar. Set its shortcut in **Controls**.
The desktop's Camera and Look pages are always available.

## Switch and move cameras while paused

After **Open replay in Dolly**, native flight starts from the rendered camera pose.
It updates position and mouse/keyboard rotation in the main-view callback
using elapsed real time. It does not move the game spectator with repeated
`spec_goto`, and entering flight does not perform the legacy adjacent-tick
height calibration. Moving the camera does not advance the paused demo.

Capture stores the view sampled with the native capture event. The camera can
continue flying after a capture without entering coordinates. Choose a saved
camera in the panel, or use PageUp/PageDown, to apply its position, rotation,
aspect and supported DOF at the **current replay moment**. This does not seek
to that key's arrival time. F10 continues flight from the displayed view.

While flying a paused camera, scroll up to zoom in and down to zoom out.
This changes `r_aspectratio`, the **Framing Curve** on the desktop Camera tab,
between 0.5 and 4. It immediately updates the selected camera's framing without
changing its position or arrival time. Before the first camera is captured,
scrolling changes the live framing that the next capture will save. The wheel
scrolls menus normally while a panel or ReShade has input focus.

Press **R** to level the paused camera you are composing with and return it to
the shot's normal aspect and zoom, for example after banking the view with Q or
E. Level view is a live reset only: no saved camera, project value or Undo step
changes, and previewing a camera again shows that camera's own framing.

The in-game **Look** tab contains the depth-of-field controls.

**Toggle floating health bars** hides unit bars using the game's panel scales.
Press it again to restore their exact previous sizes. The choice stays active
across shot playback; disconnecting restores the saved values. The tilted hero
health/ability panel is controlled by **Show game HUD during replay**, which retains its camera-mode
safeguards. Health-bar glow remains a separate **Toggle Citadel glow** setting.
After capturing a camera, **Enable DOF** turns on both native switches and
initializes **Focus ranges** like the desktop **+ Range DOF** button. Existing
range edits are preserved when switching off and back on. The four controls
are grouped into Near and Far columns; **Ground tilt** remains below them.
Drag and release to apply, hold Alt for fine adjustment or Shift to move faster,
or Ctrl+click to type an exact value. Numeric bounds are unchanged.
Range edits add/replace a key at the desktop playhead, and the range track and
its key list stay selected in desktop Look. Preview holds the current camera
and replay tick. Stop / restore retains the existing restoration behavior.

In-game capture also works while replay time advances. It keeps the replay
running by default; enable **Pause after capture** beside **Resume/Pause** on
the in-game timeline to pause after capturing or replacing a view. The setting
is remembered between sessions. The in-game keybind saves the pose and tick
sampled with its input event; desktop Capture waits for a paused rendered view
and saves that frame's tick. Small delivery delays do not change the keybind
capture's authored timestamp.

P controls replay time. If replay time advances while manual flight is active,
the current manual camera is held; pause again to continue paused editing.
P during a normal authored native shot pauses/resumes replay time without
restarting the path. Use F5 to restart the authored shot. Stop a frozen preview
before trying to resume replay time. Comma/Period uses the project's ticks per
second; check that value in **More → Coordinates / timing…** before timing work.

The in-game **Stop / restore** action keeps your selected replay speed while
editing, so you can continue positioning cameras in slow motion. The desktop
Stop action restores a changed replay speed to normal; leaving the editing
session also restores Dolly's override.

Some replays initially expose tick 0 but can only seek back to tick 1. When
the game confirms that boundary, Play shot starts at tick 1 with the matching
camera/effect phase (1/64 second for a shot starting at zero at 64 ticks/sec).
The saved cameras and arrival times stay intact.

**Console legacy movement:** the older paused-camera dialog remains available
when launching the Console driver. It uses a bounded camera calibration and
may briefly seek to an adjacent tick and return, then verifies XYZ movement.
Release movement keys during preparation. It uses external keyboard input and
arrow-key look, not native mouse look. Stop that legacy flight before typing
in the game console. The native input behavior above does not apply to it.

### Select, delete and restore saved cameras

The in-game **Campaths** panel lists each saved camera with its arrival time,
aspect and bank. Click a row to select it for editing without moving the live
camera. Double-click the row, or use **Previous / Forward**, to visit a view at
the current paused replay moment. This does not seek to the camera's recorded
replay time. For longer lists, the arrows beneath the table browse its pages.

To visit a saved camera at its recorded replay time, select its row and press
**Seek camera** on the bottom **Replay** timeline. Dolly pauses at that part of
the replay and applies the saved view. The replay slider and tick field continue
to seek while retaining the current camera. If the completed replay omits the
requested tick, these controls and relative replay seeks choose a recorded tick
in the requested direction and report the actual position.

**Delete** removes the selected camera immediately. **Undo** restores
it, including its player/bone attachment and framing; **Redo** repeats the
deletion. The desktop **Edit** menu and in-game buttons use the same shot edit
history. Ctrl+Z undoes; Ctrl+Y or Ctrl+Shift+Z redoes. Typing in a text field
keeps that field's normal editing shortcuts.

History covers authored shot data, including camera edits, timing and effect
tracks. It does not rewind the game, replay controls, Follow settings or exports.
Pause playback, finish recording and detach an active bone preview before
deleting cameras or using shot Undo/Redo. Saving keeps history; opening or
starting another project clears it. History is limited to recent edits and is
not saved across app restarts. A new edit after Undo replaces the redo branch.

### Edit a view between saved cameras

Choose **Campaths** on the bottom in-game timeline. Drag the **Shot** timeline
(or Ctrl+click its slider to type seconds), then press **Seek here**. Dolly
pauses the replay at that part of the shot and applies its position, rotation
and framing. Moving the slider alone does not seek. Hold **Alt** and drag a
camera mark to change that view's arrival time; a plain drag always scrubs.
Pause shot playback first.
A replay can reconstruct only recorded packet times; Dolly reports the actual
shot time if it has to use the following packet.

Use **Fly camera** to adjust the resulting view. With **Replay timing** selected
on the desktop, **Capture camera here** (or your capture binding) inserts the
new camera at that replay moment. An existing camera at the same time must be
replaced instead. With **Timed shot**, capture uses the configured spacing.
Desktop **Playback options → Seek replay** uses the same seek behavior.

Replay playback and camera editing use the bottom timeline in-game. Timeline
marks show saved camera times and match the path-guide camera data. Choose a
step size of **1, 2, 5, 10 or 25 ticks**, then press **Back** or **Forward** to
inspect an animation while keeping the current camera fixed. The panel stays
open and shows the actual replay tick. Some recordings omit intermediate ticks;
Dolly chooses the nearest recorded tick in the requested direction and reports
the actual movement. **Seek here** in Campaths follows the shot camera; tick
steps hold your current view. When following a participant selected on the Replay timeline,
replay seeks and tick steps resolve that player's current hero and keep following.
Use **Fly camera** to return to a fixed view. Detach an active player-camera
preview before fixed-camera tick stepping. Desktop replay library and launch
controls remain under Replay.

To replace the current camera path without leaving the game, choose **Reset
camera path** in the Camera card, then **Reset here**. Dolly captures the current
view as the first camera and updates the shot's replay start tick. Lens and
depth-of-field tracks are kept. **Cancel** keeps the existing path. Stop shot
playback before resetting.

## Make a first shot

1. **Replay timing** is selected by default on Camera. Camera arrival times
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
   same authored time. Edit aspect, bank and arrival time on Camera; use
   **Preview** to apply a changed saved view. Curve drags can update paused
   path guides without moving your composing camera.
6. Save from the desktop File menu or in-game **Save Path**. Projects remain
   editable JSON files.

**Replay timing** assigns keys from their actual replay ticks: capture a view,
advance the demo, and capture the next. Capturing twice at the same tick cannot
create distinct arrival times; advance, replace the key, or use Timed shot.
Replays are recorded at different rates (32 and 64 ticks/second are both
current), so Dolly reads the open replay's own duration metadata and uses that
rate: a new shot adopts it automatically, and a saved shot that disagrees is
offered a one-step retime when it is checked or opened. **Retime to replay**
under More → Coordinates / timing… re-offers that repair at any time;
a capture that would mix two clocks is refused with the same advice, and
playback of a mismatched shot warns in the status line. **Ticks / second**
stays manually editable. Normal playback and relative seek require a readable
current replay tick.

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
turn. Choose the rotation mode in **More → Coordinates / timing…**.

The F8 panel's **Show path guides** displays numbered camera positions,
facing directions and the spline in the game during paused flight. The selected
view is gold. Guides show through walls and hide during playback, game UI,
console use and loss of focus. Camera glyphs indicate orientation and aspect;
they are not live previews or exact lens frustums. Large paths draw a reduced
set of markers and always include the selected view. The desktop XY overview
remains available.

Position, rotation and framing curves are editable on both desktop and the
in-game timeline. Recording supports game-only audio and the reconstructed-audio
workflow in both real-time and fixed-step exports. The first reconstructed
export installs its verified helper automatically. Fixed-step audio is recorded
by the engine's frame-locked WAV and keeps the selected slow-motion speed. See
[Video and ReShade](VIDEO_AND_RESHADE.md) and [layer export](LAYER_EXPORT.md)
for the output controls and restrictions.

## Framing and rotation curves

Use **CAMERA CURVES** on the desktop **Camera** page, or **Curves** on the
in-game timeline. **Position** contains X/Y/Z, **Rotation** contains
Pitch/Yaw/Roll, and **Framing** contains Aspect Ratio. Select a group and axis,
or enable **Stack graphs** to keep several groups on the same time ruler.

Select a camera point, then drag it vertically or enter its value. Arrival
times stay unchanged. In-game, **Alt** aligns a dragged value with a nearby
value on the preceding or following camera. **Right-click** cancels an
unfinished adjustment. On desktop, Enter applies a typed value and Escape cancels it.
Each completed gesture uses the normal shot Undo history. Pause playback and
finish recording before editing; attached cameras use their attachment offsets
instead of these free-camera curves.

**Position** edits the saved XYZ coordinates. In **Smooth** path interpolation,
the selected axis also has **Arrival** and **Departure** slope handles, with
**Auto**, **Flat**, **Linked** and **Broken** modes. Handles change movement,
not key times; Linear and Spline preserve them but do not apply them. See
[saved handles](UI_REWORK.md#saved-arrival-and-departure-handles) for the controls
and save-format limits.

**Rotation** writes an override for the selected channel without replacing the
captured camera angles. The dotted reference shows those captured angles;
**Reset** removes overrides for that channel. With the desktop graph focused,
Up/Down nudges a rotation by 1 degree and Shift+Up/Down by 10.

**Framing** edits aspect ratio from **0.5–4.0**, with a reference line for the
shot's **Normal** ratio. These are Dolly's editing limits, not verified native
cvar bounds or FOV degrees. Console uses `r_aspectratio`; Native applies framing
to each main view. Up/Down changes desktop framing by 0.01, or 0.1 with Shift.
The inspector also accepts an exact **Aspect ratio** through **Update camera**.

**Path curve** chooses Smooth, Spline or Linear camera movement. The separate
**Framing** selector chooses Smooth, Linear or Step framing interpolation.
Spatial smoothness does not guarantee constant speed: camera spacing and
arrival times still matter.

The mouse wheel zooms time and right-drag pans. **Fit all**, or a double-click
on empty graph space, restores the whole-shot view. **Show original** pins a
temporary read-only snapshot behind the current curves; **Update original**
replaces that snapshot rather than restoring saved values. It is separate from
the captured-angle reference and is not saved in the project.

With Native path guides enabled in a paused replay, dragging a free-camera key
or Position handle previews the path and camera markers in the game. Release
to keep the edit or right-click to restore the saved path. Use **Preview** to
apply a saved view, **Seek camera / Seek here** for replay positioning, and
**Play shot / Play cinematic** to check the moving camera.

**Normal** starts at 16:9 (about 1.77778).
Choose 16:10, 21:9 or 4:3, or type a custom ratio/decimal and press Enter for a different standard.
Use **Reset** to set the selected camera's framing to Normal. Changing Normal
changes the reference value; it does not replace every existing framing key.

Native capture stores the rendered positive aspect ratio and lens together,
so a camera captured from a customized spectator view keeps its framing when
the replay restarts for playback. Captured lenses require project format 9 or
newer and a matching native backend; older Dolly versions cannot open them.
Existing shots without captured lenses retain their earlier behavior. If old
and new cameras are mixed, old cameras use the standard lens baseline; recapture
an old camera if it originally used a custom lens.

Console capture
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
Saving uses the format required by the shot's features; an older Dolly release
may not be able to open it. Custom tracks or
fixed values that target an old FOV control or `r_aspectratio` must be removed;
Dolly reports the conflict so that only the framing curve controls this setting.

## Program icon

Dolly uses its supplied film-reel artwork for the window and Windows taskbar
icon. Artwork terms are separate from the source-code license; see
[assets](../assets/README.md). Missing icon files are logged without preventing
startup. The portable launcher opens the editor without a Python console window.

## Editor layout

**Replay** combines replay selection, one-click startup and shot file actions.
**Export** contains destination, output passes, capture quality and recording.
**Controls** contains shortcuts, camera sensitivity and movement speed.
**Settings** contains saved paths, ReShade and troubleshooting.

**Camera** keeps the saved-camera list, Position/Rotation/Framing curves,
selected-camera controls and XY path overview.
Look contains DOF and numeric camera-variable tracks. Coordinate entry and
advanced timing remain in **More → Coordinates / timing…**. Playback options
hold preview, seek, update rate and renderer relief; Console smoothing appears
when using that backend. The desktop shot timeline and **Stop / restore** are
shown on Camera and Look.

The in-game main panel has **Camera**, **Follow**, **Bone Picker**, **Look** and
**Export** tabs. Saved views and **Save Path** are in the separate **Campaths**
panel. The bottom timeline switches between **Replay**, **Campaths** and
**Curves**, including shared curve editing. Open existing projects from the
desktop File menu. Both surfaces share the same shot and Undo history;
**Stop / restore** stays in the main panel footer. See the
[in-game editor guide](UI_REWORK.md) for layout controls.

### Attach a camera to a player

In the desktop Camera inspector, choose **Attach to player**, select the hero
and choose **Eyes**, **Weapon** or **Bone Picker...**. The picker lists names
from that player's live model. An existing Bone attachment also exposes its
name on desktop. Use **Apply to camera** to save the source and offsets, or
**Apply to all** for every view in the shot. The in-scene picker requires a
connected Native replay with validated attachment data.

In-game, choose the player and point in the **BONE PICKER** tab. Enter
**Fly camera**, frame your view, then use **Snap** to calculate offsets that
reproduce it. **Attach here** previews the source; moving while attached edits
its offsets. **Detach** ends live preview. **Hide this hero** suppresses the
identified body draws during preview and playback. Export follows the saved
sources and offsets, including edits made in preview.

From a paused **FOLLOW** view, opening **Bone Picker** or choosing **Attach here**
transfers camera control automatically. You do not need **Restore rig** first.
**Snap** still requires **Fly camera**, because it calculates offsets from your
current free-camera view.

Attachment uses replay character models; it does not create first-person arms.
For a head camera, open **Bone Picker...**, choose **head**, then **Use this bone**.
For a hand camera, choose **hand_R** or **hand_L**. Bone names belong to the
selected hero; use the picker rather than copying a name from another hero.
The in-game list is bounded; desktop also accepts a typed name.

Bone position follows the animated joint. Camera orientation follows the
player's aim, with your rotation offsets applied; it does not use the bone's
own twisting axes. Move the camera back with offsets if the hero's body
obstructs the view. **Hide this hero** does not suppress every rendering pass.

Attachment remains experimental. Motion and visibility vary with the hero,
animation, cosmetic, bone and game update; a working target does not establish
support for every model. Unsupported or incomplete skeletons are refused;
use **Eyes** as the fallback. See [Bone Picker](BONE_PICKER.md) for selection,
preview, clearance and recording limits.

If a target disappears or its model changes, Dolly stops the attachment.
Use **Detach** to restore free-camera control, select a present hero, then
retry **Attach here**. Saved camera keys are preserved when detaching.

Set **Blend in** under **Rotation & transition** to ease between
camera sources. Start with **0.5 seconds**: the blend ends at the selected
view's time and cannot begin before the previous view. **0** keeps a cut.
The setting works when arriving at either an attached or free camera; existing
shots keep their cuts. Position and shortest-turn angles ease in and out, while
the authored lens curve continues independently. Try a short preview to check
clearance from walls and character models. Saved source blends need a compatible
Dolly version; older releases may not open them.

The color video is always recorded first. **Depth master** is paired with that
color take; ticking World, Players or Effects adds extra takes. When the color
take finishes (at the end of the shot, or when you press **Finish recording**),
Dolly records each ticked layer in turn, playing
the shot again for each one. The passes land in subfolders of the take, and you
do not need to press anything between takes. Press **Finish recording** only to
end the color take early; the queued passes still follow. **Discard recording**
drops the current take instead. Layer takes require **Fixed-step export** and
stay silent; the Color take can carry game or reconstructed audio. See
[layer export](LAYER_EXPORT.md) and [video settings](VIDEO_AND_RESHADE.md).

A paired depth take also writes **camera.json** beside its frames: the exact
per-frame camera the take was rendered with, ready for a compositor or a 3D
scene. The stored conventions are documented in
[camera bundles](CAMERA_BUNDLE.md), and the take's audit report checks the camera
file against the take (run `python -m dolly.export_audit <take folder>` for the
same check on its own). The float **EXR sequence** option is only selectable once
**Depth master** is ticked, in both the desktop and the in-game panel.

**Frozen preview** is off by default. It moves along shot seconds through the
currently paused scene rather than resuming or seeking to the shot's start.
Choose the scene first. Capture timing determines authored key timestamps;
frozen preview determines whether replay time advances during playback.

### Game Follow and Player POV

Game Follow uses Deadlock's spectator camera rather than a saved bone
attachment. Pause the replay, open **Follow**, choose a player and use
**Preview Follow** or **Apply Follow**. Distance, shoulder and height adjust
that rig; right-click a slider to restore its default. **Restore rig** resets
the camera settings while keeping the selected hero. **Fly camera** returns to
a fixed native view. Follow is disabled while a bone preview owns the camera
or when the installed build lacks its reviewed support.

For a recording of that spectator view, choose **Player POV** under Export's
**Source**. Select a hero through Game Follow or **F9**, pause at the segment's
start, then use **F8** to return to Export. Set **Replay seconds** on desktop
or **Replay duration** in-game and press **Record POV**. Dolly hides the replay
HUD and finishes at the end of that segment; the saved camera path is unchanged.
Use **Camera path / Bone camera** in-game (**Camera path** on desktop) when you
want the authored path or bone attachment instead. See
[Video and ReShade](VIDEO_AND_RESHADE.md) for encoder, audio and layer settings.

## Depth of field and other camera variables

The in-game **Look** tab separates the two depth-of-field systems. **Native
Depth of Field** is Dolly's own engine override (the focus ranges and ground
tilt), and **Citadel Depth of Field** is the game's built-in effect. The
Citadel card has an **Enable DOF** switch plus **Sensor size** and **Focus
distance** sliders; both sliders author the shot at the playhead and apply it
live, so they save to the shot's effect tracks and play back through the native
effect binder.
The desktop **Camera** page carries the same Citadel card, with the sliders
applying through the paused-camera preview. Sensor size uses 0.5–3 inches and
focus distance uses 0–10000 inches on a logarithmic scale.

Right-click an in-game DOF value to restore Dolly's default for that value.
Enabling either DOF mode switches off the other. Citadel uses an aperture of
0.5 inches when no aperture value or curve is authored; the game's default of
zero produces no lens blur. An explicitly authored aperture remains unchanged.
While Citadel is active, Dolly suppresses the Native range override at runtime
and preserves its saved values and curves for switching back.

If Native DOF shows the magenta/black checkerboard, the engine is showing its
error material. Use **File > Check Native DOF support**: it checks the directory
entries for the game's precompiled DOF shader and materials. If they are missing
or unreadable, verify the game files in Steam. Present entries do not prove that
the payloads, shader permutations or rendered result are correct. If the check
reports `ready` and the checkerboard persists, use **File > Export diagnostics**
and include the ZIP with your report. The console history can help identify the
failed shader request. The shipped shader can run without a compiler; this
check does not require downloading a DLL.

On **Look**, use **+ Citadel DOF** as a starting point. The preset
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
**Look**. These values are applied for the shot; a track with the
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
only. See [native DOF support](internal/NATIVE_EFFECTS.md) for the exact controls and
restoration behavior. Other camera cvars require Console mode.

## Playback behavior

- Smooth, Spline or Linear camera paths (choose **Path curve** in the Camera
  header or the Coordinates / timing dialog); Linear, Smooth or Step cvar
  tracks. A smooth spatial spline does not automatically give constant travel
  speed. The distance between views and their arrival times determine how fast
  each section moves.
- **Play shot** starts from zero, seeks the shot's starting replay tick, applies
  the initial camera and fixed/animated cvars, verifies the camera position,
  then resumes. The replay and path
  use the same speed. At **0.1×**, a four-second shot takes about **40 real
  seconds**; at **1×**, it takes about four.
- **Native playback** publishes the complete shot before the replay resumes.
  The helper evaluates position, rotation and aspect for each main rendered
  view using the game's time. The Console smoothing filter is disabled for
  this driver. Native manual flight and saved-view capture share the native
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
- **Stop / restore** restores the captured cvar baseline, including the exact
  original `r_aspectratio`, and leaves replay time paused. The desktop action
  returns a changed replay speed to **1×**; the in-game action keeps the current
  editing speed. Leaving the session restores that override. Native ownership
  returns through the normal game-camera handoff, so the underlying spectator
  view can differ. Previous replay speed is not queried.
- Playback speed is 0.05×–4× and applies immediately through the replay's demo
  timescale, whether the replay is playing or paused, as well as to the next
  Play shot. F8 shares **Playback speed** with the desktop. Slow motion is the
  recommended way to review heavy-effect shots. **Updates / s** still requires
  a stopped shot; choices are 30/60/120. It controls camera commands with
  Console and editor monitoring with Native. Neither setting changes the game's
  render or recording frame rate.
- On supported Windows versions, frame pacing uses a dedicated high-resolution
  waitable timer. An unavailable timer falls back to normal event waits and is
  reported in diagnostics. No system-wide timer setting is changed.

Native input/render integration and built-in recording require reviewed game
builds. Console mode uses external recording software. Historical maintainer
checks are recorded in [Stage 1](internal/STAGE1_TESTING.md) and
[Stage 2](internal/STAGE2_TESTING.md); they are not current compatibility promises.

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
during a shot affects the next Play shot. **Off** disables this time filter.

Stronger settings spread small timing changes over a longer window, at the
cost of more camera delay relative to the replay action. The values above are
real-time delays, including at 0.1 replay speed. At the end, Dolly lets the
filtered camera finish before restoring the HUD; it does not snap the filter
to the last key. A discrete Step track remains discrete, with its timing
following the same delayed camera clock.

Compare modes with the same shot and replay speed before choosing one. More
filtering trades timing variation for camera delay; it cannot replace missed
console updates or smooth frames inside Deadlock's renderer. See the
[historical validation record](internal/VALIDATION.md) for past experiments,
not a guarantee for your current game build or shot.

## How the unlocker is loaded and recovered

Dolly bundles a locally built cvar unlocker based on v0.5.2 that fixes its own
command cleanup on game exit, and checks its pinned SHA-256 before launching.
Its patches and provenance are in `third_party/cvar_unlocker/`. It never
replaces the real game `server.dll`.

For the upstream SearchPaths loading method, Dolly makes a temporary edit to
the existing `game/citadel/gameinfo.gi`. It first writes an exact original backup
and recovery journal to `logs/<session>/`, then atomically adds a unique plugin
mount. The normal Citadel game directory is retained so an alternate game name
does not introduce replay game-directory mismatch errors. A read-only
`gameinfo.gi` (for example after Steam file verification or a modding guide) is
supported: Dolly clears only that attribute for the atomic swap and restores the
original bytes with the read-only attribute afterward.

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

When an error dialog appears, press **Copy error details** beside **OK** and
paste that text with your question; the first line names your Dolly version and
the failed step. Use **Export diagnostics** in Dolly and send the resulting ZIP
with a short
description of the failed step. It includes the active shot and playback
settings, measured playback update rates, console responses, capability results
and launch logs, including local file paths. Preview diagnostics include the
complete requested frame, its aspect ratio, and the action's result or error.
Camera-position checks include requested positions, readbacks and measured
corrections. This helps distinguish a timing
choice in the shot from console throughput or a stalled replay clock. Failed
launch attempts also record your selected executable/replay paths and the error, even if startup
failed before a game session existed. Diagnostics retain bounded raw console
history and logs from up to eight recent sessions, so a successful retry does
not hide a preceding crashed launch. Session journals retain exit code/time.
If Deadlock closes within seconds of a Dolly launch, development mode may have
loaded an uncompiled Panorama file left in the game folder (for example
`game/citadel/panorama/layout/hud.xml` from a HUD mod or an older build). Dolly
refuses that launch and lists the files. Move or rename the
`game/citadel/panorama` folder, or validate Deadlock's files in Steam, then retry.
If Deadlock's development build shows one of its own assertion windows, Dolly
chooses the ignore action for the game process it launched so the session
continues; the assertion text remains in that session's game output log and the
dismissal is recorded in its launch log.
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

If Windows shows a **Bad Image** dialog for `DollyNative.dll`, or the log reports
**WinError 4551: An Application Control policy has blocked this file**, Windows
Smart App Control or a WDAC policy blocked the unsigned helper. Right-click the
release ZIP, choose **Properties > Unblock** before extracting, and extract the
complete package again. If it persists, allow Dolly in **Windows Security > App &
browser control > Smart App Control** (or your organization's Application Control
policy), or use a machine where Application Control is not enforced. Dolly cannot
bypass that Windows policy.

If the game launch changes or breaks after a Deadlock update, the pinned unlocker
may need a compatible release. Dolly will not silently replace it with a new
binary. Run recovery as described above if a configuration edit remains pending.

## Package and development

- `dolly/`: complete Python editor, controller, path engine, launcher and console source.
- `tests/`: automated path, transport, controller and launcher/recovery checks.
- `native/`: native camera source, exact-build profile, C++ tests and vendored dependency.
- `docs/internal/NATIVE_CAMERA.md`: maintainer notes on native driver internals.
- `examples/demo_shot.dolly.json`: a **synthetic** example of file structure.
  Its coordinates are not a verified location in your replay; capture your own.
- `docs/internal/CONSOLE_RESEARCH.md`: historical maintainer research and source evidence.
- `docs/internal/VALIDATION.md`: historical validation, not a current-release guarantee.
- `third_party/`: official unlocker binary, provenance and third-party licenses.

See [Contributing](../CONTRIBUTING.md) and [Building](BUILDING.md) for source
setup and checks, and [Updating](UPDATING.md) for installed-app updates.

HLAE is the workflow reference, not a transplanted binary hook. Its CS2 view
offsets and the supplied CS2 animation offsets have not been treated as Deadlock
offsets. Source references: [HLAE](https://github.com/advancedfx/advancedfx),
[cvar unlocker](https://github.com/Artemon121/cvar-unhide-s2-citadel), and
[extracted Deadlock command data](https://github.com/SteamDatabase/GameTracking-Deadlock).
