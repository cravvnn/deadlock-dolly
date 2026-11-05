---- MODULE UpdateRollback ----
EXTENDS Naturals, FiniteSets
\* Existing code: update_worker.rollback. Source callers: install's exception
\* handler and _apply_plan's recovery branch. Both enter under install_lock;
\* process/plan/path/hash validation remains implementation evidence outside
\* this bounded file-content model. Initial disk states include each prefix
\* of install's sorted replace/remove loop after backup+journal+PENDING exist.
\* Three owned files cover replacement, removal and newly added files.
\* A process crash may restart rollback twice at any file/journal boundary.
\* Directory durability, power loss, external edits and corrupt bytes excluded.
CONSTANTS Files, Existing, MaxCrashes
VARIABLES files, backups, pc, crashes, journal, marker
vars == <<files,backups,pc,crashes,journal,marker>>
Original == [f \in Files |-> IF f \in Existing THEN "old" ELSE "absent"]
Updated == [f \in Files |-> IF f = 3 THEN "absent" ELSE "new"]
Prefix(k) == [f \in Files |-> IF f <= k THEN Updated[f] ELSE Original[f]]
Disk == INSTANCE FileOpsApi WITH contents <- files, availableBackups <- backups
API == INSTANCE RollbackApi WITH contents <- files, pending <- marker,
                                recovered <- journal = "rolled_back"
Init == /\ files \in {Prefix(k):k \in 0..3} /\ backups = Existing
        /\ pc = 1 /\ crashes = 0 /\ journal = "applying" /\ marker = TRUE
AlreadyRestored == files[pc] = Original[pc]
Restore == /\ pc \in Files
           /\ IF AlreadyRestored
              THEN UNCHANGED <<files,backups>>
              ELSE IF pc \notin Existing THEN Disk!Remove(pc)
                   ELSE Disk!Copy(pc)
           /\ pc' = pc+1
           /\ UNCHANGED <<crashes,journal,marker>>
RestoreError == /\ pc \in Existing /\ ~AlreadyRestored /\ pc \notin backups
                /\ pc' = 7 /\ UNCHANGED <<files,backups,crashes,journal,marker>>
SaveJournal == /\ pc = 4 /\ journal' = "rolled_back" /\ pc' = 5
               /\ UNCHANGED <<files,backups,crashes,marker>>
RemovePending == /\ pc = 5 /\ marker' = FALSE /\ pc' = 6
                 /\ UNCHANGED <<files,backups,crashes,journal>>
Crash == /\ pc < 6 /\ crashes < MaxCrashes /\ crashes' = crashes+1 /\ pc' = 1
         /\ UNCHANGED <<files,backups,journal,marker>>
Done == /\ pc \in {6,7} /\ UNCHANGED vars
Step == Restore \/ RestoreError \/ SaveJournal \/ RemovePending
Next == Step \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Step)
TypeOK == /\ files \in [Files -> {"old","new","absent"}] /\ backups \subseteq Existing
          /\ pc \in 1..7 /\ crashes \in 0..MaxCrashes
          /\ journal \in {"applying","rolled_back"} /\ marker \in BOOLEAN
Recoverable == \A f \in Existing: files[f] # Original[f] => f \in backups
RecoveryComplete == ~marker => (files = Original /\ journal = "rolled_back")
NoPrematureAck == journal = "rolled_back" => files = Original
NoRecoveryError == pc # 7
ImmutableCopyBackups == backups = Existing
Refines == API!Spec
EventuallyRecovers == <> (pc = 6)
====
