---- MODULE VideoCompleteness ----
EXTENDS Naturals
\* encode/encode_ffmpeg via worker start; capture via Present; stop via media.
\* Bounded projection: one admitted GPU frame, returning drain, cancellation,
\* fixed-step queue timeout, late GPU loss and final publication. No game clock,
\* pixel correctness, encoder reliability or OS weak-memory proof. Existing
\* MediaFinalize/MediaAdmission cover transport ownership. Quiesce abstracts
\* ready=false, producer_active wait and gpu_pending exchange as one completed
\* barrier; no final completeness decision is permitted before it.
VARIABLES pc,fixed,ffmpeg,lost,error,cancelled,pending,overflow,published
vars == <<pc,fixed,ffmpeg,lost,error,cancelled,pending,overflow,published>>
RejectLoss == TRUE
StopOnOverflow == TRUE
Init == /\ pc = "capture" /\ fixed \in BOOLEAN /\ ffmpeg \in BOOLEAN
        /\ lost = FALSE /\ error = FALSE /\ cancelled = FALSE
        /\ pending = TRUE /\ overflow = FALSE /\ published = FALSE
Drain == /\ pc = "capture" /\ pending /\ pending' = FALSE
         /\ UNCHANGED <<pc,fixed,ffmpeg,lost,error,cancelled,overflow,published>>
Overflow == /\ pc = "capture" /\ fixed /\ ~overflow
            /\ overflow' = TRUE /\ error' = StopOnOverflow
            /\ lost' = ~StopOnOverflow
            /\ UNCHANGED <<pc,fixed,ffmpeg,cancelled,pending,published>>
Cancel == /\ pc # "done" /\ ~cancelled /\ cancelled' = TRUE
          /\ UNCHANGED <<pc,fixed,ffmpeg,lost,error,pending,overflow,published>>
Quiesce == /\ pc = "capture" /\ pc' = "finalize"
           /\ lost' = (lost \/ pending) /\ pending' = FALSE
           /\ UNCHANGED <<fixed,ffmpeg,error,cancelled,overflow,published>>
Finalize == /\ pc = "finalize" /\ pc' = "done"
            /\ published' = (~cancelled /\ ~error /\ ~(RejectLoss /\ (fixed \/ ffmpeg) /\ lost))
            /\ UNCHANGED <<fixed,ffmpeg,lost,error,cancelled,pending,overflow>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Drain \/ Overflow \/ Cancel \/ Quiesce \/ Finalize \/ Done
Spec == Init /\ [][Next]_vars
TypeOK == /\ pc \in {"capture","finalize","done"}
          /\ fixed \in BOOLEAN /\ ffmpeg \in BOOLEAN /\ lost \in BOOLEAN
          /\ error \in BOOLEAN /\ cancelled \in BOOLEAN /\ pending \in BOOLEAN
          /\ overflow \in BOOLEAN /\ published \in BOOLEAN
NoIncompleteSuccess == published /\ (fixed \/ ffmpeg) => ~lost
NoCancelledSuccess == published => ~cancelled
FixedTimeoutStops == overflow => error
====
