---- MODULE LaunchRecovery ----
\* L03 storage: one Dolly window launches session N while another window's
\* recovery (recover_at_startup, File > Recover, or its own launch) runs.
\* Launcher L (launcher.launch): take game_config_lock, recover, create the
\* marked overlay, save the journal as prepared, write the patched gameinfo,
\* save the journal as mounted, spawn the game, release the lock.
\* Recoverer R (recover_pending / recover_orphans): one game check before its
\* loop, then per journal or overlay: take the lock, check the game again,
\* restore_record (prepared/mounted -> original bytes, journal restored), and
\* remove_overlay, which refuses while gameinfo still mounts the overlay.
\* The named mutex is re-entrant per thread, so L's own nested recovery is
\* not modelled. No outside edits; N's game never exits here, so its exit
\* watcher and helper are not modelled (GameinfoCleaners covers them).
VARIABLES file, journal, overlay, game, lock, lpc, rpc, atSpawn
vars == <<file, journal, overlay, game, lock, lpc, rpc, atSpawn>>

LaunchLocks == TRUE
Recheck == TRUE

Init == /\ file = "orig" /\ journal = "none" /\ overlay = FALSE /\ game = "none"
        /\ lock = "free" /\ lpc = "acquire" /\ rpc = "check" /\ atSpawn = "none"

\* ---- launcher of N
LAcquire == /\ lpc = "acquire"
            /\ IF LaunchLocks THEN lock = "free" /\ lock' = "L" ELSE UNCHANGED lock
            /\ lpc' = "overlay"
            /\ UNCHANGED <<file, journal, overlay, game, rpc, atSpawn>>
LOverlay == /\ lpc = "overlay" /\ overlay' = TRUE /\ lpc' = "journal"
            /\ UNCHANGED <<file, journal, game, lock, rpc, atSpawn>>
LJournal == /\ lpc = "journal" /\ journal' = "prepared" /\ lpc' = "patch"
            /\ UNCHANGED <<file, overlay, game, lock, rpc, atSpawn>>
\* The sha check refuses a gameinfo that is no longer the backed-up original.
LPatch == /\ lpc = "patch"
          /\ IF file = "orig" THEN file' = "patchedN" /\ lpc' = "mounted"
                              ELSE UNCHANGED file /\ lpc' = "release"
          /\ UNCHANGED <<journal, overlay, game, lock, rpc, atSpawn>>
\* launch saves its in-memory record, so this overwrites any journal state.
LMounted == /\ lpc = "mounted" /\ journal' = "mounted" /\ lpc' = "spawn"
            /\ UNCHANGED <<file, overlay, game, lock, rpc, atSpawn>>
LSpawn == /\ lpc = "spawn" /\ game' = "N"
          /\ atSpawn' = (IF overlay THEN "present" ELSE "missing") /\ lpc' = "release"
          /\ UNCHANGED <<file, journal, overlay, lock, rpc>>
LRelease == /\ lpc = "release"
            /\ IF LaunchLocks THEN lock' = "free" ELSE UNCHANGED lock
            /\ lpc' = "done"
            /\ UNCHANGED <<file, journal, overlay, game, rpc, atSpawn>>

\* ---- recoverer in another window
RCheck == /\ rpc = "check" /\ rpc' = (IF game = "N" THEN "done" ELSE "acquire")
          /\ UNCHANGED <<file, journal, overlay, game, lock, lpc, atSpawn>>
RAcquire == /\ rpc = "acquire" /\ lock = "free" /\ lock' = "R" /\ rpc' = "recheck"
            /\ UNCHANGED <<file, journal, overlay, game, lpc, atSpawn>>
RRecheck == /\ rpc = "recheck" /\ rpc' = (IF Recheck /\ game = "N" THEN "release" ELSE "restore")
            /\ UNCHANGED <<file, journal, overlay, game, lock, lpc, atSpawn>>
RRestore == /\ rpc = "restore"
            /\ IF journal \in {"prepared", "mounted"}
               THEN file' = "orig" /\ journal' = "restored"
               ELSE UNCHANGED <<file, journal>>
            /\ rpc' = "remove"
            /\ UNCHANGED <<overlay, game, lock, lpc, atSpawn>>
\* remove_overlay (or recover_orphans before any journal exists).
RRemove == /\ rpc = "remove"
           /\ overlay' = (overlay /\ file = "patchedN")
           /\ rpc' = "release"
           /\ UNCHANGED <<file, journal, game, lock, lpc, atSpawn>>
RRelease == /\ rpc = "release" /\ lock' = "free" /\ rpc' = "done"
            /\ UNCHANGED <<file, journal, overlay, game, lpc, atSpawn>>

Done == /\ lpc = "done" /\ rpc = "done" /\ UNCHANGED vars
Launcher == LAcquire \/ LOverlay \/ LJournal \/ LPatch \/ LMounted \/ LSpawn \/ LRelease
Recoverer == RCheck \/ RAcquire \/ RRecheck \/ RRestore \/ RRemove \/ RRelease
Next == Launcher \/ Recoverer \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Launcher) /\ WF_vars(Recoverer)

TypeOK == /\ file \in {"orig", "patchedN"}
          /\ journal \in {"none", "prepared", "mounted", "restored"}
          /\ overlay \in BOOLEAN /\ game \in {"none", "N"} /\ lock \in {"free", "L", "R"}
          /\ atSpawn \in {"none", "present", "missing"}
\* The game starts with Dolly's plugin mounted.
OverlayPresentAtSpawn == atSpawn # "missing"
\* A restored journal never sits beside Dolly's patched file.
RestoredNotPatched == journal = "restored" => file # "patchedN"
\* Recovery leaves a running game's files alone.
RunningGameUntouched == game = "N" => (overlay /\ file = "patchedN")
Terminates == <>(lpc = "done" /\ rpc = "done")
====
