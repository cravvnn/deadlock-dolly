---- MODULE ConsoleStart ----
EXTENDS Naturals
\* ConsoleClient.connect: one owned connected socket, Thread.start refuses.
\* Clear refused reader before close, whose socket operations return.
\* No accepted worker, reconnect, request protocol, concurrent close, socket
\* close failure or post-start exception; these are code witnesses/limits.
\* Production-only bounded contract; baseline experiments stay private.
VARIABLES pc,socketOpen,reader,connected
vars == <<pc,socketOpen,reader,connected>>
phase == IF pc = "returned" THEN "done" ELSE "active"
API == INSTANCE ConsoleStartApi WITH phase <- phase
Init == /\ pc = "start" /\ socketOpen = TRUE /\ reader = TRUE /\ connected = TRUE
Refuse == /\ pc = "start" /\ pc' = "returned"
          /\ socketOpen' = FALSE /\ reader' = FALSE /\ connected' = FALSE
Done == /\ pc = "returned" /\ UNCHANGED vars
Run == Refuse
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"start","returned"} /\ socketOpen \in BOOLEAN
          /\ reader \in BOOLEAN /\ connected \in BOOLEAN
RefusedRetired == pc = "returned" => ~socketOpen /\ ~reader /\ ~connected
Returns == <>(pc = "returned")
Refines == API!Spec
====
