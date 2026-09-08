# Previous packaging and camera validation

This file preserves validation from earlier deliveries; see VALIDATION.md for the current release.

# Validation — 0.3.6 alpha

Date: 8 September 2026.

The supplied 0.3.5-alpha diagnostics and video establish that paused camera
movement works again and that small visible steps remain during panning.
This update fixes a separately measurable endpoint jump. **No Windows executable
was built or native Deadlock session run in this workspace.** The existing
GitHub Windows gates and an in-game comparison remain necessary.

## Diagnostics and video findings

The last path has three keys, starts at tick 112181, spans 2.609375 replay seconds,
and runs at 0.1 speed with 120 requested camera updates per second.

- Playback completed 3,014 updates in 26.008 seconds: 115.89 updates/sec. Mean
  command round trip was 6.04 ms, maximum 13.81 ms, and the largest received-update
  interval was 18.71 ms. The precise clock and Windows timer were active.
- The retained 256 samples contain no repeated commanded times or camera poses.
  Before completion, yaw velocity stays between 6.64 and 7.28 degrees/sec and
  pitch between -1.49 and -1.40 degrees/sec. There is no command reversal there.
- At completion, yaw moves 0.27350 degrees in 10.02 ms: about 27.3 degrees/sec,
  versus roughly 7.1 immediately beforehand. The replay reaches tick 112348
  while the filtered camera is still short of its last key. The old code forces
  the exact endpoint early, producing that larger step.
- Visible readbacks generally follow the commanded angles within 0.09 degrees
  in the retained tail, apart from the endpoint. Position readbacks trail by
  approximately 145–165 ms, about one replay tick at 0.1 speed. There is no
  sustained drift proving that the native player camera took over.

The supplied 60-second, 2560x1440 recording was downloaded and inspected. A
bounded optical-flow check follows static upper-building features through 1,620
frames between 17.5 and 44.5 seconds. The continuous path starts around 20.8s;
earlier jumps are setup. Near 21.63–21.68s, consecutive background horizontal
steps measured at 640px analysis width are approximately -0.73, -0.34, -1.09 and
-0.73 pixels. This confirms a short step followed by catch-up movement.

No identical whole frames were found in the continuous 21–44s segment. Uneven
console-command application, rendering or recording cadence is plausible, but
these observations cannot isolate its source. Perspective and parallax also
limit frame-based motion estimates. This release does not claim to eliminate
all mid-path judder or prove native-camera takeover.

## Paused movement is preserved

The retained paused session passes direct XYZ movement and return checks with
unit gains and zero residual. Its 2.03 updates/sec average includes idle time:
18 intervals within active movement bursts range from 9.86 to 24.58 ms, median
13.84 ms. The trace does not show long active-input stalls or timestamp window
focus changes, so it cannot establish an Alt-Tab regression.

One earlier preparation fails on a small height limit cycle: desired Z 363.5,
readbacks alternating 362.5 and 364.5. The next attempt captures 364.5 and passes.
The working calibration and its tolerances are preserved rather than loosened.
The user explicitly requested retaining working paused movement unless a clear,
low-risk focus-related correction was established.

Existing input behavior requires releasing keys held during a focus change
before pressing them again. The path's temporary background-throttle setting
and the paused controls' existing behavior are retained.

## Implemented change

Normal playback uses the same continuous camera clock through its final
fraction. Successful completion requires both the acknowledged replay time and
camera time to reach the last key. The exact final position, angles, framing
and other tracks are still applied before normal pause and HUD restoration.

A bounded endpoint observation period prevents chasing a stalled camera clock:
two replay-tick periods, clamped to 0.25–2 seconds. At 64 ticks/sec and 0.1 speed,
that budget is 0.3125 seconds. Blocking console calls can extend wall-clock time.
If completion fails, ordinary cleanup runs and no forced final-view jump occurs.
Cancellation, selected-demo identity and external-seek checks remain enforced.

The replay continues during the small finishing fraction, so normal completion
can occur slightly after the first end-tick acknowledgement. Diagnostics record
the initial lag, budget, elapsed time, clock readiness and successful final write.
They do not mark completion verified merely because the camera clock is ready.

## Regression verification

`python3 -m unittest discover -s tests -q`: **499 tests passed** on Linux,
Python **3.12.13**, in **53.936 seconds**. No tests were skipped.

Six new tests exercise the real controller with a timed console simulation:
all four diagonal pan directions, actual replay acknowledgement, cancellation,
changed demo, excessive phase lag and failure of the final camera write.

A simulated 0.1-speed shot previously ended with a 0.4344-degree yaw step after
steps near 0.0995 degrees. With this change, the final sequence stays near
0.1005 degrees and ends with a 0.0328-degree remainder. Completion takes 33 ms
longer in that simulation. The original motion-clock regression now also
limits the final angular step. The exact-endpoint test uses a realistic one-tick
acknowledgement instead of a 100-tick leap; large phase jumps have an explicit
bounded-failure regression.

The existing height-compensation worker test now advances at the actual replay
tick rate for the whole shot. It verifies at least 600 interpolated camera
writes and the exact final height instead of relying on a forced endpoint after
several large tick jumps. Its compensation and no-extra-query assertions remain.

Only the controller's path-playback worker changes. Every paused-control,
calibration, focus/input and seek method is unchanged from 0.3.5, as are authored
curves and command transport. Source comparison verifies that boundary. The
logo, unlocker, build workflow and dependencies are unchanged.

The source manifest contains 83 files. All 51 Python files and the PyInstaller
spec parse under Python 3.10 syntax rules. Diagnostic logs, video, optical-flow
tools and temporary analysis dependencies are excluded from the release.

## Native comparison

1. Apply the source update to the existing repository and start a fresh
   **Actions → Build Windows app → Run workflow** on the updated branch.
2. Download the new artifact and extract its complete
   `Deadlock_Dolly_0.3.6-alpha_Windows_x64.zip`. Use the complete portable folder,
   including `_internal`, after closing the older editing session and completing
   any pending game-configuration recovery.
3. Play the same saved shot at 0.1 speed and 120 updates/sec. Compare the final
   pan into the last key separately from the small steps in the middle.
4. Briefly confirm paused movement still works. After Alt-Tab, release and
   press movement keys again; the existing focus guard ignores held keys.
5. If mid-path stepping remains, export diagnostics immediately after that shot.
   The renderer and recording are not synchronized with this console backend,
   so this narrow endpoint correction cannot promise frame-perfect motion.

See [BUILDING.md](BUILDING.md) for GitHub instructions and
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md) for previous reports.
No GitHub repository or release was modified by this work.

# Validation — 0.3.5 alpha

Date: 8 September 2026.

The supplied diagnostics come from a running **0.3.4-alpha** executable. They
show a failure while preparing paused camera controls, before any camera
position command or manual movement update. This revision repairs that seek
failure. **No 0.3.5 Windows executable or native Deadlock session was run here.**
The existing GitHub Windows test/build/icon/editor gates must run for this commit.

## What the new diagnostics establish

All five attempts complete the backward part of the adjacent-tick refresh but
stop two ticks late on the forward return:

| Attempt | Exact backward tick | Requested return tick | Observed return tick |
| --- | ---: | ---: | ---: |
| 1 | 112331 | 112332 | 112334 |
| 2 | 112333 | 112334 | 112336 |
| 3 | 112335 | 112336 | 112338 |
| 4 | 112381 | 112382 | 112384 |
| 5 | 112383 | 112384 | 112386 |

The final attempt reports 112386 in 299 consecutive status responses, never
112384, before timing out. Earlier attempts were cancelled. Some successful
backward seeks briefly report one tick ahead and then settle correctly; an
ahead reading alone is insufficient evidence to retry.

No outgoing `spec_goto` position commands, camera-calibration messages or manual
movement samples appear in these attempts. The upward pop reported by the user
is consistent with the engine changing its view during the refresh and Dolly
failing before restoring the captured view. The diagnostics do not establish
which input handler supplied the remaining arrow-key rotation.

The replay and unlocker reached their ready state. Diagnostics confirm the
0.3.4 motion-clock change is active: `perf_counter`, `QueryPerformanceCounter()`,
reported resolution 0.0000001 seconds. This recording contains no path playback
samples, so it does not establish whether the earlier rotation jitter improved.

## Implemented correction

1. Observe at least three identical status readings one or two ticks beyond
   the original seek target.
2. Pause again, recheck the selected demo and tick, and honor cancellation.
3. If the same overshoot is still present, reissue the **same exact target once**.
   That command now takes a backward-seek route, which lands exactly in the log.
4. Require three exact target readings, another pause, and three more exact
   readings before reporting success. Original-view restoration and the existing
   direct XYZ movement proof must then pass before paused controls are enabled.

A target that settles naturally during confirmation gets no corrective seek.
Unexpected movement during confirmation aborts recovery. Large, alternating or
lower tick observations do not trigger correction. One unsuccessful correction
does not retry again, reset the deadline or relax the exact-tick requirement.
The existing 15-second observation deadline is shared across attempts; blocking
console calls can extend actual elapsed time beyond that observation budget.

Diagnostics retain the original overshoot samples, source tick, requested target
and correction timestamp even when the rolling seek samples advance. Cancellation
and replay identity are checked after blocking replies and before correction.

## Regression checks

`python3 -m unittest discover -s tests -q`: **493 tests passed** on Linux,
Python **3.12.13**, in **53.778 seconds**. No tests were skipped.

Eighteen new tests exercise the real controller using a simulated console whose
forward seeks stop one or two ticks late and whose backward seeks land exactly:

- Original view restoration, exact original replay moment, unchanged 0.1
  timescale, and movement in both directions on all three axes.
- Saved-camera switching at the current tick with authored framing and bank.
- General forward seeks, six exact readbacks across pause, retained diagnostic
  evidence, transient observations and natural settling during confirmation.
- At most one correction, one observation deadline, and no correction for
  large, alternating or lower observations.
- Cancellation while settling, at confirmation, after correction and during
  final verification; changed demos and unexpected confirmation movement.
- A corrected replay tick still cannot enable a camera with weak translation.

These tests simulate command/status behavior; they do not model native render
frames. The original 0.3.4 overshoot behavior reproduced failures before the
production fix. Existing regression tests and Windows build gates are retained.

The 50 Python files and PyInstaller spec parse under Python 3.10 syntax rules.
The source manifest contains 82 files. All production modules except the
controller and version string remain byte-identical to 0.3.4. The logo, unlocker,
workflow, build scripts, dependencies and existing tests are unchanged.

## In-game checks after rebuilding

1. Upload the source update at the existing GitHub repository root, commit,
   and start a fresh **Actions → Build Windows app → Run workflow** on that
   branch. Re-running an older job builds its old commit.
2. Download the new artifact and extract its complete
   `Deadlock_Dolly_0.3.5-alpha_Windows_x64.zip`. Close the old editing session,
   complete any pending game-configuration recovery, and use the whole rebuilt
   portable folder, including `_internal`.
3. At the troublesome scene, pause and enter freecam. Frame a low camera view.
   Start Paused camera controls with movement keys released. Let setup finish;
   verify the exact replay moment and original view return after the refresh.
4. Test WASD and Space/Ctrl after enabling keyboard flight with Deadlock focused,
   then stop and start controls again. Switch to a saved view while paused.
5. Repeat with the intended 0.1 replay speed. If setup still fails or the camera
   jumps afterward, export fresh diagnostics immediately before retrying.

Refresh can briefly show a neighbouring scene. Cancellation deliberately stops
further commands and can leave the replay at a neighbouring or overshot tick.
Continuous manual movement itself neither seeks nor resumes the replay.
This release targets the paused-startup regression; unchanged rotation curves
and console-based playback still require native footage to assess smoothness.

See [BUILDING.md](BUILDING.md) for step-by-step GitHub updates and
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md) for earlier validation.
User recordings, logs and diagnostic exports are excluded from the archives.
No GitHub repository or release was modified by this work.

# Validation — 0.3.4 alpha

Date: 8 September 2026.

The user reports successfully building and running the 0.3.3 Windows executable.
The newly supplied diagnostics establish that the app connected to its replay
and ran camera playback. This release repairs issues found in that recording;
**the 0.3.4 executable and native camera behavior have not been tested here**.
The GitHub Windows test/build/icon/editor gates must run for this revision.

## Findings in the supplied executable diagnostics

The last recorded path used three camera keys, start tick 112097, 64 ticks/sec,
2.375 shot seconds, playback speed 0.1 and 120 requested updates/sec.

- The retained 256 camera samples contained 88 zero-length send intervals,
  105 intervals of 16 ms and 62 of 15 ms. There were 87 consecutive repeated
  shot times and complete camera poses. The high-resolution wait timer was
  already active. The movement clock was still coarse.
- CPython 3.12 on Windows uses `GetTickCount64` for its monotonic clock and
  `QueryPerformanceCounter` for its performance clock. The former's usual
  resolution is 10–16 ms. All controller movement, observation and deadline
  timestamps now use the performance clock consistently.
- The authored yaw keys unwrap continuously as 108.5, 43.4 and -114.1 degrees.
  Pitch/yaw angular velocity is continuous at the middle key, and 2,001 sampled
  command batches have matching angles in both camera commands. No rotation
  spline, key timing or angle-path changes were warranted by this evidence.
- Stopped paused controls retained tick 112097. A first capture after the user
  advanced to 112158 was rejected against that stale preparation; a retry
  succeeded. Capture now retires stale preparation and measures the new view.
- At tick 112249, seeking to that same tick did not reset spectator response.
  A distant preview produced about 572.5 units of residual, beyond the retained
  256-unit correction bound. Later startup checks measured about 0.245 gain:
  a commanded 22.1-unit Z change moved the visible view only 5.4 units.
- A genuine seek to 112097 produced a verified 16-to-16-unit response on all
  three axes. This supports trying an adjacent-tick seek and verified return;
  it does not prove that every native Deadlock state will recover that way.

## Implemented behavior

- High-resolution motion timestamps are shared by path playback, frozen
  preview, manual paused movement, readback scheduling and motion metrics.
  Diagnostics report the clock name, implementation and resolution.
- Paused preparation captures the intended view before an adjacent-tick round
  trip. It returns to the original tick, restores that view and then verifies
  direct XYZ response and return before enabling movement.
- A measured position failure after Play/Seek can invoke one such recovery.
  A failed lens readback, cancellation or changed replay does not retry.
- Capture after a completed external seek succeeds on its first fresh read.
  The old movement preparation is invalidated; movement still needs calibration.
  A replay changing during capture is rejected instead of mixing pose and time.
- Existing offset bounds, direct-response proof, drift detection, selected-demo
  checks, cancellation and dev/insecure process ownership remain enforced.
  No global interpolation cvars, memory offsets or scale guesses are introduced.

## Regression checks

`python3 -m unittest discover -s tests -q`: **475 tests passed** on Linux,
Python **3.12.13**, in **53.649 seconds**.

Fifteen new tests exercise the actual controller with simulated game transport:

| Tests | Conditions checked |
| --- | --- |
| 3 motion-clock regressions | Distinct coarse and precise clock epochs; 2 ms console latency; 120 Hz frozen rotation; 0.1-speed normal playback; paused yaw; endpoint/duration and cancellation |
| 12 paused-refresh regressions | Same-tick no-op vs actual seek; restoring current/distant views; bounded recovery; first/last tick; unavailable neighbor; permanent weak response; cancellation; changed demo; lens failure; stale and mixed-tick capture |

The motion-clock tests fail if the old coarse clock is restored in memory.
The native renderer is not simulated by those assertions. The prior Windows
short-name/invalid-filename test corrections are retained. No tests are skipped
and no Windows build gates are bypassed.

The 49 Python files plus the PyInstaller spec parse under Python 3.10 syntax
rules. The clean source manifest contains 81 files. Logo, unlocker, packaging
recipe, build dependencies, workflow, path interpolation and input modules
remain byte-identical to the previously supplied 0.3.3 build fix.

## Native checks still needed

1. Build the updated default branch with a fresh GitHub Actions run; download
   and extract the complete 0.3.4 Windows package, then launch its EXE.
2. At the troublesome paused scene, start Paused camera controls and let the
   brief seek/return and position check finish. Verify the final replay moment
   and visible starting view are preserved, then test vertical movement/turning.
3. Switch a saved camera while paused, including one far from the current view.
4. Stop controls, move the replay to another moment, and capture once. Start
   controls again before further manual movement.
5. Play the same saved rotation path at 0.1 speed, then compare 60 and 120 updates/s.
   Export new diagnostics if it jumps or a position check still fails.

A refresh may visibly flash the neighbouring scene while preparing. Cancelling
mid-refresh deliberately stops further commands and can leave the replay at
that neighbouring tick. Continuous flight itself neither seeks nor resumes.
A permanently weak camera response remains blocked. This console backend is
not synchronized with render frames, so the clock correction cannot guarantee
perfectly smooth native footage or eliminate stalls caused by game rendering.

## Sources and history

- [CPython 3.12.14 clock implementation](https://github.com/python/cpython/blob/v3.12.14/Python/pytime.c)
- [Python performance-counter documentation](https://docs.python.org/3.12/library/time.html#time.perf_counter)
- [Microsoft GetTickCount64 resolution](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64)
- [Previous validation](HISTORY_VALIDATION.md)
- [Build and GitHub update instructions](BUILDING.md)

Original diagnostics, log paths and recordings are excluded from source and
update archives. No GitHub repository or release was modified by this work.

# Validation — 0.3.3 alpha, build fix 1

Date: 8 September 2026.

The supplied first GitHub Windows run installed the pinned build dependencies
successfully, then stopped at the source-test gate: 460 tests in 46.221 seconds,
six failures and one error. No executable was produced by that run.

Build fix 1 corrects the four affected test files and updates this report and
the changelog. Production code, build scripts, workflow, logo and unlocker are
byte-identical to the originally delivered 0.3.3 source archive. No test is
skipped and no build gate is disabled.

**No Windows EXE was compiled or launched in this Linux workspace.** A fresh
GitHub Windows build must run after uploading these changes. Neither these
checks nor the future bundle smoke test establish native Deadlock camera
compatibility.

## Reported failure and repair

- Six assertions compared Windows short-name temporary paths (`RUNNER~1`) to
  canonical long paths (`runneradmin`). Expected paths now resolve the same
  location while still checking the full startup-log paths and replay command.
- One separator test tried to create a newline-containing filename, which the
  Windows filesystem rejected before the application's validator could run.
  Real files still exercise semicolon and plus-sign rejection through the
  command builder. Only filesystem lookup is simulated for quotes/control
  characters, with the real validator required to report console separators.
- An independent Linux harness used a directory symlink to reproduce path
  aliases and rejected writes of Windows-invalid filenames. The seven original
  cases reproduced **six failures and one error**; all seven corrected cases
  passed. This emulates the two reported assumptions, not Windows APIs.

## Completed local checks

`python3 -m unittest discover -s tests -q`: **460 tests passed** on Linux,
Python **3.12.13**, in **37.848 seconds** after the build fix.

This includes the previous 441 camera/editor regressions and 19 packaging checks:

| Area | Checks |
| --- | --- |
| Portable startup and paths — 13 tests | EXE/resource roots, windowless log streams, direct startup without a child Python process, visible failures, File/CLI recovery, and prevention of recursive bootstrap |
| External game environment | Native DLL-directory setup and restoration on successful/failed spawn, PATH filtering and unchanged source launch behavior; Windows API boundary simulated |
| Release packaging — 6 tests | Explicit source exports, excluded personal/runtime data, invalid paths, missing files, x64 GUI PE and nine embedded icon frames through a simulated PE resource tree |
| Retained application — 441 tests | Camera interpolation, aspect curves, controller transitions, paused movement, input, timing, startup, unlocker preparation and restoration |

All **47 Python files plus the PyInstaller spec** parse with Python 3.10 syntax
rules. The build itself deliberately requires Windows x64 and Python 3.12.
The existing source BAT files retain their Windows CRLF line endings.

The original packaging validation included an actual **Linux Tk 8.6.14** smoke
run using the new desktop entry point. It
created and updated the real Dolly editor, loaded its PNG icon fallback, read
and verified the bundled unlocker, checked data locations and closed. This
check ran from source (`frozen: false`), started no game and changed no game
configuration. It is not evidence of native Windows EXE or ICO loading.

The playback clock, path model, movement/input, hotkey, preferences, framing
graph and console modules are byte-identical to 0.3.2. Controller changes in
this revision locate portable logs; game-launch changes handle bundled resource
paths and external DLL lookup. Existing launch/recovery regressions pass.

The three logo assets and official cvar unlocker are byte-identical to 0.3.2.
The unlocker SHA-256 remains
`e86f270b1dedc81fd54a230f0080eee568a4f2bd39e1f41080dcf71d833267ba`.

## Windows build gates awaiting a fresh run

The builder and GitHub Actions workflow will:

1. Run the source regressions on Windows x64 with Python 3.12.
2. Build a windowed `Dolly.exe` with the existing ICO and version information.
3. Copy the official unlocker afterward and verify its pinned hash.
4. Inspect the actual PE architecture, GUI subsystem and all nine icon frames.
5. Relocate the complete folder to a path with spaces and start it from a
   different working directory. Create the actual editor, load its Windows
   icon and check the bundled runtime/resources without launching Deadlock.
6. Produce Windows/source ZIPs, checksums and `BUILD_INFO.json` only after those
   checks pass. The workflow creates downloadable artifacts; it does not publish
   a GitHub Release.

The reference PyInstaller Windows wheel was checked for the COPYING file used
by the license-copy step. Build dependencies are pinned in requirements-build.txt.
The icon, PE and API mock checks cannot substitute for executing this Windows gate.

## Source delivery

`SOURCE_FILES.txt` lists **79 files** for the GitHub source ZIP, including the
workflow, tests, logo and licensed plugin. Logs, diagnostic archives, replays,
recordings, private settings, virtual environments and build output are excluded.
No GitHub repository was modified by this repair. The small update ZIP contains
the four corrected tests and these two documentation files at their original
repository paths. The full source ZIP includes the same corrections.

Build and upload instructions are in [BUILDING.md](BUILDING.md). The retained
camera investigations, tests and previous visual checks are in
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md).


---

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

---

# Validation — 0.3.7 alpha

Date: 8 September 2026.

This build adds the user's requested experimental smoothing filter to camera
path playback. It also includes the 0.3.6 endpoint correction. **The filter has
been tested against simulated playback and recorded command timing, but its
visible effect in Deadlock has not yet been verified.** No Windows executable
was built or native game session run in this Linux workspace. A successful
GitHub Windows build and the native comparison below remain necessary.

## What changes

The optional filter averages the existing continuous playback phase over a
finite window measured in real time. The original project is then evaluated
once at that filtered phase. Position, pitch, yaw, bank, aspect ratio and
animated camera variables therefore remain on the same authored timeline.
Step-valued variables remain discrete. There is no independent axis averaging,
curve replacement, native offset change or future-pose extrapolation.

| Mode | Real-time window | Added camera delay during steady motion |
| --- | ---: | ---: |
| Off | 0 ms | 0 ms |
| Light | 80 ms | about 40 ms |
| Balanced | 160 ms | about 80 ms |
| Strong | 280 ms | about 140 ms |

The editor starts with Balanced selected. The selection is session-only and
does not alter saved projects or capture settings. Off preserves 0.3.6 playback.
Manual paused flight, camera capture and selected-frame previews do not use
this filter. Frozen path playback does use it when selected.

Smoothing trades camera timing for steadier progression. At 0.1 playback speed,
Balanced's nominal 80 ms real-time delay is 0.008 replay seconds. The camera and
its effect tracks stay together, but trail the unfiltered camera phase relative
to game action. A held endpoint is reached exactly within one full filter
window; startup and the final approach ease over that window.

The existing endpoint budget gains one smoothing window. At 64 ticks/sec and
0.1 speed, Balanced allows 0.3125 + 0.16 = 0.4725 real seconds after the replay
end acknowledgement. The ordinary drift, replay ownership, cancellation and
external-seek checks remain active. Blocking console calls can extend elapsed
wall time. Completion is verified only after the exact last view has been sent
and its response checked; a timeout does not force a final camera jump.

Diagnostics record the selected mode, window and nominal real-time delay.
Each retained sample records bounded raw shot time, filtered shot time,
`smoothing_delay_shot_seconds` and `smoothing_delay_ms` (real time).
`clock_estimate_tick` describes the unfiltered clock; `camera_estimate_tick`
describes the filtered camera. This distinguishes filter delay from console
round-trip or replay-clock lag.

## Evidence from the supplied recording and diagnostics

The supplied 0.3.5 shot has three keys, starts at tick 112181, spans 2.609375
replay seconds and runs at 0.1 speed with 120 requested updates/sec. It completed
3,014 updates in 26.008 seconds (115.89 updates/sec), with a 6.04 ms mean command
round trip and an 18.71 ms largest received-update interval. The precise clock
and Windows timer were active. The retained tail has no duplicate commanded
poses or reversals, but does contain small timing variations.

The previously inspected 60-second recording shows a short visual step and
catch-up during the continuous pan. Readbacks do not establish sustained native
player-camera takeover. Console application, game rendering and recording
cadence remain possible contributors. Detailed measurements, including the
separate endpoint jump corrected in 0.3.6, are preserved in
[HISTORY_VALIDATION.md](HISTORY_VALIDATION.md).

For this build, the filter was replayed over the retained command timestamps
and shot times. The forced final sample was excluded; statistics also exclude
the first 0.4 seconds of filter warm-up and the last 0.1 seconds of the tail.
These are numerical command-timing measurements, not newly rendered frames.

| Mode | Phase-rate standard deviation | Successive phase-rate change RMS | Measured steady delay |
| --- | ---: | ---: | ---: |
| Off | 0.00083362 | 0.00056958 | 0 ms |
| Light | 0.00074523 | 0.00006393 | 39.8 ms |
| Balanced | 0.00070924 | 0.00004442 | 79.6 ms |
| Strong | 0.00064916 | 0.00003166 | 139.2 ms |

Phase rates are shot seconds per real second; their means remain approximately
0.10002 in every mode. Balanced reduces phase-rate variation by about 15% and
successive rate-change RMS by about 92% in this retained trace. **Those numbers
do not mean 92% less visible camera jitter.** They establish that the requested
timing filter attenuates the small command-clock changes, with the documented
delay. No measured native improvement is claimed.

## Earlier-version comparison and implementation limits

Available source archives from 0.3.3 through 0.3.6 have identical authored-path,
replay-clock, pacing, navigation and console modules. The intervening controller
changes concern the precise clock, paused-camera preparation, seek handling
and endpoint completion. This comparison did not identify a removed mid-path
interpolation algorithm to restore. The earlier 0.3.2 source was unavailable.

Advancedfx's Source 2 implementation evaluates its camera path within a game
view callback and writes the view origin, angles and FOV there. Dolly currently
uses acknowledged external console commands. A timing filter cannot supply
missing rendered frames or guarantee when the engine applies each command.
See the primary
[Advancedfx Source 2 implementation](https://github.com/advancedfx/advancedfx/blob/main/AfxHookSource2/main.cpp).

If all modes show the same stepping in a controlled native comparison, more
filtering is unlikely to resolve that particular step. Renderer-side camera
integration would be a separate implementation requiring Deadlock-specific
investigation and native validation, rather than an assumption that CS2 offsets
or hooks apply unchanged.

## Regression verification

`python3 -m unittest discover -s tests -q`: **533 tests passed** on Linux,
Python **3.12.13**, with no tests skipped. The tests include:

- Twenty filter cases covering time-weighted sampling, jittered timestamps,
  equal timestamps, resets, finite inputs, bounded history, monotonic output,
  large clock epochs and exact endpoint flushing.
- Twelve controller integration cases covering all four diagonal pan
  directions at 0.1 speed, synchronized XYZ/rotation/aspect/DOF, discrete
  variables, endpoint completion, cancellation, replay changes, external seeks,
  the existing one-tick prediction cap, frozen playback and diagnostics.
- Editor coverage for selecting each mode, rejecting invalid values without
  disturbing paused controls, and retaining the chosen mode when work is queued.
- Existing paused movement, calibration, input/focus, capture, startup, seek,
  endpoint, packaging and playback regressions.

A separate timed-console comparison loaded the original 0.3.6 playback worker
from its archive. All console commands, duration and final status were identical
to the new worker with Off, for both normal 0.1 playback and frozen playback.
This checks the actual previous implementation as well as the API default.

Source comparison confirms that only `Controller.play` and `Controller._run`
change relative to 0.3.6. Paused-flight, calibration, input and seek methods are
byte-identical. The authored curves, launch/unlocker sequence, keybindings,
logo, Windows workflow and build dependencies are unchanged.

The explicit source manifest contains **86 files**. All **54 Python files** and
the PyInstaller spec parse under Python 3.10 syntax rules. Logs, recordings,
temporary analysis tools and dependencies are excluded from release archives.
The GitHub update is cumulative against 0.3.5, including the 0.3.6 endpoint fix.

## Native comparison before a public release

1. Apply `Deadlock_Dolly_0.3.7_GitHub_Update.zip` to the existing 0.3.5 or 0.3.6
   repository and start a fresh **Actions → Build Windows app → Run workflow**.
   See [BUILDING.md](BUILDING.md) for the upload and extraction steps.
2. Close the old editing session, complete any pending configuration recovery,
   and extract the complete new Windows package, including `_internal`.
3. Open the same saved shot. Keep playback speed at 0.1 and updates/sec at 120.
   Play it with Off, then Balanced, then Strong. Keep the capture/render settings
   unchanged and compare the same diagonal pan section. Do not change the path
   between runs. Off already includes the 0.3.6 endpoint correction.
4. Check both mid-path stepping and the last-key approach. Check framing and
   DOF timing around any action that needs an exact match. Export diagnostics
   immediately after each problematic run so its mode and samples are retained.
5. Briefly confirm manual paused flight still works. After switching focus,
   release and press movement keys again; the existing focus guard is unchanged.

The app remains an alpha pending this native comparison. Automated tests and a
successful packaged-editor startup do not establish cinematic smoothness.
No GitHub repository or public release was modified by this work.
