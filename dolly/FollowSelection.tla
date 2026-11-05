---- MODULE FollowSelection ----
EXTENDS Naturals
\* Controller.start_selected_game_follow, called by follow_replay_player/editor.
\* Finite projection of coherent blend -> bounded owned playback -> pause/seek
\* -> strict confirmation. Seek/monitor/game primitives are abstracted.
\* Cancellation or identity loss may abort; no game-time/visual proof is claimed.
VARIABLES pc, moved, sought, cancelled, identity, ready, mode, enabled, paused, atTick, resumed
vars == <<pc,moved,sought,cancelled,identity,ready,mode,enabled,paused,atTick,resumed>>
Init == /\ pc = "handoff" /\ moved \in BOOLEAN /\ sought = FALSE
        /\ cancelled = FALSE /\ identity = TRUE /\ ready \in BOOLEAN
        /\ mode \in {"chase", "free", "invalid"} /\ enabled = FALSE
        /\ paused = TRUE /\ atTick = ~moved /\ resumed = FALSE
NoBlend == /\ pc = "handoff" /\ ready
           /\ sought' = moved /\ atTick' = TRUE /\ pc' = "confirm"
           /\ UNCHANGED <<moved,cancelled,identity,ready,mode,enabled,paused,resumed>>
ResumeBlend == /\ pc = "handoff" /\ ~ready /\ ~cancelled /\ identity
               /\ pc' = "settling" /\ resumed' = TRUE /\ paused' = FALSE /\ atTick' = FALSE
               /\ UNCHANGED <<moved,sought,cancelled,identity,ready,mode,enabled>>
Cancel == /\ pc \in {"settling","confirm"} /\ ~cancelled /\ cancelled' = TRUE
          /\ UNCHANGED <<pc,moved,sought,identity,ready,mode,enabled,paused,atTick,resumed>>
IdentityChange == /\ pc \in {"settling","confirm"} /\ identity /\ identity' = FALSE
                  /\ UNCHANGED <<pc,moved,sought,cancelled,ready,mode,enabled,paused,atTick,resumed>>
Settle == /\ pc \in {"settling","confirm"} /\ mode # "invalid"
          /\ ready' = TRUE /\ mode' = "chase"
          /\ UNCHANGED <<pc,moved,sought,cancelled,identity,enabled,paused,atTick,resumed>>
CameraChanges == /\ pc = "confirm" /\ ready /\ ready' = FALSE
                 /\ UNCHANGED <<pc,moved,sought,cancelled,identity,mode,enabled,paused,atTick,resumed>>
PauseRestore == /\ pc = "settling" /\ ready /\ ~cancelled /\ identity
                /\ paused' = TRUE /\ atTick' = TRUE /\ sought' = TRUE /\ pc' = "confirm"
                /\ UNCHANGED <<moved,cancelled,identity,ready,mode,enabled,resumed>>
Confirm == /\ pc = "confirm" /\ ~cancelled /\ identity
           /\ ready /\ mode = "chase" /\ paused /\ atTick
           /\ enabled' = TRUE /\ pc' = "done"
           /\ UNCHANGED <<moved,sought,cancelled,identity,ready,mode,paused,atTick,resumed>>
Abort == /\ pc \in {"handoff","settling","confirm"} /\ pc' = "done"
         /\ paused' = IF identity THEN TRUE ELSE paused
         /\ UNCHANGED <<moved,sought,cancelled,identity,ready,mode,enabled,atTick,resumed>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == NoBlend \/ ResumeBlend \/ Cancel \/ IdentityChange \/ Settle \/ CameraChanges \/ PauseRestore \/ Confirm \/ Abort \/ Done
Spec == Init /\ [][Next]_vars
NoRedundantSeek == sought => (moved \/ resumed)
StrictConfirmation == enabled => (ready /\ mode = "chase" /\ identity /\ ~cancelled /\ paused /\ atTick)
====
