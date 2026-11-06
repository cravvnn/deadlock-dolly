---- MODULE PlayersRecovery ----
EXTENDS Naturals
\* DollyApp._recover_export_failure -> VideoExport.stop/_confirm_player_stop.
\* NativeOwned abstracts the pinned session, selected replay and native identity;
\* MarkerOwned abstracts the request check. Every Boolean combination is checked.
\* Stop is a file request; console verification belongs after the terminal ack.
\* PlayersRestoreRetry models the bounded post-ack console readiness helper;
\* consoleReady here abstracts its returning success versus retained failure.
\* Recheck models owner/request replacement while waiting. Restore abstracts all
\* reservation release and runtime restoration, guarded again by real code.
\* No GPU/OS freeze, file syscall liveness, or whole-program coverage is claimed.
VARIABLES pc, nativeOwned, markerOwned, consoleReady, ack, sent, sentOwned, restored
vars == <<pc, nativeOwned, markerOwned, consoleReady, ack, sent, sentOwned, restored>>
Init == /\ pc = "stop"
        /\ nativeOwned \in BOOLEAN /\ markerOwned \in BOOLEAN
        /\ consoleReady \in BOOLEAN /\ ack \in BOOLEAN
        /\ sent = FALSE /\ sentOwned = FALSE /\ restored = FALSE
StopPermitted == nativeOwned /\ markerOwned
RequestStop == /\ pc = "stop"
               /\ sent' = StopPermitted
               /\ sentOwned' = (nativeOwned /\ markerOwned)
               /\ pc' = "requested"
               /\ UNCHANGED <<nativeOwned, markerOwned, consoleReady, ack, restored>>
Recheck == /\ pc = "requested" /\ pc' = "restore"
           /\ nativeOwned' \in BOOLEAN /\ markerOwned' \in BOOLEAN
           /\ consoleReady' \in BOOLEAN
           /\ UNCHANGED <<ack, sent, sentOwned, restored>>
RestorePermitted == sent /\ ack /\ nativeOwned /\ markerOwned /\ consoleReady
Restore == /\ pc = "restore" /\ pc' = "done"
           /\ restored' = RestorePermitted
           /\ UNCHANGED <<nativeOwned, markerOwned, consoleReady, ack, sent, sentOwned>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == RequestStop \/ Recheck \/ Restore \/ Done
Spec == Init /\ [][Next]_vars
BusyConsoleCannotBlockStop == (pc = "requested" /\ nativeOwned /\ markerOwned) => sent
NoForeignStop == sent => sentOwned
KeepPendingUntilVerified == restored => (sent /\ ack /\ nativeOwned /\ markerOwned /\ consoleReady)
====
