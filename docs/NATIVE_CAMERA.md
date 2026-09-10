# Native camera and editor — 0.4.1 alpha

The Native driver moves authored path playback into Deadlock's main-view setup.
The editor sends the complete shot before playback starts. The native helper
then evaluates and applies position, rotation and aspect-ratio framing for
each main rendered view, using game time. Camera delivery no longer depends on
the editor sending a new position command for that frame.

This is an experimental implementation. Automated tests and inspection of the
supplied game binaries do not establish in-game smoothness or stability. The earlier native path renderer has user-reported smooth panning. Stage 1 adds native manual input and a DirectX 11 panel; those additions need Windows and in-game validation.

## Choose the driver before launch

Use **Home → Play replay** for automatic native startup. Dolly waits for fresh
hideout rendering, verifies the unlocker before opening the selected demo,
checks the camera, closes the startup console, and enters paused native flight.
Home troubleshooting retains the manual sequence and Console legacy driver.
The launcher retains `-dev -insecure -console` and local replay checks.

Native supports only the exact `client.dll`, `engine2.dll` and `tier0.dll` files inspected
for this release. Dolly checks their SHA-256 hashes before native launch.
A Steam update can change any of these files and make this profile unsupported.
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
| Supported DOF cvars | Native curves evaluated and read back at the camera phase |
| Other cvars | Use Console mode; Native rejects unsupported shot variables |
| Updates / s | Editor monitoring rate; native camera and DOF follow rendered views |
| Smoothing | Disabled; the older temporal filter applies only to Console |
| Manual paused camera | Native input and real-time movement evaluated during each main-view callback |
| In-game controls | DirectX 11 panel uses shared actions and acknowledged camera snapshots |

The saved path geometry and angle modes are preserved. This version does not
replace authored Euler rotations with quaternion curves or add HLAE's entire
feature set. The native callback addresses when the view receives the camera;
it does not guarantee constant path speed or repair dropped recorded frames.

Supported DOF curves now run in the native view callback. See
[NATIVE_EFFECTS.md](NATIVE_EFFECTS.md) for the seven supported variables, curve
modes, restoration behavior and the live test checklist.

## First in-game test

1. Build the Windows package through **Actions → Build Windows app → Run
   workflow** and extract the complete Windows ZIP. Keep `Dolly.exe` and
   `_internal` together. The source/update ZIP is not an executable patch.
2. Use **Home → Play replay** and wait for paused camera readiness. F8 opens
   the panel, F7 opens the original console, and F9 opens the game replay UI.
3. Use two nearby cameras with the same angle for a straight moving path.
   Play at **Speed 1**, then **0.1**. Watch a stationary wall or building edge
   for positional stepping. Keep your recording setup unchanged.
4. Play a path that combines left/right panning with up/down aiming at both
   speeds. Then check an aspect-ratio change and, separately, a DOF track.
5. Watch the last camera at completion. Confirm the replay pauses, the HUD
   returns and the camera does not jump. Also press **Pause** partway through
   a run and check the same handoff.
6. Open **Paused camera…** and test movement, **Previous / Next**, and capture
   again. Native input must resume without a height jump or delayed mouse movement.
7. Export diagnostics immediately after a problem. Note the speed, whether
   it was straight movement or rotation, and whether it happened during the
   path or when control returned to the free camera. A short recording helps.

To compare the same shot using Console, close the native editing game session,
select **Console (legacy)**, relaunch and repeat it. Changing the selector does
not switch the driver inside an already running game.

## Returning control to the free camera

Shots with native DOF keep the final camera and effects held until Play or
Stop, which restores their snapshot. Camera-only shots retain the verified
handoff below.

Completion and cancellation hold the native view while Dolly pauses the replay
and positions the underlying free camera. Dolly checks fresh view samples from
before the native override, including wrapped angles, before releasing control.
This avoids treating the overridden visible position as proof that the underlying
camera has caught up.

If the underlying camera does not settle within the bounded check, Dolly leaves
the final view held without reporting a failed shot. **Play shot** releases
that override before the normal start seek and fresh position calibration.
**Stop / restore** releases it and returns control to the game, whose spectator
view can be elsewhere. Release acknowledgement is required before another
writer starts; a release timeout still stops the operation.

**Pause** can retain a held view. If paused movement or capture reports an
unsettled handoff, use **Stop / restore** first, then enter those controls.
Stage 1 native manual movement seeds from the displayed camera and uses wall
time while the replay is paused; it does not use console position streaming.

## Native editor input and panel

The bridge uses ABI 3; the desktop editor, native helper and build metadata
must be upgraded together. Manual movement uses normalized local camera axes,
Z-up, real elapsed time and bounded raw mouse deltas. Focus/owner transitions
clear held input and pending mouse movement. Console and original replay UI
modes pass ordinary game input through. F7 remains reserved for the console.

The desktop and overlay share action IDs, persistent bindings, selected view,
shot state and an acknowledged event queue. Capture events include the camera
pose and replay tick at the input event. No render callback waits for Tk or a
console response. Authored path playback still evaluates the complete shot
natively; the new UI does not replace that renderer with frame messages.

The overlay hooks the presenting DX11 swapchain, uses a separate Dear ImGui
context, restores D3D11 context state, and releases backbuffer references on
resize. These are compatibility measures, not a claim of verified ReShade
support. This is a user-mode helper in Dolly's launched game process, with no
kernel driver. It requires the existing reviewed game fingerprints.

Full in-world camera markers, spatial curve editing, video export and isolated
render passes are subsequent stages. See STAGE1_TESTING.md before release.

## Build and validation boundary

The existing GitHub Windows workflow builds both `DollyNative.dll` and
`Dolly.exe`, runs automated checks, and packages the helper with the application.
It does not launch Deadlock or publish a release. The supplied source and GitHub
update ZIPs contain source/build files; they do not contain a Linux-built EXE.
See [BUILDING.md](BUILDING.md) for the Windows toolchain and
[VALIDATION.md](VALIDATION.md) for the checks completed for this revision.
