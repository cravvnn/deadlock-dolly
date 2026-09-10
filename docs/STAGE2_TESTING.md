# Stage 2 test notes — 0.4.5 alpha

Use the complete matching Windows package and a local replay in DX11.
Stage 1 startup, cleanup, console, game UI and capture checks still apply.

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
