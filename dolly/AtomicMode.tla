---- MODULE AtomicMode ----
EXTENDS Naturals
\* Implementation of file_ops._atomic_write; AtomicMode-noFaults checks success. Caller search: launcher
\* journal/backup/gameinfo/diagnostics, gameinfo_transaction.restore_record,
\* graphics_profiles library/prepare/apply/restore/audit, editing_session
\* prepare/verify_runtime, editing_assets.install and cleanup-marker repair.
\* Their conflict/identity/restore decisions stay above this per-file interface.
\* Existing update_worker.replace_file is a DIFFERENT helper (AtomicCopy.tla).
\* One write, one Windows read-only retry, optional mode, old/absent target.
\* File write, flush, fsync, close, chmod, replacement and finally operations
\* are separate. Failure returns, cleanup failures, and abrupt exits are kept.
\* Same finite I/O opportunities for both designs. No adversarial external
\* writer, fdopen failure/descriptor leak, partial-write bytes, POSIX ACLs,
\* directory durability, or power-loss metadata ordering is claimed.
\* Finally stat, chmod and unlink OSErrors are caught without changing the
\* body result. CleanupResult separately checks escaping programming exceptions.
CONSTANT Faults
VARIABLES pc, content, permissions, requested, temporary, tempMode, synced, outcome, cleanupFailed, errorKind
vars == <<pc,content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
phase == IF outcome = "pending" THEN "active" ELSE outcome
API == INSTANCE AtomicModeApi WITH phase <- phase
Init == /\ pc = "create" /\ content \in {"old","absent"}
        /\ permissions \in (IF content = "absent" THEN {"none"} ELSE {"ro","rw"})
        /\ requested \in {"none","ro","rw"}
        /\ temporary = "none" /\ tempMode = "none" /\ synced = FALSE
        /\ outcome = "pending" /\ cleanupFailed = FALSE /\ errorKind = "none"
Create == /\ pc = "create" /\ temporary' = "empty" /\ tempMode' = "rw"
          /\ pc' = "write" /\ UNCHANGED <<content,permissions,requested,synced,outcome,cleanupFailed,errorKind>>
Write == /\ pc = "write" /\ temporary' = "new" /\ pc' = "flush"
         /\ UNCHANGED <<content,permissions,requested,tempMode,synced,outcome,cleanupFailed,errorKind>>
Flush == /\ pc = "flush" /\ pc' = "sync"
         /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
Sync == /\ pc = "sync" /\ synced' = TRUE /\ pc' = "close"
        /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,outcome,cleanupFailed,errorKind>>
Close == /\ pc = "close"
         /\ pc' = IF requested # "none" THEN "tempMode" ELSE "replace"
         /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
PrepareMode == /\ pc = "tempMode" /\ tempMode' = requested /\ pc' = "replace"
               /\ UNCHANGED <<content,permissions,requested,temporary,synced,outcome,cleanupFailed,errorKind>>
PermissionError == /\ pc = "replace" /\ content # "absent" /\ (permissions = "ro" \/ Faults) /\ pc' = "writable"
                   /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
MakeWritable == /\ pc = "writable" /\ permissions' = "rw" /\ pc' = "retry"
                /\ UNCHANGED <<content,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
Replace == /\ pc \in {"replace","retry"} /\ permissions # "ro"
           /\ content' = temporary /\ permissions' = tempMode
           /\ temporary' = "none" /\ tempMode' = "none"
           /\ pc' = "return"
           /\ UNCHANGED <<requested,synced,outcome,cleanupFailed,errorKind>>
Return == /\ pc = "return" /\ pc' = "cleanupStat" /\ UNCHANGED outcome
          /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,cleanupFailed,errorKind>>
IOError == /\ Faults /\ pc \in {"create","write","flush","sync","close","tempMode","replace","writable","retry"}
           /\ pc' = "cleanupStat" /\ errorKind' = "primary"
           /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed>>
RetryPermissionError == /\ pc = "retry" /\ permissions = "ro"
                        /\ pc' = "cleanupStat" /\ errorKind' = "primary"
                        /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed>>
CleanupStat == /\ pc = "cleanupStat"
               /\ pc' = IF temporary = "none" THEN "finish" ELSE "cleanupMode"
               /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,cleanupFailed,errorKind>>
CleanupStatError == /\ Faults /\ pc = "cleanupStat" /\ cleanupFailed' = TRUE
                    /\ pc' = "finish"
                    /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,outcome,errorKind>>
Finish == /\ pc = "finish" /\ pc' = "done"
          /\ outcome' = IF errorKind = "none" THEN "ok" ELSE "error"
          /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,cleanupFailed,errorKind>>
CleanupMode == /\ pc = "cleanupMode"
               /\ IF temporary = "none"
                  THEN /\ pc' = "done" /\ outcome' = "error" /\ UNCHANGED tempMode
                  ELSE /\ pc' = "cleanupUnlink" /\ tempMode' = "rw" /\ UNCHANGED outcome
               /\ UNCHANGED <<content,permissions,requested,temporary,synced,cleanupFailed,errorKind>>
CleanupUnlink == /\ pc = "cleanupUnlink" /\ temporary' = "none" /\ tempMode' = "none"
                 /\ pc' = "done" /\ outcome' = "error"
                 /\ UNCHANGED <<content,permissions,requested,synced,cleanupFailed,errorKind>>
CleanupError == /\ Faults /\ pc \in {"cleanupMode","cleanupUnlink"} /\ temporary # "none"
                /\ pc' = "done" /\ outcome' = "error" /\ cleanupFailed' = TRUE
                /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,errorKind>>
Crash == /\ Faults /\ outcome = "pending" /\ pc' = "done" /\ outcome' = "crashed"
         /\ UNCHANGED <<content,permissions,requested,temporary,tempMode,synced,cleanupFailed,errorKind>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Create \/ Write \/ Flush \/ Sync \/ Close \/ PrepareMode \/ PermissionError \/ MakeWritable
       \/ Replace \/ RetryPermissionError \/ Return \/ CleanupStat \/ CleanupStatError \/ Finish \/ IOError \/ CleanupMode \/ CleanupUnlink \/ CleanupError
Next == Run \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"create","write","flush","sync","close","tempMode","replace","writable","retry","return","cleanupStat","finish","cleanupMode","cleanupUnlink","done"}
          /\ content \in {"old","absent","new"} /\ permissions \in {"none","ro","rw"}
          /\ requested \in {"none","ro","rw"} /\ temporary \in {"none","empty","new"}
          /\ tempMode \in {"none","ro","rw"} /\ synced \in BOOLEAN
          /\ outcome \in {"pending","ok","error","crashed"}
          /\ cleanupFailed \in BOOLEAN /\ errorKind \in {"none","primary","secondary"}
          /\ (temporary = "none" => tempMode = "none")
          /\ (temporary # "none" => tempMode \in {"ro","rw"})
          /\ (content = "absent" => permissions = "none")
          /\ (content # "absent" => permissions \in {"ro","rw"})
PublishAfterSync == content = "new" => synced
ReturnContract == outcome = "ok" => content = "new" /\ (requested = "none" \/ permissions = requested)
StagedModeTogether == content # "new" \/ requested = "none" \/ permissions = requested
SuccessNoFaults == ~Faults /\ outcome # "pending" => outcome = "ok"
NormalFailureCleansTemp == outcome = "error" /\ ~cleanupFailed => temporary = "none"
PrimarySurvivesCleanup == outcome = "error" => errorKind = "primary"
Returns == <>(outcome # "pending")
Refines == API!Spec
====
