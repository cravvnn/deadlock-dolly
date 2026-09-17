# Stage 2 test notes — 0.4.8 alpha

Use the complete matching Windows package and a local replay in DX11.
Stage 1 startup, cleanup, console, game UI and capture checks still apply.

## Recorded packets and frozen native preview

- Use a completed tv_record recording. Capture a shot starting at a paused
  tick between packet records and Play shot. The activity log should state the
  requested/actual tick and skipped fraction; saved camera/effect times stay intact.
- Repeat from an exact recorded packet. No boundary shift should be reported.
- At a nonpacket paused tick, run Frozen preview. The scene must stay at that
  tick without a seek, console calibration, or demo_resume. Repeat at 0.1x.
- A shot ending before the next packet should explain why it cannot start;
  it must not resume the demo or silently move keys. Preview without seeking
  remains available. Normal playback's legacy calibration recovery may still
  reject an unavailable adjacent tick; export diagnostics for that separate case.
- Compare original/applied poses in native_editor_runtime.view_history with
  input/graphics sampled_at times if frame rate drops. This is observational
  data, not an independent measurement of engine camera globals.

## Held-camera return and replay names

- Finish a shot, open F8, then close it while paused. Mouse look and WASD should
  resume without an F9 round trip. Repeat after Preview selected view and Stop.
- Hold F8 through the transition: one handoff should occur. While an operation
  is busy, keep the panel available; active paths/frozen previews must continue.
- Play several shots at 0.1x. End-of-shot handoff waits for a rendered pause;
  a late console pause must not produce the old stale-tick error.
- Test a completed custom recording with a dotted filename, such as
  practice.session.01.dem. Check startup, capture, playback and restart.
- For a rejected tv_record demo, retain its exact filename and Export diagnostics
  immediately after failure. Check whether ordinary Deadlock playdemo opens the
  same file. A short completed .dem helps reproduce content-specific failures.

## Startup mouse and crash diagnostics

- Launch a fresh replay with Play replay. Before playing a shot or using F9,
  check mouse look, arrow look and WASD in paused flight.
- Open/close F7, F8 and F9, then tab out and return. Check mouse look resumes
  without a jump or lost held input. Stop should restore the original HUD/cursor.
- Capture a first view, advance and pause the replay, then capture a second
  view. Check with Show path guides enabled and disabled as separate runs.
- If frame rate collapses, export diagnostics before restarting Dolly where
  possible. Preserve the matching newly written game .mdmp if the game exits.
  The renderer overflow is not considered fixed in this build.
- Input diagnostics distinguish received relative packets from consumed motion.
  cursor_clipped records Dolly's last successful cursor request, not an
  independently observed OS clipping rectangle. Input and graphics sampled_at values use the same monotonic observation clock;
  they are read times, not exact native event times. Graphics ABI 2 adds guide,
  draw and original-Present timing; older snapshots lack those observations.

## Playback controls

- Set 0.1x and 120 Updates / s in F8. Confirm the desktop shows the same values.
- Set 0.5x and 30 Updates / s on the desktop. Reopen F8 and confirm both values.
- Play a timed shot. Check elapsed replay time follows the chosen speed and
  camera motion stays smooth. Controls are disabled during path playback.
- Choose desktop dropdown values by mouse and keyboard. The commit highlight
  clears; manual typing and intentional text selection remain usable.
- Native Updates / s is monitoring frequency, not video output FPS.

## Path guides

- Capture at least three cameras at different heights, angles and replay ticks.
- Fly away while paused. Confirm numbered camera glyphs and the spline match
  the recorded views. Select another view and confirm its marker turns gold.
- Toggle Show path guides in F8. Check small/large windows and changed aspect.
- Rotate behind a camera/path point: no screen-wide line or near-plane streak.
- Guides hide for Play shot, running replay, F7, F9, loss of focus and Stop.
  Enter paused flight again to see them. No guide should enter a shot recording.
- Reopen/edit/delete a shot and confirm obsolete path guides disappear.
- Large shots use reduced marker density; the selected camera remains included.
  Glyphs indicate position/direction/aspect and draw through walls; no occlusion,
  exact lens cone or live camera image is claimed.
- Alternate F8/F9/F10 and fly for several minutes. Check frame rate and export
  diagnostics if there is a stall, crash, missing guide or input issue.

## Range depth of field

- On desktop Effects, use + Range DOF. Edit a key to four values, such as
  -100 0 180 2000, then alter each component at another time and play the shot.
- Check a frozen preview, selected-view preview, second playback and Stop.
  All four values should stay at the camera phase and restore together.
- Save/reopen the shot. Vector shots use format 3; test an older scalar shot too.
- Confirm one/two/three/five values, NaN, infinities and console command text
  receive validation errors. Four zeros turns off direct range override.
- Check the look under the intended game graphics settings. ABI/readback
  checks do not prove image quality or guarantee improved anti-aliasing.
- New override controls require the reviewed September 9 tier0 build. Older
  reviewed builds retain the original seven native DOF controls.

## Remaining stages

Expanded in-game position, rotation and framing curve editors remain Stage 2.
Video export and depth/world/hero/effect passes remain Stage 3. ReShade
coexistence is unverified and should be checked separately on the intended
version/preset after these DX11 checks pass.
