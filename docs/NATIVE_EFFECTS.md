# Native DOF curves — 0.4.5 alpha

Native mode now publishes DOF curves with the camera path. The game evaluates
both at the same main-view shot phase. It uses the verified native typed cvar
setter and reads each result back, instead of streaming animated values through
Netconsole. Step switches change at their authored keys; smooth numeric curves
use the same bounded cubic interpolation as the editor.

## Supported controls

The complete table and JSON catalog are in
[SUPPORTED_CAMERA_CVARS.md](SUPPORTED_CAMERA_CVARS.md) and
[SUPPORTED_CAMERA_CVARS.json](SUPPORTED_CAMERA_CVARS.json). There are fourteen
native effect controls, including the four-component `r_dof_override_ranges`.
Use **+ Range DOF** for the new range track or **+ Citadel DOF** for the existing
focus/aperture preset. Range key values and optional restore values accept
four numbers separated by spaces, in near blurry / near crisp / far crisp /
far blurry order. A value such as `-100 0 180 2000` is accepted.

All components of a range key evaluate at the camera's shot phase and enter
one typed setter call; they are not sent as four console commands. Four zeros
turns off the direct range override. Range/tilt controls require the reviewed
September 9 tier0 build; the older reviewed tier0 retains the original seven
controls. Unsupported native types/profiles fail before any effect writes.
Vector shots use project format 3; scalar-only shots retain format 2.

These support both fixed shot values and animated tracks. Framing continues to
use the native aspect-ratio camera curve. Other camera variables remain
available with the Console driver; Native mode rejects unsupported shot
variables before stopping a currently playing shot. It does not silently send
them through an asynchronous fallback or claim they are synchronized.

## Use it

1. Rebuild the entire Windows package from this commit and extract it into a
   fresh folder. Keep `Dolly.exe` and `_internal` together. ABI 3 requires the
   matching editor and native helper. Close the old editing game before updating.
2. Use Home → Play replay to initialize the unlocker in the hideout before
   loading and pausing the selected replay with the native camera.
3. In Effects, use **+ Citadel DOF**. This adds animated
   focus/aperture and fixed enable switches. Edit the keys as usual.
4. Play the shot. Native camera and supported DOF curves use the same phase.
   Updates / s affects editor monitoring, not native camera/DOF delivery.
5. A shot with native DOF keeps its final camera and effects together. **Play
   shot** releases/restores the old shot and starts again. **Stop / restore**
   restores settings and returns to the game's spectator camera, which may be
   elsewhere. F10 enters native paused movement from the currently displayed view.

Frozen previews also evaluate these curves while replay time is paused. Native saved-view selection applies the chosen camera and effects at the current
paused replay moment. Entering manual flight retains the held effect phase until
a new shot or Stop restores its settings.

## Failure and restoration behavior

All effect bindings and original values are checked before the first effect
write. Native setters retain the game's clamps and change callbacks. If a
setting is unavailable, changed to an incompatible type, deferred by callback
reentry, or does not read back as requested, playback stops and attempts to
restore the snapshot. It never labels that frame as successfully synchronized.

Stop, a native playback fault, and loss of the editor heartbeat restore the
native snapshots at the next matching main-view callback. An explicit track
restore value takes precedence. Restoration failures stay visible and retryable;
the helper does not acknowledge successful release until they clear. With no
rendered callbacks (for example, a suspended game), no frame-time update or
restoration can execute until callbacks return. The editor also retains its
existing Stop / restore snapshot.

## Compatibility and validation

The supplied `tier0.dll` SHA-256 is pinned alongside the existing client/engine
fingerprints. The VEngineCvar007 table, lookup/data accessors, setter prologue,
scalar/Vector4 types and flags are checked. The game DLLs are never distributed. The
launcher retains development mode, `-insecure` and local-replay restrictions.

The camera's clock, interpolation coefficients and view-write positions are
unchanged. The new effect code applies at the same phase before DOF render-graph
parameters are constructed. Native readback confirms the cvar value; it is not
an automated assertion about final GPU pixels. An in-game focus pull, frozen
preview, repeated shot and restoration check is still required before a public
release/tutorial claims this build has passed live testing.

The binding was checked against the uploaded tier0 disassembly. The
[Source 2 SDK convar implementation](https://github.com/alliedmodders/hl2sdk/blob/cs2/tier1/convar.cpp)
provided a reference for setter/filter/callback semantics; it is not used as a
substitute for this game's verified ABI. No SDK source is bundled in Dolly.
See VALIDATION.md for test results and the compatibility profile for addresses.
