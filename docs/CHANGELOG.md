# Changes

## 0.3.10 alpha — restart a finished native shot

- Keep an unsettled final spectator handoff as a held view, not a failed shot.
- Play and Stop release the old native override with acknowledgement before
  seeking or restoring settings; neither requires that frozen spectator to
  converge. Reset stale position calibration on explicit release.
- Preserve strict handoff checks for competing manual camera writers.
- Add repeated-play, pause/stop and failed-release regressions.
- Native DLL, render callback, interpolation and paused movement are unchanged.

## 0.3.9 alpha — Windows native connection build fix

- Fix the confirmed Windows-only test/startup error: Python looked up
  `InterlockedExchange` as a kernel32 DLL export, which was unavailable.
  Export and call three compiled atomic wrappers from DollyNative.dll instead.
  Keep atomic memory barriers and shared-memory ABI 1.
- Verify the helper hash, x64 DLL format and protocol before using its exports;
  report incomplete or mixed-version packages clearly. Loading the helper in
  the editor does not call its game factory or install camera hooks.
- Require the atomic exports in the Windows packaging check. Retain and extend
  the real Windows named-memory test; add portable binding/error regressions
  and C++ atomic return-value/high-bit checks.
- Show a bounded test-log tail in Actions when Python regressions fail, while
  keeping the complete diagnostic artifact and stopping the build on failure.
- Camera paths, native timing, hook locations, manual paused movement, unlocker
  sequence and game compatibility fingerprints are unchanged from 0.3.8.

## 0.3.8 alpha — experimental native main-view camera

- Add a Native (experimental) camera driver selected before launch. Evaluate
  the complete saved path in the game's main-view callback, after normal camera
  setup and before the view matrices. XYZ, rotation and aspect zoom no longer
  rely on repeated console camera commands during native shot playback.
- Use the verified fractional game clock for replay shots and a high-resolution
  elapsed clock for frozen previews. Preserve existing spline shapes, shortest
  rotation, zoom interpolation and exact endpoints. The optional external
  smoothing filter remains available with the Console driver.
- Support only the exact client.dll and engine2.dll builds supplied for this
  investigation. Fingerprints, function bytes and view identity are checked
  before camera ownership. A game update requires a verified native profile;
  Console remains selectable before launch. No Valve DLL is distributed.
- Arm the native path before demo_resume. Hold the last view while verifying
  that the underlying spectator camera has caught up before releasing control.
  Manual paused flight, capture, preview and position calibration are unchanged.
- Keep the unlocker-before-replay startup, -dev/-insecure requirements and
  recoverable game configuration. Stop overriding if the editor disappears,
  the replay changes, the view is unsupported or replay time jumps.
- Add native telemetry, shared-memory protocol checks, C++/Python curve parity
  tests and a Windows callback smoke test to the executable build workflow.
  DOF and other effect cvars still use console updates at sampled native phase.
- Native helper cross-compiles for Windows x64. Actual game rendering,
  responsiveness and visual smoothness remain unverified pending user testing;
  this is an experimental alpha, not a demonstrated jitter-free release.

## 0.3.7 alpha — experimental shared playback smoothing

- Add Off, Light, Balanced and Strong smoothing under Shot playback. Balanced
  is the default for each editor session; the choice is not saved in settings
  or project files. Choose before Play shot; a running shot keeps its choice.
- Smooth the shared playback time over real-time windows of 0/80/160/280 ms.
  Steady motion adds approximately 0/40/80/140 ms of camera delay relative to
  replay action. Position, rotation, aspect and DOF/cvar tracks evaluate
  together on the authored path; Step tracks remain discrete.
- Let the filtered camera finish smoothly at the endpoint. Off retains the
  0.3.6 command sequence and endpoint fix. This update is cumulative for 0.3.5
  users; manual paused-camera movement, preview and capture remain unchanged.
- Add filter, playback and GUI regression coverage and smoothing diagnostics.
  Native improvement is not yet verified; compare the same 0.1-speed, 120 Hz
  shot with Off, Balanced and Strong before publishing. The console filter
  cannot guarantee smooth delivery inside Deadlock's renderer.

## 0.3.6 alpha — smooth the last fraction of path playback

- Remove a forced final-key jump when the replay reaches the end tick before
  the continuous camera clock reaches the final key. Complete both before
  normal pause/HUD restoration, with a bounded wait for the remaining camera
  movement. The supplied 0.3.5 recording showed about a fourfold final yaw step.
- Preserve the exact final camera/framing values and stop on a failed finish,
  cancellation, replay identity change or an externally initiated large seek.
- Keep paused-camera preparation, movement, focus handling and the verified
  0.3.5 seek correction unchanged. The low paused-update average in diagnostics
  includes idle time and does not establish slow active movement after Alt-Tab.
- Keep the authored position/rotation/framing curves and normal playback clock.
  This change targets the measured endpoint bump; it does not establish a fix
  for all mid-path renderer or recording judder. Native verification is pending.

## 0.3.5 alpha — recover from a paused refresh landing two ticks late

- Correct the 0.3.4 paused-controls startup failure seen in all five supplied
  attempts. The backward refresh succeeded, but its forward return stopped two
  ticks late, preventing original-view restoration and all manual translation.
- After three unchanged observations one or two ticks past a seek target,
  reassert pause, confirm the replay and position, and retry that exact target
  once. From the overshoot this is a backward seek. Keep the original polling
  deadline and require six exact target readings across a further pause.
- Ignore transient ahead readings; do not retry large or alternating offsets.
  Cancellation, changed demos and unexpected movement during confirmation stop
  recovery. A failed retry or weak camera response still blocks camera controls.
- Preserve correction evidence in diagnostics, including the original samples,
  observed overshoot and requested target. Keep 0.3.4's high-resolution motion
  clock and the existing camera paths, bindings, logo and Windows build recipe.
- Add transport-level regression tests for the observed seek behavior, original
  view restoration, saved-camera switching and movement in both directions on
  every axis. Native 0.3.5 behavior still needs a rebuilt Windows EXE and game test.

## 0.3.4 alpha — paused camera recovery and precise rotation timing

- Use the high-resolution performance clock for every camera movement,
  replay-clock sample and frame deadline. Windows Python 3.12's coarse clock
  produced repeated poses followed by 15/16 ms steps in the supplied EXE log,
  even with the high-resolution wait timer enabled.
- Refresh paused camera state with one adjacent-tick seek and a verified
  return to the original tick. Preserve the captured or selected camera view,
  then require the existing direct XYZ movement and return check. A same-tick
  seek alone did not reset the weak spectator response in the diagnostics.
- Allow one such recovery for a failed position check after normal Play/Seek.
  Cancellation, changed demos and lens errors do not trigger retries. A camera
  that still moves only partway remains blocked; correction bounds are retained.
- Let the first Capture after an external replay seek measure the new view.
  Retire stale paused-movement preparation and require fresh calibration before
  further manual movement. Reject a replay changing during the capture itself.
- Export motion-clock implementation/resolution and both refresh seeks in
  diagnostics. Retain authored path/rotation/framing interpolation and the
  previous Windows build-test corrections. Native 0.3.4 verification is pending.

## 0.3.3 alpha — build fix 1

- Correct six test failures caused by Windows expanding temporary-directory
  short names such as `RUNNER~1` into their canonical long paths. Continue
  checking the complete expected log paths and replay command.
- Exercise separator rejection without trying to create filenames containing
  quotes or control characters that Windows forbids. Keep real-file command
  checks for semicolons and plus signs; also cover carriage return and NUL.
- Pass all 460 local tests. A separate filesystem-assumption reproduction
  fails with the original six failures/one error and passes all seven repaired
  cases. This is a Linux simulation, not a completed Windows executable build.
- Change four test files and two documentation files only. Retain the existing
  Windows test/build/icon/GUI gates, executable version, application and assets.

## 0.3.3 alpha — executable packaging

- Add a Windows x64 PyInstaller recipe for direct `Dolly.exe` startup, with
  embedded supplied logo, version resources and bundled Python/Tcl/Tk.
- Keep portable logs and recovery journals beside the executable; resolve
  bundled resources separately. Own windowed startup logging in the same
  process, avoiding a recursive Python bootstrap or an extra console window.
- Isolate the external game's DLL search environment from the frozen runtime.
- Add File-menu recovery and `Dolly.exe --recover`, using existing recovery guards.
- Add a Windows Actions build with source regressions, PE/icon verification,
  and actual relocated bundle/editor smoke checks before producing ZIPs.
- Organize the source for GitHub with a concise README, user/build guides,
  explicit source archive list and ignored runtime/build data.
- Keep 0.3.2 camera timing, curves, paused movement and bindings. The native
  Windows build is prepared but was not executed in the Linux authoring workspace.

## 0.3.2 alpha

- Replace tick-edge camera-clock snaps with gradual phase correction shared by
  position, rotation, aspect and cvar curves. Retain bounded prediction and
  explicitly apply the final key when the replay reaches the shot end.
- Wait for repeated exact seek-tick observations before and after reasserting
  pause. A transient adjacent tick no longer causes the observed start failure.
- Refresh the current replay tick before paused camera preparation, retaining
  the visible pose captured before that refresh. Require a direct XYZ response
  and verified return; reject a weak paused response before continuous flight.
- Read back visible position periodically during paths and manual movement.
  Stop sustained large drift rather than accumulating an unseen camera target.
  Preserve independent startup calibration and bounded frame traces in exports.
- Use a dedicated high-resolution Windows frame timer when available, with
  bounded cancellation and normal event-wait fallback.
- Preserve saved shot timing/curves, capture bindings, logo, startup repair and
  the dev/insecure launcher. Pass 441 automated tests. Native Deadlock
  verification remains pending.

## 0.3.1 alpha

- Fix the reported Windows `Permission denied` error opening Dolly_startup.log.
  End the batch launcher's log redirection before starting the Python bootstrap,
  so it can open the output file and pass its handle to the hidden editor.
- Keep bootstrap launch errors recorded with a best-effort append after handle
  cleanup; retain the visible console error if the log is genuinely unwritable.
- Add regression coverage for both batch interpreter branches and log-write
  failure. Extend the existing error test to verify persistence.
- Retain 0.3.0 camera controls, saved shots, bindings, framing and supplied logo.
- Pass 407 automated tests. The full native Windows launch needs a local retry.

## 0.3.0 alpha

- Add a nonmodal Paused camera panel for switching saved views at the current
  replay moment. Apply camera pose, aspect and cvar-track values without seeking
  to the camera's authored arrival time or resuming the replay.
- Add manual camera movement independent of replay time, with GUI movement
  buttons and optional Windows in-game keyboard flight. WASD follows the camera,
  Space/Ctrl changes world-Z height, arrow keys turn, Shift boosts translation
  fourfold and Escape ends flight. Expose move and turn speeds in the panel.
- Check the selected replay and fixed tick before and after camera updates;
  stop if the tick changes or its state becomes unreadable. Reuse the paused
  position calibration rather than running it for every motion sample.
- Keep one camera writer and bounded movement steps after console stalls.
  Clear held GUI input on focus loss and suppress held game keys when enabling,
  returning to the game or replacing its process. Observe input without hooks
  or changing the user's game bindings.
- Cancel initial calibration when the panel closes or Stop controls is used;
  a pending console reply may finish before cancellation takes effect.
- Preserve the capture/replace workflow, automatic aspect restoration,
  `r_aspectratio` framing graph, supplied icon and dev/insecure launcher.
- Use keyboard look in this implementation. HLAE's native mouse and render
  hooks are documented as reference work, not copied as Deadlock offsets.
- Pass 405 automated tests, including simulated flight, input, pending-console
  ownership and GUI lifecycle checks. Native Deadlock verification is pending.

## 0.2.2 alpha

- Use the exact reattached film-reel image, preserving the source bytes.
- Encode every Windows ICO size as a 32-bit DIB with a complete AND mask,
  avoiding PNG-frame parsing problems in older Tk 8.6 Windows icon readers.
- Set the root window icon explicitly and the future-dialog default; only
  fall back to PNG if the Windows ICO fails, avoiding competing icon setters.
- Launch the GUI with the validated interpreter and CREATE_NO_WINDOW so the
  batch/Python console does not retain a separate taskbar button. Keep startup
  logging and show a Windows error dialog on GUI or logger initialization failure.
- Retain all camera, framing, capture-binding and saved-preference behavior.
- Pass 329 tests, including icon-format and console-free startup regressions.


## 0.2.1 alpha

- Add a saved Capture binding dialog for keyboard keys, Mouse4, Mouse5 and
  MiddleMouse, with optional required Ctrl/Alt/Shift modifiers. Ctrl+Alt+K
  remains the default; bare mouse buttons also work with movement modifiers.
- Store the binding per user outside the extracted program folder. Keep the
  enable switch off on startup. Validate settings and save atomically; preserve
  malformed settings before explicitly replacing them.
- Observe the selected input through Windows key-state polling without
  consuming game input. Require a fresh main-key press, suppress repeats and
  held-on-enable/focus-return captures, and scope capture to the launched game.
- Discard queued capture events after disabling or changing the binding, and
  retain busy/playback/modal guards.
- Apply the supplied logo as the window/dialog and taskbar icon, with a light
  backing for contrast, multiple Windows icon sizes and a PNG fallback.
  Keep an unchanged copy of the source artwork.
- Report unexpected input-listener shutdown in the status/log and uncheck the
  capture switch instead of leaving an apparently enabled but stopped listener.
- Retain all 0.2.0 framing, camera-path and development-launcher behavior.
- Pass 315 automated tests. Verify the changed toolbar/dialog in 10 actual Tk
  views at two DPI settings, including saved binding reload.


## 0.2.0 alpha

- Replace degree-based FOV control with `r_aspectratio` framing. Camera frames
  write the aspect ratio; capture and preview read it back. The old Camera FOV
  and Spectator FOV controls are no longer queried or written.
- Add a framing graph with camera selection, vertical value dragging, keyboard
  adjustments and a visible normal-aspect reference. Framing has its own Smooth,
  Linear or Step interpolation. Smooth uses a shape-preserving cubic curve that
  does not overshoot between key values. The graph uses a fixed 0.5–4.0 editing
  range; these are editor limits, not established native cvar limits.
- Default normal framing to 16:9, with presets and a custom standard. Capture
  keeps an explicit positive override. Automatic 0 resolves to the launched
  game's visible client aspect, or the shot's normal aspect if unavailable,
  and records the source. Zero is never a curve endpoint. Stop restores the
  exact original `r_aspectratio`, including automatic 0.
- Save version-2 projects with aspect keys, the normal aspect and framing
  interpolation. Import version-1 camera poses, timing and other tracks without
  recapture, retaining old FOV values as inactive metadata. Imported framing
  starts at 16:9 and must be authored again; no FOV-to-aspect conversion is guessed.
  Reject the former Camera FOV/Spectator FOV controls and duplicate aspect
  tracks at playback validation.
- Redesign the desktop interface around Session, Cameras and Camera variables.
  Keep capture actions, camera list, framing graph, selected-camera controls and
  path overview in fixed panels with a persistent compact playback bar. Move
  raw coordinates/timing to a separate dialog, remove whole-page horizontal
  scrolling and hide the activity log by default.
- Preserve paused position calibration, replay-synchronized playback, automatic
  HUD handling, native DOF tracks and the existing development-only launcher.
- Treat a normal game exit as Game closed instead of a startup failure.
- Pass 261 automated tests and render the actual Tk editor in 24 views covering
  compact/large windows, two DPI settings and the auxiliary dialogs.

The user verified the visible effect of `r_aspectratio` in freecam and requested
this replacement after the previous FOV controls failed to change that view.
That establishes the control's usefulness in the reported game session; it does
not establish an equivalent FOV-degree conversion or validate this release's
full animated transition. See VALIDATION.md for the release's checks and limits.

## 0.1.6 alpha

- Allow up to 12 bounded position-correction passes while checking progress,
  including holding an unchanged command so the camera can settle. This avoids
  the previous three-pass cutoff rejecting a preview whose horizontal position
  was still approaching the requested view.
- Accept a meaningful height response followed by a verified return to the
  requested position. The temporary height probe no longer requires the engine
  to move exactly one unit for every unit commanded.
- Apply the selected view's lens settings before measuring its position
  correction, and read back the selected FOV cvar. This includes FOV values
  changed with **Update view** in the editor.
- Keep the last successfully verified correction as the starting estimate after
  a failed check. Every subsequent Preview, Seek replay, or Play shot still
  verifies the position before completing its positioning step.
- Include the full requested preview frame, FOV and action error/status in
  diagnostics. Saved keyframes need no coordinate edits or recapture.

The user's 0.1.5 diagnostics show that the editor saved and sent the requested
75-to-40 FOV change. The reported errors came from the position check: one
horizontal residual decreased from -5.8 to -4.2 to -3.1 units before the old
attempt limit rejected it, with height already within 0.2 units. Another height
probe moved 11.3 units for a 16-unit command and returned to the requested
position, but the old response test rejected it. These checks are corrected;
the resulting preview behavior still needs confirmation in Deadlock. The
position correction remains a measurement at the initial view, reused during
playback; this does not establish correct compensation for every animated
camera-offset or lens setting throughout a shot.

The complete suite passes **215 tests**, including seven new preview-response
regressions and a UI edit-to-preview FOV regression.

## 0.1.5 alpha

- Measure the position difference between a requested camera view and the
  game's `spec_pos` readback while the replay is paused. Compensate outgoing
  XYZ coordinates for a stable measured offset and verify the corrected view
  before Preview, Seek replay, or Play shot completes its positioning step.
- Check height response with a brief camera movement so that a fixed-height
  camera cannot pass simply because its starting position happens to match.
  Use bounded checks and correction attempts; inconsistent or unreadable
  positions stop playback before `demo_resume` instead of using a guessed offset.
- Reuse the verified correction for all updates in that shot, retaining one
  camera/status round trip per playback update. Check it again on each preview,
  seek, or play action and reset it when the game connection changes.
- Preserve saved keyframes and captured freecam coordinates. Existing shots
  need no manual height adjustment or recapture.
- Include camera-position requests, readbacks, correction and verification
  results in diagnostic exports to make further game-specific issues traceable.
- Add 16 camera-position regressions. The complete suite passes 207 tests.

The user's readback changed from `240.1 3816.2 421.3` to
`239.0 3815.0 478.7` after sending the exact reported `spec_goto` command: a
57.4-unit height increase with unchanged pitch and yaw, plus a small horizontal
shift. This confirms a position round-trip mismatch; it does not establish a
universal fixed offset or its native engine cause. The correction measures the
current session instead of hardcoding that observed value. This release still
requires in-game validation of the corrected camera height.

## 0.1.4 alpha

- Make **Play shot** start at zero every time, seek to the first captured replay
  tick, apply the camera/lens/cvar settings, then resume the demo in the same
  ordered command batch. Normal replay playback is the default. The old frozen
  mode remains available explicitly as **Frozen preview**.
- Automatically set `citadel_hud_visible 0` before playback and `1` afterward,
  including pause, stop, startup failure and playback error. If exposed, hide
  the replay controls too and restore their prior setting. Keep failed cleanup
  pending for retry after reconnecting. Automatic hiding can be disabled.
- Temporarily set the supported `engine_no_focus_sleep` to zero during a shot
  and restore its previous value afterward to avoid background-window throttling.
- Reduce playback to one acknowledged camera/status round trip per update in
  builds with the confirmed live status format. Send console delimiters and
  commands together in one socket write. Never queue unbounded camera updates.
- Estimate fractional replay ticks for smoother slow motion, capped at one tick
  ahead of the latest acknowledged position. Hold at that boundary if ticks stop;
  reset immediately on a reported rewind. The HUD remains hidden until the actual
  replay reaches the path end. This is not render-frame synchronization.
- Preserve continuous equivalent yaw/roll output across +/-180 degrees instead
  of introducing a numeric wrap jump into camera commands.
- Export the last playback project, selected speed/mode, and measured command
  rates/latency. Keep only the latest camera command/response in the diagnostic
  dictionary instead of retaining a new coordinate-named entry every update.

The user's 0.1.3 diagnostics confirm live ticks and all existing camera support
checks. They also show Frozen mode on every recorded play, variable console
cadence, and a roughly 4-second shot taking 40 real seconds, consistent with 0.1x.
Sampled video frames show stepped camera movement during a paused scene. These
fixes address identified software issues. Subsequent user feedback reports mostly
smoother paths and working replay playback, with a remaining camera-height
mismatch addressed in 0.1.5. Native camera update cadence can remain a limit of
this command-driven backend.

## 0.1.3 alpha

- Fix step 5 rejecting the actual `demo_info` metadata response. Replay identity
  and total duration are now parsed separately from the current playback tick.
  `playback_ticks` is never used as the live clock.
- Query bare `demo_goto` for current playback position. Timed captures, camera
  previews and frozen playback can also use verified replay metadata without a
  live tick. Replay-timed captures, seeking and normal playback require one.
- Make freecam capture the main workflow: Start path here, Add camera here and
  Replace selected camera. Timed shots default to three seconds between views
  and smooth spline interpolation. Existing coordinates and timing settings are
  collapsed; FOV, bank and arrival time stay visible.
- Add optional Ctrl+Alt+K capture while the launched game has foreground focus.
  The shortcut skips busy operations, path playback and modal dialogs. Capture
  works from a paused or playing replay and leaves it paused for the snapshot.
- Preserve an existing path on failed or canceled capture. Replacing a selected
  view retains its timestamp. Play path restarts at zero when the selected time
  is at the end of the path.
- Add metadata, live tick, capture workflow and shortcut regression coverage.
  The complete suite passes 162 tests.

The user's 0.1.2 export confirms unlocker completion for 626 commands and 2,896
cvars before replay loading. The new live-tick query, visible camera controls
and native Windows shortcut still require a local game test.

## 0.1.2 alpha

- Correct initialization order: game launch opens the pre-lobby/hideout without
  `+playdemo`. The editor exposes Connect, Initialize unlocker in hideout, Load
  replay and Check camera support as separate ordered steps.
- Require both cvar_unhide completion summaries before enabling replay loading.
  Wait for their actual completion messages, including delayed output after the
  echo acknowledgment, up to the initialization request deadline.
  Checking camera support no longer executes cvar_unhide after the demo loads.
- Use Netconsole by default, matching the successful connection in the user's
  0.1.1 diagnostics. Keep VConsole selectable for separate testing.
- Treat missing unlocker output as an unconfirmed result, not proof of a missing
  DLL. Retain bounded raw console history to expose delayed or unrelated output.
- Include recent sessions in diagnostic exports and record game exit codes/times
  in new journals. A later successful launch no longer hides older launch logs.
- Preserve -dev/-insecure and gameinfo backup/restoration. Initialization restores
  the original gameinfo on disk before the selected replay is dispatched.

The first recorded 0.1.1 failure was the Steam-running check, before process
creation. A later VConsole process was created, but its exit details were absent
from the attached diagnostic ZIP; the reported game crash is not yet diagnosed.
Netconsole echo was successful, while version and cvar_unhide returned empty.
The corrected startup order and actual camera effects still need a local test.

## 0.1.1 alpha

- Fix the launcher rejecting installations whose executable is `deadlock.exe`.
  The user's explicitly selected supported executable is retained. Folder and
  Steam discovery support both `deadlock.exe` and older `citadel.exe` layouts.
- Update running-game checks to recognize both names, including the recovery
  and pre-launch checks. Required `-dev -insecure` launch options remain enforced.
- Report missing executable, gameinfo and server files more precisely.
- Retain launch inputs and the error in diagnostics even when startup fails
  before a session is created. Background UI failures now reach the disk log.

The reported 0.1.0 failure occurred during installation validation, before any
game process was started or any game configuration was modified. Use the same
selected `deadlock.exe` and replay paths in 0.1.1. A non-C: Steam library is valid.

This fix does not establish Windows game startup, unlocker compatibility, or
visible camera behavior. Those remain first-run checks described in README.md.

## 0.1.0 alpha

Initial camera-path editor, replay controller, console transport, cvar tracks,
development launcher and recoverable unlocker mount.
