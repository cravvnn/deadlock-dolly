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
