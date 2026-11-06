---- MODULE PublishReplayApi ----
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
