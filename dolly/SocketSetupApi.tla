---- MODULE SocketSetupApi ----
\* Completion shape only; see implementation for safety rules and exclusions.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
