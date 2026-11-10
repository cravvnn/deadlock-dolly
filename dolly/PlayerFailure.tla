---- MODULE PlayerFailure ----
EXTENDS Naturals
\* VideoExport.status -> GUI._refresh_video worker recovery -> stop/finally.
\* Owned error telemetry reports failure without releasing capture ownership.
\* _confirm_player_stop acknowledges native terminal failure; restoration still
\* requires current identity (PlayersRecovery/PlayersRestoreRetry model those
\* checks). Native readback/flush barrier is CaptureFailure in native/src.
\* One request/observation and one stop retry. No wall-time/liveness guarantee.
VARIABLES pc, owned, ack, failed, pending, request, restored
vars == <<pc, owned, ack, failed, pending, request, restored>>
ReportFailure == TRUE
KeepPending == TRUE
KeepFailure == TRUE
Init == /\ pc = "observe" /\ owned \in BOOLEAN /\ ack \in BOOLEAN
        /\ failed = FALSE /\ pending = TRUE /\ request = TRUE /\ restored = FALSE
Observe == /\ pc = "observe" /\ pc' = "stop"
           /\ failed' = (owned /\ ReportFailure)
           /\ UNCHANGED <<owned, ack, pending, request, restored>>
Stop == /\ pc = "stop" /\ pc' = "done"
        /\ pending' = IF owned /\ ack THEN FALSE ELSE KeepPending
        /\ request' = IF owned /\ ack THEN FALSE ELSE KeepPending
        /\ failed' = IF owned /\ ack THEN KeepFailure ELSE failed
        /\ restored' = (owned /\ ack)
        /\ UNCHANGED <<owned, ack>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Observe \/ Stop \/ Done
Spec == Init /\ [][Next]_vars
PromptError == (pc = "stop" /\ owned) => failed
NoForeignError == ~owned => ~failed
RetainUntilAck == (pc = "done" /\ ~(owned /\ ack)) => (pending /\ request /\ ~restored)
FailureSurvivesCleanup == (pc = "done" /\ owned /\ ack) => failed
====
