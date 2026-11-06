# Maintained model coverage

The worker/file-output supplements are described at the end of this inventory.

This inventory covers the models currently maintained beside their source. It
does not claim whole-program coverage. Models run locally via
`python tools/check_tla.py` and before native compilation in the Windows builder.
Node 20 or newer and `npm ci --prefix tools/tla --ignore-scripts` are required
for this development check. No hosted checker or workflow is needed.

| Component and callers | Spec | Contract and bounds |
| --- | --- | --- |
| `session_cleanup.remove_overlay`; Session.close, recover_pending, recover_orphans, detached waiter | [CleanupMarker.tla](dolly/CleanupMarker.tla), [OwnershipApi.tla](dolly/OwnershipApi.tla) | One validated unmounted overlay, one late plain user file, final-directory failure and staged marker repair. Successful compensation restores retry evidence. Crash/repair failure are unresolved terminal outcomes; no universal crash-recovery or hostile namespace-race claim. Actual cleanup/recovery regression preserves late user bytes. |
| `update_worker.rollback`; called by `install` on failure and `_apply_plan` during recovery | [UpdateRollback.tla](dolly/UpdateRollback.tla), [RollbackApi.tla](dolly/RollbackApi.tla) | Three sorted owned files: replacement, addition, removal; each interrupted install prefix; up to two process restarts during rollback. Restore all original contents before acknowledgement and pending-marker removal. Backups stay immutable. |
| `update_worker.replace_file`; used by installation and rollback | [AtomicCopy.tla](dolly/AtomicCopy.tla), [FileOpsApi.tla](dolly/FileOpsApi.tla) | Single-file temporary copy/flush/replace and process interruption. Preserve the source and publish original bytes. Temporary leftovers after abrupt exit are allowed; normal failures run cleanup. |
| `release_launcher.main` ordinary startup, concurrent `_apply_plan`/`install` | [LauncherHandoff.tla](dolly/LauncherHandoff.tla), [RecoveryResourcesApi.tla](dolly/RecoveryResourcesApi.tla) | One writer admitted before startup or attempting admission while startup owns the shared install lock; one old staged workspace, one file, one optional writer exit, one editor handoff. Pending check, cleanup and handoff share ownership. Target-side staging and commit are separate, allowing delayed executable replacement. |
| `cleanup_workspaces`, `_workspace_finished`; sole production caller `release_launcher.main` | [WorkspaceCleanup.tla](dolly/WorkspaceCleanup.tla) | Lower projection of the caller-held lock around eligibility and removal. Backup unlink and directory removal are separate actions. Preserve recovery resources and unfinished journals; PENDING prevents cleanup. This helper does not reacquire the caller's lock. |
| Input validation, reviewed-build admission, manifest verification and explicit update consent | Core Python contracts P1, P13 and P15 in [TESTING.md](docs/internal/TESTING.md); outside these implementation models | Tests exercise validated files and simulated network/process boundaries. Models still assume correct hashes and OS lock semantics; source tests do not qualify a published game profile or packaged Windows update. |
| Game-process admission and session recovery | Core Python contracts P2 and P3 in [TESTING.md](docs/internal/TESTING.md) | A simulated process snapshot exercises launch refusal and recovery ordering. Actual Windows process visibility/identity, kernel locks and recovery-worker parent waiting require platform qualification; they are not established by the 15 headless contracts. |
| Other stateful Dolly components | Not covered by the maintained model set yet | No inference about camera/game compatibility, GPU capture, encoding, UI state or whole-program correctness. |

The implementation model instances the per-file interface and checks refinement
of the caller's recovery interface. The lower copy implementation checks that
same per-file contract separately. Default configurations keep deadlock checking
enabled with named terminal actions. Recovery liveness assumes returning file
operations and finitely many process interruptions, not an end to persistent I/O
failures.

Actual journal entries include the manifest and every owned file. The bounded
model uses three representative file roles and abstracts exact filenames/bytes.
Source comments map actions to operations; the updater tests exercise real
manifest restoration, exact bytes, user-file preservation and interrupted retry.
The already-restored skip is retained for I/O efficiency; with immutable backups
it is not a correctness guard whose removal must break the model.

No proof is made about machine power loss, filesystem directory durability,
concurrent external writers, corrupted backups or every interruption inside an
OS call. Atomic target replacement after ordinary process interruption is an
explicit primitive assumption. Copying originals costs payload I/O and retains
their disk allocation until cleanup. The caller's smaller state graph does not
remove the shared copy helper's internal states.

Ordinary startup now creates `.dolly-update.lock` and keeps it through the pending
check, workspace cleanup and asynchronous process handoff. Busy or unwritable
locks defer startup with a diagnostic and return code 1. Health-check bypasses
remain outside this lock because updater smoke checks intentionally run during
an update. Read-only installation startup therefore changes. Recovery workers
wait for their launcher parent before acquiring the install lock.

The cleanup models start with one eligible old staged workspace; retention age,
all malformed legacy/result metadata combinations, multiple workspaces/writers,
download liveness and arbitrary external writers are outside their bounds.
CleanupReturns assumes returning file operations and weak fairness for started
operations. The interface maps needed to pending AND committed change, resources
to workspace AND backup. It is a scoped refinement, not a whole-program model.
Maintained two-process fake-install regressions exercise both ownership
orderings, delayed first commit, process exit and exact-byte rollback. Those
regressions run in test_update_handoff_process.py alongside the core
contracts; neither set establishes packaged Dolly or kernel image-lock behavior.
The former unlocked design fails the recovery contract, so its partial search
is not a state-reduction baseline. Platform qualification must still check the
real process handoff when updater or bootstrap ownership changes.

Each adjacent `.mutants.json` names deliberately broken rules. Parse/evaluation
errors and stale replacements do not count as valid mutant kills. When modifying
modeled behavior, update the implementation spec, interface mapping, mutants and
owning tests together. The checker cannot automatically extract Python semantics
or detect every model/code mismatch.

## Owned replay, reconstructed-audio setup and scratch-status publication

`PublishReplay.tla` beside replay_repair.py covers repair_replay after its
source scan/patch preparation. Empty/partial/full copy, flush/fsync/close,
source identity/size check, packet verification, no-overwrite publication and
best-effort owned-stage cleanup are separate steps. Windows rename consumes
the stage; the link config covers POSIX's remaining private alias. Another
owner may create the final before publication. Publication never exposes an
incomplete final, and cleanup never deletes a foreign final. Source/packet
parsing and record transforms remain owner tests; hostile private namespace
replacement, native POSIX certification and power-loss durability are excluded.
Crash stages can remain and consume replay-sized disk space; POSIX needs a
same-filesystem hard link. Six deliberately broken invariants guard this model.

`HelperWorkspace.tla` beside source2viewer.py covers two cache-missing setup
attempts with distinct workspace owners. Allocation, verified download,
extraction, existing per-file installation, CLI confirmation, notices, marker
and artifact cleanup are separate. The noFaults config isolates independent
progress; the default also includes returning I/O faults and operation-boundary
process exits. A locked private archive can remain, without masking a completed
installation. Four mutants protect private ownership, cleanup results, cache
confirmation and marker publication. Individual DLL moves/download internals
are abstracted: final cache publication remains shared/per-file and is not a
fully atomic or serialized installation. The clip_audio adapter still trusts
explicit external overrides and reuses the existing verified cache policy.

`ScratchStatus.tla` beside player_scratch.py covers two status writes to one
valid owned take: read, ownership validation, exclusive private open, partial/
full JSON dump, close, replacement and cleanup. Sequential retry and concurrent
variants run with/without new I/O faults and process exits; a prior orphan is
always allowed. A separate collision config checks that an unsuccessful open
does not authorize cleanup. Five mutants guard fresh progress, complete JSON,
ownership validation and cleanup results/ownership. Five configs exhaust only
these finite bounds. GUI captured/cancelled/encode_failed and cleanup's completed
values are abstracted as distinct payloads; their whole workflows are not proved.
The actual cleanup adapter preserves finished output and unexpected old files.
This writer does not fsync or lock its read/modify/replace transaction: no
power-loss, status ordering or concurrent field-merge guarantee is introduced.
Private leftovers remain; the random basename has a longer fixed length.

All three implementation models are specialized to the production design;
historical baseline comparisons stay in private analysis. Explicit terminal
Done stutters; weak fairness assumes returning operations, not an eventual end
to permanent I/O failure. Minimal API refinements check completion shape;
invariants hold the publication/ownership obligations. They are scoped models,
not equivalence, whole-program coverage, live/native or performance proofs.
Counts can increase as previously blocked retries/peers become available.
Real file/process/Windows-lock qualification supplements the maintained owning
regressions. Changes to these modeled paths must update source, specs and tests
together. The existing held legacy-graphics proposal is outside this change.

## Settings save results and update-manifest shape validation

SaveResult.tla beside settings.py models the outer validated-payload save:
temporary create/write/flush/fsync/close, delegated invalid-settings backup,
atomic replacement and final best-effort unlink. Cleanup OSError preserves the
body's success/primary exception; unexpected programming errors remain visible.
One immutable original and one new payload, returning I/O/failures/process exits
are bounded. Backup internals remain BackupPublish's contract. GUI scheduling,
external writers, power loss and directory durability are outside this model.
Four mutants protect cleanup, exception scope, content and sync. The supported
contract is strengthened, not an equivalence/state-reduction claim.

ManifestScan.tla beside updater.py models two sorted staged workspaces and the
read_manifest error boundary. Bad decoded root/version/digest types now raise
the existing validation ValueError, allowing a later matching workspace to be
selected. Schemas must be integers1/2; bool/float encodings are rejected. Four
mutants protect type rejection, continued scan and first matching selection.
Its API checks completion shape; invariants own the stronger selection rules.
Marker reads, JSON parser internals/resource exhaustion, arbitrary concurrent
mutations and filesystem namespace races are outside the scan. Workspace reuse
does not replace verify_payload or install-time checksum/consent/lock checks.

Both default configs select the guarded production contract. Completion assumes
returning operations and weak fairness; terminal Done intentionally stutters.
Core contracts P14 and P15 supplement the specs with real temporary-file and
malformed-workspace checks. Historical Windows-lock regressions remain in Git
history and require platform qualification when their ownership code changes.
No whole-program, live/game, native POSIX or power-loss certification follows.
Current settings schema and graphics recovery are unchanged.

## Participant Follow selection

`FollowLens.tla` beside `follow_camera.py` models `FollowTransaction.apply` and
`restore`: a saved descriptor, two updates, one write/readback or restoration
failure, ownership loss, retry, and disable/restore/enable ordering. Normal FOV90
and unsupported150 exercise the neutral aimed-lens admission boundary. One
opaque offset represents the independent position fields. Its five mutants
protect neutral zoom, the first snapshot, pending restoration, reviewed ranges,
and write ownership. Core contract P12 checks a neutral aimed lens, concrete
rig restoration, target identity and F9 handoff. The retired detailed Python
suite covered additional callbacks, ranges and descriptor fields; those checks
are not all retained. This projection does not certify the game or all camera state.

FollowSelection.tla beside controller.py models only the selected-player handoff:
paused unchanged/advanced ticks, transient observer mode4, camera readiness,
cancellation and identity loss. Existing seek, monitor and game primitives are
abstracted; this is not whole-program or live proof. Default214states/6guard
mutants checked by tools/check_tla.py and before native release compilation.
The code avoids redundant reconstruction, retains actual drift restoration and
strict rig admission. No state-count reduction claim against a failing baseline.
Other Dolly stateful components remain outside this scoped model.

The live-observed build6849 intro transition lasts7.5 replay seconds. The controller
waits cancellably for readiness while temporarily resuming its owned replay, then
pauses and restores the chosen tick before admitting the rig. It does not change
playback speed. The160s wall bound accommodates7.5s at supported minimum0.05x
plus10s margin. Model time is abstracted to settle/abort. P12 simulates a stock
blend advancing the selected tick and checks restoration before rig admission;
it does not reproduce the full160s bound. The older virtual-time and bounded
live six-switch evidence are historical, not current-suite qualification.

## Playback identity query during Players backpressure

PlaybackIdentity.tla models the _run_native console-timeout decision with zero
or one prior unpaced miss and a report appearing after query admission. It keeps
progressing owned capture alive and retains the ordinary failure budget for
invalid reports. The existing pacing predicate abstracts request, PID, session,
replay, freshness and progress validation; real file tests own those checks.
Native replay identity, cancellation, clock checks, GPU behavior and wall time
remain outside this model. This is a scoped race repair, with no state reduction
or whole-program claim. The original pre-query-only decision is a failing mutant;
the complementary mutant proves unverified waits cannot bypass the miss budget.

Normal `capture` progress now satisfies the same predicate as GPU/storage/drain
progress. The third mutant restores the previous stage exclusion. Idle and
complete reports remain untrusted. This adds no state holder or timeout; the
simplification is one shared progress decision for the active capture stages.

## Configuration file mode publication

AtomicMode.tla and AtomicModeApi.tla beside file_ops.py model its shared atomic
writer, including optional modes, absent/writable/read-only targets, temporary
write/flush/fsync/close, staged mode, Windows destination retry, returning failures,
finally cleanup and process exit. Both fault-enabled and no-fault configurations
run in the common gate, with six load-bearing mutants. New bytes are published
with the requested read-only mode; a preparation failure leaves the target alone.
The baseline comparison is private research: this hardening removes one visible
intermediate combination but does not reduce the complete internal state graph.

Callers and assumptions are mapped in the module comment. Returning OS calls and
weak fairness establish bounded completion; no-fault runs also require success.
No power-loss/directory durability, external writer/namespace race, POSIX ACL or
fdopen-failure guarantee is made. Finally's exists/stat query and its caught OSError now join the full writer
model; body success and the primary failure survive all modeled cleanup OSErrors. The existing old-target
writable-on-failed-retry gap and abandoned temporary files after crash remain.
Updater replace_file has a different implementation and AtomicCopy model; user
preference saves retain their own backup/conflict policy. No game or whole-program
correctness follows from these checks.


## Cleanup results and invalid-settings backups

CleanupResult.tla beside file_ops.py checks the finally tail after body success
or failure, including returning stat/chmod/unlink calls. Its guarded config and
four mutants preserve the body result under OSError while leaving unexpected
programming exceptions visible. AtomicMode includes stat failures in the complete
writer; the tail count is not an additive whole-writer reduction.

BackupPublish.tla beside settings.py checks explicit saves over invalid settings:
the entire original is copied privately, flushed, fsynced and closed before a
non-overwriting .invalid publication. Windows uses rename; POSIX uses hard links
and therefore requires filesystem support. Both platform configurations include
unavailable publication, collision, cleanup failures and process exits. Four
mutants protect completion, sync and prior backup preservation. The original
settings are not replaced when publication fails. Existing partial backups are
retained; interrupted .invalid.tmp stages may remain. Normal valid-settings saves
do not use the publication primitive. Schema11 and read-only behavior are unchanged.

The model starts after the new settings payload is prepared and assumes an
immutable invalid original. GUI/editor save scheduling, external writers,
power-loss/directory durability and native POSIX filesystem behavior are outside
the qualification. Two copy chunks abstract arbitrary bytes. P14 checks complete
invalid-original backups, collision and failure preservation. Oversized-original
and private-process exit regressions remain historical checks outside the 15.
Return uses weak fairness of returning I/O calls.
These are scoped hardening contracts, not whole-program or game verification.
The independent outer save_settings payload cleanup remains unchanged.

## Failed Players export recovery

PlayersRecovery.tla beside gui.py/video_export.py models the GUI's failed-export
entry to the recorder, native session/replay ownership and request checks, terminal
acknowledgement, and the identity recheck before reservation release/restoration.
A busy console cannot prevent the owned file stop request. Console identity is
still required after acknowledgement before runtime restoration. Normal encoder
stops retain the outer console guard. Eight mutants protect these boundaries.
All combinations of identity, marker, console and acknowledgement are enumerated,
including owner/request changes during waiting. Native GPU/drain internals, audio,
OS scheduling/storage stalls and entire-program correctness remain outside scope.
The simplification removes a redundant pre-stop console dependency; it does not
remove the recorder's native or post-stop guards or introduce another state holder.

## Bounded Players post-stop console recovery

PlayersRestoreRetry.tla beside video_export.py models _wait_player_restore_context,
called only by stop after the owned capture acknowledges shutdown. Three read
attempts use existing console query timeouts, with 0.1 seconds between timeouts.
Every attempt checks native session/replay identity and pinned capture request
before querying. Only ConsoleTimeout retries; other failures refuse immediately.
The capture reservation, cancel intent, raw data and restoration originals remain
until successful verification. Mutating restoration keeps its own fresh guards.

The model enumerates success on attempts 1..3, persistent timeout, and identity or
request loss between attempts. Five mutants protect ownership, request, response,
budget and transient recovery. Its 81 states extend behavior; no state reduction or
whole-program/GPU/OS-hang claim. PlayersRecovery abstracts this helper as its
consoleReady result. Core contract P6 checks native drain and post-response
ownership through the real controller and recorder with simulated game/bridge
boundaries. GUI failed-export orchestration is outside the 15 and requires a
targeted packaged UI check when changed.


## Worker results, streamed PNG stages and first-use presets

CleanupWorker.tla beside player_encode.py starts after job/process cleanup
returns. Two owned MOV paths, immutable body outcome, ordered deletion and
returning OSError check result preservation and attempted cleanup of both files.
Two mutants guard those rules. Minimal API refinement checks completion shape.
ProcessJob failures and TemporaryDirectory cleanup remain outside this scope;
locked leftovers may remain. No live encoder/GUI or process-crash proof.

PngStage.tla beside screenshot.py covers two same-output Windows PNG attempts,
exclusive private stages and open-handle identities. Stream and whole-buffer
Close are abstractions, not atomic implementations of individual PNG chunks.
Windows publication refuses open handles; full and noFaults configs separate
returning I/O faults from healthy progress; collision stress protects unowned
files. Three mutants guard progress, result and cleanup ownership. Actual
independent PNG decoding and hero-still adapter checks complement this model.
POSIX behavior, hostile private namespace replacement, process crash/power loss,
whole GUI orchestration and multi-image bundle atomicity are not proved.

PresetPublish.tla beside reshade_setup.py covers one first-use preset after
source read, existing/competing target, preflight separate from allocation,
write/close/no-overwrite publication, cleanup and one interruption/retry.
Windows rename and POSIX link alias configs differ; separate collision stress
checks cleanup authority. Five mutants guard incomplete exposure, overwrite,
close-before-publication and cleanup results/ownership. Source bytes, optional
preset selection and outer prepare_config are abstracted, with real adapters
checking first/repeated use. Existing files, even old incomplete ones, are
never automatically repaired. No multi-preset/config transaction is promised.
Hard-link capability is required on the POSIX branch; native POSIX, ReShade
rendering and power-loss durability are not certified.

These maintained models select production behavior only; obsolete comparison
designs remain private. Weak fairness assumes returning started operations;
named terminal Done stutters. Minimal completion-shape interfaces do not prove
whole-program equivalence. Distinct/generated counts are scoped, non-additive
and not performance metrics. Stronger retry/ownership behavior can add states.
No fsync/sweeper/new permanent recovery flags are introduced by these patches.
