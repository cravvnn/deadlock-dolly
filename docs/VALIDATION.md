# Validation — 0.5.3 alpha

## 120 FPS update

- 120 FPS is accepted by desktop options, native command/status transport and
  the encoder. The default remains 60 FPS.
- 53 targeted Python tests passed for video export, media transport and native
  packaging.
- Portable video math tests passed, including 120 Hz cadence, skipped slots and
  one-hour timestamp drift checks.
- Windows x64 native DLL and bridge/overlay/video smoke programs compiled and
  linked. The video smoke now writes and inspects a 120 FPS MP4, including its
  advertised rate and increasing sample timestamps. Windows programs were not
  executed in this Linux workspace; GitHub Windows CTest runs them.
- Owned C++ formatting passed.
- Camera hook, interpolation, ReShade integration and recording handoff logic
  are unchanged from 0.5.1.

The owner reports successful recording and ReShade loading in 0.5.1. Sustained
120 FPS throughput and the new output mode still need a Windows/Deadlock test.
Separated layers are not implemented; renderer evidence requirements are in
[LAYER_EXPORT.md](LAYER_EXPORT.md).

## Previous 0.5.1 checks

## Recording handoff fix

The submitted 0.5.0 diagnostics report a completed 2560×1440 MP4 with 154
frames over 2.895 seconds and no encoder error. Native camera playback completed
separately. The diagnostics do not identify which automatic-stop branch fired.
Code inspection found that editor disable, an unavailable editor pose, or focus
loss could stop recording independently of the live camera session.

0.5.1 separates media lifetime from editor input ownership. Initial capture waits
for game focus; an active recording continues across input/readiness/focus changes.
The verified session heartbeat still gates capture and disconnect finalization.
The camera hook, path evaluator, effects and flight code were not changed.

Targeted Python checks passed: 72 tests covering media transport, video export,
native packaging and native playback control. Owned C++ formatting passed.

Windows x64 DLL and all three smoke programs compiled and linked on Linux. The
new overlay smoke exercises actual Present capture across editor disable, missing
pose readiness, focus loss, and disconnect. It is compiled here, not executed;
GitHub Windows CTest runs it. Deadlock runtime validation remains outstanding.

## Previous 0.5.0 validation

## Scope and completed checks

This build adds real-time MP4 recording and an optional ReShade manual runtime.
It does not relocate the main-view hook or change the camera clock. The
`on_view`/hook block, path evaluator, effects evaluator and flight-math header
were compared with the 0.4.7 source archive and remain byte-for-byte identical.
The owner reports stable playback with 0.4.7; earlier renderer crashes still
do not have a confirmed general cause/fix.

- Python regression suite: 899 tests run, no failures, two platform-specific skips.
- Five portable native test programs passed: camera paths, effects, flight,
  visualization, and video cadence/timestamps/color conversion/row orientation.
- Windows x64 native DLL, bridge smoke, overlay smoke and video encoder smoke
  compiled and linked with Zig/Clang. These Windows programs were not executed
  in this Linux workspace.
- The video smoke test writes and reopens an MP4, checks dimensions, timestamps,
  sample count/duration, finalization without further Presents, no-overwrite,
  and cancellation. GitHub's Windows CTest gate executes it.
- The frozen Windows GUI gate checks the new Export controls at minimum window
  size. No local graphical display or Windows desktop was available here.
- Owned C++ formatting passed token-preservation and idempotence checks.
- Native PE architecture, required exports, static runtime and artifact hash
  were checked. Media Foundation is loaded optionally; the camera DLL has no
  mandatory Media Foundation DLL import.

Optional media commands use a separate bounded mapping (media ABI 1), retaining
camera ABI 3. Editor ABI 2 adds the ReShade binding/owner and recording actions;
its configuration remains 448 bytes. Settings v3 migrates older preferences
while preserving conflicting existing keybindings.

## New behavior requiring Windows/Deadlock verification

ReShade 6.8.0/API 20 full-add-on headers are pinned. Manual API signatures and
single-sample SDR callback ordering were checked against official source.
Capture follows effects and precedes both interfaces. MSAA/HDR ReShade
swapchains are refused. There is no supplied game depth texture.

DLL loading runs on an optional loader thread. Menu transitions use the input
worker; render callbacks do not move/confine the OS cursor. Closing Dolly or
losing its heartbeat requests finalization and processes render-resource/menu
teardown on the next real Present. Unconfigured ReShade cannot leave a pending
menu request that hides the editor.

Before publishing this feature build, verify these in an editing session:

1. Clean launch, paused flight, capture, repeated playback, F7/F8/F9 and focus
   changes with ReShade unconfigured.
2. Record 30/60 FPS at the chosen game resolution; finish, replay the MP4,
   compare elapsed time and inspect the missed-slot count. Confirm markers and
   both editor interfaces stay out of the file.
3. Record with a visible color effect, then with effects disabled. Test F11,
   F7 return, rebinding, preset reload, and ordinary paused movement afterward.
4. Finish on focus loss/resize, cancel, close Dolly while keeping the game open,
   and verify that the file finalizes and mouse input returns.

This is real-time video-only capture. It does not provide fixed-step simulation,
audio synchronization, depth/world/hero/effect layers or arbitrary output sizing.

# Validation — 0.4.8 alpha

## Recorded-demo evidence and scope

The supplied completed SourceTV demo contains 17,019 ordinary packets with
three-/four-tick spacing. Its FileInfo reports 51,585 ticks in 806.015625 seconds.
The supplied 0.3.13 logs show successful replay loading followed by a paused-
camera calibration failure: tick 48,393 is absent, while 48,394 is recorded;
the same issue occurs around 22,636/22,637. A normal paused render tick can also
lie between packets. Current native flight avoids that old calibration path,
but native shot starts still needed explicit recorded-packet handling.

The new reader inspects framing and bounded metadata, skips payloads, requires
a completed header/Stop/FileInfo relationship, and caches two file identities.
Bounds, replacement, growth/truncation and cancellation are checked. Unavailable
indexes preserve strict seeking. This is not a decoder or a replacement for
the game's recording-content validation.

Native shot playback/seek targets the indexed following packet when needed,
then retains exact stable tick/pause validation and replay identity checks.
Camera and effect phase use the actual tick on the unchanged authored timeline.
The initial skipped fraction is reported. Targets past the shot end fail.
Frozen native preview instead confirms fresh paused telemetry and prepares its
camera without a seek or console position calibration, preserving that scene.
It checks the same paused tick again before starting native motion.

Generic legacy seeks remain exact. Normal native playback's existing console
position-calibration recovery can still request an unavailable adjacent tick.
That separate recovery is not claimed fixed. Future deterministic export needs
preroll to preserve its first requested frame, rather than an initial skip.

## Renderer research and observations

September 12 camera consistency correction: native callback tests now cover
the main/auxiliary/cached camera state together, including inactive and fault
paths. A read-only mapping of the installed client passed the production
layout resolver. See [Native camera visibility](CAMERA_VISIBILITY.md) for exact
evidence and live-test limits. The following text records the earlier finding.

Static comparison with HLAE found that Dolly overrides after Deadlock stores
derived main-camera caches. A verified particle-system query reads those cached
position/angle values. This can disagree with the rendered Dolly view, but the
fatal dynamic call chain and resulting buffer growth have not been proved.
An earlier hook needs separate pose/projection stages and full lifecycle review;
no hook relocation or renderer-memory patch is shipped here.

View history reuses already validated status reads, at most once per second,
with a 120-sample bound and copied poses. It shares the input/graphics monotonic
observation clock and survives close or invalid later telemetry. Original pose
is the game-provided view before override, not a separate cache measurement.
The native DLL, camera clock/interpolation and native ABI remain unchanged.
Severe FPS drops and the renderer overflow remain unresolved.

STAGE3_PLAN.md records the primary-source research on ReShade ordering, fixed
simulation timing, bounded readback, video encoding and separate scene passes.
Those are proposed implementations, not features delivered by this patch.

## Verification

- Full Python suite: 849 tests run, no failures, two Windows-only skips.
- The actual supplied demo indexes 17,019 packets in about 0.05 seconds locally.
  Its observed missing-tick boundaries match the new reader. Synthetic cases
  cover framing/metadata bounds, partial files, changed-file caches, cancellation,
  strict seek/identity checks, phase alignment and frozen-scene preservation.
- Native source, includes, tests, vendor code and DollyNative.dll are byte-for-
  byte unchanged from 0.4.7. The same checked five-file native runtime is included;
  its source package metadata identifies the unchanged native build. No new
  native compilation or live-game renderer-fix claim is made for this patch.
- All 194 source entries, archive integrity and clean re-export were verified.
  The supplied demo, private logs, game DLLs and authoring context are excluded.

GitHub Windows CI remains the packaged-EXE build/runtime gate. No live
Windows/Deadlock execution was available for the new Python behavior.

## Earlier validation records

# Validation — 0.4.7 alpha

## Paused input and completion

Input history records Flight ownership while manual camera updates are inactive.
Closing F8 previously selected Flight locally even when the camera was held or
completed. It now requests the existing acknowledged flight handoff and retains
the panel while waiting or busy. Normal manual-flight toggles stay immediate;
active shots and frozen previews are not interrupted. This addresses a confirmed
input-ownership defect, not every low-frame-rate interval in the diagnostics.

The end-of-shot handoff previously used a rendered tick sampled before the
console pause had taken effect. It now waits for two fresh paused views at the
same tick before moving the underlying spectator, then requires the usual three
matching original views before release. Timeout keeps the native view held.
A replay change after the pause still prevents handoff.

## Custom recording names

Console, startup and native replay guards now match basenames with or without
the final .dem suffix. Dots within the name remain significant; a different
suffix cannot alias the selected file. Native and Python regression cases cover
dotted extensionless names and rejection of other replay names/extensions.

Dolly already discovers .dem files without a downloaded-versus-recorded origin
filter. The reported tv_record rejection has not been reproduced. Its exact
error, fresh diagnostics and a short completed recording are needed to establish
whether the filename correction resolves it or the engine rejects the contents.
No recording-header, addon or game-version validation is bypassed.

## Renderer investigation

The supplied particles.dll and materialsystem2.dll match the earlier September
10 crash dump's PE identities. Exception-directory unwind identifies particle
rope-render operators performing material binding and small pooled constant-
buffer allocation. Those are distinct from the fatal vertex-buffer retirement
list. The captured stacks do not establish an unbounded particle-spawn loop.
The dump still lacks the command-stream and retirement-list backing allocations
needed to identify the exact producer.

The latest diagnostics contain repeated slowdowns, not a new fatal event.
One interval falls to roughly 1.4 main views per second with about 6,169 pending
renderer buffers. During much of it, guide drawing is inactive and measured
overlay/original-Present calls finish in fractions of a millisecond. F9 round
trips correlate with the queue shrinking and frame production recovering.
These observations place substantial work outside the measured calls; they do
not rule out an indirect interaction with Dolly. **Severe slowdown and the
reported renderer overflow remain unresolved.** No additional DLL is currently
needed. A fresh diagnostic export during the slowdown, before F9 recovery, and
any same-run crash dump would be more useful.

Input and graphics cached samples now share a monotonic sampled_at observation
time. This records when the controller first reads a sample, not an exact native
event time. Duplicate reads preserve it and cached observations survive closure.
Camera ABI 3 and the native diagnostic wire layouts remain unchanged.

## Verification

- Full Python suite: 824 tests run, no failures, two Windows-only skips.
- Native path, effect, flight and visualization programs compiled and passed
  on Linux. Camera evaluation and flight integration are unchanged.
- Current Windows x64 sources compiled and linked into DollyNative.dll and
  the callback/DX11 WARP harnesses. New callback cases cover F8 rearming,
  unavailable-view recovery and extensionless dotted replay identities.
  Windows executables were compiled, not executed in this Linux workspace.
- Owned C++ formatting, protected tokens and idempotence pass with clang-format
  18.1.8. Vendor files remain unchanged.
- Native PE exports, five-file runtime allowlist and DLL SHA-256 metadata match.
  All 190 source entries, archive integrity and clean re-export were verified.
  Game binaries, logs, dumps, recordings and private authoring context are excluded.

GitHub Windows CI remains the packaged-EXE and native Windows runtime test gate.
Live Deadlock testing is still required for the corrected transitions.
The camera path, native flight integration and per-rendered-view effects retain
their established evaluation behavior.

## Earlier validation records

# Validation — 0.4.6 alpha

## Startup input

Automatic flight entry previously left hud_free_cursor at its automatic value
until an explicit game-UI round trip. Flight entry now reads the original
HUD/cursor together, verifies the explicit hide/cursor handoff before arming,
and preserves that original baseline across later entries for Stop / restore.
A failed read or unapplied cursor setting prevents camera arming.

The native owner could also become Flight before the first ready replay view.
Cursor confinement was only reconciled on owner/focus/overlay changes, so that
ready transition could be missed. The view callback now marks a refresh; the
control worker applies it without resetting held keys or accumulated mouse
motion. No additional hook, device registration or mouse-warp fallback is added.
The fixes address demonstrated startup gaps; live mouse delivery still needs
confirmation on Windows.

## Crash investigation

The supplied September 10 dump matches the log's fatal vertex-buffer retirement
list overflow at 32,767 entries. AMD64 unwind through the matching renderer
confirms the same allocation-limit path as the previous dump. The queue drains
between bursts, then grows during very slow frames; it is not a permanently
stopped retirement clock. The fatal append handles one 10,816-byte upload backed
by a 64 KiB engine vertex buffer. Multiple distinct engine buffer objects are
captured, rather than evidence of a single oversized path-guide draw.

Other captured worker stacks contain particle/material calls into the renderer's
constant-buffer allocation path. This is a lead, not proof of the initiating
fault. The dump omits the command-stream allocation and retirement-list backing
storage needed to identify the exact producer. Matching particles.dll and
materialsystem2.dll are needed to trace the remaining call sites. No particle
suppression, renderer-memory mutation or allocation-limit increase is applied.
**The crash remains unresolved.**

The optional graphics diagnostic block now distinguishes guide/panel/total
rendering, Dolly draw time, original-Present time, active calls and skipped
locks. Its ABI 2 uses existing padding; the reader also accepts ABI 1 without
inventing missing observations. Camera ABI 3 is unchanged. An optional input
block records raw mouse registration, received/accepted/consumed motion and
cursor request failures. It retains a bounded history after game closure.
The cursor-clipped flag records Dolly's last successful request; it is not an
independent OS cursor-rectangle measurement.

## Source cleanup and checks

- All 24 owned C++ files use clang-format 18.1.8. Formatting was applied after
  recording functional changes and verified for protected token/literal and
  preprocessing-boundary preservation plus idempotence. Vendor sources are
  untouched. Contributor formatting is optional for builds.
- Full Python suite: 814 tests run, no failures, two Windows-only skips.
- Native path, effect, flight and visualization test programs compiled and
  passed on Linux. Targeted one-/two-camera viewer cases also passed address
  and undefined-behavior sanitizers; leak checking was unavailable here.
- Current Windows x64 sources compiled and linked into the native DLL and
  callback/DX11 WARP test executables. New Windows cases cover delayed input
  readiness, preserved motion, input wire publication and guide/Present timing.
  The Windows executables were not executed in this Linux workspace.
- Native PE exports, runtime allowlist, version and DLL SHA-256 metadata match.
  All 190 source entries, archive integrity and clean re-export were verified.
  Game DLLs, diagnostics, dumps, recordings and private authoring context are
  excluded. Existing cvar catalog and camera-path formats remain compatible.

GitHub Windows CI remains the packaged-EXE and native Windows test gate.
STAGE2_TESTING.md covers fresh-launch mouse testing and capture with guides
shown/hidden. Camera interpolation, playback clock and effect evaluation have
no behavioral changes in this update.

## Earlier validation records

# Validation — 0.4.5 alpha

## Changes and scope

The F8 panel shares playback speed and Updates / s with the desktop. Changes
apply to the next shot. In Native mode, Updates / s controls monitoring;
camera and supported effects still follow the rendered view. Desktop dropdown
commit highlights clear without removing keyboard focus or manual text selection.

Optional paused-editing guides show the authored spline, numbered camera poses
and selected view. A separate bounded mapping supplies immutable geometry from
the worker; drawing uses the existing DX11 overlay. Guides hide during playback,
console/game UI ownership and loss of focus. Projection clips the near plane
and viewport edges. Camera glyphs are symbolic, draw through walls and do not
provide live thumbnails or exact lens cones. Native camera path evaluation,
flight integration and playback clock behavior are unchanged.

Seven reviewed range-DOF controls extend the original seven supported native
effects. r_dof_override_ranges uses the verified Vector4 type: each component
samples the same shot time, then one typed setter call applies the whole value.
Restoration also uses the whole value. New controls require the reviewed
September 9 tier0 fingerprint; older reviewed builds retain the original seven.
Validation rejects unsupported types, incomplete vectors and non-finite values
before effect writes. Vector projects use format 3; scalar-only projects remain
format 2. These checks do not establish image quality or an anti-aliasing gain.

## Verification

- Full Python suite: 791 tests run, no failures, two Windows-only skips.
- Current native path, effects, flight and visualization tests compiled and
  passed on Linux. Guide sampling matches the existing Python/native path.
- All current Windows x64 native sources compiled and linked into DollyNative.dll,
  the callback test executable and the DX11 WARP test executable.
- Expanded Windows harnesses cover shared playback settings, vector write/readback
  and restoration, guide pixels/visibility, mapping lifecycle and existing DX11
  state/cursor/resize checks. The 384-frame/768-buffer lifetime check now includes
  guide-only flight. These Windows executables were compiled, not executed here.
- Native PE architecture, required exports, runtime files, DLL SHA-256 and build
  metadata verified. The readable and JSON cvar catalogs match supported controls
  and are included beside the EXE by the Windows packager.
- All 185 source manifest entries, archive integrity and clean extraction/
  re-export verified byte-for-byte. Game binaries, diagnostics, recordings,
  personal files and private authoring context are excluded.

The previous 0.4.4 build was reported working smoothly. This update has not run
in Deadlock or as a packaged Windows EXE in this Linux workspace. GitHub Windows
CI runs the native callback/WARP tests and packaged startup checks; live-game
checks for the new features are in STAGE2_TESTING.md. Expanded in-game curve
editing remains Stage 2. Video/render-pass export remains Stage 3; ReShade
coexistence is unverified.

## Earlier validation records

# Validation — 0.4.4 alpha

## Windows build correction

GitHub Actions run 93326745970 compiled the native DLL with MSVC and passed
all five native tests, including the callback and DX11 WARP tests. Packaging
then stopped on three cleanup-test failures: two BOM-bearing fixtures used
Windows' default cp1252 encoding, and one assertion compared a resolved long
path with the same temporary directory's 8.3 alias.

The fixtures now write UTF-8 explicitly and the assertion compares the
normalized path. The Windows integration test retains and waits for its
cleanup helper before removing its temporary directory. Production cleanup,
editor code and the native DLL are unchanged by this build correction.
The integration test in the supplied Windows run already passed the real
process-handle inheritance and generated-folder removal checks.

Local portability checks reproduce all three failures with the original test
file, then pass with the corrected file under the same cp1252 default and
alternate-path conditions. These checks emulate the reported differences;
they do not replace running the corrected build on Windows.
The corrected full Python suite ran 762 tests without failures, with two
Windows-only skips. All 174 source archive entries and a clean extraction/
re-export were verified. Only this validation record, the changelog and
test_session_cleanup.py differ from the original 0.4.4 package.

## Crash analysis and limits

The supplied minidump and matching rendersystemdx11.dll identify Source2's
pending vertex-buffer retirement list as the fatal allocation path. The
renderer tries to exceed its 32,767-entry limit. Unwinding the render worker
confirms that path; the dump does not contain the queue header/backing storage
or the pointer needed to establish the completion threshold at failure.
A captured buffer stamp is 11226 and a render-worker frame field is 11225;
these are rendering counters, not the paused replay tick. No causal link to
mouse input has been demonstrated.

This update does not fix that overflow. It adds read-only samples once per
second for the reviewed renderer fingerprint, plus overlay Present, drawing,
initialization, release and resize counters. Reads touch a fixed queue header
and at most two nodes. Head/tail stamps are samples, not minimum/maximum values
for the whole queue; consistency checks cannot establish an atomic snapshot
of multiple render threads. Unknown renderer builds skip the private probe
without changing camera support. No engine resource is deleted, frame marker
advanced, allocator limit raised, or GPU state changed by this probe.

The editor retains up to 120 distinct samples and caches the last observation
before closing shared memory. Session native_diagnostics.json preserves that
snapshot across an editor restart; diagnostic exports include recent sessions.
Native camera input, path evaluation, flight integration and effects match
0.4.3. Changes to the native view bridge are confined to its control worker's
diagnostic calls; overlay changes are atomic counters only.

## Temporary session cleanup

Folders under game/citadel_dolly_... are created per editing launch. The
original gameinfo.gi is restored after unlocker initialization and on normal
editor closure. If Dolly closes while Deadlock remains open, a hidden helper
inherits a wait-only handle to that exact game process, waits for its exit,
removes the generated plugin files and exits. It neither injects nor launches
a game. Failure to start or complete the helper is logged for later recovery.

Recovery retries already-restored journals and discovers marked leftovers
from older/moved portable folders. It verifies ownership, the installation,
current search paths and allowed generated entries before removal. Referenced
mounts, external gameinfo edits, unknown files, links/junctions, logs and
original backups remain intact. Forced termination or power loss can defer
recovery until the next Dolly launch or explicit Recover action.

## Verification

- Python suite: 762 tests run, no failures, two Windows-only skips.
- Current native path, effects and flight tests compiled with g++ and passed.
- All Windows x64 native sources compiled and linked into DollyNative.dll,
  the callback test executable and the DX11 WARP test executable.
- Native PE architecture, required exports, runtime contents and SHA-256
  metadata verified. Windows callback/WARP executables were not run here.
- Source archive integrity, all 174 manifest entries, required new modules
  and clean re-export contents verified byte-for-byte. Uploaded game DLLs, dumps,
  diagnostics, personal files and private authoring context are excluded.

Windows CI runs the real process-handle inheritance and DX11 WARP tests.
The frozen helper's entry routing is covered by a dispatch test; its complete
packaged runtime still needs a Windows check.

The WARP graphics test now cycles Panel/Flight/GameUI for 384 frames with 768
fresh vertex/index buffers. Private-data lifetime sentinels verify retired
buffers are released after state clearing and GPU completion. Existing pixel,
state, cursor, input, resize and shutdown checks remain. The optional diagnostic
block is checked for layout, untouched surrounding bytes, missing renderer and
unknown-fingerprint behavior. WARP does not reproduce Source2's private queue.

Neither Deadlock nor the packaged Windows EXE can run in this Linux workspace.
Export diagnostics after reproducing the slowdown/crash, before restarting
Dolly where possible. The read-only measurements are the next evidence needed
to choose a targeted crash fix. See STAGE1_TESTING.md for the runtime checklist.

## Earlier validation records

# Validation — 0.4.3 alpha

## Reported failures and changes

The supplied 0.4.2 recording visibly reports 5 FPS. Native samples show frame
intervals around 228–264 ms, with no authored path or animated effects running.
Console output includes 86 instances of QueuePresentAndWait waiting 21–22
iterations without a present event. This is a presentation-stall symptom;
it does not indicate a camera spline/timestamp problem.

F9 previously hid only citadel_hide_replay_hud, leaving citadel_hud_visible
set to 1. Returning now hides both the replay controls and character HUD.
Opening F9 shows both again. Original HUD/cursor settings are read together
once and restored on Stop. Redundant writes during return-to-flight were
removed; each return applies one complete hide operation and verifies it.

The overlay forwarded mouse releases to ImGui while Deadlock owned input.
The Win32 backend could consequently ReleaseCapture on the game's window
even though Dolly did not own that click. Queued pointer events also reached
Win32 capture handling from Present, and each visible frame allowed Win32
cursor updates. Those calls can interfere with a window thread waiting for
frame presentation.

Pointer events now update ImGui input directly, hidden panels discard their
pending events, and Present disables Win32 cursor changes. Cursor hiding is
handled on the game's window-message thread. Dolly does not acquire new OS
mouse capture; dragging works outside the panel within the game window, but
not beyond the entire window. The native camera callback, interpolation,
paused-flight integrator and effects remain byte-identical to 0.4.2.

## Checks completed here

- Python suite: 727 tests, no failures, one Windows-only skip. HUD regression
  checks cover full hide/show, original-value restoration and one hide write
  per return. Existing capture, seeking, playback and UI tests remain enabled.
- Complete Windows x64 crosscompile/link passed for the helper and both test
  executables. PE architecture/exports and native metadata/hash were verified.
- Expanded real DX11 WARP smoke tests verify game mouse capture survives a
  hidden-panel mouse release, legacy/raw pointer input still reaches Dolly,
  the first click after reopening survives, and Present retains game capture
  with OS cursor changes disabled. These compiled here; Windows executes them
  through the existing CTest gate, with its timeout retained.
- Full source ZIP integrity, manifest contents and clean extraction/re-export
  were checked. Uploaded game binaries, recordings, diagnostics and private
  authoring context are excluded.

## Remaining validation

Neither Deadlock nor the Windows EXE can run in this Linux workspace. The
mouse-ownership errors and unsafe render-side call paths are corrected, but
live testing must establish whether the 5 FPS stall is fully resolved. Build
the full Windows package, restart both Dolly and Deadlock, and check F7/F8/F9,
replay/hero UI clicks, panel dragging and frame rate. Repeated presentation
waits after this update would require another diagnostic export.

## Earlier validation records

# Validation — 0.4.2 alpha

## Reported failures and changes

Three diagnostic exports and the supplied replay-editor clip were reviewed.
The recent console output repeatedly rejects `demoui` with "no cvar or command
named". The capability parser nevertheless accepted that reply. F9 now uses
explicit `citadel_hud_visible`, `citadel_hide_replay_hud` and `hud_free_cursor`
values with readback verification. Their registration/types and cursor/HUD
uses were inspected in the supplied client binary. Original values, including
cursor auto mode -1, are restored by Stop/disconnect. F7/F8 and Alt-Tab keep
the intended input owner across the console, Dolly and the game replay UI.

The failed shot starts at tick 0. Both initial and corrective seeks report
"from full packet 1", then remain at tick 1. That failure is an unavailable
replay boundary, not proof that the renderer cannot keep up. Only project
Play/Seek accepts this explicitly reported boundary, after repeated paused
readbacks and an exact retry. Other seeks remain strict. The actual tick is
recorded, and camera/effect phase starts at 1/64 second for the supplied shot.
Saved keyframes and their authored timing are not modified.

Desktop capture previously mixed console ticks with a later native pose,
then rejected the mismatch. It now waits for fresh paused render telemetry
and samples pose/tick together. In-game capture retains its input event's
pose/tick while waiting for the replay to pause; an ordinary advancing replay
is allowed, while stale paused captures and rewind are rejected. The current
paused tick stays separate from an earlier captured event tick. Capture after
P re-arms held manual movement without a console position write. Flight entry
compares native acknowledgement with native status, avoiding stale console
pause timing.

The native path interpolation, main-view callback/clock, DOF evaluator and
manual movement integrator match 0.4.1 byte-for-byte. Native changes are limited
to UI input ownership/status and associated regression tests. The diagnostic
that ends with game exit code 1 has no crash stack; this update does not claim
to identify or repair that separate process exit.

## Checks completed here

- Full Python suite: 724 tests, no failures, one Windows-only skip. The 33 new
  regressions cover HUD/cursor handoffs and restoration, delayed paused render
  samples, captures while playing, stale/rewound snapshots, and the exact
  tick-zero/full-packet-one seek boundary.
- All current Windows native sources compiled and linked into the x64 helper,
  callback harness and DX11 WARP harness. PE architecture, required exports,
  static runtime and native build metadata/hash were verified.
- New callback cases exercise F8 console/game-UI return, F9 ownership while
  waiting for acknowledgement, and preserved owner status while unfocused.
  These Windows cases compiled here; execution is a GitHub Windows CTest gate.
- Source archive integrity, manifest completeness and clean-extraction source
  re-export were checked. Uploaded game DLLs, diagnostics, video and private
  authoring context are excluded.

## Remaining validation

The packaged Windows EXE and Deadlock cannot run in this Linux workspace.
GitHub must rebuild the EXE and execute the Windows native/startup checks.
The live F9 → F7 → F8/F9 transitions, captures after P, and reopened tick-zero
shots still need an in-game check. See STAGE1_TESTING.md for those cases.

## Earlier validation records

# Validation — 0.4.1 alpha

## Reported failure and changes

The supplied 0.4.0 diagnostic records a manual-flight request failing with
"Native editor input is unavailable" immediately after replay startup. Later
telemetry shows both DX11 and native input available, but the desktop session
never marked the editor active. Its console events were left queued.

Startup now waits for enabled editor configuration, a paused replay view,
DX11 and input readiness before sending a flight command. Foreground focus is
not a readiness requirement. Pending native configuration or DX11 setup can
recover without latching a camera fault. Input-hook failure still rejects
manual control. An initialized panel remains connected to console, Stop and
retry actions after a failed flight entry.

Ownership changes previously cleared the recorded shortcut edges. Duplicate
raw/window messages or auto-repeat could turn a held F7/F8 into new toggles.
Those edges now survive ownership changes and shortcuts fire on their own
fresh key-down transition. F8 can close the console after hideconsole is
confirmed; configurable text keys remain available for console typing.

The in-game panel uses the desktop slate/teal palette, installed Windows
Segoe UI fonts with fallback, grouped controls and a persistent Stop/status
footer. Replay timing is the default capture mode. Path interpolation and
the native DOF evaluator are unchanged.

## Checks completed here

- Python suite: 691 tests run, no failures, one Windows-only skip. New regressions cover
  delayed DX11/input readiness, foreground independence, timeout/cancellation,
  and console-event dispatch after startup fails.
- The actual desktop Cameras page was rendered at 1000x700. Replay timing is
  selected initially, interval spacing is disabled, and selecting Timed shot
  enables the interval field again.
- All current native Windows sources compiled and linked into the x64 helper,
  bridge callback harness and DX11 WARP overlay harness. PE architecture,
  required exports, static C++ runtime and metadata/hash were inspected.
- Existing native path, effect and flight evaluator checks passed on Linux.
- New Windows callback tests exercise production input handlers with duplicate
  F7/F8 messages, asynchronous console confirmation, typing, movement handoffs,
  and deferred DX11/configuration readiness. Compiled here; execution remains
  a Windows CTest check.
- Panel layout preview uses the actual ImGui draw code with a headless software
  renderer and substitute Linux fonts. It checks geometry and clipping, not
  the game's GPU, Windows font rasterization, or ReShade.

## Remaining validation

This Linux workspace cannot run Deadlock or the packaged Windows EXE. The new
Windows CTest regressions and DX11 smoke test must run in the GitHub build.
Use STAGE1_TESTING.md to check launch, F7/F8, paused movement and captures in
the game. These fixes are not yet confirmed in a live Deadlock session.

The prior supplied GitHub run 93281580262 passed all five Windows native tests,
686 Python tests, the EXE build and its startup checks before source packaging
failed on a local context-file entry. That entry and its documentation link
were removed; the source export remains independent of local context files.
Those earlier Windows results do not validate the new 0.4.1 native changes.

## Earlier validation records

# Validation — 0.4.0 alpha, Stage 1

Stage 1 is a test candidate for Windows and the reviewed Deadlock builds.
The existing rendered-view path interpolation and typed DOF evaluator are
retained. New code adds native manual flight, DX11 editor rendering, shared
input actions, automatic startup and a reorganized desktop editor.

## Verified in this workspace

- Python regression suite from a clean extraction of the full source ZIP:
  686 tests completed successfully, with one Windows-only skip. Coverage
  includes queued console/game-UI ownership, startup configuration races,
  capture snapshots and Stop recovery.
- Native path, effect and manual-flight evaluator tests run on Linux. Flight
  checks cover frame-rate independence, Z-up movement, diagonal normalization,
  mouse sensitivity, long-frame discard and preserved framing/bank.
- Fresh Windows x64 DollyNative.dll and both Windows test harnesses compile and
  link. PE architecture, required exports, static C++ runtime and the matching
  ABI 3 build metadata/hash are verified.
- The callback harness includes manual seed/hold, replay resume, effect phase,
  capture snapshot/ring overflow and acknowledgement-lock contention cases.
  These Windows callback tests were compiled, not executed here.
- The DX11 WARP harness checks actual panel pixels, context-state restoration,
  resize/backbuffer release and shutdown when executed by Windows CTest.
- All five desktop tabs were rendered at 1000x700 and 1500x1050 with normal and
  150% scaling using Xvfb. The layout fits without page-level scrollbars. This
  checks layout with available Linux fonts, not Windows font rendering.
- Dear ImGui files match their pinned upstream SHA-256 manifest. Full source
  packaging includes all new CMake sources, headers, tests and required notices.
  Supplied game DLLs, private settings, logs and recordings are excluded.

## Required Windows and in-game validation

### Windows build log received September 10

The supplied GitHub logs for run 93278796097 show a successful MSVC native
build and all five Windows CTest checks passing: path, effects, flight, bridge
callback and DX11 WARP overlay. The run then stopped at one of 686 Python
tests. Its expected replay command retained the short Windows TEMP spelling
RUNNER~1, while the launcher correctly used the resolved path.

The startup test now compares the resolved path. Its fixture includes an
existing parent-directory alias and a directory with spaces, reproducing the
same mismatch on Linux. The original assertion fails on that fixture; the
corrected startup suite passes and still checks exactly one quoted replay
command after unlocker initialization. No application or native code changed.

The EXE packaging/startup steps were not reached in that Windows run. A fresh
Windows build is needed to complete them. These synthetic Windows checks do
not establish live Deadlock or ReShade compatibility.

### Remaining game checks

No Windows executable, DX11 WARP harness, or Deadlock session has been run in
this Linux workspace. Cross-compilation does not establish that native input
routes, foreground focus, console closure, renderer state or ReShade work in
the installed game. The GitHub Windows workflow rebuilds the native helper,
runs CTest and Python tests, verifies the executable icon, and starts the
packaged editor. It does not launch Deadlock or publish a release.

Use STAGE1_TESTING.md for automatic startup, held camera/hero switching,
F7/F8/F9/F10, capture, Alt-Tab, 0.1x paths, repeated shots, supported DOF and
resize tests. Live results should accompany any pre-release. Stage 2 world
markers/full in-game curve editing and Stage 3 video/render-pass export are
not implemented by this candidate.

## Earlier validation records

# Validation — 0.3.13 alpha

Static review covers the supplied updated client, engine2 and tier0 files.
The new engine mapped sections match the previous engine except for debug
entry timestamps and CodeView PDB age. Code, data, exception entries, relocation
entries and image layout are unchanged. For tier0, 3,530 saved instructions
match, including every byte of the complete 633-byte typed setter. The current
lookup and data-accessor functions were disassembled and their ABI/table slots
reviewed; those RVAs and the scalar layout remain unchanged.

The new compatibility profile records these hashes and review evidence.
Startup retains both old and new reviewed module fingerprints. An unknown
build remains blocked, and all mismatching module names are reported together.
No other game DLL is a native-camera compatibility dependency.

The Windows EXE and current game are not executed in this Linux workspace.
GitHub Windows CTest and an in-game camera/DOF playback check remain required.

## Local 0.3.13 checks

- The actual three uploaded game files pass the updated launcher compatibility
  gate using the newly compiled helper and its verified metadata.
- Python suite: 616 tests, OK, one platform-dependent skip.
- Launcher tests cover unknown builds and reporting all changed modules together.
  Profile, launcher and native pins match for all three game modules.
- Windows x64 helper cross-compilation and PE export/import checks passed.
- Native camera callback and DOF writer match 0.3.12 apart from fingerprint
  gates. Camera controller, interpolation and paused navigation are unchanged.
- Full source ZIP checked against SOURCE_FILES.txt and every CMake source path;
  the complete DOF source/header/test files are included.

## Earlier validation

# Validation — 0.3.12 alpha

Static comparison reconstructed both previous camera functions from saved
disassembly and reproduced their recorded SHA-256 hashes. Against the supplied
September 9 client, the only six differing instructions were original call
targets shifted by 0x90. Each new target starts a PE runtime function.
The relocated framing helper has equivalent instructions and identical
absolute RIP-relative data/import targets. The globals setter and view table
matrix entry match. The image size and bridge offsets are unchanged.

The compatibility profile records the candidate hash and reviewed differences.
Engine and tier0 pins remain unchanged. This review does not establish that
the current installed engine/tier0 files match: startup still verifies them.
Current-game playback and the Windows executable are not run in this Linux
workspace; the GitHub Windows build and an in-game test are still needed.

## Local checks for 0.3.12

- Python suite: 615 tests run, OK, one platform-dependent test skipped.
- Native path and effect evaluator tests: passed on Linux.
- Windows x64 DollyNative.dll: cross-compiled, exports/imports and package
  fingerprint verified. Windows callback execution remains a GitHub CTest gate.
- Previous and new approved client fixtures accepted; a third unknown client
  rejected. Launcher, native bridge and profile client pins agree.
- Camera controller, paused movement, interpolation and native DOF sources
  match the 0.3.11 source archive byte-for-byte.

## Previous 0.3.11 validation

# Validation — 0.3.11 alpha

This revision adds native frame-phase DOF tracks to the working native camera.
It is a release candidate pending GitHub Windows gates and an in-game test.

## Verified implementation

The uploaded tier0 SHA-256 is
`b4300eb0abfe73e1e877516ab6b8bdd1a1bdb4ffc47c7515a349d0623b852f69`.
Disassembly identified VEngineCvar007's table and find/data functions, the
16-byte reference layout, scalar type/value fields, and the typed setter at
RVA 0x20ec60. That setter filters, clamps, increments the change count and calls
native change callbacks. Runtime checks enforce the exact module fingerprint,
interface table, entry points, types and flags. The client DOF graph builder
reads scalar values from the same value storage (including focus/aperture and
mode); these reads are recorded in the bundled compatibility profile.

Seven explicit effect IDs are accepted; no command strings, unknown variables,
per-user or server-controlled cvars enter this native writer. Camera and effect
coefficients are compiled once and published together using bridge ABI 2.
Every supported effect uses the camera phase and a typed readback. Failure
stops playback rather than reverting silently to console streaming.

The existing native phase/clock, camera evaluation and aspect conversion block
was compared verbatim against 0.3.10. Camera interpolation source, navigation,
pacing and manual paused movement methods are unchanged. Controller changes are
limited to native Play preparation, monitoring and the held-camera handoff.

## Checks completed here

- Full Python suite: 611 tests run, 610 passed, one Windows-only mapping test
  skipped. This includes the actual C++ path/effect evaluator parity runners.
- Final focused native suite after two additional regressions: 71 tests run,
  70 passed, the same Windows-only mapping test skipped. Added coverage checks
  fixed-value restore overrides and refusing a mismatched effect/camera phase.
- C++ evaluator compared with editor output at 394 times, including subtick
  samples, nonuniform smooth curves, exact step transitions and endpoints.
- Corrupt/truncated shot envelopes, unknown effect IDs, oversized counts,
  unsupported cvars and invalid switch/range values are rejected.
- Native controller checks cover no animated console writes, repeated DOF
  shots, retained final effects, Stop restoration and early rejection.
- Updated Windows x64 DLL and actual callback smoke-test executable compiled
  using Zig/Clang. PE architecture, exports and runtime dependencies checked.
- The Windows callback harness covers native scalar binding, shared phase,
  boolean/integer switches, held phases, wrong types, rejected setters, failed
  restoration/retry, explicit restore values and editor-heartbeat loss.
  It was compiled, NOT executed here. GitHub's CTest gate executes it on Windows.
- Source/update archive allowlists exclude logs, replays, personal settings,
  caches and all supplied game DLLs, including tier0.dll. The update is checked
  to reconstruct the full source from 0.3.10 exactly.

## Not established by these checks

No Windows EXE or game session was run in this workspace. Native cvar readback
is not a pixel-level verification of final GPU output. Neither the synthetic
harness nor static disassembly guarantees all game rendering conditions.
The earlier 0xC0000005 game exit still has no supplied crash stack; this update
is not claimed to fix that separate crash.

Before publishing, build a fresh Windows Actions run from this commit. Use a
focus/aperture pull with the DOF preset at 1x and 0.1x, check Frozen preview,
play the shot to completion three times, and verify Stop restores effects.
Export diagnostics if any phase/readback error appears. See NATIVE_EFFECTS.md
for the exact supported controls and user workflow.
