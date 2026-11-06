---- MODULE RestoreHandlesApi ----
\* Returning cleanup has one completion boundary. Flag/error obligations live
\* in RestoreHandles; this interface does not prove child inheritance or DLSS.
VARIABLE phase
Spec == /\ phase = "active" /\ [][phase = "active" /\ phase' = "done"]_phase
====
