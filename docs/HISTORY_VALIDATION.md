# Validation — 0.3.2 alpha

Date: 8 September 2026.

## Latest replay evidence and repairs

Both supplied 60-second clips and diagnostics export 7 were inspected. In the
later clip, the user clicks Deadlock's native Play control at about 54.15 s;
by 54.4 s the viewpoint is beneath/inside geometry. The native speed selector
reads 1x in that scene. The earlier clip shows small uneven movements during
the uninterrupted slow shot. Its initial large changes cannot be assigned to
a specific Dolly action because the editor is not visible.

The export records a 4.53125-second path at speed 0.1, played over 45.299 real
seconds. Its achieved command rate was 92.83/s for a requested 120/s, with mean
round trip 5.783 ms and maximum update interval 32.876 ms. These measurements
establish overall timing and uneven command cadence, not renderer-frame timing.

Two distinct problems have direct diagnostic support:

- The seek for tick 112697 first reports transient 112698. The old check accepted
  that neighbor; the engine then settled to 112697 and playback falsely reported
  movement during calibration. The new check requires three exact observations,
  reasserts pause and requires three more, with a bounded timeout and cancellation.
- Fresh-seek camera checks gave a 16-unit response to a 16-unit probe. Later
  paused checks gave 4.1, 4.0 and 3.1 units. The last gain was 0.19375, while
  manual commands accumulated a target far from the visible view. This supports
  the inference that native Resume exposed a displaced stored camera origin.

Paused preparation now captures the visible view, refreshes its exact current
tick, discards stale position correction, and reapplies that view. Saved-view
switches and readable-tick previews use the same refresh. Continuous control
requires a direct XYZ response within 10% of the commanded movement plus a
verified return. If refresh does not recover that response, setup fails before
flight. This recovery is supported by the fresh-seek observations and simulated
regressions; it has not yet been confirmed in the native game.

Position feedback is sampled approximately four times a second inside existing
command batches. Readbacks are compared with recent commanded segments to allow
short rendering delays. Three consecutive distances above 64 units stop further
movement. This bounded guard limits continued divergence; it cannot guarantee
zero displacement if the engine changes its response between samples. Readbacks
do not recalibrate the camera while it is moving. Each export retains up to
256 recent frame samples per mode and separate playback startup calibration.

The revised ReplayClock makes continuous phase corrections instead of snapping
to each acknowledged integer. Position, rotation, aspect and camera variables
use the same evaluated shot time. Correction speed is bounded to 20% of nominal
playback speed; prediction remains limited to one tick. Once the replay reaches
the end, the controller applies the exact final key before cleanup.

A deterministic 0.1x simulation with jittered tick arrivals and 60 camera updates
per second reduced the standard deviation of tick increments from 0.016803 to
0.001246 after settling. Maximum lag was below 0.29 tick. This **92.6% reduction
is a synthetic clock measurement**, not a measured improvement in Deadlock's
image stability or an expected percentage for users.

The Windows frame waiter uses a dedicated high-resolution timer where supported,
falls back to Event.wait when unavailable, and reports its backend/error. Native
waits check cancellation at most every 20 ms, excluding OS scheduling delays.
Tests simulate native handles, relative deadlines, failures and cancellation.
No Windows process or Deadlock renderer was available to measure actual timing.

## Retained startup correction

The supplied startup log reports Errno 13 opening `logs/Dolly_startup.log`
inside the bootstrap, before the editor process is created. Both batch launch
branches redirected stdout/stderr into that file while the bootstrap reopened
it for the child process. The correction removes that active redirection from
the two bootstrap commands; earlier interpreter checks still write their log
and close it before branching. Bootstrap errors now append after cleanup, with
console output retained if appending fails.

The Windows sharing mechanism is documented in
[CreateFile](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).
The child continues receiving explicit redirected standard handles with
`close_fds=True`, as supported by
[Python subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.Popen).
Tests check both batch branches and both writable/unwritable error-log cases.
The earlier mocks did not exercise the batch/Windows file-sharing overlap.
The successful 0.3.1 session in the latest diagnostics confirms that the user
could start the editor and game. This revision retains those startup files.
The GUI, input and artwork are unchanged from 0.3.1; the rendering checks below
are retained evidence from earlier releases.

## Automated results

`python -m unittest discover -s tests -q`: **441 tests passed** on Python 3.12 in 37.798 seconds.

| Area | Tests | Coverage |
| --- | ---: | --- |
| Path model | 17 | Spatial interpolation, uneven timestamps, rotation seams, continuous output and schema validation |
| Aspect path model | 13 | Positive aspect keys, independent smooth/linear/step framing, no overshoot, version-1 migration and version-2 round trips |
| Console | 31 | Both protocols through loopback simulations, fragmented responses, completion markers, timeouts, concurrency and live tick parsing |
| Controller | 64 | Capture, seek/play ordering, replay identity, HUD/cvar restoration, startup sequence, bounded frame batches, diagnostics and clean close versus unexpected exit |
| Camera position | 16 | Measured XYZ correction, direct XYZ response, verified return, cancellation and unchanged authored coordinates |
| Preview convergence | 7 | Bounded private convergence, rejection of unsafe weak-response public previews, alternating aspect views and failed lens readback |
| Aspect controller | 12 | Aspect commands/readback, no old FOV commands, automatic-0 resolution, exact restoration, capture and conflicting track rejection |
| Launcher | 41 | Mandatory launch flags, paths, Steam libraries, gameinfo backup/recovery, concurrent changes, DLL hash and unlocker before demo loading |
| GUI handlers | 38 | Retained camera/aspect coverage, saved bindings, live rebinding, generation/focus checks, invalid settings and unexpected listener shutdown |
| Paused controller | 28 | Current-tick refresh/switching, one-camera shots, Z movement, capture/restart, tick and identity failure, original restoration, mid-calibration cancellation and real-thread pending-reply ownership |
| Paused GUI | 17 | Previous/Next without authored-time seek, input focus, capture/restart, invalid speed, panel close/Stop before and during preparation, cancellation propagation and playback transition |
| Movement model | 15 | Source-Z elevation, camera-relative axes, rotation, normalized diagonals, stall cap, speed bounds and independent metadata copies |
| Movement input | 16 | GUI focus/physical modifiers, foreground PID, held-on-enable/return suppression, mid-query focus change, Alt/Windows chords, disabled state and native failure |
| Framing graph | 13 | Actual playback-curve sampling, baseline, fixed positive bounds, vertical-only dragging, keyboard adjustment, cancellation and disabled state |
| Hotkey | 25 | Simulated Windows input polling, fresh presses, required modifiers, focus recovery, repeats, callback delivery, native failures and cleanup |
| Binding model | 8 | Mouse/keyboard virtual keys, labels and strict serialization |
| User settings | 19 | Defaults, keyboard/mouse round trips, schema/size limits, malformed backups, atomic save failures and per-user paths |
| Playback clock | 17 | Continuous phase, jittered 0.1x increments, delayed/coalesced ticks, sampling-rate independence, one-tick cap, rewinds and numeric bounds |
| Playback transitions | 15 | Exact settled seek, visible-pose refresh/native-resume simulation, weak-response rejection, drift stops, delayed-render tolerance, exact endpoint, forward/frozen-seek guards and independent bounded traces |
| Frame pacing | 13 | Simulated native timer deadlines/handles, cancellation, API failures, event fallback and custom clock compatibility |
| Branding | 6 | Explicit/default ICO ordering, no successful-icon overwrite, PNG fallback, missing assets and every DIB header/mask |
| Desktop bootstrap/startup | 10 | Both batch branches release the startup log before bootstrap, launch-error persistence/fallback, same interpreter/cwd, hidden child handles and visible GUI/logger failures |

Windows APIs and game responses in these tests are simulated. A deterministic
20 ms console round-trip fixture checks the reported 50 updates/second; that
does not predict actual game/render speed. The position-response fixture has
aspect-dependent affine offsets to exercise calibration. It is not a claim
that Deadlock implements that mathematical response.

All **40 Python source/test files** parse with Python 3.10 syntax rules. GUI
imports succeed. The version-2 example loads and produces numeric aspect
samples. Windows batch files retain CRLF line endings. The editor requires no
third-party Python packages.

The bundled unlocker remains the unmodified official v0.5.2 x64 PE DLL, with
SHA-256 `e86f270b1dedc81fd54a230f0080eee568a4f2bd39e1f41080dcf71d833267ba`.
Provenance and license files remain included.

## Actual desktop rendering

The paused-panel checks use the actual Tk UI with a simulated controller; they
never launch a game or enable native Windows input. They exercise the new
Cameras toolbar, saved-camera switching at a fixed tick, keyboard and mouse
holds, focus/unmap clearing, speed-entry isolation, capture/restart, Escape and
closing the panel. All **12 layouts** passed without inaccessible overflowing controls: four
Cameras-tab layouts and eight default/minimum paused panels at normal and 150%
font scaling. **Ten interaction checks passed at each scale.** The normal panel
opens at 610×660 with a 590×650 minimum; these dimensions scale with DPI. Its
help text wraps within the scaled width. These screenshots use Ubuntu Tk 8.6
and simulated replay tick 112674, not native Windows game output.

Historical rendering evidence for earlier releases follows.

For 0.2.2, a real Tk startup smoke check loaded the PNG fallback at 1000×700
without widget overflow. Native Windows ICO loading, console-free process
creation and taskbar display were not executable here; those API calls are
covered with mocks and the icon itself is decoded and checked independently.
All nine icon sizes use 40-byte DIB headers, valid dimensions, 32-bit pixels and
complete DWORD-aligned AND masks. Each decoded image matches a resampling of
the reattached source pixel-for-pixel; the source file itself is byte-identical.
The six branding regressions prevent a return to PNG-backed ICO frames or an
icon setter that overwrites a successful Windows icon. The bootstrap tests
verify that startup errors remain visible when there is no console window.

Historical 0.2.1 rendering coverage follows:

Version 0.2.1 adds **10 actual Tk render checks** for the Cameras toolbar and
Capture binding dialog at normal and 150% DPI, with no inaccessible overflowing
controls. Mouse4/Mouse5 selection, required-modifier toggles, the Middle preset,
saving, toolbar updates and preference reload in a fresh app were exercised.
Settings were written only to a scratch test location; no native listener or
game was started by these checks. A separate bare Mouse4 view confirms the
side-button preset without required modifiers.

The 256 px icon loads through the actual Tk application. All nine ICO entries
from 16 to 256 px decode successfully. Comparison against the original on a
white background confirms the adapted mark retains its central film reel,
compass ring and plain crossed bars. The supplied original is byte-identical
in the package. An incorrect first icon adaptation was rejected and is not
included. Native Windows window/taskbar appearance remains unverified here.

The following larger layout matrix was completed for the 0.2.0 baseline:

The running Tk editor was rendered using Ubuntu Tk 8.6 with antialiased fonts
and a virtual display. This uses the actual application widgets, with a
synthetic five-camera shot and a DOF track; it is not a design mockup.

The final check produced **24 screenshots** covering all three tabs at normal
DPI and 150% DPI, plus Coordinates / timing, Fixed values and Log dialogs.
Normal-DPI window sizes were 1000×700, 1180×800 and 1920×1080. At 150%, the
editor enforces a readable minimum of approximately 1500×1050 where the screen
allows it. A request for a smaller physical window at that scale expands to
the minimum; this is not a claim that the interface fits at 1000 physical
pixels with 150% scaling.

Widget-bound and table-column checks found no inaccessible overflowing
controls in that matrix. Tab selection was also exercised with actual mouse
events. Camera and variable tables scroll vertically within their panels;
the main pages do not scroll. At the smallest window the path overview may
show an enlargement hint, keeping the framing graph and editing controls
available. Playback controls remain visible. Advanced settings and logs open
separate resizable dialogs.

Native Windows font metrics, window decorations and display behavior can
differ. Displays too small for the scaled minimum were not verified.

## Capture preference behavior

The binding is stored in `%APPDATA%/DeadlockDolly/settings.json`, with a
home `.config/DeadlockDolly` fallback. Enablement is session-only and off at
startup. Strict settings validation and atomic replacement preserve the prior
file on save failure; an explicit save retains malformed prior content in a
unique `.invalid` backup. Tests verify the JSON round trip for Mouse4/Mouse5
and keyboard chords, without modifying the user's game or its keybindings.

The listener uses the current-down bit of GetAsyncKeyState and polls every
8 ms while enabled. It observes the configured key and required modifiers,
without consuming the game's input. Bare side buttons work while Ctrl/Shift
movement modifiers are held. Windows-key chords are ignored. A held main key
does not repeat, and changing modifiers alone does not create another capture.
Focus/PID changes reset the input baseline. Regressions include Windows
returning zero input outside the game, then reporting a held button on return.
Queued captures must still match the active listener generation, enabled
state, game focus and idle editor state. A stopped listener disables the UI
switch and reports its error once.

For the historical 0.2.1 capture-binding release, camera/aspect/path/console/launcher
modules were byte-identical to 0.2.0. Version 0.3.0 changes the camera controller
for paused flight; this does not constitute in-game validation of aspect playback.

## User runtime evidence and migration

Earlier user logs establish successful Windows launch, Netconsole and cvar
unlocker initialization in the hideout before replay loading. The user's
feedback established working camera travel and replay playback, followed by
height and lens issues addressed in subsequent revisions.

The reported native position round trip changed
`240.1 3816.2 421.3` to `239.0 3815.0 478.7` after executing the reported
`spec_goto`, with unchanged pitch/yaw. This establishes a view-position
mismatch, without establishing a universal fixed offset. The retained
calibration measures the active response, corrects the requested view, checks
a second height and verifies its return before playback resumes.

The latest supplied 0.1.6 export contains a completed 8.328125-second shot,
five distinct camera poses and successful position verification. Its selected
FOV command/readback matched, but the user confirmed that degree-based FOV
changes did not visibly affect freecam. The user separately verified a visible
effect from `r_aspectratio` and requested it as the replacement.

Version 0.2.0 therefore uses `r_aspectratio` for camera framing. It does not
query or write the former Camera FOV/Spectator FOV controls. The existing
shot's five poses and times survive migration; its 40/75/40/40/100 degree
values remain inactive metadata, and aspect keys start at 16:9. They must be
authored as ratios rather than converted by an assumed FOV formula.

When capture reads `r_aspectratio 0` (automatic), it resolves a positive ratio
from the launched game's visible client area, falling back to the shot's
normal aspect. It records that source in diagnostics. A client rectangle
cannot identify internal letterboxing or a custom render viewport; choose the
appropriate normal aspect when needed. The exact original cvar, including 0,
is retained for Stop / restore. The graph's 0.5–4.0 bounds are editor limits,
not established native cvar limits.

## Remaining runtime checks

No Windows/Deadlock process was available for running this release. The
automated and desktop-render checks do not establish:

- Correct visible aspect transitions, bank or DOF in every spectator mode.
- Renderer-frame synchronization, fixed frame rate or HLAE view-hook behavior.
- Exact camera positioning throughout an animated aspect/cvar path. Playback
  reuses the correction verified at its starting view. Periodic readbacks can
  stop large sustained drift, but native offsets may still change with framing
  or camera mode between observations.
- Native Windows input polling, taskbar appearance or window-aspect capture
  on the user's machine. Taps shorter than the polling interval can be missed;
  mouse-vendor remapping can change which key Windows reports.

## Local check for this revision

1. Update using the recovery/extraction instructions in `Start_Here.txt`. Launch
   the hideout, initialize the unlocker, load the replay and check camera support.
   Enter freecam and pause at a recognizable moment.
2. Open **Cameras > Paused camera**. With a saved shot loaded, use Previous,
   Next and Apply selected. The view and its aspect/DOF should change while the
   replay tick and scene action remain fixed. A one-camera shot also works.
3. Start camera controls with keys released during the position check. Try the
   movement buttons, especially Up/Down for world-Z height, then arrow-key look.
   Enable keyboard flight to repeat this while the game has focus. Switch away
   and return with a movement key held: release and press it again to move.
4. Use Timed shot capture, capture a view, move somewhere else and capture again.
   Flight should continue after capture. Stop controls or press Escape; the
   current view should remain paused. Close the panel during preparation once
   to check that calibration stops promptly after its pending response.
5. Use the normal Play shot workflow with Frozen preview off. The replay should
   resume with the saved path and HUD handling. Stop / restore should restore
   prior aspect/DOF values, including automatic aspect 0 where applicable.
6. Repeat the reported case: move the paused camera near a recognizable object,
   end camera controls and press Deadlock's own Play button. The view should
   remain in that location. Also try the same native resume while controls are
   active but no movement key is held; Dolly should stop when it detects replay
   advancement rather than continue writing camera positions.
7. Play the existing shot at 0.1x with the same command-rate setting used before.
   Compare steady travel and the sections where rotation/aspect change. Keep
   Smooth selected for camera/framing curves if those changes should be gradual.

If movement is wrong, jerky or fails, export diagnostics immediately. The export
includes the paused mode's fixed tick, current pose, update rate and error, plus
existing aspect and position-calibration evidence. Stop controls before typing
in the game's console; this command-based implementation cannot see whether it
is open. If the console disconnects, reconnect and use Stop / restore to retry
retained camera settings.
