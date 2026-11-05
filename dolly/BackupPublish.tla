---- MODULE BackupPublish ----
EXTENDS Naturals
\* settings.save_settings -> _backup_invalid_settings on an explicit save over
\* invalid data. Valid new payload is already staged/fsynced. Original snapshot
\* immutable during this transaction. Callers: GUI save, editor_dispatch save
\* callbacks and tests. Their asynchronous scheduling is outside this model.
\* Two copy chunks; flush/fsync/close/publish/error cleanup and exit separate.
\* Publication uses a same-filesystem non-overwriting Windows rename or POSIX
\* hard link; availability/collision are explicit environment inputs. No external writer, power-loss/directory durability certification.
CONSTANTS Staged, AvailabilityValues, CollisionValues, LinkPublication
VARIABLES pc, stage, content, synced, published, target, prior, collision, available, outcome
vars == <<pc,stage,content,synced,published,target,prior,collision,available,outcome>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE BackupPublishApi WITH phase <- phase
Init == /\ pc = "create" /\ stage = FALSE /\ content = "none" /\ synced = FALSE
        /\ published = FALSE /\ target = "old" /\ prior = "prior"
        /\ collision \in CollisionValues /\ available \in AvailabilityValues /\ outcome = "pending"
Create == /\ pc = "create" /\ stage' = TRUE /\ content' = "empty"
          /\ published' = ~Staged /\ pc' = "copyFirst"
          /\ UNCHANGED <<synced,target,prior,collision,available,outcome>>
CopyFirst == /\ pc = "copyFirst" /\ content' = "partial" /\ pc' = "copyRest"
             /\ UNCHANGED <<stage,synced,published,target,prior,collision,available,outcome>>
CopyRest == /\ pc = "copyRest" /\ content' = "full" /\ pc' = "flush"
            /\ UNCHANGED <<stage,synced,published,target,prior,collision,available,outcome>>
Flush == /\ pc = "flush" /\ pc' = "sync"
         /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available,outcome>>
Sync == /\ pc = "sync" /\ synced' = TRUE /\ pc' = "close"
        /\ UNCHANGED <<stage,content,published,target,prior,collision,available,outcome>>
Close == /\ pc = "close" /\ pc' = IF Staged THEN "publish" ELSE "commit"
         /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available,outcome>>
Publish == /\ pc = "publish" /\ (available /\ ~collision)
           /\ published' = TRUE /\ prior' = prior /\ stage' = (IF LinkPublication THEN stage ELSE FALSE) /\ pc' = "cleanupSuccess"
           /\ UNCHANGED <<content,synced,target,collision,available,outcome>>
PublishRefused == /\ pc = "publish" /\ (~available \/ collision) /\ pc' = "cleanupFailure" /\ outcome' = "error"
                 /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available>>
CleanupSuccess == /\ pc = "cleanupSuccess" /\ stage' = FALSE /\ pc' = "commit"
                  /\ UNCHANGED <<content,synced,published,target,prior,collision,available,outcome>>
CleanupSuccessError == /\ pc = "cleanupSuccess" /\ pc' = "commit"
                       /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available,outcome>>
Commit == /\ pc = "commit" /\ target' = "new" /\ pc' = "return"
          /\ UNCHANGED <<stage,content,synced,published,prior,collision,available,outcome>>
Return == /\ pc = "return" /\ pc' = "done" /\ outcome' = "ok"
          /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available>>
IOError == /\ pc \in {"create","copyFirst","copyRest","flush","sync","close","publish","commit"}
           /\ pc' = (IF pc = "commit" THEN "done" ELSE "cleanupFailure") /\ outcome' = "error"
           /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available>>
CleanupFailure == /\ pc = "cleanupFailure" /\ stage' = FALSE
                  /\ published' = (IF Staged THEN published ELSE FALSE) /\ pc' = "done"
                  /\ UNCHANGED <<content,synced,target,prior,collision,available,outcome>>
CleanupFailureError == /\ pc = "cleanupFailure" /\ pc' = "done"
                       /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available,outcome>>
Crash == /\ pc # "done" /\ pc' = "done" /\ outcome' = "crashed"
         /\ UNCHANGED <<stage,content,synced,published,target,prior,collision,available>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Create \/ CopyFirst \/ CopyRest \/ Flush \/ Sync \/ Close \/ Publish \/ PublishRefused
       \/ CleanupSuccess \/ CleanupSuccessError \/ Commit \/ Return \/ IOError \/ CleanupFailure \/ CleanupFailureError
Next == Run \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ stage \in BOOLEAN /\ content \in {"none","empty","partial","full"}
          /\ synced \in BOOLEAN /\ published \in BOOLEAN /\ target \in {"old","new"}
          /\ prior \in {"prior","old"} /\ collision \in BOOLEAN /\ available \in BOOLEAN
          /\ outcome \in {"pending","error","ok","crashed"}
NamedBackupsComplete == ~Staged \/ ~published \/ (content = "full" /\ synced)
SaveRequiresBackup == target = "new" => published /\ content = "full" /\ synced
PriorBackupSurvives == prior = "prior"
FailurePreservesOriginal == outcome = "error" => target = "old"
Returns == <>(pc = "done")
Refines == API!Spec
====
