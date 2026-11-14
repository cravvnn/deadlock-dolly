---- MODULE CameraRetirementApi ----
\* Caller-visible generation retirement: a shot's lease ends only with a quiet
\* playback worker (Controller._halt -> worker.join) and release evidence (a
\* matching restoration ack, or the game process gone).
\* Refined by CameraSessionJoin (one generation, starting leased). Composed by
\* DollyRoot, where Begin is a new shot: a retired generation may start another,
\* whose worker is live. While retired nothing observable changes.
VARIABLES retired,quiet,evidence
vars == <<retired,quiet,evidence>>
Init == retired => (quiet /\ evidence)
Begin == /\ retired /\ ~retired' /\ ~quiet' /\ evidence' \in BOOLEAN
Observe == /\ ~retired /\ ~retired' /\ quiet' \in BOOLEAN /\ evidence' \in BOOLEAN
Retire == /\ ~retired /\ quiet' /\ evidence' /\ retired'
Spec == Init /\ [][Begin \/ Observe \/ Retire]_vars
====
