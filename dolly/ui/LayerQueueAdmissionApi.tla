---- MODULE LayerQueueAdmissionApi ----
\* Minimal returning completion shape; allows internal stuttering.
\* No I/O liveness, whole-caller or whole-program refinement claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
