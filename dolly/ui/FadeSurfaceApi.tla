---- MODULE FadeSurfaceApi ----
\* Completion shape only; no whole-caller/whole-program refinement claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
