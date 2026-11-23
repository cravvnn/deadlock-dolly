---- MODULE EditorEventQueue ----
EXTENDS Naturals
\* L01 editor event path: editor_session.poll drains the native action queue
\* of the bridge it read this tick, in sequence order, on Tk's thread. Each head
\* event is accepted (applied, then acknowledged), refused with a message
\* (app._error, then acknowledged), deferred (dispatch returned False: left
\* queued) or hits a torn native read (NativeStatusBusy from a handler's
\* reread, or NativeEditorStatusBusy from the status itself: left queued and
\* retried next tick). Handlers raise busy before they change anything.
\* The controller may replace the bridge at any time; a replaced game's queue
\* is gone with it. Poll rebinds the editor (configure) to the bridge it read
\* before dispatching, so an event is applied in that bridge's session.
\* Busy reads and deferrals are transient: bounded by K each. Not modeled:
\* handler internals, curve preview offers, dropped_events, save_shot during
\* busy, worker-thread operations submitted by a handler.
CONSTANTS B, N, K
VARIABLES bridge, session, produced, acked, applies, refused, where, busyLeft, deferLeft
vars == <<bridge, session, produced, acked, applies, refused, where, busyLeft, deferLeft>>
Bridges == 1..B
Events == 1..N
API == INSTANCE EditorEventQueueApi WITH acked <- acked, B <- B

Init == /\ bridge = 1 /\ session = 0
        /\ produced = [b \in Bridges |-> 0] /\ acked = [b \in Bridges |-> 0]
        /\ applies = [b \in Bridges |-> [i \in Events |-> 0]]
        /\ refused = [b \in Bridges |-> [i \in Events |-> FALSE]]
        /\ where = [b \in Bridges |-> [i \in Events |-> 0]]
        /\ busyLeft = K /\ deferLeft = K

\* The native editor queues one more in-game action on the live bridge.
Produce == /\ produced[bridge] < N
           /\ produced' = [produced EXCEPT ![bridge] = @ + 1]
           /\ UNCHANGED <<bridge, session, acked, applies, refused, where, busyLeft, deferLeft>>
\* Reconnect or game restart: the controller now returns a new bridge.
Replace == /\ bridge < B /\ bridge' = bridge + 1
           /\ UNCHANGED <<session, produced, acked, applies, refused, where, busyLeft, deferLeft>>

\* One poll tick reads bridge s, rebinds the editor to it and takes the head.
Head(s) == acked[s] + 1
Pending(s) == Head(s) <= produced[s]
Accept == LET s == bridge IN LET e == Head(s) IN
          /\ Pending(s)
          /\ applies' = [applies EXCEPT ![s][e] = @ + 1]
          /\ where' = [where EXCEPT ![s][e] = s]
          /\ acked' = [acked EXCEPT ![s] = e]
          /\ session' = s
          /\ UNCHANGED <<bridge, produced, refused, busyLeft, deferLeft>>
Refuse == LET s == bridge IN LET e == Head(s) IN
          /\ Pending(s)
          /\ refused' = [refused EXCEPT ![s][e] = TRUE]
          /\ acked' = [acked EXCEPT ![s] = e]
          /\ session' = s
          /\ UNCHANGED <<bridge, produced, applies, where, busyLeft, deferLeft>>
Defer == LET s == bridge IN
         /\ Pending(s) /\ deferLeft > 0 /\ deferLeft' = deferLeft - 1
         /\ session' = s
         /\ UNCHANGED <<bridge, produced, acked, applies, refused, where, busyLeft>>
\* except NativeStatusBusy: break -- no ack, the event stays queued.
Busy == LET s == bridge IN
        /\ Pending(s) /\ busyLeft > 0 /\ busyLeft' = busyLeft - 1
        /\ acked' = acked
        /\ session' = s
        /\ UNCHANGED <<bridge, produced, applies, refused, where, deferLeft>>
Idle == /\ ~Pending(bridge) /\ session # bridge /\ session' = bridge
        /\ UNCHANGED <<bridge, produced, acked, applies, refused, where, busyLeft, deferLeft>>

Dispatch == Accept \/ Refuse \/ Defer \/ Busy \/ Idle
Done == /\ ~Pending(bridge) /\ session = bridge /\ UNCHANGED vars
Next == Produce \/ Replace \/ Dispatch \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Dispatch)

TypeOK == /\ bridge \in Bridges /\ session \in 0..B
          /\ produced \in [Bridges -> 0..N] /\ acked \in [Bridges -> 0..N]
          /\ applies \in [Bridges -> [Events -> 0..2]]
          /\ refused \in [Bridges -> [Events -> BOOLEAN]]
          /\ where \in [Bridges -> [Events -> 0..B]]
          /\ busyLeft \in 0..K /\ deferLeft \in 0..K
\* Acknowledgements follow the native queue and never pass it.
AckInOrder == \A b \in Bridges : acked[b] <= produced[b]
\* Every acknowledged event was applied once or refused with a message; a busy
\* read never acknowledges (silently drops) an action.
AckedHandled == \A b \in Bridges : \A i \in Events :
                    i <= acked[b] => (applies[b][i] = 1) # refused[b][i]
Unacked == \A b \in Bridges : \A i \in Events :
               i > acked[b] => applies[b][i] = 0 /\ ~refused[b][i]
AppliedOnce == \A b \in Bridges : \A i \in Events : applies[b][i] <= 1
\* An action from bridge A is never applied in bridge B's editor session.
NoCrossSession == \A b \in Bridges : \A i \in Events : applies[b][i] > 0 => where[b][i] = b
\* While its bridge stays current, every queued action is eventually handled.
Handled == \A b \in Bridges : \A i \in Events :
               (bridge = b /\ produced[b] >= i) ~> (acked[b] >= i \/ bridge # b)
Refines == API!Spec
====
