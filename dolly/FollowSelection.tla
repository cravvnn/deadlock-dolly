---- MODULE FollowSelection ----
EXTENDS Naturals
\* start_selected_game_follow, called by follow_replay_player and editor_dispatch.
\* Scoped handoff model: unchanged/advanced tick; transient observer mode4;
\* fresh identity/cancel check and strict readiness before rig enable.
\* Existing seek, monitor, restore and OS/game behavior are abstract primitives.
\* No claim that avoiding reconstruction ends an actual engine blend.
VARIABLES pc, moved, sought, cancelled, identity, ready, mode, enabled
vars == <<pc,moved,sought,cancelled,identity,ready,mode,enabled>>
Init == /\ pc = "handoff" /\ moved \in BOOLEAN /\ sought = FALSE
        /\ cancelled = FALSE /\ identity = TRUE /\ ready \in BOOLEAN
        /\ mode \in {"chase", "free", "invalid"} /\ enabled = FALSE
RestoreTick == /\ pc = "handoff"
               /\ sought' = moved
               /\ pc' = "confirm"
               /\ UNCHANGED <<moved,cancelled,identity,ready,mode,enabled>>
Cancel == /\ pc = "confirm" /\ ~cancelled /\ cancelled' = TRUE
          /\ UNCHANGED <<pc,moved,sought,identity,ready,mode,enabled>>
IdentityChange == /\ pc = "confirm" /\ identity /\ identity' = FALSE
                  /\ UNCHANGED <<pc,moved,sought,cancelled,ready,mode,enabled>>
Settle == /\ pc = "confirm" /\ mode # "invalid"
          /\ ready' = TRUE /\ mode' = "chase"
          /\ UNCHANGED <<pc,moved,sought,cancelled,identity,enabled>>
Confirm == /\ pc = "confirm" /\ ~cancelled /\ identity
           /\ ready /\ mode = "chase"
           /\ enabled' = TRUE /\ pc' = "done"
           /\ UNCHANGED <<moved,sought,cancelled,identity,ready,mode>>
TimeoutOrReject == /\ pc = "confirm" /\ pc' = "done"
                   /\ UNCHANGED <<moved,sought,cancelled,identity,ready,mode,enabled>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == RestoreTick \/ Cancel \/ IdentityChange \/ Settle \/ Confirm \/ TimeoutOrReject \/ Done
Spec == Init /\ [][Next]_vars
NoRedundantSeek == sought => moved
StrictConfirmation == enabled => (ready /\ mode = "chase" /\ identity /\ ~cancelled)
====
