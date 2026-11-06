---- MODULE Admission ----
EXTENDS Naturals
\* Production-only design. Private baseline comparisons are not shipped.
\* Weak fairness assumes returning calls; terminal Done deliberately stutters.
\* API refinement checks completion shape only; safety obligations stay here.
\* DollyApp._submit calls OperationQueue.submit; GUI error/done polling is
\* outside this model. OperationQueue.submit after closed/blocked/busy guards passed on UI thread.
\* started(label) may return, raise, or reenter submit once synchronously.
\* Main job is not enqueued until started returns. Worker/event drain is outside
\* this admission boundary. Queue.put failure/partial publication is NOT covered.
\* Callback does not directly mutate busy/closed; no concurrent submit threads.
VARIABLES pc,busy,queued,failed,nested,nestedAccepted
vars == <<pc,busy,queued,failed,nested,nestedAccepted>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE AdmissionApi WITH phase <- phase
Init == /\ pc = "reserve" /\ busy = FALSE /\ queued = 0
        /\ failed = FALSE /\ nested = FALSE /\ nestedAccepted = FALSE
Reserve == /\ pc = "reserve" /\ busy' = TRUE /\ pc' = "callback"
           /\ UNCHANGED <<queued,failed,nested,nestedAccepted>>
Reenter == /\ pc = "callback" /\ ~nested /\ nested' = TRUE
           /\ nestedAccepted' = ~busy
           /\ UNCHANGED <<pc,busy,queued,failed>>
Started == /\ pc = "callback" /\ pc' = "enqueue"
           /\ UNCHANGED <<busy,queued,failed,nested,nestedAccepted>>
StartError == /\ pc = "callback" /\ pc' = "done" /\ failed' = TRUE
              /\ busy' = FALSE
              /\ UNCHANGED <<queued,nested,nestedAccepted>>
Enqueue == /\ pc = "enqueue" /\ queued' = 1 /\ pc' = "done"
           /\ UNCHANGED <<busy,failed,nested,nestedAccepted>>
Run == Reserve \/ Reenter \/ Started \/ StartError \/ Enqueue
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"reserve","callback","enqueue","done"} /\ queued \in {0,1}
          /\ busy \in BOOLEAN /\ failed \in BOOLEAN /\ nested \in BOOLEAN /\ nestedAccepted \in BOOLEAN
NoNestedAdmission == ~nestedAccepted
SuccessReserved == (pc = "done" /\ ~failed) => busy /\ queued = 1
FailureReleased == (pc = "done" /\ failed) => ~busy /\ queued = 0
Returns == <>(pc = "done")
Refines == API!Spec
====
