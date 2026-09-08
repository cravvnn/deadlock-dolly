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
