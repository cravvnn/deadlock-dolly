---- MODULE PreparedShotApi ----
\* Semantic admission interface: a start requires a valid prepared identity
\* AND observed recorder readiness. This is stronger than call completion only.
VARIABLES phase, started, permitted
vars == <<phase,started,permitted>>
Init == /\ phase = "pending" /\ started = FALSE /\ permitted \in BOOLEAN
Observe == /\ phase = "pending" /\ phase' = phase /\ started' = started /\ permitted' \in BOOLEAN
Start == /\ phase = "pending" /\ permitted /\ phase' = "started" /\ started' = TRUE /\ UNCHANGED permitted
Refuse == /\ phase = "pending" /\ phase' = "refused" /\ started' = FALSE /\ UNCHANGED permitted
Done == /\ phase \in {"started","refused"} /\ UNCHANGED vars
Spec == Init /\ [][Observe \/ Start \/ Refuse \/ Done]_vars /\ [](started => permitted)
====
