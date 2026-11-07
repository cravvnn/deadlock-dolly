---- MODULE HotkeyStart ----
EXTENDS Naturals
\* CaptureHotkey.start/stop, called by DollyApp._change_capture_listener.
\* Two enable attempts; accepted workers remain owned until returning join.
\* GUI retirement and a joinable dead thread abstract to no outstanding owner.
\* No input/focus model, concurrent callers, worker hangs or post-start exceptions.
\* Finite safety/completion-shape contract; deadlock checking disabled at terminal states.
VARIABLES pc, owner, live, attempts, stopError
vars == <<pc, owner, live, attempts, stopError>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE HotkeyStartApi WITH phase <- phase
Init == /\ pc = "idle" /\ owner = "none" /\ live = FALSE /\ attempts = 0 /\ stopError = FALSE
Start == /\ pc = "idle" /\ attempts < 2
         /\ pc' = "created" /\ owner' = "new" /\ attempts' = attempts + 1
         /\ UNCHANGED <<live, stopError>>
Refused == /\ pc = "created" /\ pc' = "cleanup"
           /\ owner' = "none"
           /\ UNCHANGED <<live, attempts, stopError>>
Accepted == /\ pc = "created" /\ pc' = "ready" /\ owner' = "worker" /\ live' = TRUE
            /\ UNCHANGED <<attempts, stopError>>
Ready == /\ pc = "ready" /\ pc' = "running" /\ UNCHANGED <<owner, live, attempts, stopError>>
Timeout == /\ pc = "ready" /\ pc' = "cleanup" /\ owner' = owner
           /\ UNCHANGED <<live, attempts, stopError>>
Disable == /\ pc = "running" /\ pc' = "cleanup" /\ UNCHANGED <<owner, live, attempts, stopError>>
Join == /\ pc = "cleanup" /\ owner = "worker" /\ live
        /\ live' = FALSE /\ UNCHANGED <<pc, owner, attempts, stopError>>
Stop == /\ pc = "cleanup" /\ ~live
        /\ stopError' = (owner = "new")
        /\ owner' = "none"
        /\ pc' = IF attempts = 2 THEN "done" ELSE "idle"
        /\ UNCHANGED <<live, attempts>>
Next == Start \/ Refused \/ Accepted \/ Ready \/ Timeout \/ Disable \/ Join \/ Stop
TypeOK == /\ pc \in {"idle","created","cleanup","ready","running","done"}
          /\ owner \in {"none","new","worker"} /\ live \in BOOLEAN
          /\ attempts \in 0..2 /\ stopError \in BOOLEAN
WorkerOwned == live => owner = "worker"
NoUnstartedJoin == ~stopError
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
