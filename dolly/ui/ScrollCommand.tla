---- MODULE ScrollCommand ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* WheelScroller._time registers the cost marker; _destroyed cancels pending
\* job. Python Tk widget destruction removes commands registered on that widget.
\* Root and one child; no root destruction until after child teardown. In the
\* baseline root-owned command retains the dead child's scroller callback.
\* Real Tcl ownership/GC and callback measurement are implementation probes.
\* Direct external Tcl destruction, arbitrary Tcl errors and animation loop excluded.
VARIABLES pc,registered,child,job
vars == <<pc,registered,child,job>>
phase == IF pc = 3 THEN "done" ELSE "active"
API == INSTANCE ScrollCommandApi WITH phase <- phase

Init == /\ pc = 1 /\ registered = FALSE /\ child = TRUE /\ job = FALSE
Register == /\ pc = 1 /\ pc' = 2 /\ registered' = TRUE /\ job' = TRUE /\ UNCHANGED child
Destroy == /\ pc = 2 /\ pc' = 3 /\ child' = FALSE /\ job' = FALSE
           /\ registered' = FALSE
Done == /\ pc = 3 /\ UNCHANGED vars
Next == Register \/ Destroy \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Register \/ Destroy)
TypeOK == /\ pc \in 1..3 /\ registered \in BOOLEAN /\ child \in BOOLEAN /\ job \in BOOLEAN
TimerOwned == ~child => ~job
CommandOwned == ~child => ~registered
Returns == <>(pc = 3)

Refines == API!Spec
====
