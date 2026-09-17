# Native camera visibility

## Native shot replay preparation

Native Play shot now resets the selected replay inside the same owned game
process before each non-frozen shot. The engine must report no active replay
before Dolly sends one playdemo command, verifies the selected replay's initial
update, and confirms fresh paused native views. Saved camera and DOF tracks are
unchanged. Frozen preview keeps its current replay scene.

This is a workaround for the Bebop particle assertion after replay rewind,
not a demonstrated correction to the engine's particle lifecycle. The supplied
shot passed after each reset; reuse without another reset reproduced the
assertion. Preparation adds a replay-loading and seeking delay.

Recovery never launches a process. A game crash, timeout, identity mismatch or
cancellation ends the attempt, invalidates camera readiness and reports the
failure. It does not retry or schedule another launch. Stop / restore or Pause
can cancel an active recovery. The user must explicitly restore/reload a live
session or start a new game after a failure.

Recording preparation occurs before native capture and fixed-step timing start.
One prepared shot can play during that recording without reloading; finish the
recording and start a new recording for another take. A changed replay tick or
an explicit seek invalidates the prepared take. Dolly refuses a mid-recording
reload. Original startup, module compatibility and configuration recovery gates
remain in place.

## Replay clock correction

On the reviewed September 11b client, native shots now use the local replay tick
plus its render fraction. Client simulation time can correct backward during
packet processing; following that value made the camera briefly hold and then
catch up, including in fixed-step recordings. Camera and DOF evaluation now share
the continuous replay render clock. Paused/frozen behavior and jump checks remain.

The fraction field is enabled only for an exact client hash with an explicit
clock review. Signature fallback and older profiles retain their previous clock;
carrying a profile forward cannot authorize this field for a new client hash.
Diagnostics retain the existing `engine_time` field name, but it reports replay
render time on the reviewed build.

This removes the measured clock discontinuities, not rendering cost. A demanding
view can still reduce preview frame rate. Fixed-step recording should be assessed
from saved frames and timestamps, separately from its slower on-screen rendering.
The built-in MP4 writer now ends its last fixed-step frame at the next output-frame
boundary; slow rendering no longer stretches that frame to the recording wall time.

## Follow-up: distant vendor slowdown

The owner confirmed that the camera correction below fixes the missing world
geometry. A separate report shows lingering bright effects and a drop from
about 170 to 12–16 main views per second after briefly advancing the replay
at a distant vendor. Pending renderer buffers rose from dozens to thousands;
this alone does not establish a leak or identify the expensive draw calls.

Native startup was bypassing the initial replay-update guard and pausing at
tick 0. Both backends now wait for observed replay advancement before pausing
and opening editing. A rendered native view alone is not proof that the initial
entity update has finished. Cancellation or timeout leaves the editor unarmed.

The corrected startup was tested in an owned development replay: editing began
at tick 6, and distant travel followed by advancing to tick 205 stayed around
173–175 FPS. Baseline runs also sometimes stayed fast, so this is **not a
confirmed fix for the intermittent vendor slowdown**. No camera hook timing,
particle rendering, culling toggle, or renderer-pressure threshold was changed.
Editor polling now retains one camera-history sample per second alongside
graphics observations, including manual flight without shot playback. A busy
camera snapshot does not block editor controls.

Retest the same travel/rotation and short replay advance with a freshly launched
session. If it recurs, export diagnostics while the view is still affected;
the added continuous camera history helps isolate the transition.

## Camera correction

The September 12 camera correction keeps the main view, auxiliary camera
origins/angles, and cached camera direction vectors on the same authored pose.
It applies to manual flight, held previews and shot playback. Release, replay
seeks, invalid views and expired control connections leave the game's current
camera values untouched.

## Why this changed

The owner reported missing exterior building geometry when flying far from a
player. It remained missing while the replay advanced. Static inspection of
the installed September 11b client confirmed that Dolly previously wrote only
the main origin/angles after SetUpView returned. The game had already retained
other camera origins, an auxiliary angle, and cached origin/angles plus
forward/right/up vectors from its original camera.

The correction updates those related fields together. It also clears the view
flag that selects an independent auxiliary camera, so later view construction
uses the main camera's matrices. Other view flags are preserved. No global
occlusion, GPU culling, distance culling or replay-speed setting is changed.

This corrects a verified camera-state mismatch. It does not prove that every
source of missing geometry is fixed. Visibility work that happens earlier
inside game camera setup is outside this correction; an earlier callback may
still be needed if the same scene continues to fail.

## Compatibility and evidence

Existing module fingerprints, main-view caller/type checks, projection checks,
replay identity and heartbeat checks still apply. Before installing the camera
hook, the bridge additionally requires unique reviewed SetUpView instruction
patterns for the auxiliary camera copies and cached basis/pose writes.

Cache addresses and the engine's AngleVectors helper are decoded from signed
relative operands, not fixed absolute addresses. The split XYZ/angle stores,
cache spacing, data/text section bounds and helper entry bytes must agree.
Unsupported layouts reject Native startup before installing the camera hook.
The engine helper recomputes all three basis vectors, including camera roll.

Inspected client SHA-256:
`6b574bb0cc044fdf7f76d0abce78e2a10ab17507b92ade14fc2313e5495b7a61`.
SetUpView is at RVA `0x16bd550`. Its auxiliary-copy block is at `0x16bd771`,
additional origin copy at `0x16bd7b5`, and cache block at `0x16bda09`.
The latter resolves cache RVAs `0x3800660` through `0x38006a0` and the basis
helper at `0x1e98fd0`. The view-builder at `0x160dcd0` checks view flag `0x04`:
set selects origin/angles at `+0x558/+0x564`; clear copies the primary matrix.
These addresses record evidence for this file, not production constants.

The real callback's synthetic test checks that active flight/playback update
all camera values, that the helper receives the same authored angles, and that
release/fault/seek/heartbeat paths do not change the game's caches. Decoder
tests reject mismatched split stores, out-of-section helpers, truncated data,
changed auxiliary layouts and ambiguous cache blocks.

For read-only layout verification on a known client file, the Windows native
test accepts `bridge_smoke --camera-layout <client.dll> <setup RVA in hex>`.
It maps the image with `SEC_IMAGE_NO_EXECUTE`; it does not invoke DllMain,
resolve imports, call the game helper or attach to a game process. This passed
against the file above. The callback test uses a synthetic helper, so neither
test establishes live game rendering behavior.

## In-game check

1. Restart Dolly and its local replay using the complete updated package.
2. Fly from a player to the same distant building and viewpoint that showed
   missing exterior geometry. Check both paused and advancing replay time.
3. Play a saved shot through those positions, including rotation and roll.
4. Use Stop / restore and check the normal game camera still renders correctly.

If it persists, save a RenderDoc frame at the broken viewpoint plus Dolly
diagnostics before moving away. The earlier captures demonstrate depth and
shared world/character buffers, but are not proof of this particular failure.
