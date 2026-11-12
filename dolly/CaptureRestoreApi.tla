---- MODULE CaptureRestoreApi ----
VARIABLES pending,ack,context
vars == <<pending,ack,context>>
Init == pending = TRUE
Observe == /\ pending /\ pending' /\ ack' \in BOOLEAN /\ context' \in BOOLEAN
Restore == /\ pending /\ ~pending' /\ ack' /\ context'
Done == /\ ~pending /\ UNCHANGED vars
Spec == Init /\ [][Observe \/ Restore \/ Done]_vars
====
