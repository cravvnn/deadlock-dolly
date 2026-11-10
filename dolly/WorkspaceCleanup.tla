---- MODULE WorkspaceCleanup ----
EXTENDS Naturals
\* Sources: update_worker.cleanup_workspaces/_workspace_finished, _apply_plan,
\* install and install_lock. Caller: release_launcher.main. The actual writer
\* holds the installation's msvcrt byte-range lock. release_launcher.main now
\* owns that same nonblocking lock around cleanup's check-and-delete. This
\* lower model projects the caller's acquisition/release; cleanup itself does
\* not reacquire the lock. LauncherHandoff covers the wider caller boundary.
\* One old reusable staged workspace, one writer, one cleaner, one original
\* app file, one optional writer process exit. Init models stale metadata, not
\* an empty workspace. Eligibility observation and deletion are separate steps.
\* DeleteBackup is a single critical unlink within rmtree, not an assertion
\* that recursive directory removal is atomic. OS file operations must return.
\* WorkspaceEligibility covers existing ambiguous/rolled-back journal admission;
\* "none" here is an already eligible old unjournaled workspace, not every age.
\* Download liveness, legacy ownership, path/link attacks, launcher/editor
\* startup exclusion and machine power loss remain outside this model.
CONSTANT Design
VARIABLES lock, wpc, cpc, workspace, backup, journal, marker, changed
vars == <<lock,wpc,cpc,workspace,backup,journal,marker,changed>>
UsesLock == Design = "locked"
API == INSTANCE RecoveryResourcesApi WITH needed <- marker /\ changed,
                                           resources <- workspace /\ backup
Init == /\ lock = "none" /\ wpc = "start" /\ cpc = "start"
        /\ workspace = TRUE /\ backup = FALSE /\ journal = "none"
        /\ marker = FALSE /\ changed = FALSE
WriterStart == /\ wpc = "start"
               /\ IF lock = "none" THEN /\ lock' = "writer" /\ wpc' = "backup"
                  ELSE /\ wpc' = "aborted" /\ UNCHANGED lock
               /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed>>
WriterBackup == /\ wpc = "backup" /\ workspace /\ backup' = TRUE /\ wpc' = "journal"
                /\ UNCHANGED <<lock,cpc,workspace,journal,marker,changed>>
WriterJournal == /\ wpc = "journal" /\ workspace /\ journal' = "applying" /\ wpc' = "marker"
                 /\ UNCHANGED <<lock,cpc,workspace,backup,marker,changed>>
WriterMarker == /\ wpc = "marker" /\ workspace /\ marker' = TRUE /\ wpc' = "replace"
                /\ UNCHANGED <<lock,cpc,workspace,backup,journal,changed>>
WriterReplace == /\ wpc = "replace" /\ workspace /\ changed' = TRUE /\ wpc' = "complete"
                 /\ UNCHANGED <<lock,cpc,workspace,backup,journal,marker>>
WriterComplete == /\ wpc = "complete" /\ workspace /\ journal' = "complete" /\ wpc' = "unmark"
                  /\ UNCHANGED <<lock,cpc,workspace,backup,marker,changed>>
WriterUnmark == /\ wpc = "unmark" /\ marker' = FALSE /\ wpc' = "unlock"
                /\ UNCHANGED <<lock,cpc,workspace,backup,journal,changed>>
WriterUnlock == /\ wpc = "unlock" /\ lock' = "none" /\ wpc' = "done"
                /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed>>
WriterMissingWorkspace == /\ wpc \in {"backup","journal","marker","replace","complete"}
                          /\ ~workspace /\ lock' = "none" /\ wpc' = "aborted"
                          /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed>>
WriterCrash == /\ wpc \in {"backup","journal","marker","replace","complete","unmark","unlock"}
               /\ lock' = "none" /\ wpc' = "crashed"
               /\ UNCHANGED <<cpc,workspace,backup,journal,marker,changed>>
CleanerFinish == /\ cpc' = "done"
                 /\ lock' = IF UsesLock /\ lock = "cleaner" THEN "none" ELSE lock
                 /\ UNCHANGED <<wpc,workspace,backup,journal,marker,changed>>
CleanerStart == /\ cpc = "start"
                /\ IF UsesLock /\ lock # "none" THEN CleanerFinish
                   ELSE /\ cpc' = "pending"
                        /\ lock' = IF UsesLock THEN "cleaner" ELSE lock
                        /\ UNCHANGED <<wpc,workspace,backup,journal,marker,changed>>
CleanerPending == /\ cpc = "pending"
                  /\ IF marker THEN CleanerFinish
                     ELSE /\ cpc' = "eligible"
                          /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed>>
CleanerEligible == /\ cpc = "eligible"
                   /\ IF journal \notin {"none","complete"} \/ ~workspace THEN CleanerFinish
                      ELSE /\ cpc' = "deleteBackup"
                           /\ UNCHANGED <<lock,wpc,workspace,backup,journal,marker,changed>>
CleanerDeleteBackup == /\ cpc = "deleteBackup" /\ backup' = FALSE /\ cpc' = "deleteWorkspace"
                       /\ UNCHANGED <<lock,wpc,workspace,journal,marker,changed>>
CleanerDeleteWorkspace == /\ cpc = "deleteWorkspace" /\ workspace' = FALSE /\ cpc' = "done"
                          /\ lock' = IF UsesLock THEN "none" ELSE lock
                          /\ UNCHANGED <<wpc,backup,journal,marker,changed>>
WriterRun == WriterBackup \/ WriterJournal \/ WriterMarker \/ WriterReplace \/
             WriterComplete \/ WriterUnmark \/ WriterUnlock \/ WriterMissingWorkspace
CleanerRun == CleanerPending \/ CleanerEligible \/ CleanerDeleteBackup \/ CleanerDeleteWorkspace
Done == /\ cpc = "done" /\ wpc \in {"start","done","crashed","aborted"} /\ UNCHANGED vars
Next == WriterStart \/ WriterRun \/ WriterCrash \/ CleanerStart \/ CleanerRun \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(WriterRun) /\ WF_vars(CleanerRun)
TypeOK == /\ lock \in {"none","writer","cleaner"}
          /\ wpc \in {"start","backup","journal","marker","replace","complete","unmark","unlock","done","crashed","aborted"}
          /\ cpc \in {"start","pending","eligible","deleteBackup","deleteWorkspace","done"}
          /\ workspace \in BOOLEAN /\ backup \in BOOLEAN /\ marker \in BOOLEAN /\ changed \in BOOLEAN
          /\ journal \in {"none","applying","complete"}
NoRecoveryLoss == (marker /\ changed) => (workspace /\ backup)
UnfinishedJournalKept == journal = "applying" => workspace
CleanupReturns == (cpc # "start") ~> (cpc = "done")
Refines == API!Spec
====
