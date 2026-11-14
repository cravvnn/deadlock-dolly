---- MODULE ReleaseJoin ----
EXTENDS Naturals
\* Bounded join, not an INSTANCE composition of all maintained models.
\* Controller._release_native_camera retains lease until NativeBridge.release's
\* _wait observes this command. bridge_win.cpp on_view Release -> finish restores
\* gEffects before Stopped; failed restore publishes current Fault, not Stopped.
\* Initial cached Fault is from the previous command. Two explicit Release
\* requests, one callback each, restoration success/failure, one retry. No other
\* writers, media/GPU/recording, other timeouts, process exit, mapping races or faults
\* during publication. Callback and polling calls return; no real I/O liveness.
\* Environment Probe: bridge_win.cpp publishes Probe (no gEffects.restore) only
\* while gCommand is null, and never resets gCommand, so Probe carrying this
\* release's ack is unreachable today. It is retained as a hostile callback
\* outcome so ReleaseAccepts is load bearing: _wait keeps polling and its
\* deadline (ObserveTimeout) refuses the release with the lease retained.
RequireCurrentFault == TRUE
RetainFailedLease == TRUE
ReleaseAccepts == {"stopped"}
VARIABLES pc, command, ack, pending, state, nativeOwned, restored, lease
vars == <<pc,command,ack,pending,state,nativeOwned,restored,lease>>
Init == /\ pc = "publish" /\ command = 0 /\ ack = 0 /\ pending = FALSE
        /\ state = "fault" /\ nativeOwned = FALSE /\ restored = FALSE /\ lease = TRUE
Publish == /\ pc = "publish" /\ command < 2 /\ command' = command+1
           /\ pc' = "wait" /\ pending' = TRUE
           /\ UNCHANGED <<ack,state,nativeOwned,restored,lease>>
Callback == /\ pending /\ pc \in {"wait","blocked"}
            /\ ack' = command /\ pending' = FALSE
            /\ state' \in {"stopped","fault","probe"} /\ restored' = (state' = "stopped")
            /\ nativeOwned' = FALSE /\ UNCHANGED <<pc,command,lease>>
ObserveStop == /\ pc = "wait" /\ ack = command /\ state \in ReleaseAccepts
               /\ pc' = "idle" /\ lease' = FALSE
               /\ UNCHANGED <<command,ack,pending,state,nativeOwned,restored>>
ObserveFault == /\ pc = "wait" /\ state = "fault"
                /\ (RequireCurrentFault => ack = command)
                /\ pc' = "blocked" /\ lease' = IF RetainFailedLease THEN lease ELSE FALSE
                /\ UNCHANGED <<command,ack,pending,state,nativeOwned,restored>>
ObserveTimeout == /\ pc = "wait" /\ ~pending /\ ack = command
                  /\ state \notin ReleaseAccepts \cup {"fault"} /\ pc' = "blocked"
                  /\ UNCHANGED <<command,ack,pending,state,nativeOwned,restored,lease>>
Retry == /\ pc = "blocked" /\ command < 2 /\ pc' = "publish"
         /\ UNCHANGED <<command,ack,pending,state,nativeOwned,restored,lease>>
Done == /\ (pc = "idle" \/ (pc = "blocked" /\ command = 2 /\ ~pending)) /\ UNCHANGED vars
Observe == ObserveStop \/ ObserveFault \/ ObserveTimeout
Next == Publish \/ Callback \/ Observe \/ Retry \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Publish) /\ WF_vars(Callback)
        /\ WF_vars(Observe) /\ WF_vars(Retry)
TypeOK == /\ pc \in {"publish","wait","blocked","idle"} /\ command \in 0..2 /\ ack \in 0..2
          /\ pending \in BOOLEAN /\ state \in {"fault","stopped","probe"}
          /\ nativeOwned \in BOOLEAN /\ restored \in BOOLEAN /\ lease \in BOOLEAN
MatchingFailure == pc = "blocked" => ack = command
NoUnrestoredWriter == ~lease => (~nativeOwned /\ restored)
Settles == <>(pc = "idle" \/ (pc = "blocked" /\ command = 2 /\ ~pending))
====
