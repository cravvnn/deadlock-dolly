---- MODULE AtomicModeApi ----
EXTENDS Naturals
\* Observable per-file contract, shared by both existing and staged-mode designs.
\* One owned path, ordinary process interruption, returning OS calls, no external
\* namespace replacement. Old bytes can be absent; no machine-power-loss claim.
VARIABLES content, permissions, requested, phase
vars == <<content,permissions,requested,phase>>
Init == /\ content \in {"old","absent"}
        /\ permissions \in (IF content = "absent" THEN {"none"} ELSE {"ro","rw"})
        /\ requested \in {"none","ro","rw"} /\ phase = "active"
Work == /\ phase = "active" /\ phase' = "active"
        /\ content' \in {content,"new"}
        /\ permissions' \in {permissions,"ro","rw"}
        /\ UNCHANGED requested
Finish == /\ phase = "active" /\ phase' \in {"ok","error","crashed"}
          /\ UNCHANGED <<content,permissions,requested>>
Done == /\ phase \in {"ok","error","crashed"} /\ UNCHANGED vars
Next == Work \/ Finish \/ Done
Spec == Init /\ [][Next]_vars
====
