---- MODULE HistoryShare ----
EXTENDS Naturals
\* Production-only contract; baseline comparisons stay private.
\* API refinement checks completion shape only; local safety rules remain here.
\* Weak fairness assumes returning actions; terminal Done deliberately stutters.

\* ShotHistory.reset shares the initial private project snapshot, not a saved
\* GUI new/open and ShotHistory.__init__ call reset; consumers receive move's
\* detached copy. Their full editor/revision/session lifecycles are not modeled.
\* position and not the caller's mutable project. Immutable snapshot identities:
\* 0 mutable caller,1 initial history snapshot,2 later entry. Baseline saved-copy slot removed.
\* One later record/eviction allowed; saved identity remains reachable when its
\* history entry is gone. Select edits ShotState metadata only (code probe).
\* mark_saved, arbitrary edits, slider merge, GUI/session/revision are outside.
\* This is content/alias ownership proof, not Python allocation/state reduction.
VARIABLES objects,saved,history,recorded,calls
vars == <<objects,saved,history,recorded,calls>>
phase == IF calls = 4 THEN "done" ELSE "active"
API == INSTANCE HistoryShareApi WITH phase <- phase
Init == /\ objects = [i \in 0..2 |-> 0] /\ saved = 1
        /\ history = 1 /\ recorded = FALSE /\ calls = 0
Edit(v) == /\ calls < 4 /\ objects' = [objects EXCEPT ![0] = v]
           /\ calls' = calls + 1 /\ UNCHANGED <<saved,history,recorded>>
Record == /\ calls < 4 /\ ~recorded /\ objects' = [objects EXCEPT ![2] = objects[0]]
          /\ history' = 2 /\ recorded' = TRUE /\ calls' = calls + 1 /\ UNCHANGED saved
Run == (\E v \in 0..2 : Edit(v)) \/ Record
Done == /\ calls = 4 /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ objects \in [0..2 -> 0..2] /\ saved \in 0..2 /\ history \in {1,2}
          /\ recorded \in BOOLEAN /\ calls \in 0..4
SavedFrozen == objects[saved] = 0
DirtyMatchesContent == (objects[0] # objects[saved]) = (objects[0] # 0)
Returns == <>(calls = 4)
Refines == API!Spec

====
