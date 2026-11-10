---- MODULE RecorderReadUncertainty ----
EXTENDS Naturals
\* VideoExport.status -> mark_recording_pending -> Stop finally (carried patch).
\* One recording, native packet read error, process live/unknown/exited, then
\* terminal telemetry and retry. Valid native-failed/command-error paths omitted.
\* NativeActive is abstract actual lifetime; knowledge is the process observation.
\* Read failure is NOT a terminal native observation. Exited is independently
\* confirmed; unknown process status retains ownership rather than inventing exit.
\* Actual MediaTransport/wire/VideoExport probes bind invalid stable packet and
\* recovery; no actual process/GPU/encoder/weak-memory/timer or liveness proof.
\* GUI recovery/audio/multiple takes and pre-admission uncertainty excluded.
\* Terminal stuttering intentional, deadlock checking enabled.
KeepLive == TRUE
VARIABLES pc,knowledge,nativeActive,local,reserved,settings,unavailable
vars == <<pc,knowledge,nativeActive,local,reserved,settings,unavailable>>
Init == /\ pc = "read" /\ knowledge \in {"alive","unknown","exit"}
        /\ nativeActive = (knowledge # "exit") /\ local = "recording"
        /\ reserved = TRUE /\ settings = TRUE /\ unavailable = FALSE
ReadError == /\ pc = "read" /\ pc' = "stop"
             /\ local' = (IF KeepLive /\ knowledge # "exit" THEN "recording" ELSE "failed")
             /\ reserved' = (local' = "recording") /\ unavailable' = (local' = "recording")
             /\ UNCHANGED <<knowledge,nativeActive,settings>>
Stop == /\ pc = "stop" /\ pc' = "waiting"
        /\ settings' = (IF local = "recording" THEN settings ELSE FALSE)
        /\ UNCHANGED <<knowledge,nativeActive,local,reserved,unavailable>>
TerminalRead == /\ pc = "waiting" /\ pc' = "restore" /\ nativeActive' = FALSE
                /\ local' = "cancelled" /\ reserved' = FALSE /\ unavailable' = FALSE
                /\ UNCHANGED <<knowledge,settings>>
Restore == /\ pc = "restore" /\ pc' = "done" /\ settings' = FALSE
           /\ UNCHANGED <<knowledge,nativeActive,local,reserved,unavailable>>
Done == /\ pc = "done" /\ UNCHANGED vars
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE RecorderReadUncertaintyApi WITH phase <- phase
Next == ReadError \/ Stop \/ TerminalRead \/ Restore \/ Done
TypeOK == /\ pc \in {"read","stop","waiting","restore","done"}
          /\ knowledge \in {"alive","unknown","exit"}
          /\ local \in {"recording","failed","cancelled"}
          /\ nativeActive \in BOOLEAN /\ reserved \in BOOLEAN
          /\ settings \in BOOLEAN /\ unavailable \in BOOLEAN
NoFalseTerminal == nativeActive => (local = "recording" /\ reserved)
ActiveSettingsHeld == nativeActive => settings
ExitReported == (pc \in {"stop","waiting"} /\ knowledge = "exit") => (local = "failed" /\ ~reserved)
TerminalRestored == pc = "done" => (~nativeActive /\ ~reserved /\ ~settings /\ ~unavailable)
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
