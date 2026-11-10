---- MODULE VideoStopOwnership ----
EXTENDS Naturals
\* VideoExport.stop finally after ordinary recorder stop error/deadline, followed
\* by later terminal telemetry and Stop retry. Existing Players handling omitted.
\* One admitted take with saved timing/scale, optional POV; two active states,
\* two stop failure modes, one terminal retry. Same pinned session/replay.
\* Native telemetry stays coherent and active during the failed Stop. Cached
\* status transport errors rewritten as failed, uncertain start, actual engine,
\* concurrent owner replacement, GUI recovery/controller.stop and I/O liveness
\* are NOT covered. No general acknowledgement/correct-frame guarantee.
\* Actual VideoExport start/status/stop probes bind controlled readback/command
\* boundaries; no game/native/encoder is run. Terminal stuttering intentional;
\* deadlock checks enabled. Safety projection, no new persistent code flag.
CanRestore(s) == s = "cancelled"
VARIABLES pc,state,mode,wantPov,timing,scale,pov
vars == <<pc,state,mode,wantPov,timing,scale,pov>>
Init == /\ pc = "request" /\ state \in {"recording","finalizing"}
        /\ mode \in {"command_error","deadline"} /\ wantPov \in BOOLEAN
        /\ timing = TRUE /\ scale = TRUE /\ pov = wantPov
StopFailed == /\ pc = "request" /\ pc' = "finally"
              /\ UNCHANGED <<state,mode,wantPov,timing,scale,pov>>
Cleanup == /\ pc = "finally" /\ pc' = (IF state = "cancelled" THEN "done" ELSE "waiting")
           /\ timing' = (IF CanRestore(state) THEN FALSE ELSE timing)
           /\ scale' = (IF CanRestore(state) THEN FALSE ELSE scale)
           /\ pov' = (IF CanRestore(state) THEN FALSE ELSE pov)
           /\ UNCHANGED <<state,mode,wantPov>>
NativeDone == /\ pc = "waiting" /\ state' = "cancelled" /\ pc' = "finally"
              /\ UNCHANGED <<mode,wantPov,timing,scale,pov>>
Done == /\ pc = "done" /\ UNCHANGED vars
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE VideoStopOwnershipApi WITH phase <- phase
Next == StopFailed \/ Cleanup \/ NativeDone \/ Done
TypeOK == /\ pc \in {"request","finally","waiting","done"}
          /\ state \in {"recording","finalizing","cancelled"}
          /\ mode \in {"command_error","deadline"}
          /\ wantPov \in BOOLEAN /\ timing \in BOOLEAN /\ scale \in BOOLEAN /\ pov \in BOOLEAN
RetainActive == (pc = "waiting" /\ state # "cancelled") => (timing /\ scale /\ (wantPov => pov))
TerminalRestored == pc = "done" => (~timing /\ ~scale /\ ~pov)
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
