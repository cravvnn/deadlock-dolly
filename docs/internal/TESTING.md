# Development checks

The native callback smoke also checks Follow anchor ownership during game-owned
POV arm/play/hold, retaining replay, lease, active Follow and camera-writing
refusals. These synthetic-memory checks do not execute installed game hooks.


Regular Follow keeps its crouching pivot equal to the current standing pivot
so a brief crouch blend cannot lower the selected view. P12 exercises the real
controller/console write and full F9 restoration; `test_follow_stance.py` adds
nondefault XYZ/update and failed-write/restore-retry checks. FollowLens projects
the added stance write and restoration; existing identity, lease and native
anchor guards are unchanged. This does not qualify every hero or replay.

The native rail owner checks recovery from an attached Bone Picker preview in
Campaths. Active picker inspection now disables Delete/Clear in the drawer,
matching existing native admission, and exposes the existing Cancel Bone Picker
action. Delete/Clear remain refused while preview owns the camera; the visible
Detach preview control sends the existing AttachPreview-off action. It remains
inert during an operation, an unpaused view, shot playback or recording. After
the existing detach operation acknowledges a free view, Delete and confirmed
Clear All are available. The regression fails on the UI without that recovery
control. This presentation change adds no stored state, wire action or worker
transition; existing preview/detach ownership and camera-edit guards are retained.
The fixture checks the real native controls with observed enqueue output, not a
live game transition or the reporter's exact session.

The mod-inspection owner also checks a real replay-storage junction beside a
mixed skin VPK, preservation through session mount merging, no target traversal,
and continued refusal of malformed packs and render-only content. Replay folders
cannot supply skin evidence. This is offline mount preparation, not in-game skin
rendering qualification.

`test_authoring_audit_boundaries.py` adds four owner checks: popup selection
must still match the visible list and an enabled combobox; leading-camera
deletion retains sampled scalar/vector effects at the cut; replay-rate retiming
scales source-arrival durations in native packets and rejects overflow before
mutation; paired color/depth frame evidence must agree. The audit test uses the
real VideoExport finishing adapter and checks that completed status/counts and
all take-file bytes remain unchanged. Its inert video bytes prove presence,
not decoding. The source suite uses the normal private Windows desktop for Tk.
PopupSelection is adjacent to the popup owner, required by the model gate, with
two load-bearing mutants. Actual native arrival weights and synthetic encoded
frame order are separately checked at preflight; real game/hardware encoder
qualification and packaged GUI smoke remain receiving-release obligations.

`test_metadata_boundaries.py` owns five additional contracts: incomplete mod
inspection cannot become mixed-skin permission; dump reads enforce their cap;
post-spawn source-launcher logging failure preserves the admitted PID; invalid
completion markers cannot block later valid staged reuse; optional art manifests
retain flat-colour fallback. Tests use owned files, an NTFS junction on Windows
(a symbolic link elsewhere), controlled I/O faults and a real harmless child
inheriting its log. No game, editor GUI, network, clipboard or installed user
settings are accessed. ManifestScan includes recursive completion-marker failure
and six mutants. Byte bounds stay with real readers. Packaged GUI/update checks
remain receiving-release obligations. Existing tests and assertions are unchanged.

[Maintainer references](README.md) Ãƒâ€šÃ‚Â· [Current user documentation](../README.md)

Run the core user contracts and compact supplements from the checkout root:

```console
python -m unittest discover -s tests -q
```

The portfolio prioritizes safe startup after a game update, preserving the game
install and user files, and completing native camera/Players recordings honestly.
Keep the 15 core contracts with a bounded set of related failure scenarios;
do not hide the retired suite behind a runner or test implementation text.

- `test_core_install.py` owns P01 reviewed-build admission and additive published
  definitions; P02 launch, journal-before-write and exact restoration; P03
  interrupted-session recovery; P04 deletion of only owned temporary files.
- `test_core_native.py` owns P05 native preparation/playback/watchdogs/handoff;
  P06 owned Players progress and drain-before-restore; P08 verified native seeks;
  P09 rendered-pose/tick capture and replacement.
- `test_core_workflows.py` owns P07 recorder admission and cleanup; P10 authored
  motion/retiming/native parity; P11 USD/AE camera export; P12 Follow/Bone and F9;
  P13 shot persistence/validation; P14 settings preservation/migration; P15
  verified update installation, recovery and explicit consent.

These tests use real product code and owned temporary files, with simulated
processes, console/native telemetry, network responses and deliberate I/O faults.
They check independently known bytes, numeric values, output data and event
order. P01 checks generated compatibility data as part of its normal run. The
standalone equivalent is `python tools/generate_compatibility.py --check`.

P10 compiles the actual C++ path parser/evaluator and checks Python-generated
paths against independent numeric expectations. Windows uses CMake and Visual
Studio 2022 x64, matching the release toolchain; missing tools fail the test.
Linux uses `g++`; without it, that portion warns and Python wire checks still
run. The release native gates remain necessary. If Tk cannot import, the updater's UI imports are isolated
behind test stubs. P15 exercises real consent policy with a fake notice widget;
no window is constructed and those imports are restored afterward.

[CORE_TEST_AUDIT.json](CORE_TEST_AUDIT.json) maps each contract to recent release
fixes, selected decision-function coverage, seeded faults and remaining gaps.
Two or more targeted faults per contract must be detected without parse/import
errors or unrelated invalid fixtures. Coverage is a smell check on those owning
decisions, not a whole-application percentage target or proof of correctness.
The remaining retired detailed suite remains in Git history; its full coverage is not
preserved by these 15 contracts.

Run these fast source checks for runtime changes. Keep live and packaged checks
proportional to the change:

- After each game build, qualify reviewed offsets/hooks in a local replay before
  publishing definitions or a release: ready paused camera, capture, four exact
  seeks, shot playback, F9 and exact file restoration on exit.
- For a release, retain native compilation/ctest, PE/ABI and packaged executable
  gates, plus affected output qualification when recording behavior changes. Verify
  Players/depth/audio/EXR output when their implementation changes.
- When updater/bootstrap ownership changes, qualify the actual Windows process
  handoff and file locks, including one update from the preceding release.
- When UI behavior changes, use packaged GUI smoke and inspect the changed
  interaction/layout. ReShade, Discord, replay repair, DLSS, graphics profiles
  and reconstructed audio receive targeted manual checks when touched.
- When a modeled protocol changes, run its maintained TLA+ checks and update the
  source/spec mapping. A model result does not replace implementation tests.

The source suite does not qualify Windows, live game/render/encoder behavior,
all GUI orchestration, abrupt process death or every protocol interleaving.
The audit records which checks actually ran and which remain unqualified.

For a Windows release, use the normal builder with the qualified Python
environment described in [BUILDING.md](../BUILDING.md):

```powershell
& .\.venv\Scripts\python.exe tools/build_windows.py
```

The builder runs the source suite and retains its existing native compilation,
model checks, and packaged executable smoke gates. Those gates are separate from
the Python source tests. An explicit `--output-dir <new-folder>` preserves
an older candidate with the same version; it refuses to overwrite an existing
release archive or checksum.

The source-side TLA+ models can also be checked independently with Node 20 or
newer and the pinned npm dependencies:

```console
npm ci --prefix tools/tla --ignore-scripts
python tools/check_tla.py
```

[SPECS.md](../../SPECS.md) describes their scope and bounds. Passing a model
check does not establish source correspondence or in-game behavior.

## Manual integration checks

`test_admission_cleanup.py` owns three distinct regressions: provisional console
socket cleanup/transfer, failed preenqueue notification/reentrancy and real worker
retry, and camera-export cleanup result preservation. Real unconnected sockets,
owned temporary files and hard links are used; factory, reader and failures are
simulated. No network/game/AE/USD application is contacted. Private pilot matrices
stay outside discovery. SocketSetup, Admission and ExportCleanup models/API/
mutants run in the normal model gate; their finite contracts do not replace
native, packaged or live qualification at those boundaries.

`test_resource_boundaries.py` adds three compact regression contracts for gaps
outside the core workflows: bounded local definitions reads (including valid
oversized prefixes), warning-episode quiescence with reset/rearm, and Windows
stdio inheritance restoration after the first callback fails. The latter uses
real owned handles and injected failure; it skips off Windows. Private matrices,
large-file allocation measurements and child/GUI adapters stay in analysis.
MemoryWatch and RestoreHandles models run in the normal model gate with API
refinement and two invariant mutants each. These checks do not qualify engine
stdout/DLSS, real process memory usage or live game behavior.

Select the checks for the behavior changed. Record the tested commit, Dolly
and game versions, driver, result and any limits. Automated checks and a
packaged editor launch do not establish in-game behavior. Use a local replay
launched through Dolly with its normal development and insecure-mode gates.

| Changed workflow | Observe in the actual integration |
| --- | --- |
| Startup and recovery | Selected replay reaches a ready paused camera; F7/F8/F9 preserve input ownership; normal exit restores session configuration. |
| Camera capture and selection | Capture/Replace retain the rendered pose and tick. In-game capture keeps playback running by default; enabling Pause after capture pauses it. Desktop capture waits for a paused view. |
| Replay and camera seeking | In Replay, select another saved camera and use Seek camera; verify its saved time and view. Typed replay seeks target replay ticks. In Campaths, Seek here uses shot time. |
| Path and curve editing | Desktop and in-game changes agree; handles and live guides reflect edits; Play shot, frozen preview and Stop / restore behave as documented. |
| Follow and Bone Picker | Change replay participants, check framing and motion, then return to Fly camera. Verify attachment identity, bone selection and any saved binding affected by the change. |
| Video, stills and layers | Verify completion, decoded frame counts, output layout and the affected depth/matte/audio behavior using the limits in the current export guides. |
| Updates | Verify the packaged notice, explicit Update now consent, busy-state refusal, restart and interrupted-transaction recovery without losing user files. |

Use the [current user guides](../README.md) for exact labels and options.
The Stage 1/2 checklists are historical integration records and may contain
retired UI, defaults and build instructions. Untested behavior remains
untested; do not carry a result to a different binary or game build.

## Focused regressions and temporary investigations

The core portfolio is a starting point, not a test-count ceiling. The maintained
source suite also retains replay repair, helper setup and Players scratch owner
regressions, plus two real child-process updater/startup schedules. These protect
publication-before-verification, foreign-file deletion, retry blockage and
recovery-resource races that the headless core and abstract models do not establish.
Run all maintained source tests with the discovery command above.

Before adding a permanent test, name its observable contract, plausible regression
and gap in current proof. Prefer the existing owner and independent expected
results. Add a new test only for a distinct risk; demonstrate that it detects the
intended defect. Temporary probes and one-off investigation harnesses belong in
ignored analysis/ directories, outside discovery and source exports. Promote
only durable regression proof. Do not delete valuable proof to meet a quota.

TLA+ remains a bounded protocol/interleaving check. Keep implementation witnesses
for critical model contracts and reconcile their mapping when code changes.
Model mutants establish model sensitivity; they do not establish code sensitivity.
Use targeted checks during development and one normal complete release build;
reuse passing proof while relevant inputs match. Live/recording checks need their
own current authorization and are selected for the changed integration boundary,
not automatically requested for every source-only repackage.


## Output ownership supplement

`test_output_ownership.py` adds three bounded owning contracts to the compact
portfolio: Players cancellation/encoder errors survive denied MOV cleanup and
both files are attempted; still PNG failures preserve unrelated partial files
and matte/preview bytes decode correctly; first-use ReShade preset failures
leave no incomplete final and retries preserve existing user edits. The worker
uses a real owned Python process with a local protocol stand-in, never FFmpeg or
the game. Deletion/row/write refusal boundaries are injected. These are distinct
gaps, not restored retired tests. CORE_TEST_INTEGRATION.json records the current
portfolio inventory. Private exhaustive/fault/process probes remain outside discovery.

## Reviewed failure-boundary supplement

`test_review_hardening.py` adds ten compact owner regressions: layered admission
rollback/accepted-worker ordering; replay-thread startup status; malformed camera
bundle numbers/version; exact EXR channel shape; minidump stream containment;
read-only audit I/O findings; pre-admission scratch cleanup; ReShade write results;
history reset allocation/isolation; and updater-thread failure/explicit retry.
Fixtures use real local files, known numeric bytes and queue methods. Thread,
permission, write and network boundaries are simulated. History counts a real
copy allocation while checking saved content, not source text or a saved index.
The current inventory is 70 methods, including the 15 core contracts. No retired
matrix is restored and no network/game/application renderer is exercised here.
Individual larger private probes, real Windows locks, 400 history traces and a
standalone actual C++ EXR writer remain matching-input evidence in analysis.
Six source-adjacent production-only models/API/config/mutants join the required
gate; four one-call validation/diagnostic scopes have test-only rationale in SPECS.

## Scene appearance supplement

`test_scene_appearance.py` adds three owner contracts: opacity toggle exact
restore and failed-readback retry; glow/health hide choices (including already
hidden external settings and both bar backends); and eight scene flag values
in the existing final reserved wire byte. The baseline methods fail the opacity
and hide-intent regressions. Current discovery is 73 methods. Native rail
regressions exercise real checkbox dispatch, keyboard access, viewport fit
and playback guards at 720p, 1080p, 1440p and 4K. Render captures use the actual
renderer and embedded fonts over a synthetic background; no game proof.

## Next ten review supplement

`test_next_ten.py` retains ten real-owner regressions from the qualified
private review, with routine free-shot and Tk iteration counts reduced while
retaining distinct failure boundaries. The portfolio now has 83 methods.
Cases exercise real mmap/heartbeat/Tk objects and local files plus controlled
console/filesystem/worker failures. They cover saved outputs, explicit override
policy and invalid settings backups. No network/game/GPU qualification is
inferred. Six source-adjacent production-only models join the required gate.
Private broad comparisons, Windows lock/junction and software still probes are
retained separately and not repeated behind a permanent suite wrapper.


## Numeric and binary boundary supplement

`test_numeric_boundaries.py` adds six contracts for invalid still pixels,
overflowed camera comparisons, optional FFmpeg lookup I/O, linear attached-key
compilation with known intervals, finite offline normals options and EXR
metadata-alias rejection. The portfolio now has 89 methods. PNG bytes, decoded
intervals and numeric outputs are checked independently; filesystem errors are
injected. A work budget detects repeated scans, not game FPS. Broader paired
attach-format comparisons remain private qualification evidence. No game,
long-recording, lifecycle or general OpenEXR compatibility proof is inferred.

## Capture boundary supplement

`test_capture_boundaries.py` adds seven contracts for shortcut refusal/retry,
both movie-audio external environments and failure unwind, finite projection
pixels, duplicate VPK listings, optional helper I/O fallback, exact fade bytes/
copy bounds and provisional GDI admission/cleanup. The portfolio is 96 methods.
Real threads and temporary files/WAV alignment are used; external process/input
and GDI failure returns are simulated. No renderer/game/packaged-output proof
is inferred. Three maintained adjacent specs/APIs join the required gate, with
seven intended invariant mutants. See SPECS.md for mapping and finite bounds.

## Audio and camera-export supplement

`test_audio_boundaries.py` adds eight owner contracts; the portfolio is 104
methods. Real CSV/WAV parsing, independent interpolation/output values and
explicit allocation/read budgets cover streaming, digest and marker changes.
Child/environment, cleanup-refusal and competing-output interleavings are
controlled; fitting covers either silent stereo channel, mono, ignored extra
channels and invalid consumed samples. Larger resampled/loopback/tiny/silent
fixtures and real Windows file-lock/DLL-directory/process probes stay private
qualification evidence. No actual converter, acoustic/game fidelity, native
DLL or packaged-output result is inferred from the source suite.

Three source-adjacent specs in tools/ join check_tla's explicit configuration
list; the gate checks both runtime and tool contracts. Coverage, assumptions,
six invariant mutants and the one-call modeling exceptions are in SPECS.md.

## Reviewed owner supplement

The portfolio now has 110 source methods. `test_reviewed_owners.py` adds six
contracts for competing diagnostics ZIPs, project-save cleanup results, verified
export-timing transitions, independent recovery after malformed JSON, reviewed
file-cache identity/budget and typed KeyValues failures. The existing history
allocation keeper in `test_review_hardening.py` also checks matching saved-snapshot
reuse and mutable-caller isolation. Larger trace/fault matrices stay private.

`native_sequence_writer_start_tests` substitutes only worker construction in a
generated copy of the production header. Both real output modes must retire a
refused start and permit a flushed retry. CMake checks/reconfigures that creation
boundary; no production failure-injection API exists. The uninstrumented scratch
test separately covers normal asynchronous operation. These are Windows native
tests, outside Python discovery; ordinary native CTest includes them.

Six adjacent model/API/config/mutant sets join the required gate, including
`native/include/WriterAdmission.cfg`: 64 configurations and 145 intended mutants.
See SPECS.md for the precise bounds. Simulated console replies do not qualify
actual game timing. Real temporary files/handles and local native compilation
do not establish packaged DLL, GPU, encoder, acoustic or live replay behavior.

## Reviewed completion supplement

The portfolio now has 113 methods. `test_completion_boundaries.py` adds three
contracts: attempted audio/video/ReShade cleanup and mapping retirement despite
independent stop errors; terminal replay-reader results for malformed bounded
JSON with current/stale identity checks; and typed graphics-library errors that
leave user bytes unchanged while preserving normal selection. Actual parser,
worker publication and finalization owner methods run; subprocess/native replies
and ordinary errors are controlled. No game, renderer, full GUI startup or real
encoder output is exercised. The two adjacent production models/APIs and four
mutants join the 66-config/149-mutant normal model gate. Native/package/output
qualification remains with the receiving release thread under normal gates.

## Startup and audit supplement

The portfolio now has 118 methods. `test_startup_audit_boundaries.py` retains
five contracts for console reader start/cleanup/retry, native/console/POV worker
refusal and recovery, original read-only mode after failed Windows replacement,
decoded color-count findings and bounded malformed audit JSON. Windows file locks
and owned sockets/readers are real; native/replay/process replies are simulated.
The Windows lock portion is platform-specific; portable write-error checks remain.
Larger private probes stay in analysis. No decoder/game or packaged-output result
is implied. Three adjacent model/API/config sets and six mutants join the normal
70-config/158-mutant model gate. Release native/package/output gates remain needed
at their own boundaries. No maintained test or production timeout was removed.

## Player draw proof lifetime

`native_player_draw_slots_tests` compiles the actual private capture owner into
an owned WARP executable without configuring or installing game hooks. It
streams 1,024 distinct snapshot records through 128 slots in one image's
accounting, checks both completion barriers and failed-proof retention, and
releases the staging pool. Redirect completion is supplied by the harness;
this does not establish full redirected-image coverage or hardware throughput.
Existing matte/vertex tests retain pixel and shader checks. The lifecycle test
covers the reusable predicate; DrawProof models the bounded completion join.
Private negative copies restore whole-frame retention or omit success barriers
and must fail the actual-owner test. No game or hardware encoder runs here.

P06's failure-before-ack regression checks foreign telemetry, GUI recovery dispatch,
request/timing retention on retry and failed terminal status after cleanup.
The native actual-owner WARP test checks failed-image retirement, independent
image/proof barriers, callback close, repeated polls and completion-only Present.
GPU submission/verification is real WARP; pending flags in failure-barrier cases
and desktop telemetry are simulated. Game wall-time behavior is not qualified by
these tests; production timeouts remain unchanged.

Live failure follow-up: terminal pacing publication bypasses the 250ms progress throttle, because no later tick republishes after capture closes. The actual-owner test verifies a real pacing file transitions from progress/error0 to terminal/error1 immediately; the old throttle fails it. CaptureFailure models terminal visibility with an additional required-publication mutant.

`test_reviewed_runtime.py` retains six compact contracts: camera result isolation, refused monitor/watchdog cleanup with actual owned children, real Windows locked partial error preservation, optional audit failure/cache completion, recursive definitions fallback through launcher pins, and realistic per-frame metadata plus actual read bounds. Native media_disconnect_tests compiles the actual media owner with controlled external services/clock; checks inactive disconnect, starting/recording/finalizing grace and unnamed shutdown. No game/real sound-hook flush or FFmpeg output qualification is inferred.


## Capture completion supplement

test_capture_completion.py adds five compact owner contracts: bounded actual
movie-audio map reads and typed malformed-input failure; acknowledged cancellation
and retired stop authority; optional preview completion; primary Players result
preservation with strict captured-state admission; and layer/queue raised admission
versus ambiguous publication. The existing test_review_hardening refusal keeper
continues to cover explicit False and pre-submit ordering. Owned files and real
queue workers run; console, encoder and denied I/O boundaries are controlled. No
game, real FFmpeg output, PCM fidelity or whole-GUI recovery is inferred.

MovieCancel and LayerQueueAdmission join the normal model gate with six expected
mutants. Current inventory is emitted by tools/check_tla.py --json. Minimal API
refinement and bounded composition evidence do not establish a whole-program proof.
The maintained source suite now has 130 methods, including the draw-proof keeper
and six reviewed-runtime owner tests from the preceding batch.


## Audio retirement and resource inventory supplement

test_audio_inventory.py adds three owner contracts: audio temporary cleanup must
preserve completion/primary stop errors; loopback filtering retains any overlapping
lifetime of a reused voice key and downstream paired evidence; VPK listing includes
CRC-readable preload-only resources while retaining duplicate/entry limits. Real
PCM/files and production mux/parse/presence adapters run; conversion, subprocess,
source-stop and ordinary cleanup faults are controlled. Real Win32 sharing-lock
probes remain matching-input private qualification, not game/acoustic proof.
The maintained suite now has 133 methods. AudioWorkRetirement and VoiceWindowReuse
run in the normal model gate with three expected mutants and minimal API properties.

`test_recorder_updater.py` owns five related failure contracts: ordinary Stop keeps
runtime overrides until terminal telemetry (pre-admission unwind remains covered),
unreadable recorder status retains ownership until valid terminal/confirmed exit,
manifest actual read bounds and recursive bad-stage skipping, conservative journal
cleanup with real installation locks, and primary write-error preservation under
real Windows deny-delete temporary locks. The fsync error and process/native
observations are controlled; real game/encoder and packaged update acceptance are
not inferred. Keep test_update_handoff_process's actual child/lock/crash/rollback
checks and existing P07/P15 contracts; these additions do not replace them.


`test_shot_definitions_audit.py` adds four contracts: one strict shot/version
snapshot across BOM and actual file replacement; first definitions read versus
concurrent lookup/refresh/withdrawal; bounded typed camera-bundle loading with
actual audit/CLI continuation; and declared nonempty video evidence before audit
success. The camera byte-budget case checks the default read argument then4KiB
exact/overrun fixtures. Test video bytes prove presence only, not valid media.
GUI/network/process inputs are controlled; cache I/O, threads, parsers and audit
owners are real. Keep existing P01/P13 and reviewed-runtime fallback/preservation
assertions. The obsolete private loaded flag disappears from its save/restore
fixture without changing assertions. DefinitionsFirstRead/ProjectOpenSnapshot run
in the normal model gate with three expected mutants and bounded API projections.


The combined camera/bridge ownership keepers are test_camera_session_contracts
(ledger and prepared/release/worker/capture contracts) and test_bridge_lifetime
(real owned anonymous mmap heartbeat teardown, command-lock independence and
coherent native-status readback). No game is launched. The mmap test uses the
qualified local atomic helper on Windows, a real heartbeat thread paused by a
thread-local trace at _store entry, and actual bounded close/join. Release can
time out because there is no native consumer; its ordinary cleanup must still
run. Resume/join cleanup runs even on a failed assertion. These keepers cover
distinct failure schedules; P06 remains the owner of end-to-end drain/retry.

The ledger checks all exported code/model roles and normalized implementation
fingerprints. Review changed source dispositions and model assumptions before
refreshing hashes. New guarded models are required by tools/check_tla.py; native
snapshot publication has an explicit native/src target. A green finite model
does not establish whole-program, live-game or native weak-memory correctness.
