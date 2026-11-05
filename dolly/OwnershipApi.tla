---- MODULE OwnershipApi ----
VARIABLES phase, evidence
vars == <<phase,evidence>>
Init == /\ phase = "active" /\ evidence = TRUE
Internal == /\ phase = "active" /\ phase' = "active" /\ evidence' \in BOOLEAN
Retry == /\ phase = "active" /\ phase' = "retry" /\ evidence' = TRUE
Finish == /\ phase = "active" /\ phase' \in {"done","aborted"} /\ UNCHANGED evidence
Done == /\ phase \in {"retry","done","aborted"} /\ UNCHANGED vars
Next == Internal \/ Retry \/ Finish \/ Done
Spec == Init /\ [][Next]_vars
====
