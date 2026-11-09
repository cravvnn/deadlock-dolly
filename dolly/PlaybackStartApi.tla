---- MODULE PlaybackStartApi ----
\* Minimal completion shape; not whole-caller/game behavior or OS liveness.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
