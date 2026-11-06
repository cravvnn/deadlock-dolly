---- MODULE PlayersRestoreRetry ----
EXTENDS Naturals
\* VideoExport._wait_player_restore_context after _confirm_player_stop ack.
\* stop() is the only caller. Query is _require_export_context; it preserves
\* post-response session/request checks. NativeOwned includes native replay,
\* pinned session and selected replay. Wake models identity loss between tries.
\* ReadyAt bounds transient vs persistent ConsoleTimeout. Refuse models lost
\* ownership; other non-timeout errors terminate in real code and owner tests.
\* No game writes occur
\* in this helper; Restore denotes permission to return to the guarded caller.
\* Actual native drain, console framing, OS hangs and mutating restoration are
\* outside scope; PlayersRecovery and real owner tests cover their interfaces.
CONSTANT MaxAttempts
VARIABLES pc, nativeOwned, markerOwned, queries, readyAt, foreignRead, restored
vars == <<pc, nativeOwned, markerOwned, queries, readyAt, foreignRead, restored>>
Init == /\ pc = "verify" /\ nativeOwned \in BOOLEAN /\ markerOwned \in BOOLEAN
        /\ queries = 0 /\ readyAt \in 1..(MaxAttempts+1)
        /\ foreignRead = FALSE /\ restored = FALSE
ReadAllowed == nativeOwned /\ markerOwned
RetryAllowed == queries < MaxAttempts
Query == /\ pc = "verify" /\ ReadAllowed
         /\ queries' = queries+1
         /\ foreignRead' = (foreignRead \/ ~(nativeOwned /\ markerOwned))
         /\ pc' = IF queries+1 >= readyAt THEN "restore" ELSE "timeout"
         /\ UNCHANGED <<nativeOwned, markerOwned, readyAt, restored>>
Refuse == /\ pc = "verify" /\ ~ReadAllowed /\ pc' = "done"
          /\ UNCHANGED <<nativeOwned, markerOwned, queries, readyAt, foreignRead, restored>>
Timeout == /\ pc = "timeout"
           /\ pc' = IF RetryAllowed THEN "wait" ELSE "done"
           /\ UNCHANGED <<nativeOwned, markerOwned, queries, readyAt, foreignRead, restored>>
Wake == /\ pc = "wait" /\ pc' = "verify"
        /\ nativeOwned' \in {nativeOwned, FALSE}
        /\ markerOwned' \in {markerOwned, FALSE}
        /\ UNCHANGED <<queries, readyAt, foreignRead, restored>>
Restore == /\ pc = "restore" /\ pc' = "done" /\ restored' = TRUE
           /\ UNCHANGED <<nativeOwned, markerOwned, queries, readyAt, foreignRead>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Query \/ Refuse \/ Timeout \/ Wake \/ Restore \/ Done
Spec == Init /\ [][Next]_vars
NoForeignRead == ~foreignRead
AttemptsBound == queries <= MaxAttempts
KeepPendingUntilVerified == restored => (nativeOwned /\ markerOwned /\ queries >= readyAt)
TransientConsoleRecovered == (pc = "done" /\ nativeOwned /\ markerOwned /\ readyAt <= MaxAttempts) => restored
====
