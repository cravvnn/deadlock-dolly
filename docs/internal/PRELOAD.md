# Dashboard preload readiness

Maintainer reference with versioned implementation and validation evidence.
For current operation and controls, use the [user documentation](../README.md).
Recorded results apply to the revisions described below.

Automatic startup retains hideout, unlocker, restored gameinfo, selected replay,
initial-full-packet and camera checks. Between unlocker initialization and replay
dispatch it now verifies dashboard map/shader preloading. The game console is
hidden and Dolly advances Deadlock's normal click-to-continue screen. Dolly
then opens the selected replay automatically; no extra Load confirmation is needed.

Separately, Dolly's temporary GameInfo sets the stock
`deadlock_early_development_warning_disabled` value to1, preventing the specific
early-development warning from being created. This uses the same preference as
that warning's disable checkbox inside the isolated session; normal-play config
is preserved. It does not dismiss hero announcements or arbitrary splash popups.
The normal intro still needs the verified advancement and preload gates below.

The intro panel does not update its cached phase while `citadel_hud_visible` is
zero. This reproduces a startup timeout with a rendering hideout, equal shader
counters, no manifest and phase 0. After unlocker initialization, startup reads
the HUD value and temporarily enables it if hidden. The previous value is
restored before replay dispatch and on cancellation, timeout or read failure.
A failed restoration blocks replay dispatch and retains the original value for
Stop / restore. No intro preference is changed and preload verification is not
bypassed. Diagnostics record the original HUD value and restoration outcome.

`dolly/preload.py` is a read-only observer shared by Native and Console camera
backends. It uses only VM_READ and QUERY_INFORMATION on the live session created
by Dolly, checks the launch arguments and console ownership, then validates the
live executable and loaded module paths. It never injects, calls engine functions,
or writes game memory. It adds no third-party Python dependency.

For the reviewed 6821 client (`8da25ddc...`), the hideout constructor at
client+`0x1bd2f05` installs the `CCitadel_Hud_Hideout` vtable `0x2c6ab38`.
Its store at `0x1bd2f63` (`48 89 35 de db 2a 02`) resolves to the singleton
at `0x3e80b48`. The stock load at `0x1c0ec77` reads that same singleton and
calls the phase getter at `0x1bef6d0`. The previous `0x3e80bd8` value points
to unrelated data and could make every preload observation unreadable.
The contract now records the constructor's RIP-relative instruction and pins
the stock reader span. Generation checks that the instruction resolves to the
declared singleton inside a saved code span; installed-module verification also
checks the instruction bytes. No preload readiness condition is relaxed.

Intro advancement uses one window-targeted WM_KEYDOWN/WM_KEYUP Escape pair, only
after the reviewed intro object's phase is 2 (interactive intro), with no preload
manifest yet. The window must be the unique visible Deadlock window belonging to
the launched PID, rechecked before dispatch. It never uses global SendInput,
changes focus, clicks coordinates, alters archived intro preferences, or dispatches
guessed Panorama events. Input queue success is not preload readiness: the same
full preload gate must still pass. A failed or ignored action is not blindly retried.

Some sessions never run the hideout intro at all: the preload manager stays
coherent but unstarted at intro phase 0, with no manifest, while the hideout
renders normally and the HUD is visible. Waiting cannot change that state, so
automatic startup stops early after a bounded window (`PRELOAD_INTRO_STALL_SECONDS`),
records the observed last sample and trace under `preload_unavailable`, and
leaves the game open. If another game screen needs attention, the desktop offers
**Check startup again** after the user dismisses it. That action repeats the
same-session hideout/preload checks before replay loading. The early stop never
fires once a started preload or a nonzero
intro phase has been observed; those keep the full gate and its timeout.

The build 6712 intro constructor at client+0x1a82910 stores the object at
0x3bdf908 with vtable 0x2aa9390. The update at 0x1ac6050 uses phase getter
0x1a9fa30, maps phase 1 to InPreIntro and 2 to InIntro, and caches it at +0x80.
The normal key handler at 0x1aae800 calls dismissal at 0x1a8b7d0. Relevant live
code spans and object type/phase are verified under the same exact client hash.

Build 6726 was reviewed separately: manager client+0x31487b0, manager vtable
0x26246a8, resource singleton pointer 0x3d823b0, and intro pointer 0x3be1e08.
The resource module fingerprint is now 86d09bc9.... Its CResourceSystem RTTI
still identifies table 0x6bc58 and slot +0xc0 points to 0x1c860. The complete
13-byte query is unchanged: null returns true; otherwise it reads manifest+0x44.
The client lifecycle code spans retain their reviewed instructions after relocation.
This offline review does not substitute for the startup gate's live readiness checks.

The reviewed client and resourcesystem SHA-256 pins are separate from Native
camera compatibility. Exact file hashes, relevant live code spans and runtime
vtable/query identities must agree. Unknown builds fail closed for automatic
startup. Existing manual startup is still available, but does not claim automatic
preload verification. Do not update pins just to get past a game update.

Reviewed offline for 2026-09-29 build 6712 (client bc0dae38...): getter
0x5ec8a0 yields manager 0x31456e0, vtable 0x2623068. Status 0x5f5b40 is
consumed by the Panorama preload panel at 0x1cb8800. Counts +0x24/+0x28,
resource handle +0x30 and job handle +0x38 retain their offsets. The resource
singleton at client+0x3d7fe30 has vtable resourcesystem+0x6bc58; its +0xc0 slot
is resourcesystem+0x1c860. The reviewed 13-byte query returns true for null and
otherwise reads manifest+0x44. The resource system hash is 1eb0193b....

The new status predicate also checks lifecycle flags: +0x3c is set after work
is scheduled and +0x3d by the job completion callback at 0x5f8ee0. An unset
+0x3c makes the game's UI report completion even before preloading starts.
With work scheduled, status requires +0x3d, a non-null completed resource and
completed >= total, then waits/releases the job and clears +0x38. Dolly does
not invoke that function. It requires **both flags**, a **non-null manifest**,
completed resource/counters and the subsequently cleared job handle. Relevant
producer, completion callback and invalidation spans are also checked. Invalid
flag values fail closed. This avoids treating idle or partially scheduled work
as ready while preserving the observer's read-only behavior.

Counts reset across batches and may be equal while a job/resource is unfinished.
The observer rejects changing manager snapshots and implausible fields. Startup
requires three consecutive complete observations, a fresh hideout check, and one
final complete sample before dispatch. A deadline is never evidence of readiness,
and cancellation and failures always close the observer handle.

The verified wait stops on silence, not on a stopwatch. Counters move only when a
batch finishes, so one batch can look like no activity for well over a minute on a
slower machine, and a game update discards the compiled cache and rebuilds all of
it. While the reviewed job handle or its scheduled flag is still live, or a new
observation arrives, the wait is extended (``PRELOAD_STALL_SECONDS`` per window,
``PRELOAD_MAX_SECONDS`` in total) and every extension is reported in the status and
recorded under ``preload_extensions``. An unreadable preload object is never
extended: with no evidence of work, the existing bounded window applies unchanged.
A wait that ends raises ``PreloadUnavailableError`` for the stalled and the ceiling
case exactly as it already does for the never-started intro, so the desktop offers
the explicit unverified manual load instead of a dead end. The message carries the
last observed batch, and no readiness claim is ever made from elapsed time.

Diagnostics include a bounded `preload_trace`: the first observation, the latest
64 state changes with elapsed times, a dropped-change count, and whether a
coherent started/ready state was ever observed. Repeated identical polls are
collapsed; the final sample after hideout revalidation is also recorded. These
historical observations never authorize replay loading in place of current
readiness. A null manifest is ambiguous: the reviewed add-on-list notification
callback (client+0x5d2420, `IAddonListChangeNotify` subobject at manager+0x10)
releases/clears the manifest and job without resetting progress counters.
Do not treat equal counters following that invalidation as completed map preload.

For a game update, re-review the entire predicate and consumers offline, add
reviewed hash/layout support deliberately, and validate both camera backends in
bounded Dolly-owned local replay sessions. Test not-started/equal counts,
incomplete resources, changed identities, cancellation and timeout. Never replace
the gate with a sleep or a guessed console event. No FPS or crash-rate improvement
is claimed by this startup change, nor coverage of every future replay shader.

Build 6712 support is statically reviewed and covered by offline regression tests.
Native preload has since passed bounded local replay startup tests. Console
startup and export compatibility require their own verification.

## Replay camera handoff after preload

Build6712 can begin replay simulation while a scripted opening camera still
owns the view. `GameInProgress` and dashboard intro phase0 were observed during
that opening and must not be used alone to enter the paused editor.

`dolly/replay_camera.py` verifies the same exact client hash, owned development
session and loaded module path, then compares the reviewed main-view/manager
consumer code. `CCitadelCameraManager` at client+0x35f5780 (vtable0x261d0a0)
supplies current camera+0x28, previous+0x30, blend byte+0x38 and weight+0x44.
Startup accepts only coherent game state7 plus the reviewed normal
`CCitadel_ThirdPersonCamera` vtable0x2624b08 with blending finished. Unknown
cameras remain waiting; unknown builds/types/invalid states fail closed.

The controller retains selected-replay and initial-full-packet checks first,
then requires two ready observations with advancing rendered frames (native)
or replay ticks (console) before pausing. A90-second deadline is failure, never
readiness. Cancellation and failure close the read-only process handle and
leave manual startup available. No seek, scripted-camera skip, fake game
state, spectator-mode change or game-memory write is used by this gate.

The direct view consumer has a later CViewEffects call; reviewed code only
adds position and roll offsets and does not select a replacement camera.
The private state-gated prototype was visually accepted on replay108605183;
production-path validation is recorded separately in the live update handoff.

The same guard also runs after an in-process replay reload for shot/recording
preparation. A fresh playdemo restarts the opening camera; reaching the initial
packet is insufficient even if the editor's original startup already passed.
Recovery retains its inactive-replay boundary and single-load policy, then waits
for camera handoff before pausing or seeking to the authored start.
