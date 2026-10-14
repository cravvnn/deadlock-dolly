# Deadlock Dolly 0.6.45-alpha

Compatibility update for Deadlock build 6779, including the DX11 export-startup repair.

## Changes

- **Reliable export startup.** *Before:* switching away from Deadlock or using Dolly's desktop controls could block the first recording frame and produce a misleading DirectX 11 error. *After:* recording starts from available game frames even when another window has focus.
- **Clearer recording errors.** *Before:* a timeout waiting for the first frame looked like missing DirectX 11. *After:* the message explains the frame timeout and suggests restoring the game window, retrying, and exporting diagnostics if it persists.

## Game update compatibility

Compatibility update for Deadlock build 6779 (October 13, 2026).

- **Before:** the game update changed the module identities and several private
  addresses used by Dolly. **After:** reviewed startup, replay-camera, Follow,
  audio, effects, particle, and Players scene profiles recognize build 6779.
- **Before:** new intro-state numbers and a moved hero-component field invalidated
  saved replay/HUD checks. **After:** those checks use the verified current game
  definitions, including the updated controller initialization span.
- **Before:** the Free Cam damage/death and screen-particle filter had no reviewed
  entry for recent builds. **After:** build 6779 has its own verified controller,
  render-call and screen-option entry; POV and unrelated effects keep normal behavior.
- **Before:** the experimental native prop probe relied on a common byte prefix.
  **After:** it is restricted to its previously reviewed build. The supported
  Object Picker continues to use its existing proxy objects.

The bundled unlocker was rebuilt with the exact reviewed tier0/server pair.
Existing reviewed module support and secure-mode/cleanup guards are retained.

The supported Object Picker retains proxy objects; native game-model prop creation remains experimental.

## Validation

The regression reproduces the first-frame timeout on the old code and writes frames successfully with the fix using a synthetic DX11 WARP game window. It also checks camera handoff, subsequent focus changes and session disconnect. All 28 enabled native checks pass; the hardware encoder stress test is excluded. The full Python suite passes: 2,014 tests, 22 skipped.

## Limits

The fix has been verified locally with synthetic rendering and recording. The affected user's export still needs confirmation on the updated build. A minimized or stalled game that stops rendering can still time out.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe, or use Dolly's updater once the release is published. Keep the previous installation until your workflow is checked. Python and Tcl/Tk are bundled.
