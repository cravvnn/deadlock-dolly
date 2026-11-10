---- MODULE LayerQueueAdmission ----
EXTENDS Naturals
\* Production-only composition witness: DollyApp._advance_layer_pipeline -> LayerPipeline.advance
\* -> DollyApp._submit -> OperationQueue.submit -> started(label) before jobs.put.
\* Minimal active/done API, not whole-caller or whole-program refinement.
\* LayerAdmission covers explicit False. Admission covers the queue's raised
\* pre-enqueue callback but excludes the caller's retained layer intent and
\* partial queue publication. This contract joins those bounded observations.
\* Two request identities, four branch kinds, explicit rejection, raised notice,
\* accepted queue publication or ambiguous publish failure; one retry then
\* acceptance. UI caller serialized. Queue.busy=false confirms pre-admission
\* refusal; after a thrown put the queue may own a job, so do not rearm blindly.
\* pending abstracts the branch's capture/combine/base value, or take intent.
\* Real owner probes check all branches and original exception identity, actual
\* worker delivery after retry, accepted pre-clear and ambiguous post-put failure.
\* direct callback mutation of pipeline, close races, audit effects, full GUI,
\* capture output/restoration and I/O liveness excluded. No whole-program proof.
\* Intentional terminal stutter; deadlock checking stays enabled. No state-count
\* reduction is promised by replacing invalid lost-work states with retry states.
RestoreRaised == TRUE
RestoreRejected == TRUE
KeepAccepted == TRUE
HoldUncertain == TRUE
VARIABLES pc,kind,outcome,pending,original,advance,busy,queued,delivered,attempt
vars == <<pc,kind,outcome,pending,original,advance,busy,queued,delivered,attempt>>
phase == IF pc = "terminal" /\ ~advance THEN "done" ELSE "active"
API == INSTANCE LayerQueueAdmissionApi WITH phase <- phase
Init == /\ pc = "ready" /\ kind \in {"capture","combine","take","restore"}
        /\ outcome \in {"raise","reject","accept","ambiguous"} /\ pending \in {1,2}
        /\ original = pending /\ advance = TRUE /\ busy = FALSE
        /\ queued = 0 /\ delivered = 0 /\ attempt = 0
ClearIntent == /\ pc = "ready" /\ advance /\ pc' = "call"
               /\ pending' = (IF kind = "take" THEN pending ELSE 0)
               /\ advance' = FALSE
               /\ UNCHANGED <<kind,outcome,original,busy,queued,delivered,attempt>>
Reject == /\ pc = "call" /\ outcome = "reject" /\ pc' = "terminal"
          /\ pending' = (IF RestoreRejected THEN original ELSE pending)
          /\ advance' = RestoreRejected
          /\ UNCHANGED <<kind,outcome,original,busy,queued,delivered,attempt>>
Reserve == /\ pc = "call" /\ outcome \in {"raise","accept","ambiguous"}
           /\ pc' = "notify" /\ busy' = TRUE
           /\ UNCHANGED <<kind,outcome,pending,original,advance,queued,delivered,attempt>>
NoticeError == /\ pc = "notify" /\ outcome = "raise" /\ pc' = "terminal"
               /\ busy' = FALSE /\ advance' = RestoreRaised
               /\ pending' = (IF RestoreRaised THEN original ELSE pending)
               /\ UNCHANGED <<kind,outcome,original,queued,delivered,attempt>>
Enqueue == /\ pc = "notify" /\ outcome = "accept" /\ pc' = "terminal"
           /\ queued' = 1 /\ delivered' = original /\ advance' = ~KeepAccepted
           /\ UNCHANGED <<kind,outcome,pending,original,busy,attempt>>
Uncertain == /\ pc = "notify" /\ outcome = "ambiguous" /\ pc' = "terminal"
             /\ queued' \in {0,1} /\ delivered' = (IF queued' = 1 THEN original ELSE 0)
             /\ advance' = ~HoldUncertain
             /\ pending' = (IF HoldUncertain THEN pending ELSE original)
             /\ UNCHANGED <<kind,outcome,original,busy,attempt>>
Retry == /\ pc = "terminal" /\ queued = 0 /\ attempt = 0 /\ advance
         /\ pc' = "ready" /\ outcome' = "accept" /\ attempt' = 1
         /\ UNCHANGED <<kind,pending,original,advance,busy,queued,delivered>>
Done == /\ pc = "terminal" /\ UNCHANGED vars
Next == ClearIntent \/ Reject \/ Reserve \/ NoticeError \/ Enqueue \/ Uncertain \/ Retry \/ Done
TypeOK == /\ pc \in {"ready","call","notify","terminal"}
          /\ kind \in {"capture","combine","take","restore"}
          /\ outcome \in {"raise","reject","accept","ambiguous"}
          /\ pending \in {0,1,2} /\ original \in {1,2}
          /\ advance \in BOOLEAN /\ busy \in BOOLEAN /\ queued \in 0..1
          /\ delivered \in {0,1,2} /\ attempt \in 0..1
FailureRetained == (pc = "terminal" /\ outcome \in {"raise","reject"}) => (pending = original /\ advance /\ ~busy)
AcceptedOwned == (pc = "terminal" /\ queued = 1) => (delivered = original /\ ~advance /\ busy)
UnknownHeld == (pc = "terminal" /\ outcome = "ambiguous") => ~advance
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
