---- MODULE FileOpsApi ----
EXTENDS Naturals
\* Process-interruption contract for checked owned files under the install lock.
\* Copy keeps the verified source; Move consumes it. Temporary-file internals
\* are checked separately by AtomicCopy.tla. No power-loss durability claim.
CONSTANTS Files, Existing
VARIABLES contents, availableBackups
vars == <<contents, availableBackups>>
Init == /\ contents \in [Files -> {"old","new","absent"}]
        /\ availableBackups = Existing
Move(f) == /\ f \in Existing /\ f \in availableBackups
           /\ contents' = [contents EXCEPT ![f] = "old"]
           /\ availableBackups' = availableBackups \ {f}
Copy(f) == /\ f \in Existing /\ f \in availableBackups
           /\ contents' = [contents EXCEPT ![f] = "old"]
           /\ UNCHANGED availableBackups
Remove(f) == /\ f \in Files \ Existing
             /\ contents' = [contents EXCEPT ![f] = "absent"]
             /\ UNCHANGED availableBackups
Next == \E f \in Files: Move(f) \/ Copy(f) \/ Remove(f)
Spec == Init /\ [][Next]_vars
====
