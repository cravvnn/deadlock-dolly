# Native camera playback — 0.3.8 alpha

The Native driver moves authored path playback into Deadlock's main-view setup.
The editor sends the complete shot before playback starts. The native helper
then evaluates and applies position, rotation and aspect-ratio framing for
each main rendered view, using game time. Camera delivery no longer depends on
the editor sending a new position command for that frame.

This is an experimental implementation. Automated tests and inspection of the
supplied game binaries do not establish in-game smoothness or stability. The
first live test is still needed; this release does not claim the jitter is fixed.

## Choose the driver before launch

In **Session**, **Camera driver → Native (experimental)** is the default.
Keep Netconsole selected and follow the normal sequence: launch the hideout,
connect, initialize the unlocker, load the replay, then check camera support.
The launcher retains `-dev -insecure` and the selected local replay checks.

Native supports only the exact `client.dll` and `engine2.dll` files inspected
for this release. Dolly checks their SHA-256 hashes before native launch.
A Steam update can change either file and make this profile unsupported.
If that happens, select **Console (legacy)** before launching. Do not replace
the game's DLLs or edit the hash checks to force a different build to load.

The driver is fixed for the launched game session. Close that game and launch
a new editing session to change drivers. Your saved shot works with either.
The original game DLLs are not included in a Dolly release.

## What changes

| Control | Native playback behavior |
| --- | --- |
| Position and rotation | Evaluated and applied during each main-view callback |
| Aspect-ratio framing | Applied to the main view using the saved framing curve |
| DOF and other cvars | Console updates follow sampled native time; best effort |
| Updates / s | Controls effect-cvar updates, not the native camera's render rate |
| Smoothing | Disabled; the older temporal filter applies only to Console |
| Manual paused camera | Existing movement, switching and capture implementation retained |

The saved path geometry and angle modes are preserved. This version does not
replace authored Euler rotations with quaternion curves or add HLAE's entire
feature set. The native callback addresses when the view receives the camera;
it does not guarantee constant path speed or repair dropped recorded frames.

DOF and other cvars are **not synchronized to every rendered frame**. They use
the native camera's sampled shot time but still depend on console delivery.
Start by testing the camera without animated effects, then add them back.

## First in-game test

1. Build the Windows package through **Actions → Build Windows app → Run
   workflow** and extract the complete Windows ZIP. Keep `Dolly.exe` and
   `_internal` together. The source/update ZIP is not an executable patch.
2. Launch with **Native (experimental)** and finish the normal hideout/unlocker
   setup. Enter the replay's free camera before **Check camera support**.
3. Use two nearby cameras with the same angle for a straight moving path.
   Play at **Speed 1**, then **0.1**. Watch a stationary wall or building edge
   for positional stepping. Keep your recording setup unchanged.
4. Play a path that combines left/right panning with up/down aiming at both
   speeds. Then check an aspect-ratio change and, separately, a DOF track.
5. Watch the last camera at completion. Confirm the replay pauses, the HUD
   returns and the camera does not jump. Also press **Pause** partway through
   a run and check the same handoff.
6. Open **Paused camera…** and test movement, **Previous / Next**, and capture
   again. These working controls should behave as before after handoff.
7. Export diagnostics immediately after a problem. Note the speed, whether
   it was straight movement or rotation, and whether it happened during the
   path or when control returned to the free camera. A short recording helps.

To compare the same shot using Console, close the native editing game session,
select **Console (legacy)**, relaunch and repeat it. Changing the selector does
not switch the driver inside an already running game.

## Returning control to the free camera

Completion and cancellation hold the native view while Dolly pauses the replay
and positions the underlying free camera. Dolly checks fresh view samples from
before the native override, including wrapped angles, before releasing control.
This avoids treating the overridden visible position as proof that the underlying
camera has caught up.

If the underlying camera does not settle within the bounded check, Dolly leaves
the view held and blocks competing camera controls. Keep the replay paused and
use **Stop / restore** to retry. Export diagnostics if it persists. Close the
editing game session if you need to leave that held state. This behavior needs
the live completion/cancellation checks above.

## Build and validation boundary

The existing GitHub Windows workflow builds both `DollyNative.dll` and
`Dolly.exe`, runs automated checks, and packages the helper with the application.
It does not launch Deadlock or publish a release. The supplied source and GitHub
update ZIPs contain source/build files; they do not contain a Linux-built EXE.
See [BUILDING.md](BUILDING.md) for the Windows toolchain and
[VALIDATION.md](VALIDATION.md) for the checks completed for this revision.
