---- MODULE BackupPublishApi ----
VARIABLES target, phase
vars == <<target,phase>>
Init == /\ target = "old" /\ phase = "active"
Work == /\ phase = "active" /\ phase' = "active" /\ target' \in {target,"new"}
Finish == /\ phase = "active" /\ phase' = "done" /\ UNCHANGED target
Done == /\ phase = "done" /\ UNCHANGED vars
Spec == Init /\ [][Work \/ Finish \/ Done]_vars
====
