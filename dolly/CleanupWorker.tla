---- MODULE CleanupWorker ----
EXTENDS Naturals
\* player_encode.encode_in_worker AFTER job.close/process wait have returned.
\* Its body result is success (staged MOV already replaced final), cancellation,
\* or worker error. Two distinct owned file paths; successful body consumed stage.
\* Deletion actions follow Python finally order: partial then staged. Any initial
\* subset may remain; returning delete-denial faults occur only for present files.
\* GUI _finish_player_capture distinguishes InterruptedError from other errors;
\* preserving that type preserves its cancellation branch. GUI scheduling and
\* metadata writes are not modeled/proved here. No child execution, encoding,
\* ProcessJob failures, TemporaryDirectory cleanup, hostile namespace replacement,
\* process crash/power loss or indefinitely blocking operations are modeled.
VARIABLES pc, files, body, result, attempted
vars == <<pc,files,body,result,attempted>>
phase == IF pc = 3 THEN "done" ELSE "active"
API == INSTANCE CleanupWorkerApi WITH phase <- phase
Init == /\ pc = 1 /\ body \in {"ok","cancel","error"}
        /\ result = body /\ files \in SUBSET {1,2}
        /\ (body = "ok" => 2 \notin files) /\ attempted = {}
Delete == /\ pc \in {1,2}
          /\ files' = files \ {pc}
          /\ attempted' = attempted \cup {pc}
          /\ pc' = pc + 1
          /\ UNCHANGED <<body,result>>
Deny == /\ pc \in {1,2} /\ pc \in files
        /\ attempted' = attempted \cup {pc}
        /\ result' = result
        /\ pc' = pc + 1
        /\ UNCHANGED <<body,files>>
Run == Delete \/ Deny
Done == /\ pc = 3 /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {1,2,3} /\ files \in SUBSET {1,2}
          /\ attempted \in SUBSET {1,2}
          /\ body \in {"ok","cancel","error"}
          /\ result \in {"ok","cancel","error","cleanup_error"}
PreserveBody == result = body
AttemptBoth == (pc = 3) => attempted = {1,2}
OnlyOwned == files \subseteq {1,2}
Returns == <>(pc = 3)
Refines == API!Spec
====
