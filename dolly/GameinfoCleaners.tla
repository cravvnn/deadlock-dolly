---- MODULE GameinfoCleaners ----
\* L03 storage: two cleaners of one finished session at the same time.
\* The old session S's game has exited and its Dolly is gone, so its detached
\* helper cleans (session_cleanup.wait_and_cleanup). Meanwhile another Dolly
\* window launches: launch -> recover_pending recovers S, then backs up the
\* current gameinfo.gi as the new session N's original and writes N's file.
\* restore_record is split into its real reads and writes: read the journal
\* (skip when already restored), read gameinfo.gi, write it back if it is S's
\* file, save the journal (restored, or conflict for anything else); then
\* remove_overlay. GameinfoTransaction treats each of these as one atomic step,
\* so it cannot show this interleaving.
\* Locked: every cleaner holds a per-user named mutex (session_cleanup.
\* game_config_lock) across restore and overlay removal, in wait_and_cleanup,
\* recover_pending and Session.close. Without it the helper can read N's file
\* after the new Dolly recovered S, and mark S as changed outside Dolly.
\* remove_overlay may keep the folder (it holds user files); the journal
\* re-read inside the lock is then what stops a second restore.
\* No outside edits here, so any conflict is false. One game process at a time;
\* S's game has already exited. The mutex is abandoned on process exit.
CONSTANT Locked
VARIABLES file, journal, overlay, game, lock, hpc, hJournal, hFile, dpc, dJournal, dFile, backup
vars == <<file, journal, overlay, game, lock, hpc, hJournal, hFile, dpc, dJournal, dFile, backup>>
others(v) == v

Init == /\ file = "patchedS" /\ journal = "mounted" /\ overlay = TRUE /\ game = "none"
        /\ lock = "free" /\ hpc = "start" /\ hJournal = "none" /\ hFile = "none"
        /\ dpc = "start" /\ dJournal = "none" /\ dFile = "none" /\ backup = "none"

Acquire(who) == IF Locked THEN lock = "free" /\ lock' = who ELSE UNCHANGED lock
Release(who) == IF Locked THEN lock' = "free" ELSE UNCHANGED lock
Clean == journal = "restored" /\ ~overlay
Decide(seen) == IF seen = "patchedS" \/ seen = "orig" THEN "restored" ELSE "conflict"

\* ---- helper of S (wait_and_cleanup, after S's game exited and its Dolly died)
HStart == /\ hpc = "start" /\ Acquire("H") /\ hpc' = "check"
          /\ UNCHANGED <<file, journal, overlay, game, hJournal, hFile, dpc, dJournal, dFile, backup>>
\* Already clean, or a Deadlock is running again: defer to the next launch.
HCheck == /\ hpc = "check"
          /\ IF Clean \/ game = "N" THEN hpc' = "release" ELSE hpc' = "readJ"
          /\ UNCHANGED <<file, journal, overlay, game, lock, hJournal, hFile, dpc, dJournal, dFile, backup>>
HReadJ == /\ hpc = "readJ" /\ hJournal' = journal
          /\ hpc' = IF journal = "restored" THEN "remove" ELSE "readF"
          /\ UNCHANGED <<file, journal, overlay, game, lock, hFile, dpc, dJournal, dFile, backup>>
HReadF == /\ hpc = "readF" /\ hFile' = file /\ hpc' = "write"
          /\ UNCHANGED <<file, journal, overlay, game, lock, hJournal, dpc, dJournal, dFile, backup>>
HWrite == /\ hpc = "write" /\ file' = (IF hFile = "patchedS" THEN "orig" ELSE file) /\ hpc' = "journal"
          /\ UNCHANGED <<journal, overlay, game, lock, hJournal, hFile, dpc, dJournal, dFile, backup>>
HJournal == /\ hpc = "journal" /\ journal' = Decide(hFile)
            /\ hpc' = IF Decide(hFile) = "restored" THEN "remove" ELSE "release"
            /\ UNCHANGED <<file, overlay, game, lock, hJournal, hFile, dpc, dJournal, dFile, backup>>
HRemove == /\ hpc = "remove" /\ (overlay' = (overlay /\ file = "patchedS") \/ overlay' = overlay) /\ hpc' = "release"
           /\ UNCHANGED <<file, journal, game, lock, hJournal, hFile, dpc, dJournal, dFile, backup>>
HRelease == /\ hpc = "release" /\ Release("H") /\ hpc' = "done"
            /\ UNCHANGED <<file, journal, overlay, game, hJournal, hFile, dpc, dJournal, dFile, backup>>

\* ---- the other Dolly's launch: recover_pending for S, then mount N
DStart == /\ dpc = "start" /\ Acquire("D") /\ dpc' = "readJ"
          /\ UNCHANGED <<file, journal, overlay, game, hpc, hJournal, hFile, dJournal, dFile, backup>>
DReadJ == /\ dpc = "readJ" /\ dJournal' = journal
          /\ dpc' = IF journal = "restored" THEN "remove" ELSE "readF"
          /\ UNCHANGED <<file, journal, overlay, game, lock, hpc, hJournal, hFile, dFile, backup>>
DReadF == /\ dpc = "readF" /\ dFile' = file /\ dpc' = "write"
          /\ UNCHANGED <<file, journal, overlay, game, lock, hpc, hJournal, hFile, dJournal, backup>>
DWrite == /\ dpc = "write" /\ file' = (IF dFile = "patchedS" THEN "orig" ELSE file) /\ dpc' = "journal"
          /\ UNCHANGED <<journal, overlay, game, lock, hpc, hJournal, hFile, dJournal, dFile, backup>>
DJournal == /\ dpc = "journal" /\ journal' = Decide(dFile)
            /\ dpc' = IF Decide(dFile) = "restored" THEN "remove" ELSE "refused"
            /\ UNCHANGED <<file, overlay, game, lock, hpc, hJournal, hFile, dJournal, dFile, backup>>
DRemove == /\ dpc = "remove" /\ (overlay' = (overlay /\ file = "patchedS") \/ overlay' = overlay) /\ dpc' = "release"
           /\ UNCHANGED <<file, journal, game, lock, hpc, hJournal, hFile, dJournal, dFile, backup>>
DRelease == /\ dpc = "release" /\ Release("D") /\ dpc' = "backup"
            /\ UNCHANGED <<file, journal, overlay, game, hpc, hJournal, hFile, dJournal, dFile, backup>>
\* A conflict stops the launch (recover_pending raises).
DRefused == /\ dpc = "refused" /\ Release("D") /\ dpc' = "done"
            /\ UNCHANGED <<file, journal, overlay, game, hpc, hJournal, hFile, dJournal, dFile, backup>>
DBackup == /\ dpc = "backup" /\ backup' = file /\ dpc' = "mount"
           /\ UNCHANGED <<file, journal, overlay, game, lock, hpc, hJournal, hFile, dJournal, dFile>>
DMount == /\ dpc = "mount" /\ file' = "patchedN" /\ dpc' = "spawn"
          /\ UNCHANGED <<journal, overlay, game, lock, hpc, hJournal, hFile, dJournal, dFile, backup>>
DSpawn == /\ dpc = "spawn" /\ game' = "N" /\ dpc' = "done"
          /\ UNCHANGED <<file, journal, overlay, lock, hpc, hJournal, hFile, dJournal, dFile, backup>>

Done == /\ hpc = "done" /\ dpc = "done" /\ UNCHANGED vars
Helper == HStart \/ HCheck \/ HReadJ \/ HReadF \/ HWrite \/ HJournal \/ HRemove \/ HRelease
Launch == DStart \/ DReadJ \/ DReadF \/ DWrite \/ DJournal \/ DRemove \/ DRelease \/ DRefused
          \/ DBackup \/ DMount \/ DSpawn
Next == Helper \/ Launch \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Helper) /\ WF_vars(Launch)

TypeOK == /\ file \in {"orig", "patchedS", "patchedN"}
          /\ journal \in {"mounted", "restored", "conflict"} /\ overlay \in BOOLEAN
          /\ game \in {"none", "N"} /\ lock \in {"free", "H", "D"}
          /\ backup \in {"none", "orig", "patchedS", "patchedN"}
\* Dolly never blames an outside program for its own file.
NoFalseConflict == journal # "conflict"
\* The new session backs up the user's own file as its original.
BackupIsOriginal == backup \in {"none", "orig"}
\* Both finish, and S ends restored.
Settles == <>(hpc = "done" /\ dpc = "done" /\ journal = "restored")
====
