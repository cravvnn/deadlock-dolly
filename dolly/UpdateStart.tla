---- MODULE UpdateStart ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* UpdateUI.check/update_now from startup/button callbacks; poll consumes
\* available/error/done. Notification state is set before Thread.start.
\* Complete denotes settled accepted work, not verified download/installation.
\* RuntimeError before start completion creates no worker/completion event.
\* Returning start/final event delivery only. Request IDs, consent/safe-restart
\* and retry event handling stay actual-method probes, not full async refinement.
\* No concurrent consumers, post-start exceptions or OS thread-frequency claim.
VARIABLES pc,status,started
vars == <<pc,status,started>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE UpdateStartApi WITH phase <- phase
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
