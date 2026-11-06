---- MODULE SocketSetup ----
EXTENDS Naturals
\* Production-only design. Private baseline comparisons are not shipped.
\* Weak fairness assumes returning calls; terminal Done deliberately stutters.
\* API refinement checks completion shape only; safety obligations stay here.
\* ConsoleClient.connect factory -> TCP_NODELAY -> timeout -> transfer.
\* Before publication to self._socket, cleanup authority is provisional.
\* ExitStack callback is discarded on success; actual client publication follows
\* outside this model. No whole-console refinement is claimed.
\* One allocation, returning successful close, failures before either setting.
\* Post-transfer publication/reader startup/concurrent close/requests excluded.
\* Controller.connect's error cleanup cannot reach a non-published socket.
VARIABLES pc, open, owner, failed
vars == <<pc,open,owner,failed>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE SocketSetupApi WITH phase <- phase
Init == /\ pc = "create" /\ open = FALSE /\ owner = "none" /\ failed = FALSE
Allocate == /\ pc = "create" /\ open' = TRUE
            /\ owner' = "scope"
            /\ pc' = "option" /\ UNCHANGED failed
FactoryError == /\ pc = "create" /\ pc' = "done" /\ failed' = TRUE
                /\ UNCHANGED <<open,owner>>
Option == /\ pc = "option" /\ pc' = "timeout" /\ UNCHANGED <<open,owner,failed>>
Timeout == /\ pc = "timeout" /\ pc' = "transfer" /\ UNCHANGED <<open,owner,failed>>
SetupError == /\ pc \in {"option","timeout"} /\ failed' = TRUE
              /\ pc' = "rollback"
              /\ UNCHANGED <<open,owner>>
Rollback == /\ pc = "rollback" /\ open' = FALSE /\ owner' = "none"
            /\ pc' = "done" /\ UNCHANGED failed
Transfer == /\ pc = "transfer" /\ owner' = "client" /\ pc' = "done"
            /\ UNCHANGED <<open,failed>>
Run == Allocate \/ FactoryError \/ Option \/ Timeout \/ SetupError \/ Rollback \/ Transfer
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"create","option","timeout","transfer","rollback","done"}
          /\ open \in BOOLEAN /\ failed \in BOOLEAN /\ owner \in {"none","scope","client"}
SuccessfulTransfer == (pc = "done" /\ ~failed) => open /\ owner = "client"
NoUnownedFailure == (pc = "done" /\ failed) => ~open
Returns == <>(pc = "done")
Refines == API!Spec
====
