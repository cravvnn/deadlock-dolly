---- MODULE BridgeClose ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* NativeBridge.close from launcher.Session.close/failed launch; Controller
\* recovery reaches Session.close. Exit watcher also calls Session.close.
\* Their outer configuration restoration/startup is not modeled. Each serialized
\* close phase returns; media failure or final diagnostic failure is retained.
\* Existing caller methods, native release/heartbeat protocol and OS unmapping
\* are not composed. Real held mmap views and heartbeat threads are code probes.
\* Media may stay open behind a borrowed view; remaining cleanup must run.
\* Later cleanup exceptions, indefinitely blocking joins/close and crashes excluded.
VARIABLES pc,fault,primary,stopped,closed,released
vars == <<pc,fault,primary,stopped,closed,released>>
phase == IF pc = 6 THEN "done" ELSE "active"
API == INSTANCE BridgeCloseApi WITH phase <- phase

Init == /\ pc = 1 /\ fault \in 0..2 /\ primary = FALSE
        /\ stopped = FALSE /\ closed = FALSE /\ released = FALSE
Phase == /\ pc \in 1..2
         /\ primary' = (primary \/ fault = pc)
         /\ pc' = pc + 1
         /\ UNCHANGED <<fault,stopped,closed,released>>
Release == /\ pc = 3 /\ pc' = 4 /\ released' = TRUE
           /\ UNCHANGED <<fault,primary,stopped,closed>>
Stop == /\ pc = 4 /\ pc' = 5 /\ stopped' = TRUE
        /\ UNCHANGED <<fault,primary,closed,released>>
Unmap == /\ pc = 5 /\ pc' = 6 /\ closed' = TRUE
         /\ UNCHANGED <<fault,primary,stopped,released>>
Run == Phase \/ Release \/ Stop \/ Unmap
Done == /\ pc = 6 /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in 1..6 /\ fault \in 0..2 /\ primary \in BOOLEAN
          /\ stopped \in BOOLEAN /\ closed \in BOOLEAN /\ released \in BOOLEAN
KeepFailure == pc = 6 => primary = (fault # 0)
FinishOthers == pc = 6 => stopped /\ closed /\ released
Returns == <>(pc = 6)

Refines == API!Spec
====
