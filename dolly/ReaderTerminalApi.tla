---- MODULE ReaderTerminalApi ----
\* Minimal returning-operation completion interface, not whole-caller behavior.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
