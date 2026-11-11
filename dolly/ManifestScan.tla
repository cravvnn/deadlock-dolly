---- MODULE ManifestScan ----
EXTENDS Naturals, Sequences
\* updater.staged_payload calls read_manifest on two sorted private workspaces.
\* good: matching approved version/digest + manifest; mismatch: valid but not
\* approved; invalid: existing ValueError; attribute/key/type: bad decoded JSON
\* shapes formerly escaping the scan; noncanonical: bool/float schema accepted
\* as integer by old equality. Guarded candidate normalizes these to ValueError.
\* read_manifest normalizes recursive JSON to ValueError; actual byte bound is
\* a synchronous reader contract in test_recorder_updater, not lifecycle state.
\* Recursive completion-marker decoding is contained by staged_payload too.
\* Both marker decode and payload manifest decode are returning synchronous
\* calls; one abstract scan decision preserves their observable select/skip.
\* Ordinary missing/malformed marker + OSError remain caught skip behavior.
\* Byte budgets are actual-reader probes, not lifecycle/model state. Payload hash verification remains verify_payload
\* and update_worker.install, outside this selection model. No network/game.
CONSTANT Guarded
ContainRecursion == TRUE
ContainMarkerRecursion == TRUE
Kinds == {"good","mismatch","invalid","attribute","key","type","noncanonical","recursive","marker_recursive"}
VARIABLES work, index, pc, result
vars == <<work,index,pc,result>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ManifestScanApi WITH phase <- phase
Init == /\ work \in [1..2 -> Kinds] /\ index = 1 /\ pc = "scan" /\ result = "pending"
Select == /\ pc = "scan" /\ index <= 2
          /\ (work[index] = "good" \/ (~Guarded /\ work[index] = "noncanonical"))
          /\ pc' = "done" /\ result' = IF index = 1 THEN "first" ELSE "second"
          /\ UNCHANGED <<work,index>>
SkipInvalid == /\ pc = "scan" /\ index <= 2
               /\ ((work[index] \in {"invalid","mismatch"} \/ (ContainMarkerRecursion /\ work[index] = "marker_recursive")) \/ (ContainRecursion /\ work[index] = "recursive") \/ (Guarded /\ work[index] \in {"attribute","key","type","noncanonical"}))
               /\ index' = index + 1
               /\ UNCHANGED <<work,pc,result>>
Unhandled == /\ pc = "scan" /\ index <= 2 /\ ~Guarded
             /\ work[index] \in {"attribute","key","type"}
             /\ pc' = "done" /\ result' = work[index]
             /\ UNCHANGED <<work,index>>
UnhandledRecursion == /\ pc = "scan" /\ index <= 2 /\ ~ContainRecursion
                      /\ work[index] = "recursive" /\ pc' = "done" /\ result' = "recursive"
                      /\ UNCHANGED <<work,index>>
UnhandledMarker == /\ pc = "scan" /\ index <= 2 /\ ~ContainMarkerRecursion
                   /\ work[index] = "marker_recursive" /\ pc' = "done" /\ result' = "marker_recursive"
                   /\ UNCHANGED <<work,index>>
Miss == /\ pc = "scan" /\ index = 3 /\ result' = "miss" /\ pc' = "done"
        /\ UNCHANGED <<work,index>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Select \/ SkipInvalid \/ Unhandled \/ UnhandledRecursion \/ UnhandledMarker \/ Miss
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ index \in 1..3 /\ pc \in {"scan","done"}
          /\ result \in {"pending","miss","first","second","attribute","key","type","recursive","marker_recursive"}
          /\ work \in [1..2 -> Kinds]
StrictSelection == ~Guarded \/ (IF result = "first" THEN work[1] = "good" ELSE IF result = "second" THEN work[2] = "good" ELSE TRUE)
ValidationDoesNotAbort == ~Guarded \/ result \notin {"attribute","key","type"}
FirstMatching == ~Guarded \/ result # "second" \/ work[1] # "good"
MatchingNotMissed == ~Guarded \/ pc # "done" \/ ~("good" \in {work[1],work[2]}) \/ result \in {"first","second"}
MarkerDoesNotAbort == result # "marker_recursive"
RecursionDoesNotAbort == result # "recursive"
Returns == <>(pc = "done")
Refines == API!Spec
====
