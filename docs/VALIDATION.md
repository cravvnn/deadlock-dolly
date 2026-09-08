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
