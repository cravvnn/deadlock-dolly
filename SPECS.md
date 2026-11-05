# Maintained model coverage

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
| Input validation, manifest verification, process visibility/identity, consent and recovery-worker parent wait | Owning Python tests; outside these implementation models | Models assume validated paths, correct hashes and OS lock semantics. Writer admission models a successful process snapshot before startup; a visible editor prevents new admission. Recovery handoff terminates the launcher model; parent waiting is reviewed/tested separately. |
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
Real two-process fake-install regressions exercise both ownership orderings,
delayed first commit, process exit and exact-byte rollback; they do not execute
packaged Dolly or prove kernel image-lock behavior. The former unlocked design
fails the recovery contract, so its partial search is not a state-reduction
baseline. This integration consolidates ownership and hardens recovery.

Each adjacent `.mutants.json` names deliberately broken rules. Parse/evaluation
errors and stale replacements do not count as valid mutant kills. When modifying
modeled behavior, update the implementation spec, interface mapping, mutants and
owning tests together. The checker cannot automatically extract Python semantics
or detect every model/code mismatch.

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
Real temporary-file, Windows-lock and malformed-workspace owner regressions
supplement the specs. No whole-program, live/game, native POSIX or power-loss
certification follows. Current settings schema and graphics recovery are unchanged.

## Participant Follow selection

`FollowLens.tla` beside `follow_camera.py` models `FollowTransaction.apply` and
`restore`: a saved descriptor, two updates, one write/readback or restoration
failure, ownership loss, retry, and disable/restore/enable ordering. Normal FOV90
and unsupported150 exercise the neutral aimed-lens admission boundary. One
opaque offset represents the independent position fields. Its five mutants
protect neutral zoom, the first snapshot, pending restoration, reviewed ranges,
and write ownership. Fresh normal-FOV reads on updates, concrete ConVar values,
exact float readback, callback failures and all descriptor fields remain owning
Python tests; this projection does not certify the game or all camera state.

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
plus10s margin. Model time is abstracted to settle/abort; clock duration and exact
speed preservation are covered by owner tests and bounded live evidence. No new
state holder was added for speed. A virtual-time regression exceeds the old5s
budget; actualsix-switch verification uses the matching replay at581 and5000.

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
the qualification. Two copy chunks abstract arbitrary bytes. Real owner tests
check oversized originals, collision and failure preservation; private process
exits/retries supplement them. Return uses weak fairness of returning I/O calls.
These are scoped hardening contracts, not whole-program or game verification.
The independent outer save_settings payload cleanup remains unchanged.
