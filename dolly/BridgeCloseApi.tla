---- MODULE BridgeCloseApi ----
\* Returning-call completion only; no full caller refinement claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
