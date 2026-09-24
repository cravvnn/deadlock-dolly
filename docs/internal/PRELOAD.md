# Dashboard preload readiness

Automatic startup retains hideout, unlocker, restored gameinfo, selected replay,
initial-full-packet and camera checks. Between unlocker initialization and replay
dispatch it now verifies dashboard map/shader preloading. The game console is
hidden and Dolly advances Deadlock's normal click-to-continue screen. Dolly
then opens the selected replay automatically; no extra Load confirmation is needed.

`dolly/preload.py` is a read-only observer shared by Native and Console camera
backends. It uses only VM_READ and QUERY_INFORMATION on the live session created
by Dolly, checks the launch arguments and console ownership, then validates the
live executable and loaded module paths. It never injects, calls engine functions,
or writes game memory. It adds no third-party Python dependency.

Intro advancement uses one window-targeted WM_KEYDOWN/WM_KEYUP Escape pair, only
after the reviewed intro object's phase is 2 (interactive intro), with no preload
manifest yet. The window must be the unique visible Deadlock window belonging to
the launched PID, rechecked before dispatch. It never uses global SendInput,
changes focus, clicks coordinates, alters archived intro preferences, or dispatches
guessed Panorama events. Input queue success is not preload readiness: the same
full preload gate must still pass. A failed or ignored action is not blindly retried.

The reviewed intro constructor at client+0x18c34b0 stores the object at global
0x38198c8 with vtable 0x27a6768. The update at 0x18f0d50 uses phase getter
0x18d5250, maps phase 1 to InPreIntro and 2 to InIntro, and caches it at +0x80.
The normal key handler at 0x18dfec0 calls dismissal at 0x18ca970. Relevant live
code spans and object type/phase are verified under the same exact client hash.

The reviewed client and resourcesystem SHA-256 pins are separate from Native
camera compatibility. Exact file hashes, relevant live code spans and runtime
vtable/query identities must agree. Unknown builds fail closed for automatic
startup. Existing manual startup is still available, but does not claim automatic
preload verification. Do not update pins just to get past a game update.

Reviewed 2026-09-23 build: client getter RVA 0x571e20 yields the manager at
0x2dd3ef0. Status at 0x57de80 is consumed by the Panorama preload panel at
0x1a88740. Manager vtable 0x2341738, counts +0x24/+0x28, resource handle +0x30,
job handle +0x38. The resource singleton at client+0x3994fb0 has vtable
resourcesystem+0x61058; its +0xc0 slot is resourcesystem+0x18c00. The reviewed
13-byte query returns true for null and otherwise reads manifest+0x44.

Readiness requires a **non-null manifest**, inactive job, completed resource and
completed >= total. The manifest condition distinguishes not-started from idle:
the game's own UI completion predicate is already true before preload begins.
Counts reset across batches and may be equal while a job/resource is unfinished.
The observer rejects changing manager snapshots and implausible fields. Startup
requires three consecutive complete observations, a fresh hideout check, and one
final complete sample before dispatch. The deadline is a failure, never evidence
of readiness; cancellation and failures always close the observer handle.

For a game update, re-review the entire predicate and consumers offline, add
reviewed hash/layout support deliberately, and validate both camera backends in
bounded Dolly-owned local replay sessions. Test not-started/equal counts,
incomplete resources, changed identities, cancellation and timeout. Never replace
the gate with a sleep or a guessed console event. No FPS or crash-rate improvement
is claimed by this startup change, nor coverage of every future replay shader.
