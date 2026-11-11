---- MODULE PopupSelectionApi ----
\* Minimal call-completion shape, not whole-UI or real-I/O liveness.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
