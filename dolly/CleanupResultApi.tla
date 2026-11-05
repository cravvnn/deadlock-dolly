---- MODULE CleanupResultApi ----
VARIABLES phase, result
vars == <<phase,result>>
Init == /\ phase = "active" /\ result = "pending"
Finish == /\ phase = "active" /\ phase' = "done"
          /\ result' \in {"ok","primary","cleanup","bug"}
Done == /\ phase = "done" /\ UNCHANGED vars
Spec == Init /\ [][Finish \/ Done]_vars
====
