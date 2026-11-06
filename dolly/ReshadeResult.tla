---- MODULE ReshadeResult ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* reshade_setup._write_config: owned mkstemp, write/close, atomic replace,
\* called by prepare_config. Outer config/preset selection is not modeled here.
\* then finally unlink. Body failure includes returning write/replace OSError.
\* No new fsync/mode/no-overwrite policy; successful replace consumes the stage.
\* Cleanup OSError may leave a private temp. Real locks and programmer errors
\* are implementation probes; no power loss, namespace substitution or game APIs.
VARIABLES pc,temp,target,body,result
vars == <<pc,temp,target,body,result>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ReshadeResultApi WITH phase <- phase
Init == /\ pc = "body" /\ temp = TRUE /\ target = "user"
        /\ body \in {"ok","body_error"} /\ result = body
Write == /\ pc = "body" /\ body = "ok" /\ target' = "new" /\ temp' = FALSE
         /\ pc' = "cleanup" /\ UNCHANGED <<body,result>>
Error == /\ pc = "body" /\ body = "body_error" /\ pc' = "cleanup"
         /\ UNCHANGED <<temp,target,body,result>>
Cleanup == /\ pc = "cleanup" /\ temp' = FALSE /\ pc' = "done" /\ UNCHANGED <<target,body,result>>
Deny == /\ pc = "cleanup" /\ temp /\ pc' = "done"
        /\ result' = result
        /\ UNCHANGED <<temp,target,body>>
Run == Write \/ Error \/ Cleanup \/ Deny
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"body","cleanup","done"} /\ temp \in BOOLEAN
          /\ target \in {"user","new"} /\ body \in {"ok","body_error"}
          /\ result \in {"ok","body_error","cleanup_error"}
KeepUserOnFailure == (body = "body_error") => target = "user"
PreserveResult == result = body
Returns == <>(pc = "done")
Refines == API!Spec

====
