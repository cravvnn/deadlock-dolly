---- MODULE ScratchCreate ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* player_scratch.create AFTER fresh directory allocation and marker/manifest
\* write failure, before raw capture admission. player_layer.begin_capture is
\* the caller; its complete capture/engine lifecycle is not modeled here.
\* Only these two known files may be removed.
\* Every subset of present owned files; a foreign file may appear and must stay.
\* Returning OSError unlink/rmdir only. Root has no admitted raw capture yet.
\* Namespace replacement, close/permission restoration/crash/disk durability excluded.
VARIABLES pc,files,foreign,dir,result,attempted
vars == <<pc,files,foreign,dir,result,attempted>>
phase == IF pc = 4 THEN "done" ELSE "active"
API == INSTANCE ScratchCreateApi WITH phase <- phase
Init == /\ pc = 1 /\ files \in SUBSET {1,2} /\ foreign \in BOOLEAN
        /\ dir = TRUE /\ result = "primary" /\ attempted = {}
Delete == /\ pc \in {1,2} /\ files' = files \ {pc} /\ pc' = pc + 1
          /\ attempted' = attempted \cup {pc} /\ UNCHANGED <<foreign,dir,result>>
Deny == /\ pc \in {1,2} /\ pc \in files
        /\ result' = result
        /\ pc' = pc + 1
        /\ attempted' = attempted \cup {pc} /\ UNCHANGED <<files,foreign,dir>>
Directory == /\ pc = 3 /\ pc' = 4 /\ attempted' = attempted \cup {3}
             /\ dir' = IF files = {} /\ ~foreign THEN FALSE ELSE dir
             /\ result' = result
             /\ UNCHANGED <<files,foreign>>
Run == Delete \/ Deny \/ Directory
Done == /\ pc = 4 /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in 1..4 /\ files \subseteq {1,2} /\ foreign \in BOOLEAN /\ dir \in BOOLEAN
          /\ result \in {"primary","cleanup"} /\ attempted \subseteq {1,2,3}
ForeignRetained == foreign => dir
PreservePrimary == result = "primary"
AttemptAll == (pc = 4) => attempted = {1,2,3}
Returns == <>(pc = 4)
Refines == API!Spec

====
