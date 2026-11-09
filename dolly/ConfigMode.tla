---- MODULE ConfigMode ----
EXTENDS Naturals
\* file_ops._atomic_write: first Windows replace denied; destination read-only.
\* Callers include launcher GameInfo writes, legacy graphics restoration and
\* editing-session journals. Only the permission-error retry/compensation branch.
\* Three returning outcomes: retry succeeds, retry fails and mode restoration
\* succeeds, or both retry and restoration fail. Temp finalization collapsed.
\* Same destination identity throughout; no external namespace writer, crash,
\* chmod failure before retry, ACL changes, machine durability or whole caller.
\* Best-effort original mode; never mask the primary replacement failure.
\* Production-only bounded contract; baseline experiments stay private.
VARIABLES pc,fault,readonly,error
vars == <<pc,fault,readonly,error>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ConfigModeApi WITH phase <- phase
Init == /\ pc = "clear" /\ fault \in {"success","replace","restore"}
        /\ readonly = TRUE /\ error = "none"
Clear == /\ pc = "clear" /\ pc' = "retry" /\ readonly' = FALSE /\ UNCHANGED <<fault,error>>
Retry == /\ pc = "retry"
         /\ pc' = (IF fault = "success" THEN "done" ELSE "rollback")
         /\ readonly' = (fault = "success")
         /\ error' = (IF fault = "success" THEN "none" ELSE "replace")
         /\ UNCHANGED fault
Rollback == /\ pc = "rollback" /\ pc' = "done"
            /\ readonly' = (fault # "restore")
            /\ error' = error /\ UNCHANGED fault
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Clear \/ Retry \/ Rollback
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"clear","retry","rollback","done"}
          /\ fault \in {"success","replace","restore"}
          /\ readonly \in BOOLEAN /\ error \in {"none","replace","restore"}
ModeRetained == pc = "done" /\ fault # "restore" => readonly
PrimaryKept == pc = "done" /\ fault # "success" => error = "replace"
Returns == <>(pc = "done")
Refines == API!Spec
====
