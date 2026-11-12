---- MODULE CameraRetirementApi ----
\* Caller-visible old-generation retirement: quiet worker plus release evidence.
VARIABLES retired,quiet,evidence
vars == <<retired,quiet,evidence>>
Init == ~retired
Observe == /\ ~retired /\ ~retired' /\ quiet' \in BOOLEAN /\ evidence' \in BOOLEAN
Retire == /\ ~retired /\ quiet' /\ evidence' /\ retired'
Done == /\ retired /\ UNCHANGED vars
Spec == Init /\ [][Observe \/ Retire \/ Done]_vars
====
