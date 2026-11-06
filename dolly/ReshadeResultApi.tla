---- MODULE ReshadeResultApi ----
\* Minimal completion shape, not a whole-caller or program contract.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
