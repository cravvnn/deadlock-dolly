---- MODULE GameinfoTransaction ----
\* L03 storage: Dolly's gameinfo.gi and overlay transaction across processes.
\* launch (launcher.py): back up the original (Prepare), write Dolly's base
\* gameinfo with the carried mod mounts and create the overlay (Mount), start
\* the game (Spawn). The game reads gameinfo.gi once while loading; it loads
\* Dolly's plugin only if it read Dolly's file (GameLoad, loaded).
\* Restore (gameinfo_transaction.restore_record): the original bytes are
\* written back only over Dolly's own file; an outside change becomes a
\* conflict and is never overwritten. It runs after the unlocker initializes,
\* when Dolly closes (Controller.close), and in every cleanup.
\* Cleanup (restore, then remove_overlay) runs in Dolly's exit watcher while
\* Dolly is up, in the detached helper Dolly starts when it closes before the
\* game (session_cleanup.start_waiter/wait_and_cleanup), and when Dolly opens
\* again (recover_at_startup, never while a Deadlock runs). The overlay is
\* removed only once no game is loading or running and gameinfo.gi no longer
\* mounts it. A crash starts no helper. Conflicts are resolved only by the
\* user's choice in an open Dolly: put the original back, or keep the current
\* file unless it still mounts Dolly's overlay.
\* Abstractions: one session, one game process; edits are outside writers
\* (mod manager, Steam verify) while Dolly's file is in place; each step is
\* atomic. Steam starts, PID reuse and file-system failures are excluded.
VARIABLES file, journal, overlay, game, loaded, dolly, helper, edited
vars == <<file, journal, overlay, game, loaded, dolly, helper, edited>>

Init == /\ file = "orig" /\ journal = "none" /\ overlay = FALSE /\ game = "none"
        /\ loaded = FALSE /\ dolly = "up" /\ helper = "none" /\ edited = FALSE

Live == game \in {"loading", "running"}
\* Who may clean: the in-process watcher, the detached helper, or recovery
\* when Dolly opens again (only with no Deadlock running).
CanClean == \/ (dolly = "up" /\ game = "exited")
            \/ (helper = "waiting" /\ game = "exited")
            \/ (dolly = "reopened" /\ ~Live)

Prepare == /\ dolly = "up" /\ journal = "none" /\ journal' = "prepared"
           /\ UNCHANGED <<file, overlay, game, loaded, dolly, helper, edited>>
Mount == /\ dolly = "up" /\ journal = "prepared" /\ file = "orig"
         /\ file' = "patched" /\ overlay' = TRUE /\ journal' = "mounted"
         /\ UNCHANGED <<game, loaded, dolly, helper, edited>>
Spawn == /\ dolly = "up" /\ journal = "mounted" /\ game = "none" /\ game' = "loading"
         /\ UNCHANGED <<file, journal, overlay, loaded, dolly, helper, edited>>
GameLoad == /\ game = "loading" /\ game' = "running" /\ loaded' = (file = "patched")
            /\ UNCHANGED <<file, journal, overlay, dolly, helper, edited>>
GameExit == /\ Live /\ game' = "exited"
            /\ UNCHANGED <<file, journal, overlay, loaded, dolly, helper, edited>>
\* A mod manager or Steam verify rewrites the file while Dolly's is in place;
\* "editRef" still mounts Dolly's overlay.
OutsideEdit == /\ journal = "mounted" /\ file = "patched"
               /\ file' \in {"edit", "editRef"} /\ edited' = TRUE
               /\ UNCHANGED <<journal, overlay, game, loaded, dolly, helper>>

RestoreStep == /\ journal \in {"prepared", "mounted", "conflict"}
               /\ IF file = "patched" THEN file' = "orig" /\ journal' = "restored"
                  ELSE IF file = "orig" THEN journal' = "restored" /\ UNCHANGED file
                  ELSE journal' = "conflict" /\ UNCHANGED file
               /\ UNCHANGED <<overlay, game, loaded, dolly, helper, edited>>
\* After the unlocker initializes, or as Controller.close starts.
EarlyRestore == /\ dolly = "up" /\ (game = "running" \/ game = "loading") /\ RestoreStep
CleanRestore == /\ CanClean /\ RestoreStep
RemoveOverlay == /\ CanClean /\ overlay /\ journal = "restored"
                 /\ file \notin {"patched", "editRef"}
                 /\ overlay' = FALSE
                 /\ UNCHANGED <<file, journal, game, loaded, dolly, helper, edited>>

\* Controller.close -> handoff_cleanup: a helper takes over unfinished work.
CloseClean == /\ dolly = "up" /\ dolly' = "closed"
              /\ helper' = IF game # "none" /\ (overlay \/ journal \in {"mounted", "conflict"})
                           THEN "waiting" ELSE "none"
              /\ UNCHANGED <<file, journal, overlay, game, loaded, edited>>
Crash == /\ dolly = "up" /\ dolly' = "crashed"
         /\ UNCHANGED <<file, journal, overlay, game, loaded, helper, edited>>
Reopen == /\ dolly \in {"closed", "crashed"} /\ dolly' = "reopened"
          /\ UNCHANGED <<file, journal, overlay, game, loaded, helper, edited>>

\* The user's explicit choice in an open Dolly, with no Deadlock running.
ResolveRestore == /\ journal = "conflict" /\ dolly \in {"up", "reopened"} /\ ~Live
                  /\ file' = "orig" /\ journal' = "restored" /\ edited' = FALSE
                  /\ UNCHANGED <<overlay, game, loaded, dolly, helper>>
ResolveKeep == /\ journal = "conflict" /\ dolly \in {"up", "reopened"} /\ ~Live
               /\ file = "edit" /\ journal' = "restored" /\ edited' = FALSE
               /\ UNCHANGED <<file, overlay, game, loaded, dolly, helper>>

Clean == CleanRestore \/ RemoveOverlay
Done == /\ ~Live /\ ~overlay /\ journal \in {"none", "restored"} /\ UNCHANGED vars
\* Dolly may stay closed (recovery waits for the next open) or sit idle.
StayClosed == /\ dolly \in {"closed", "reopened"} /\ UNCHANGED vars
Next == \/ Prepare \/ Mount \/ Spawn \/ GameLoad \/ GameExit \/ OutsideEdit
        \/ EarlyRestore \/ Clean \/ CloseClean \/ Crash \/ Reopen
        \/ ResolveRestore \/ ResolveKeep \/ Done \/ StayClosed
\* The game finishes loading and cleaners run; a crashed Dolly is opened
\* again eventually. The game may run forever and the user may never close.
Spec == /\ Init /\ [][Next]_vars
        /\ WF_vars(GameLoad) /\ WF_vars(Clean)
        /\ WF_vars(Reopen /\ dolly = "crashed")

TypeOK == /\ file \in {"orig", "patched", "edit", "editRef"}
          /\ journal \in {"none", "prepared", "mounted", "restored", "conflict"}
          /\ overlay \in BOOLEAN /\ loaded \in BOOLEAN /\ edited \in BOOLEAN
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
\* Once the game has exited, the original comes back and the overlay goes,
\* unless an outside change is waiting for the user's choice. This holds
\* when Dolly closes first (helper) and after a crash (recovery on reopen).
ExitCleansUp == game = "exited" ~> (journal = "conflict" \/ (journal \in {"none", "restored"} /\ ~overlay))
====
