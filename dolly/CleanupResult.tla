---- MODULE CleanupResult ----
EXTENDS Naturals
\* file_ops._atomic_write finally block after body success/failure. Callers:
\* launcher/gameinfo_transaction, graphics_profiles, editing_session/assets and
\* cleanup marker repair. Body is the lower API boundary; AtomicMode models its
\* normal write/publication stages separately. No body failure is invented here.
\* Successful replacement consumed the temp; primary failure may precede or
\* follow replacement. Stat, chmod and unlink are separate returning calls.
\* No external writer, power loss, pathname attacker or process-restart proof.
\* OSError and unexpected programming exceptions are intentionally distinct.
CONSTANTS Protected, Strict
VARIABLES pc, expected, temporary, result, unexpected
vars == <<pc,expected,temporary,result,unexpected>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE CleanupResultApi WITH phase <- phase
Init == /\ pc = "stat" /\ expected \in {"ok","primary"}
        /\ temporary \in (IF expected = "ok" THEN {FALSE} ELSE BOOLEAN)
        /\ result = "pending" /\ unexpected = FALSE
Stat == /\ pc = "stat" /\ pc' = IF temporary THEN "chmod" ELSE "finish"
        /\ UNCHANGED <<expected,temporary,result,unexpected>>
Chmod == /\ pc = "chmod" /\ pc' = "unlink"
         /\ UNCHANGED <<expected,temporary,result,unexpected>>
Unlink == /\ pc = "unlink" /\ temporary' = FALSE /\ pc' = "finish"
          /\ UNCHANGED <<expected,result,unexpected>>
CleanupOSError == /\ pc \in {"stat","chmod","unlink"}
                  /\ IF pc = "stat" /\ ~Protected
                     THEN /\ pc' = "done" /\ result' = "cleanup"
                     ELSE /\ pc' = "finish" /\ UNCHANGED result
                  /\ UNCHANGED <<expected,temporary,unexpected>>
Unexpected == /\ pc \in {"stat","chmod","unlink"}
              /\ IF Strict THEN /\ pc' = "done" /\ result' = "bug"
                 ELSE /\ pc' = "finish" /\ UNCHANGED result
              /\ unexpected' = TRUE
              /\ UNCHANGED <<expected,temporary>>
Finish == /\ pc = "finish" /\ pc' = "done" /\ result' = expected
          /\ UNCHANGED <<expected,temporary,unexpected>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Stat \/ Chmod \/ Unlink \/ CleanupOSError \/ Unexpected \/ Finish
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"stat","chmod","unlink","finish","done"}
          /\ expected \in {"ok","primary"} /\ temporary \in BOOLEAN
          /\ result \in {"pending","ok","primary","cleanup","bug"} /\ unexpected \in BOOLEAN
GuardedPreservesResult == ~Protected \/ result \in {"pending",expected,"bug"}
UnexpectedVisible == pc = "done" /\ unexpected => result = "bug"
Returns == <>(pc = "done")
Refines == API!Spec
====
