---- MODULE RollbackApi ----
EXTENDS Naturals
\* Caller contract: _apply_plan(recover=True) / release_launcher recovery.
\* Recovery must restore all original owned bytes before reporting recovered
\* and removing PENDING. User files, path validation and process locks stay
\* with their real owners; this interface grants no live update permission.
CONSTANTS Files, Existing
VARIABLES contents, pending, recovered
vars == <<contents,pending,recovered>>
Original == [f \in Files |-> IF f \in Existing THEN "old" ELSE "absent"]
Init == /\ contents \in [Files -> {"old","new","absent"}]
        /\ pending = TRUE /\ recovered = FALSE
Restore(f) == /\ f \in Files
              /\ contents' = [contents EXCEPT ![f] = Original[f]]
              /\ UNCHANGED <<pending,recovered>>
Acknowledge == /\ contents = Original /\ ~recovered /\ recovered' = TRUE
               /\ UNCHANGED <<contents,pending>>
Release == /\ recovered /\ pending /\ pending' = FALSE
           /\ UNCHANGED <<contents,recovered>>
Next == (\E f \in Files: Restore(f)) \/ Acknowledge \/ Release
Spec == Init /\ [][Next]_vars
====
