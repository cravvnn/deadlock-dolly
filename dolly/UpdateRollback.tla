---- MODULE UpdateRollback ----
EXTENDS Naturals, FiniteSets
\* Existing code: update_worker.rollback. Source callers: install's exception
\* handler and _apply_plan's recovery branch. Both enter under install_lock;
\* process/plan/path/hash validation remains implementation evidence outside
\* this bounded file-content model. Initial disk states include each prefix
\* of install's apply loop after backup+journal+PENDING exist, up to the first
\* step the file system refuses (install then rolls back from there).
\* Three owned files cover replacement, removal and newly added files.
\* Nested (2026-11-14) adds path kinds: path 1 is "a" and path 2 is "a/x", so
\* a release can turn a file into a folder ("fileToDir") or a folder into a
\* file ("dirToFile"). The file system refuses a file where a folder stands
\* (empty or not) and a file below a path that is a file. install and rollback
\* therefore remove before they write (removals deepest first, then writes)
\* and prune each folder a removal empties (folders holds the empty ones).
\* Without that, a failed update that turned a file into a folder could not
\* roll back, and the reverse update was refused outright. install's own
\* pruning is assumed in its prefix states; rollback's is modeled.
\* A process crash may restart rollback twice at any file/journal boundary.
\* Directory durability, power loss, external edits and corrupt bytes excluded.
CONSTANTS Files, Existing, MaxCrashes, Nested
VARIABLES files, backups, pc, crashes, journal, marker, folders
vars == <<files,backups,pc,crashes,journal,marker,folders>>
N == Cardinality(Files)
SavePc == N + 1
ReleasePc == N + 2
DonePc == N + 3
ErrorPc == N + 4
Original == [f \in Files |-> IF f \in Existing THEN "old" ELSE "absent"]
Updated == [f \in Files |->
              CASE Nested = "fileToDir" -> IF f = 1 THEN "absent" ELSE "new"
                [] Nested = "dirToFile" -> IF f = 2 THEN "absent" ELSE "new"
                [] OTHER -> IF f = 3 THEN "absent" ELSE "new"]
\* Path 2 lies inside path 1 in the nested cases.
Inside(c, p) == Nested # "none" /\ c = 2 /\ p = 1
\* No content below f and no parent path of f that is a file.
PathClear(disk, f) == /\ \A c \in Files: Inside(c, f) => disk[c] = "absent"
                      /\ \A p \in Files: Inside(f, p) => disk[p] = "absent"
WriteOk(f) == f \notin folders /\ PathClear(files, f)
\* install's apply loop order: removals first (deepest first), then writes.
Key(f) == IF Updated[f] = "absent" THEN 10 - f ELSE 20 + f
Rank(f) == Cardinality({g \in Files: Key(g) < Key(f)}) + 1
At(k) == CHOOSE f \in Files: Rank(f) = k
Prefix(k) == [f \in Files |-> IF Rank(f) <= k THEN Updated[f] ELSE Original[f]]
StepOk(j) == Updated[At(j)] = "absent" \/ PathClear(Prefix(j - 1), At(j))
AppliedUpTo(k) == \A j \in 1..k: StepOk(j)
\* rollback's order: drop files the update added (deepest first), then restore.
RKey(f) == IF Original[f] = "absent" THEN 10 - f ELSE 20 + f
RRank(f) == Cardinality({g \in Files: RKey(g) < RKey(f)}) + 1
RAt(k) == CHOOSE f \in Files: RRank(f) = k
Disk == INSTANCE FileOpsApi WITH contents <- files, availableBackups <- backups
API == INSTANCE RollbackApi WITH contents <- files, pending <- marker,
                                recovered <- journal = "rolled_back"
Init == /\ files \in {Prefix(k): k \in {j \in 0..N: AppliedUpTo(j)}} /\ backups = Existing
        /\ pc = 1 /\ crashes = 0 /\ journal = "applying" /\ marker = TRUE /\ folders = {}
Current == RAt(pc)
AlreadyRestored == files[Current] = Original[Current]
\* Removing a file also removes the folder it leaves empty (prune).
Pruned(f) == folders
Restore == /\ pc \in 1..N
           /\ IF AlreadyRestored
              THEN /\ UNCHANGED <<files,backups,folders>> /\ pc' = pc + 1
              ELSE IF Current \notin Existing
                   THEN /\ Disk!Remove(Current) /\ folders' = Pruned(Current) /\ pc' = pc + 1
                   ELSE IF WriteOk(Current)
                        THEN /\ Disk!Copy(Current) /\ UNCHANGED folders /\ pc' = pc + 1
                        ELSE /\ pc' = ErrorPc /\ UNCHANGED <<files,backups,folders>>
           /\ UNCHANGED <<crashes,journal,marker>>
RestoreError == /\ pc \in 1..N /\ Current \in Existing /\ ~AlreadyRestored /\ Current \notin backups
                /\ pc' = ErrorPc /\ UNCHANGED <<files,backups,crashes,journal,marker,folders>>
SaveJournal == /\ pc = SavePc /\ journal' = "rolled_back" /\ pc' = ReleasePc
               /\ UNCHANGED <<files,backups,crashes,marker,folders>>
RemovePending == /\ pc = ReleasePc /\ marker' = FALSE /\ pc' = DonePc
                 /\ UNCHANGED <<files,backups,crashes,journal,folders>>
Crash == /\ pc < DonePc /\ crashes < MaxCrashes /\ crashes' = crashes+1 /\ pc' = 1
         /\ UNCHANGED <<files,backups,journal,marker,folders>>
Done == /\ pc \in {DonePc, ErrorPc} /\ UNCHANGED vars
Step == Restore \/ RestoreError \/ SaveJournal \/ RemovePending
Next == Step \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Step)
TypeOK == /\ files \in [Files -> {"old","new","absent"}] /\ backups \subseteq Existing
          /\ pc \in 1..ErrorPc /\ crashes \in 0..MaxCrashes
          /\ journal \in {"applying","rolled_back"} /\ marker \in BOOLEAN /\ folders \subseteq Files
Recoverable == \A f \in Existing: files[f] # Original[f] => f \in backups
RecoveryComplete == ~marker => (files = Original /\ journal = "rolled_back")
NoPrematureAck == journal = "rolled_back" => files = Original
NoRecoveryError == pc # ErrorPc
ImmutableCopyBackups == backups = Existing
\* A path is never a file and a folder at once.
NoFileFolderClash == /\ \A c, p \in Files: Inside(c, p) => (files[c] = "absent" \/ files[p] = "absent")
                     /\ \A f \in folders: files[f] = "absent"
\* The release can be installed at all: every step of the apply loop is legal.
UpdateInstallable == pc \in 1..ErrorPc => AppliedUpTo(N)
Refines == API!Spec
EventuallyRecovers == <> (pc = DonePc)
====
