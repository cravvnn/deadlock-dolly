---- MODULE CameraRetirementApi ----
\* Caller-visible generation retirement: a shot's lease ends only with a quiet
\* playback worker (Controller._halt -> worker.join) and release evidence (a
\* matching restoration ack, or the game process gone).
\* Refined by CameraSessionJoin (one generation, starting leased). Composed by
\* DollyRoot and CameraSessionModes, where Begin is a new shot or free flight:
\* a retired generation may start another (a played shot's worker is live,
\* free flight has none). While retired nothing observable changes.
VARIABLES retired,quiet,evidence
vars == <<retired,quiet,evidence>>
Init == retired => (quiet /\ evidence)
Begin == /\ retired /\ ~retired' /\ quiet' \in BOOLEAN /\ evidence' \in BOOLEAN
Observe == /\ ~retired /\ ~retired' /\ quiet' \in BOOLEAN /\ evidence' \in BOOLEAN
Retire == /\ ~retired /\ quiet' /\ evidence' /\ retired'
Spec == Init /\ [][Begin \/ Observe \/ Retire]_vars
====
