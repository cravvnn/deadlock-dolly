---- MODULE CleanupMarker ----
EXTENDS Naturals
\* remove_overlay: one validated unmounted overlay, preflight, marker unlink,
\* final rmdir and marker repair. Game exit is a caller
\* precondition, not inferred from the directory. Callers: Session.close,
\* recover_pending, recover_orphans and wait_and_cleanup. One attempt; retry's
\* actual preflight is exercised by test_session_cleanup via recover_pending.
\* One late plain user file can arrive after scan. Parent/marker identity cannot
\* be replaced concurrently. rootFailure represents held-open/permission failure.
\* Atomic marker repair is split into staging and publication. A returning
\* repair failure and process crash are modeled, not counted as handled retry.
CONSTANT Repair
VARIABLES pc, marker, root, unknown, arrived, rootFailure
vars == <<pc,marker,root,unknown,arrived,rootFailure>>
phase == IF pc = "retry" THEN "retry" ELSE IF pc = "done" THEN "done"
         ELSE IF pc \in {"crashed","repairFailed"} THEN "aborted" ELSE "active"
API == INSTANCE OwnershipApi WITH phase <- phase, evidence <- marker \/ ~root \/ ~unknown
Init == /\ pc = "scan" /\ marker = TRUE /\ root = TRUE
        /\ unknown = FALSE /\ arrived = FALSE /\ rootFailure \in BOOLEAN
Scan == /\ pc = "scan" /\ pc' = "unlink"
        /\ UNCHANGED <<marker,root,unknown,arrived,rootFailure>>
Arrive == /\ pc \in {"unlink","rmdir"} /\ ~arrived
          /\ unknown' = TRUE /\ arrived' = TRUE
          /\ UNCHANGED <<pc,marker,root,rootFailure>>
UnlinkMarker == /\ pc = "unlink" /\ marker' = FALSE /\ pc' = "rmdir"
                /\ UNCHANGED <<root,unknown,arrived,rootFailure>>
RemoveRoot == /\ pc = "rmdir"
              /\ IF unknown \/ rootFailure
                 THEN /\ pc' = (IF Repair THEN "stage" ELSE "retry") /\ UNCHANGED root
                 ELSE /\ pc' = "done" /\ root' = FALSE
              /\ UNCHANGED <<marker,unknown,arrived,rootFailure>>
StageRepair == /\ pc = "stage" /\ pc' = "publish"
               /\ UNCHANGED <<marker,root,unknown,arrived,rootFailure>>
PublishRepair == /\ pc = "publish" /\ marker' = TRUE /\ pc' = "retry"
                 /\ UNCHANGED <<root,unknown,arrived,rootFailure>>
RepairError == /\ pc \in {"stage","publish"} /\ pc' = "repairFailed"
               /\ UNCHANGED <<marker,root,unknown,arrived,rootFailure>>
Crash == /\ pc \in {"unlink","rmdir","stage","publish"} /\ pc' = "crashed"
         /\ UNCHANGED <<marker,root,unknown,arrived,rootFailure>>
Done == /\ pc \in {"retry","done","repairFailed","crashed"} /\ UNCHANGED vars
Run == Scan \/ UnlinkMarker \/ RemoveRoot \/ StageRepair \/ PublishRepair
Next == Run \/ Arrive \/ RepairError \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"scan","unlink","rmdir","stage","publish","retry","done","repairFailed","crashed"}
          /\ marker \in BOOLEAN /\ root \in BOOLEAN /\ unknown \in BOOLEAN
          /\ arrived \in BOOLEAN /\ rootFailure \in BOOLEAN
RetryRecognizable == (pc = "retry" /\ root /\ unknown) => marker
UserBytesSurvive == unknown => root
Returns == <>(pc \in {"retry","done","repairFailed","crashed"})
Refines == API!Spec
====
