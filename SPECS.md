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

## Participant Follow selection

[FollowSelection.tla](dolly/FollowSelection.tla) models the controller handoff
called by participant switching and editor hero selection. A paused same-tick
handoff avoids reconstruction; drift/unpaused state uses the existing seek.
Transient observer mode4 may settle within the existing confirmation timeout.
Readiness, observer mode, cancellation and fresh identity remain required before
enabling the rig. 110 bounded states; five guard mutants fail. Seek/monitor/game
primitives are abstracted. This does not prove engine blending completes or
whole-program camera correctness. Python regressions cover both selected paths,
actual drift repair, timeout restoration and cancellation. The simplification
removes an unconditional operation; no state-count reduction is claimed.
