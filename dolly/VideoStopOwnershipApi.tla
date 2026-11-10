---- MODULE VideoStopOwnershipApi ----
\* Minimal call-completion projection; no whole-program/liveness claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
