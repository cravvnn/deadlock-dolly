---- MODULE LauncherHandoff ----
EXTENDS Naturals
\* Sources: update_worker.cleanup_workspaces/_workspace_finished, _apply_plan,
\* install and install_lock. Caller: release_launcher.main. The actual writer
\* holds the installation's msvcrt byte-range lock. The launcher takes the same
\* lock through pending-check, cleanup and process launch. Design = "unlocked"
\* retains the former launcher for deliberately failing diagnostic checks.
\* One old reusable staged workspace, one writer, one cleaner, one original
\* app file, one optional writer process exit. Init models stale metadata, not
\* an empty workspace. Eligibility observation and deletion are separate steps.
\* DeleteBackup is a single critical unlink within rmtree, not an assertion
\* that recursive directory removal is atomic. OS file operations must return.
\* Download liveness, legacy ownership, path/link attacks, live PID visibility
\* details and machine power loss remain outside this model.
\* WriterStart combines lock acquisition and successful process admission: a
\* visible editor prevents admission; an admitted writer can predate startup.
\* CleanerPending terminates at recovery handoff; worker parent waiting and
\* rollback completion are outside this model (see SPECS.md and UpdateRollback).
CONSTANT Design
VARIABLES lock, wpc, cpc, workspace, backup, journal, marker, changed, editor, staged
vars == <<lock,wpc,cpc,workspace,backup,journal,marker,changed,editor,staged>>
UsesLock == Design = "locked"
API == INSTANCE RecoveryResourcesApi WITH needed <- marker /\ changed,
                                           resources <- workspace /\ backup
Init == /\ lock = "none" /\ wpc = "start" /\ cpc = "start"
        /\ workspace = TRUE /\ backup = FALSE /\ journal = "none"
        /\ marker = FALSE /\ changed = FALSE /\ editor = FALSE /\ staged = FALSE
WriterStart == /\ wpc = "start"
               /\ IF lock = "none" /\ ~editor THEN /\ lock' = "writer" /\ wpc' = "backup"
                  ELSE /\ wpc' = "aborted" /\ UNCHANGED lock
               /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed,editor,staged>>
WriterBackup == /\ wpc = "backup" /\ workspace /\ backup' = TRUE /\ wpc' = "journal"
                /\ UNCHANGED <<lock,cpc,workspace,journal,marker,changed,editor,staged>>
WriterJournal == /\ wpc = "journal" /\ workspace /\ journal' = "applying" /\ wpc' = "marker"
                 /\ UNCHANGED <<lock,cpc,workspace,backup,marker,changed,editor,staged>>
WriterMarker == /\ wpc = "marker" /\ workspace /\ marker' = TRUE /\ wpc' = "replace"
                /\ UNCHANGED <<lock,cpc,workspace,backup,journal,changed,editor,staged>>
WriterStage == /\ wpc = "replace" /\ workspace /\ staged' = TRUE /\ wpc' = "commit"
               /\ UNCHANGED <<lock,cpc,workspace,backup,journal,marker,changed,editor>>
WriterReplace == /\ wpc = "commit" /\ staged /\ changed' = TRUE /\ staged' = FALSE /\ wpc' = "complete"
                 /\ UNCHANGED <<lock,cpc,workspace,backup,journal,marker,editor>>
WriterComplete == /\ wpc = "complete" /\ workspace /\ journal' = "complete" /\ wpc' = "unmark"
                  /\ UNCHANGED <<lock,cpc,workspace,backup,marker,changed,editor,staged>>
WriterUnmark == /\ wpc = "unmark" /\ marker' = FALSE /\ wpc' = "unlock"
                /\ UNCHANGED <<lock,cpc,workspace,backup,journal,changed,editor,staged>>
WriterUnlock == /\ wpc = "unlock" /\ lock' = "none" /\ wpc' = "done"
                /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed,editor,staged>>
WriterMissingWorkspace == /\ wpc \in {"backup","journal","marker","replace","complete"}
                          /\ ~workspace /\ lock' = "none" /\ wpc' = "aborted"
                          /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed,editor,staged>>
WriterCrash == /\ wpc \in {"backup","journal","marker","replace","commit","complete","unmark","unlock"}
               /\ lock' = "none" /\ wpc' = "crashed"
               /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed,editor,staged>>
CleanerFinish == /\ cpc' = "done"
                 /\ lock' = IF UsesLock /\ lock = "cleaner" THEN "none" ELSE lock
                 /\ UNCHANGED <<wpc,workspace,backup,journal,marker,changed,editor,staged>>
CleanerStart == /\ cpc = "start"
                /\ IF UsesLock /\ lock # "none" THEN CleanerFinish
                   ELSE /\ cpc' = "pending"
                        /\ lock' = IF UsesLock THEN "cleaner" ELSE lock
                        /\ UNCHANGED <<wpc,workspace,backup,journal,marker,changed,editor,staged>>
CleanerPending == /\ cpc = "pending"
                  /\ IF marker THEN CleanerFinish
                     ELSE /\ cpc' = "eligible"
                          /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed,editor,staged>>
SkipCleanup == /\ cpc' = "openEditor"
               /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed,editor,staged>>
CleanerEligible == /\ cpc = "eligible"
                   /\ IF journal = "applying" \/ ~workspace THEN SkipCleanup
                      ELSE /\ cpc' = "deleteBackup"
                           /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed,editor,staged>>
CleanerDeleteBackup == /\ cpc = "deleteBackup" /\ backup' = FALSE /\ cpc' = "deleteWorkspace"
                       /\ UNCHANGED <<lock,wpc,workspace,journal,marker,changed,editor,staged>>
CleanerDeleteWorkspace == /\ cpc = "deleteWorkspace" /\ workspace' = FALSE /\ cpc' = "openEditor"
                          /\ UNCHANGED lock
                          /\ UNCHANGED <<wpc,backup,journal,marker,changed,editor,staged>>
OpenEditor == /\ cpc = "openEditor" /\ editor' = TRUE /\ cpc' = "unlockLauncher"
              /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed,staged>>
UnlockLauncher == /\ cpc = "unlockLauncher" /\ cpc' = "done"
                  /\ lock' = IF UsesLock THEN "none" ELSE lock
                  /\ UNCHANGED <<wpc,workspace,backup,journal,marker,changed,editor,staged>>
WriterRun == WriterBackup \/ WriterJournal \/ WriterMarker \/ WriterStage \/ WriterReplace \/
             WriterComplete \/ WriterUnmark \/ WriterUnlock \/ WriterMissingWorkspace
CleanerRun == CleanerPending \/ CleanerEligible \/ CleanerDeleteBackup \/ CleanerDeleteWorkspace \/ OpenEditor \/ UnlockLauncher
Done == /\ cpc = "done" /\ wpc \in {"start","done","crashed","aborted"} /\ UNCHANGED vars
Next == WriterStart \/ WriterRun \/ WriterCrash \/ CleanerStart \/ CleanerRun \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(WriterRun) /\ WF_vars(CleanerRun)
TypeOK == /\ lock \in {"none","writer","cleaner"}
          /\ wpc \in {"start","backup","journal","marker","replace","commit","complete","unmark","unlock","done","crashed","aborted"}
          /\ cpc \in {"start","pending","eligible","deleteBackup","deleteWorkspace","openEditor","unlockLauncher","done"}
          /\ workspace \in BOOLEAN /\ backup \in BOOLEAN /\ marker \in BOOLEAN /\ changed \in BOOLEAN
          /\ journal \in {"none","applying","complete"} /\ editor \in BOOLEAN /\ staged \in BOOLEAN
NoRecoveryLoss == (marker /\ changed) => (workspace /\ backup)
UnfinishedJournalKept == journal = "applying" => workspace
NoEditorDuringAdmittedUpdate == editor => wpc \notin {"backup","journal","marker","replace","commit","complete","unmark","unlock"}
CleanupReturns == (cpc # "start") ~> (cpc = "done")
Refines == API!Spec
====
