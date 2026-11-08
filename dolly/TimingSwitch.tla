---- MODULE TimingSwitch ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* ExportTimingMixin.suspend_export_timing/resume_export_timing; Controller.play
\* invokes these around paused camera calibration before path/native dispatch.
\* A Boolean actual value means both host_framerate/r_wait_on_present match
\* their requested pair; a mismatching/partially accepted pair is not verified.
\* Two transitions with ignored/applied commands and returning coherent reads.
\* Refusal leaves the local flag intact and raises; actual retry/clear restoration
\* is checked in code. No concurrent/external cvar writer or real game timing.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
\* ExportTimingMixin suspend/resume, Controller._position_start: two switches.
\* Console commands may return without applying, verified reads return current values.
\* Abstract one host/wait pair as target bit; transport errors leave flag unchanged.
\* No replay-clock, concurrent command, external change after verification, or game proof.
VARIABLES pc,actual,claimed,target,calls,refused
vars == <<pc,actual,claimed,target,calls,refused>>
Verify == TRUE
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE TimingSwitchApi WITH phase <- phase

Init == /\ pc = "send" /\ actual = FALSE /\ claimed = FALSE /\ target = TRUE /\ calls = 0 /\ refused = FALSE
Send(applied) == /\ pc = "send" /\ pc' = "read"
                 /\ actual' = IF applied THEN target ELSE actual
                 /\ UNCHANGED <<claimed,target,calls,refused>>
Read == /\ pc = "read" /\ pc' = "next" /\ refused' = (Verify /\ actual # target)
        /\ claimed' = IF Verify /\ actual # target THEN claimed ELSE target
        /\ UNCHANGED <<actual,target,calls>>
Again == /\ pc = "next" /\ calls = 0 /\ calls' = 1 /\ target' = ~target /\ pc' = "send" /\ UNCHANGED <<actual,claimed,refused>>
Finish == /\ pc = "next" /\ calls = 1 /\ pc' = "done" /\ UNCHANGED <<actual,claimed,target,calls,refused>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Send(TRUE) \/ Send(FALSE) \/ Read \/ Again \/ Finish \/ Done
TypeOK == /\ pc \in {"send","read","next","done"} /\ actual \in BOOLEAN /\ claimed \in BOOLEAN
          /\ target \in BOOLEAN /\ refused \in BOOLEAN /\ calls \in 0..1
VerifiedClaim == pc \in {"next","done"} /\ ~refused => claimed = actual
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
