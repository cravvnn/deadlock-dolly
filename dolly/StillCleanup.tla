---- MODULE StillCleanup ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* screenshot.cleanup called only by finish after final still outputs exist.
\* Three file entries: two documented intermediates and one foreign entry.
\* Any can be linked or unlink-denied. Final outputs lie outside this set.
\* Nested depth/EXR traversal is abstracted to these returning file decisions;
\* actual names, known directories, junctions and no-recursion are code probes.
\* No concurrent namespace replacement, matching-name foreign file identity,
\* crash durability or indefinitely blocking filesystem operation is modeled.
VARIABLES pc,files,linked,denied,initial
vars == <<pc,files,linked,denied,initial>>
phase == IF pc = 4 THEN "done" ELSE "active"
API == INSTANCE StillCleanupApi WITH phase <- phase

Init == /\ pc = 1 /\ files \in SUBSET {1,2,3} /\ initial = files
        /\ linked \in SUBSET {1,2,3} /\ denied \in SUBSET {1,2,3}
Remove == /\ pc \in 1..3 /\ pc' = pc + 1
          /\ files' = IF (pc \in {1,2} /\ pc \notin linked) /\ pc \notin denied
                      THEN files \ {pc} ELSE files
          /\ UNCHANGED <<linked,denied,initial>>
Done == /\ pc = 4 /\ UNCHANGED vars
Next == Remove \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Remove)
TypeOK == /\ pc \in 1..4 /\ files \subseteq {1,2,3} /\ initial \subseteq {1,2,3}
          /\ linked \subseteq {1,2,3} /\ denied \subseteq {1,2,3}
OnlyRemoveExisting == files \subseteq initial
KeepUnknown == 3 \in initial => 3 \in files
KeepLinked == initial \cap linked \subseteq files
Returns == <>(pc = 4)

Refines == API!Spec
====
