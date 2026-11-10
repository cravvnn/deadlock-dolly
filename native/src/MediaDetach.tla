---- MODULE MediaDetach ----
EXTENDS Naturals
\* dolly_media_win.cpp media_worker_tick, called by bridge worker. Named session
\* loses heartbeat: active video keeps its existing grace, then all media retire.
\* Video inactive case skips grace. One returning sound_capture_stop is assumed
\* to retire its capture. Worker/clock serialized; grace boundary abstracts0/29/30s.
\* No audio hooks, real file flush, render thread, interprocess heartbeats, C++
\* weak-memory, I/O failure or guarantee of successful video encoding modeled.
UseFix == TRUE
VARIABLES pc,video,age,audio,detached
vars == <<pc,video,age,audio,detached>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE MediaDetachApi WITH phase <- phase
Init == /\ pc = "disconnect" /\ video \in BOOLEAN /\ age = 0
        /\ audio = TRUE /\ detached = FALSE
Observe == /\ pc = "disconnect" /\ pc' = (IF video THEN "grace" ELSE "cleanup")
           /\ UNCHANGED <<video,age,audio,detached>>
Wait == /\ pc = "grace" /\ age < 30 /\ age' \in {29,30}
        /\ pc' = (IF age' = 30 THEN "cleanup" ELSE "grace")
        /\ UNCHANGED <<video,audio,detached>>
Cleanup == /\ pc = "cleanup" /\ pc' = "done" /\ detached' = TRUE
           /\ audio' = ~UseFix /\ UNCHANGED <<video,age>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Observe \/ Wait \/ Cleanup \/ Done
TypeOK == /\ pc \in {"disconnect","grace","cleanup","done"}
          /\ video \in BOOLEAN /\ age \in {0,29,30}
          /\ audio \in BOOLEAN /\ detached \in BOOLEAN
StoppedOnDetach == detached => ~audio
KeepGrace == pc = "grace" => audio /\ ~detached
Refines == API!Spec
Spec == Init /\ [][Next]_vars
====
