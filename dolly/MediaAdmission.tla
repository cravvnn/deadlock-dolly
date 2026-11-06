---- MODULE MediaAdmission ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* MediaTransport._ensure_media called by _media_command, under transport lock.
\* An allocated mapping remains provisional until size and zero-initialization
\* succeed. Only returning close is modeled; no command seqlock/native worker,
\* post-publication failure, hostile factory, close failure or GC guarantee.
VARIABLES pc,fault,closed,published
vars == <<pc,fault,closed,published>>
phase == IF pc = 2 THEN "done" ELSE "active"
API == INSTANCE MediaAdmissionApi WITH phase <- phase

Init == /\ pc = 1 /\ fault \in 0..2 /\ closed = FALSE /\ published = FALSE
Initialize == /\ pc = 1 /\ pc' = 2
              /\ published' = (fault = 0)
              /\ closed' = (fault # 0)
              /\ UNCHANGED fault
Done == /\ pc = 2 /\ UNCHANGED vars
Next == Initialize \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Initialize)
TypeOK == /\ pc \in 1..2 /\ fault \in 0..2 /\ closed \in BOOLEAN /\ published \in BOOLEAN
NoInvalidPublication == published => fault = 0 /\ ~closed
ProvisionalClosed == (pc = 2 /\ fault # 0) => closed
Returns == <>(pc = 2)

Refines == API!Spec
====
