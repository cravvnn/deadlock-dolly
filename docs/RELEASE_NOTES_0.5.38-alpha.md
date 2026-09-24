# Deadlock Dolly 0.5.38-alpha

## Depth exports with reduced render scale

Depth exports now temporarily use 100% render scale. The game can render a
smaller scene inside its depth texture and overwrite it during spatial
upscaling. Previously, this could discard every frame with a
`depth function=ALWAYS` error. Dolly now uses the verified full-resolution
scene pass instead of accepting unverified depth data.

The color/depth take and its selected World, Players and Effects passes inherit
the same render scale. The previous scale is restored after finishing,
discarding or a failed start. Window resolution, anti-aliasing and the selected
upscaling mode stay as configured. Full render scale can make export slower;
the Export tab explains this behavior.

## Verification and scope

The reported depth error was reproduced on the supplied replay with 66.67%
render scale, spatial upscaling, and a short test shot at 600 FPS, fixed step,
speed 1. With the fix, the same setup produced 302 decoded color frames and
302 decoded depth frames with identical timestamps and no reported drops.
The scale was verified at 100% during capture and restored to 66.67% afterward.
The game exited cleanly and saved configuration bytes were restored exactly.
Separate World and Players takes also decoded to 302 frames with matching
timestamps; the Players MOV contains a real alpha channel. World exited
cleanly. The Players capture completed and restored render scale, but its CPU
alpha encoding outlasted the test's 240-second process limit: the watchdog
closed the game and the encoder finished afterward. Configuration restoration
passed; this Players run is not claimed as a clean-exit end-to-end pass.
The reporting user's exact failing shot and PC have not been retested.

Regression coverage checks scale restoration, failed setup, discard, a rejected
second start, restoration retry, asynchronous failure cleanup, and inheritance by the additional passes.
The existing hardware encoder and depth-scene smoke-test exclusions remain.

Separate out-of-memory and access-violation reports remain unresolved and are
not claimed fixed by this release.
