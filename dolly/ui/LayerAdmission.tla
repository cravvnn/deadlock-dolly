---- MODULE LayerAdmission ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* LayerPipeline.advance: preserve old pre-submit clearing for accepted jobs;
\* rollback pending intent only when serialized submit explicitly returns False.
\* DollyApp._advance_layer_pipeline calls this with DollyApp._submit, which
\* delegates to OperationQueue.submit. Four branch types/two request identities.
\* At most one refused submission followed by acceptance. Worker calls/callbacks,
\* side effects of audit and close excluded; LayerQueueAdmission separately
\* covers raised submission and ambiguous publication across the queue boundary.
\* The real queue starts functions asynchronously; UI completion cannot reenter
\* advance while this Tk-thread call is still returning. No lock atomicity claim.
VARIABLES pc,pending,original,advance,accepted,rejected,attempt,kind,delivered
vars == <<pc,pending,original,advance,accepted,rejected,attempt,kind,delivered>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE LayerAdmissionApi WITH phase <- phase
Init == /\ pc = "ready" /\ pending \in {1,2} /\ original = pending
        /\ advance = TRUE /\ accepted = FALSE /\ rejected = FALSE /\ attempt = 0
        /\ kind \in {"capture","combine","take","restore"} /\ delivered = 0
Reject == /\ pc = "ready" /\ attempt = 0 /\ rejected' = TRUE /\ attempt' = 1
          /\ pending' = pending
          /\ advance' = TRUE /\ pc' = "ready"
          /\ UNCHANGED <<original,accepted,kind,delivered>>
Accept == /\ pc = "ready" /\ advance /\ accepted' = TRUE /\ delivered' = pending
          /\ pending' = IF kind = "take" THEN pending ELSE 0
          /\ advance' = FALSE /\ attempt' = attempt + 1 /\ pc' = "done"
          /\ UNCHANGED <<original,rejected,kind>>
Run == Reject \/ Accept
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"ready","done"} /\ pending \in {0,1,2} /\ attempt \in 0..2
          /\ accepted \in BOOLEAN /\ advance \in BOOLEAN /\ rejected \in BOOLEAN
RightRequest == accepted => delivered = original
RefusalPreserved == (rejected /\ ~accepted) => advance /\ pending = original
Returns == <>(pc = "done")
Refines == API!Spec

====
