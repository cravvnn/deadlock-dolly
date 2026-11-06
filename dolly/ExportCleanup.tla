---- MODULE ExportCleanup ----
EXTENDS Naturals
\* Production-only design. Private baseline comparisons are not shipped.
\* Weak fairness assumes returning calls; terminal Done deliberately stutters.
\* API refinement checks completion shape only; safety obligations stay here.
\* export_jsx/export_usda call camera_export.write_script; builders and external
\* applications are not modeled. camera_export.write_script after temporary creation; file is private and closed
\* before no-overwrite publication. Body abstracts write/ASCII/flush/fsync errors.
\* Windows rename consumes the temporary; link keeps a private alias until cleanup.
\* No concurrent namespace substitution/crash/power loss/close hangs modeled.
CONSTANT Link
VARIABLES pc,temp,final,body,result
vars == <<pc,temp,final,body,result>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ExportCleanupApi WITH phase <- phase
Init == /\ pc = "publish" /\ temp = TRUE /\ final \in {"absent","user"}
        /\ body \in {"ok","body_error"} /\ result = body
Publish == /\ pc = "publish" /\ final = "absent" /\ body = "ok"
           /\ final' = "complete" /\ temp' = Link /\ pc' = "cleanup"
           /\ UNCHANGED <<body,result>>
Collision == /\ pc = "publish" /\ final = "user" /\ body = "ok"
             /\ result' = "collision" /\ body' = "collision" /\ pc' = "cleanup"
             /\ UNCHANGED <<temp,final>>
BodyError == /\ pc = "publish" /\ body = "body_error" /\ pc' = "cleanup"
             /\ UNCHANGED <<temp,final,body,result>>
Cleanup == /\ pc = "cleanup" /\ temp' = FALSE /\ pc' = "done"
           /\ UNCHANGED <<final,body,result>>
Deny == /\ pc = "cleanup" /\ temp /\ pc' = "done"
        /\ result' = result
        /\ UNCHANGED <<temp,final,body>>
Run == Publish \/ Collision \/ BodyError \/ Cleanup \/ Deny
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"publish","cleanup","done"} /\ temp \in BOOLEAN
          /\ final \in {"absent","user","complete"}
          /\ body \in {"ok","body_error","collision"}
          /\ result \in {"ok","body_error","collision","cleanup_error"}
PublishedComplete == (final = "complete") => body = "ok"
PreserveResult == result = body
Returns == <>(pc = "done")
Refines == API!Spec
====
