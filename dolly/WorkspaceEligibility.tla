---- MODULE WorkspaceEligibility ----
EXTENDS Naturals
\* Source: release_launcher.main holds install_lock while cleanup_workspaces
\* calls _workspace_finished. One ordinary owned workspace, existing journal,
\* one backup, marker observation, keep selection, classification then deletion.
\* Journal observation cannot change while lock held: ordinary Dolly writer
\* honors same lock. External edits and dishonest terminal journals excluded.
\* "unknown" covers missing/unrecognized state; "unreadable" covers malformed
\* JSON/type, recursive JSON, or I/O error. Only terminal states grant deletion.
\* Real probes separately cover legacy target shape and native file locks.
\* No no-journal/result/age branch, path attacks, writer race, restore correctness,
\* editor UI, actual crash or power-loss proof. WorkspaceCleanup retains writer
\* interleavings and lock obligation; this is an admission submodel.
\* Caller opens editor after successful classification. Synchronous operations
\* assumed to return; Done intentionally stutters, deadlock check enabled.
KnownTerminal == TRUE
CheckMarker == TRUE
CheckKeep == TRUE
AllowTerminal == TRUE
VARIABLES pc,journal,marker,kept,backup,eligible,opened
vars == <<pc,journal,marker,kept,backup,eligible,opened>>
Init == /\ pc = "start" /\ journal \in {"applying","unknown","unreadable","complete","rolled_back"}
        /\ marker \in BOOLEAN /\ kept \in BOOLEAN /\ backup = TRUE
        /\ eligible = FALSE /\ opened = FALSE
Terminal == journal \in {"complete","rolled_back"}
CleanupPending == /\ pc = "start" /\ pc' = "eligible"
                  /\ eligible' = ((~CheckMarker \/ ~marker) /\ (~CheckKeep \/ ~kept))
                  /\ UNCHANGED <<journal,marker,kept,backup,opened>>
WorkspaceFinished == /\ pc = "eligible" /\ pc' = "delete"
                     /\ eligible' = (eligible /\ AllowTerminal /\
                         (IF KnownTerminal THEN Terminal ELSE journal # "applying" /\ journal # "unreadable"))
                     /\ UNCHANGED <<journal,marker,kept,backup,opened>>
DeleteBackup == /\ pc = "delete" /\ pc' = "open" /\ backup' = ~eligible
                /\ UNCHANGED <<journal,marker,kept,eligible,opened>>
OpenEditor == /\ pc = "open" /\ pc' = "done" /\ opened' = TRUE
              /\ UNCHANGED <<journal,marker,kept,backup,eligible>>
Done == /\ pc = "done" /\ UNCHANGED vars
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE WorkspaceEligibilityApi WITH phase <- phase
Next == CleanupPending \/ WorkspaceFinished \/ DeleteBackup \/ OpenEditor \/ Done
TypeOK == /\ pc \in {"start","eligible","delete","open","done"}
          /\ journal \in {"applying","unknown","unreadable","complete","rolled_back"}
          /\ marker \in BOOLEAN /\ kept \in BOOLEAN /\ backup \in BOOLEAN
          /\ eligible \in BOOLEAN /\ opened \in BOOLEAN
AmbiguousRetained == ~Terminal => backup
PendingRetained == marker => backup
SelectedRetained == kept => backup
TerminalRemoved == (pc = "done" /\ Terminal /\ ~marker /\ ~kept) => ~backup
LauncherCanOpen == pc = "done" => opened
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
