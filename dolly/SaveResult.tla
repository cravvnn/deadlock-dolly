---- MODULE SaveResult ----
EXTENDS Naturals
\* settings.save_settings after payload validation, before temporary creation.
\* Create/write/flush/fsync/close -> real NamedTemporaryFile operations; Backup
\* delegates _backup_invalid_settings (BackupPublish owns its internal protocol).
\* Commit is os.replace; Cleanup is Path.unlink(missing_ok=True), not exists/stat.
\* One immutable original and one new payload; no concurrent writer, native/game,
\* power-loss/directory durability, GUI callback or descriptor-leak claim.
\* Callers: GUI preference saves, editor_dispatch callbacks, direct tests.
CONSTANTS Guarded, Strict
VARIABLES pc, tracked, temporary, target, synced, expected, outcome, unexpected
vars == <<pc,tracked,temporary,target,synced,expected,outcome,unexpected>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE SaveResultApi WITH phase <- phase
Init == /\ pc = "create" /\ tracked = FALSE /\ temporary = "none"
        /\ target = "old" /\ synced = FALSE /\ expected = "pending"
        /\ outcome = "pending" /\ unexpected = FALSE
Create == /\ pc = "create" /\ tracked' = TRUE /\ temporary' = "empty" /\ pc' = "write"
          /\ UNCHANGED <<target,synced,expected,outcome,unexpected>>
Write == /\ pc = "write" /\ temporary' = "new" /\ pc' = "flush"
         /\ UNCHANGED <<tracked,target,synced,expected,outcome,unexpected>>
Flush == /\ pc = "flush" /\ pc' = "sync"
         /\ UNCHANGED <<tracked,temporary,target,synced,expected,outcome,unexpected>>
Sync == /\ pc = "sync" /\ synced' = TRUE /\ pc' = "close"
        /\ UNCHANGED <<tracked,temporary,target,expected,outcome,unexpected>>
Close == /\ pc = "close" /\ pc' = "backup"
         /\ UNCHANGED <<tracked,temporary,target,synced,expected,outcome,unexpected>>
Backup == /\ pc = "backup" /\ pc' = "commit"
          /\ UNCHANGED <<tracked,temporary,target,synced,expected,outcome,unexpected>>
Commit == /\ pc = "commit" /\ target' = temporary /\ temporary' = "none"
          /\ expected' = "ok" /\ pc' = "cleanup"
          /\ UNCHANGED <<tracked,synced,outcome,unexpected>>
IOError == /\ pc \in {"create","write","flush","sync","close","backup","commit"}
           /\ expected' = "primary" /\ pc' = "cleanup"
           /\ UNCHANGED <<tracked,temporary,target,synced,outcome,unexpected>>
Cleanup == /\ pc = "cleanup" /\ temporary' = "none" /\ pc' = "finish"
           /\ UNCHANGED <<tracked,target,synced,expected,outcome,unexpected>>
CleanupOSError == /\ pc = "cleanup" /\ tracked
                  /\ (IF Guarded THEN /\ pc' = "finish" /\ UNCHANGED outcome
                      ELSE /\ pc' = "done" /\ outcome' = "cleanup")
                  /\ UNCHANGED <<tracked,temporary,target,synced,expected,unexpected>>
Unexpected == /\ pc = "cleanup" /\ tracked /\ unexpected' = TRUE
              /\ (IF Strict THEN /\ pc' = "done" /\ outcome' = "bug"
                  ELSE /\ pc' = "finish" /\ UNCHANGED outcome)
              /\ UNCHANGED <<tracked,temporary,target,synced,expected>>
Finish == /\ pc = "finish" /\ pc' = "done" /\ outcome' = expected
          /\ UNCHANGED <<tracked,temporary,target,synced,expected,unexpected>>
Crash == /\ pc # "done" /\ pc' = "done" /\ outcome' = "crashed"
         /\ UNCHANGED <<tracked,temporary,target,synced,expected,unexpected>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Create \/ Write \/ Flush \/ Sync \/ Close \/ Backup \/ Commit \/ IOError \/ Cleanup \/ CleanupOSError \/ Unexpected \/ Finish
Next == Run \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"create","write","flush","sync","close","backup","commit","cleanup","finish","done"}
          /\ tracked \in BOOLEAN /\ temporary \in {"none","empty","new"}
          /\ target \in {"old","new"} /\ synced \in BOOLEAN /\ unexpected \in BOOLEAN
          /\ expected \in {"pending","ok","primary"}
          /\ outcome \in {"pending","ok","primary","cleanup","bug","crashed"}
PublishAfterSync == target = "new" => synced
PrimaryFailureKeepsOriginal == outcome = "primary" => target = "old"
CleanupPreservesResult == ~Guarded \/ outcome \in {"pending",expected,"bug","crashed"}
UnexpectedVisible == pc = "done" /\ unexpected => outcome = "bug"
SuccessfulSavePublished == outcome = "ok" => target = "new"
Returns == <>(pc = "done")
Refines == API!Spec
====
