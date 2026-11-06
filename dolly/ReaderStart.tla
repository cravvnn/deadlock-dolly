---- MODULE ReaderStart ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* ReplayEventReader.snapshot, consumed by editor_publication.configure:
\* notification state set before Thread.start. Successful start owns work;
\* RuntimeError before start completion creates no worker/completion event.
\* Returning start/final event delivery only. Reader identity checks, sticky
\* failure and replacement-file restart stay actual-method probes, not refinement.
\* No concurrent consumers, post-start exceptions or OS thread-frequency claim.
VARIABLES pc,status,started
vars == <<pc,status,started>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ReaderStartApi WITH phase <- phase
Init == /\ pc = "start" /\ status = "loading" /\ started = FALSE
Start == /\ pc = "start" /\ pc' = "work" /\ started' = TRUE /\ UNCHANGED status
StartError == /\ pc = "start" /\ pc' = "done"
              /\ status' = "failed"
              /\ UNCHANGED started
Complete == /\ pc = "work" /\ pc' = "done" /\ status' = "complete" /\ UNCHANGED started
Run == Start \/ StartError \/ Complete
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"start","work","done"} /\ status \in {"loading","failed","complete"} /\ started \in BOOLEAN
NoFalseCompletion == (status = "complete") => started
NoPhantomLoading == (pc = "done") => status # "loading"
Returns == <>(pc = "done")
Refines == API!Spec

====
