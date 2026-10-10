# Deadlock Dolly 0.6.37-alpha

Restores Dolly startup and camera support after Deadlock build 6774 (October 9).

## Changes

- Updated the reviewed client compatibility profiles and unlocker for the exact game build.
- Fixed replay seeks that could time out when Deadlock stopped four ticks past the requested tick. Dolly confirms the overshoot and allows one corrective seek while retaining cancellation, the existing deadline and exact-target verification.

## Validation

The isolated release build passed 1,925 Python tests (24 skipped) and all 26 enabled native checks. The hardware-encoder smoke test was excluded. Independent verification covered 55 client code/schema records and the installed module fingerprints.

Portable application startup, updater install/rollback, archive integrity, embedded source/native identities and two packaged FFmpeg mux/frame-alpha checks passed.

One bounded public-Dolly replay check at the saved normal 2560x1440 resolution, using the exact packaged native DLL, passed normal startup/preload, exact seeks to ticks 15300/15301, Game Follow, POV preparation/finish without recording, and Native/Citadel DOF guards. Runtime settings and all 43 saved configuration files were restored, the temporary deployment was removed and the game exited normally in 88.0 seconds. The previously observed Steam networking assertion recurred; no new matched incident was found.

## Limits

This was a compatibility and workflow check, not visual certification of every output. It did not record game footage or validate new-hero appearance, Players/Depth output, audio capture or DOF appearance. Existing Players appearance limitations remain.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe. Keep the previous installation until your workflow is checked. Python and Tcl/Tk are bundled.
