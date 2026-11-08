---- MODULE SaveCleanup ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* GUI save/Save As calls Project.save. Body abstracts the validated payload,
\* private stage write/flush/fsync/close and os.replace; Clean/Locked are unlink.
\* Only ordinary cleanup OSError is suppressed. Real regression separately
\* keeps unexpected programmer errors visible. SaveResult owns settings saves.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
\* Project.save, GUI save operation: one private stage, returning body and cleanup.
\* Body includes write/fsync/replace. No crash/durability or hostile namespace proof.
VARIABLES pc,body,result,stage
vars == <<pc,body,result,stage>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE SaveCleanupApi WITH phase <- phase

Init == /\ pc = "body" /\ body = "none" /\ result = "none" /\ stage = TRUE
Body(outcome) == /\ pc = "body" /\ body' = outcome /\ result' = outcome
                 /\ stage' = (outcome # "success") /\ pc' = "cleanup"
Clean == /\ pc = "cleanup" /\ pc' = "done" /\ stage' = FALSE /\ UNCHANGED <<body,result>>
Locked == /\ pc = "cleanup" /\ pc' = "done"
          /\ result' = body /\ UNCHANGED <<body,stage>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == (\E outcome \in {"success","primaryError"} : Body(outcome)) \/ Clean \/ Locked \/ Done
TypeOK == /\ pc \in {"body","cleanup","done"} /\ stage \in BOOLEAN
          /\ body \in {"none","success","primaryError"}
          /\ result \in {"none","success","primaryError","cleanupError"}
ResultPreserved == pc = "done" => result = body
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
