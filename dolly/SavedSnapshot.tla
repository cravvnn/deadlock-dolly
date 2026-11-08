---- MODULE SavedSnapshot ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* ShotHistory.mark_saved follows a successful GUI Project.save; history is
\* private and immutable except ShotState selection metadata. Record allocates
\* a new snapshot (old ones may be evicted); Edit changes caller slot0 only.
\* Four calls, contents0..2 and fresh snapshot slots1..5. Undo/redo/merge/session
\* revision and selection are real method regressions, not this refinement.
\* HistoryShare separately covers reset; no assumption of a saved list index.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
\* ShotHistory.mark_saved; GUI save/current snapshot. Four calls, three contents.
\* Immutable history snapshots; Edit touches only mutable caller slot0.
\* Fresh allocation identities stand for record/eviction; no arbitrary private-field edits.
VARIABLES objects,history,saved,wanted,calls
vars == <<objects,history,saved,wanted,calls>>
MatchHistory == objects[history] = objects[0]
phase == IF calls = 4 THEN "done" ELSE "active"
API == INSTANCE SavedSnapshotApi WITH phase <- phase

Init == /\ objects = [i \in 0..5 |-> 0] /\ history = 1 /\ saved = 1 /\ wanted = 0 /\ calls = 0
Edit(v) == /\ calls < 4 /\ objects' = [objects EXCEPT ![0] = v] /\ calls' = calls + 1
           /\ UNCHANGED <<history,saved,wanted>>
Record == /\ calls < 4 /\ history' = calls + 2
          /\ objects' = [objects EXCEPT ![calls+2] = objects[0]]
          /\ calls' = calls+1 /\ UNCHANGED <<saved,wanted>>
Save == /\ calls < 4 /\ wanted' = objects[0] /\ calls' = calls+1 /\ UNCHANGED history
        /\ saved' = IF MatchHistory THEN history ELSE calls+2
        /\ objects' = IF MatchHistory THEN objects ELSE [objects EXCEPT ![calls+2] = objects[0]]
Done == /\ calls = 4 /\ UNCHANGED vars
Next == (\E v \in 0..2 : Edit(v)) \/ Record \/ Save \/ Done
TypeOK == /\ objects \in [0..5 -> 0..2] /\ history \in 1..5 /\ saved \in 0..5 /\ wanted \in 0..2 /\ calls \in 0..4
SavedContent == objects[saved] = wanted
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
