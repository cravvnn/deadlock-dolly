---- MODULE ProjectOpenSnapshotApi ----
\* Minimal call-completion shape, not whole-program or real-I/O liveness.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
