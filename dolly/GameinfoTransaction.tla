---- MODULE GameinfoTransaction ----
\* L03 storage: Dolly's gameinfo.gi and overlay transaction across processes.
\* launch (launcher.py): back up the original (Prepare), write Dolly's base
\* gameinfo with the carried mod mounts and create the overlay (Mount), start
\* the game and the detached helper (Spawn). The game reads gameinfo.gi once
\* while loading; it loads Dolly's plugin only if it read Dolly's file
\* (GameLoad, loaded).
\* Restore (gameinfo_transaction.restore_record): the original bytes are
\* written back only over Dolly's own file; an outside change becomes a
\* conflict and is never overwritten. It runs after the unlocker initializes,
\* when Dolly closes (Controller.close), and in every cleanup.
\* Cleanup (restore, then remove_overlay) runs in Dolly's exit watcher while
\* Dolly is up, in the detached helper started with the game
\* (Session.start_cleanup_helper, session_cleanup.start_waiter and
\* wait_and_cleanup), and when Dolly opens again (recover_at_startup, never
\* while a Deadlock runs). The helper holds the game's and Dolly's process
\* handles and acts only once the game has exited and that Dolly is gone
\* (closed, crashed or killed), so a crash no longer waits for the next open.
\* The helper itself can be lost (failed start, killed, power loss); recovery
\* when Dolly opens covers that. The overlay is removed only once no game is
\* loading or running and gameinfo.gi no longer mounts it. Conflicts are
\* resolved only by the user's choice in an open Dolly: put the original
\* back, or keep the current file unless it still mounts Dolly's overlay.
\* Abstractions: one session, one game process; edits are outside writers
\* (mod manager, Steam verify) while Dolly's file is in place; each step is
\* atomic, so the reason the helper defers to a live Dolly (two cleaners
\* racing on the exit record and diagnostics) is not visible here. Steam
\* starts, PID reuse and file-system failures are excluded.
VARIABLES file, journal, overlay, game, loaded, dolly, helper, edited, lost
vars == <<file, journal, overlay, game, loaded, dolly, helper, edited, lost>>

Init == /\ file = "orig" /\ journal = "none" /\ overlay = FALSE /\ game = "none"
        /\ loaded = FALSE /\ dolly = "up" /\ helper = "none" /\ edited = FALSE
        /\ lost = FALSE

Live == game \in {"loading", "running"}
\* The caller-visible interface (GameinfoInstallApi) this transaction refines.
Cfg == CASE file = "orig" -> "original" [] file = "patched" -> "dolly"
         [] file = "edit" -> "other" [] OTHER -> "otherRef"
API == INSTANCE GameinfoInstallApi WITH cfg <- Cfg, plugin <- overlay, alive <- Live
\* Who may clean: the in-process watcher, the detached helper once its Dolly
\* is gone, or recovery when Dolly opens again (only with no Deadlock running).
CanClean == \/ (dolly = "up" /\ game = "exited")
            \/ (helper = "waiting" /\ game = "exited" /\ dolly # "up")
            \/ (dolly = "reopened" /\ ~Live)

Prepare == /\ dolly = "up" /\ journal = "none" /\ journal' = "prepared"
           /\ UNCHANGED <<file, overlay, game, loaded, dolly, helper, edited, lost>>
Mount == /\ dolly = "up" /\ journal = "prepared" /\ file = "orig"
         /\ file' = "patched" /\ overlay' = TRUE /\ journal' = "mounted"
         /\ UNCHANGED <<game, loaded, dolly, helper, edited, lost>>
Spawn == /\ dolly = "up" /\ journal = "mounted" /\ game = "none" /\ game' = "loading"
         /\ helper' = "waiting"
         /\ UNCHANGED <<file, journal, overlay, loaded, dolly, edited, lost>>
GameLoad == /\ game = "loading" /\ game' = "running" /\ loaded' = (file = "patched")
            /\ UNCHANGED <<file, journal, overlay, dolly, helper, edited, lost>>
GameExit == /\ Live /\ game' = "exited"
            /\ UNCHANGED <<file, journal, overlay, loaded, dolly, helper, edited, lost>>
\* A mod manager or Steam verify rewrites the file while Dolly's is in place;
\* "editRef" still mounts Dolly's overlay.
OutsideEdit == /\ journal = "mounted" /\ file = "patched"
               /\ file' \in {"edit", "editRef"} /\ edited' = TRUE
               /\ UNCHANGED <<journal, overlay, game, loaded, dolly, helper, lost>>

RestoreStep == /\ journal \in {"prepared", "mounted", "conflict"}
               /\ IF file = "patched" THEN file' = "orig" /\ journal' = "restored"
                  ELSE IF file = "orig" THEN journal' = "restored" /\ UNCHANGED file
                  ELSE journal' = "conflict" /\ UNCHANGED file
               /\ UNCHANGED <<overlay, game, loaded, dolly, helper, edited, lost>>
\* After the unlocker initializes, or as Controller.close starts.
EarlyRestore == /\ dolly = "up" /\ (game = "running" \/ game = "loading") /\ RestoreStep
CleanRestore == /\ CanClean /\ RestoreStep
RemoveOverlay == /\ CanClean /\ overlay /\ journal = "restored"
                 /\ file \notin {"patched", "editRef"}
                 /\ overlay' = FALSE
                 /\ UNCHANGED <<file, journal, game, loaded, dolly, helper, edited, lost>>

CloseClean == /\ dolly = "up" /\ dolly' = "closed"
              /\ UNCHANGED <<file, journal, overlay, game, loaded, helper, edited, lost>>
Crash == /\ dolly = "up" /\ dolly' = "crashed"
         /\ UNCHANGED <<file, journal, overlay, game, loaded, helper, edited, lost>>
Reopen == /\ dolly \in {"closed", "crashed"} /\ dolly' = "reopened"
          /\ UNCHANGED <<file, journal, overlay, game, loaded, helper, edited, lost>>
\* The helper never started, was killed, or the machine lost power.
HelperLost == /\ helper = "waiting" /\ helper' = "none" /\ lost' = TRUE
              /\ UNCHANGED <<file, journal, overlay, game, loaded, dolly, edited>>

\* The user's explicit choice in an open Dolly, with no Deadlock running.
ResolveRestore == /\ journal = "conflict" /\ dolly \in {"up", "reopened"} /\ ~Live
                  /\ file' = "orig" /\ journal' = "restored" /\ edited' = FALSE
                  /\ UNCHANGED <<overlay, game, loaded, dolly, helper, lost>>
ResolveKeep == /\ journal = "conflict" /\ dolly \in {"up", "reopened"} /\ ~Live
               /\ file = "edit" /\ journal' = "restored" /\ edited' = FALSE
               /\ UNCHANGED <<file, overlay, game, loaded, dolly, helper, lost>>

Clean == CleanRestore \/ RemoveOverlay
Done == /\ ~Live /\ ~overlay /\ journal \in {"none", "restored"} /\ UNCHANGED vars
\* Dolly may stay closed or crashed (recovery waits for the next open) or sit idle.
StayClosed == /\ dolly \in {"closed", "crashed", "reopened"} /\ UNCHANGED vars
Next == \/ Prepare \/ Mount \/ Spawn \/ GameLoad \/ GameExit \/ OutsideEdit
        \/ EarlyRestore \/ Clean \/ CloseClean \/ Crash \/ Reopen \/ HelperLost
        \/ ResolveRestore \/ ResolveKeep \/ Done \/ StayClosed
\* The game finishes loading and cleaners run. The game may run forever and
\* the user may never close or reopen Dolly.
Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(GameLoad) /\ WF_vars(Clean)

TypeOK == /\ file \in {"orig", "patched", "edit", "editRef"}
          /\ journal \in {"none", "prepared", "mounted", "restored", "conflict"}
          /\ overlay \in BOOLEAN /\ loaded \in BOOLEAN /\ edited \in BOOLEAN
          /\ lost \in BOOLEAN
          /\ game \in {"none", "loading", "running", "exited"}
          /\ dolly \in {"up", "closed", "crashed", "reopened"}
          /\ helper \in {"none", "waiting"}
\* An outside change is never overwritten without the user's choice.
NeverOverwriteEdit == edited => file \in {"edit", "editRef"}
\* A running game that loaded Dolly's plugin keeps its files.
RunningGameKeepsPlugin == (Live /\ loaded) => overlay
\* gameinfo.gi never mounts a missing overlay.
NoDanglingMount == file \in {"patched", "editRef"} => overlay
\* A finished transaction leaves a file that loads on its own.
RestoredStandalone == journal = "restored" => file \in {"orig", "edit"}
Cleaned == journal = "conflict" \/ (journal \in {"none", "restored"} /\ ~overlay)
\* Once the game has exited, the original comes back and the overlay goes,
\* unless an outside change is waiting for the user's choice. No reopen is
\* needed: this holds when Dolly closes first and when it crashes, as long as
\* the helper survives.
ExitCleansUp == game = "exited" ~> (Cleaned \/ lost)
\* Whatever happened to the helper, opening Dolly again finishes the job.
ReopenCleansUp == (game = "exited" /\ dolly = "reopened") ~> Cleaned
Refines == API!Spec
====
