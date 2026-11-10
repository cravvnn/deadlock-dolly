---- MODULE VoiceWindowReuseApi ----
\* Minimal active/done completion shape; no whole-program or liveness claim.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
