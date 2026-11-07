---- MODULE AssetEnvironmentApi ----
\* Minimal completion shape, not caller/program behavior or liveness.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
