---- MODULE CravCapture ----
EXTENDS Naturals
\* capture_to_crav on the existing operation worker; GUI owns only a cancel
\* Event, native capture and VideoExport own resources. Each control mutation
\* and its ownership recheck share Controller._op_lock (an RLock).
\* One capture, one optional session replacement/cancel, complete or failed
\* native result, equal or changed post-capture replay bytes. Commit admission
\* is the last cancellation point. ExportCleanup covers the file publisher;
\* CameraOnlyCapture covers native admission/drain, not game/render fidelity.
\* Native completion and successful restoration are abstract boundary returns.
\* No universal crash recovery, hostile filesystem or whole-program proof.
VARIABLES pc, owner, cancelled, raw, sameReplay, active, restored, published, foreignControl
vars == <<pc, owner, cancelled, raw, sameReplay, active, restored, published, foreignControl>>
Init == /\ pc = "start" /\ owner = TRUE /\ cancelled = FALSE
        /\ raw = FALSE /\ sameReplay = FALSE /\ active = FALSE
        /\ restored = FALSE /\ published = FALSE /\ foreignControl = FALSE
ChangeOwner == /\ pc \notin {"done", "publish"} /\ owner /\ ~published
               /\ owner' = FALSE
               /\ UNCHANGED <<pc, cancelled, raw, sameReplay, active, restored, published, foreignControl>>
Cancel == /\ pc \notin {"done", "publish"} /\ ~cancelled /\ ~published
          /\ cancelled' = TRUE
          /\ UNCHANGED <<pc, owner, raw, sameReplay, active, restored, published, foreignControl>>
Start == /\ pc = "start"
         /\ pc' = IF owner /\ ~cancelled THEN "wait" ELSE "cleanup"
         /\ active' = (owner /\ ~cancelled)
         /\ UNCHANGED <<owner, cancelled, raw, sameReplay, restored, published, foreignControl>>
Wait == /\ pc = "wait" /\ raw' \in BOOLEAN
        /\ pc' = "restore"
        /\ UNCHANGED <<owner, cancelled, sameReplay, active, restored, published, foreignControl>>
Restore == /\ pc = "restore"
           /\ pc' = IF owner /\ ~cancelled /\ raw THEN "repin" ELSE "cleanup"
           /\ restored' = (owner /\ ~cancelled /\ raw)
           /\ active' = IF owner /\ ~cancelled /\ raw THEN FALSE ELSE active
           /\ UNCHANGED <<owner, cancelled, raw, sameReplay, published, foreignControl>>
Repin == /\ pc = "repin" /\ sameReplay' \in BOOLEAN /\ pc' = "publish"
         /\ UNCHANGED <<owner, cancelled, raw, active, restored, published, foreignControl>>
Publish == /\ pc = "publish"
           /\ published' = (owner /\ ~cancelled /\ raw /\ sameReplay /\ restored /\ ~active)
           /\ pc' = "cleanup"
           /\ UNCHANGED <<owner, cancelled, raw, sameReplay, active, restored, foreignControl>>
Cleanup == /\ pc = "cleanup"
           /\ active' = IF owner THEN FALSE ELSE active
           /\ foreignControl' = (foreignControl \/ (~owner /\ FALSE))
           /\ pc' = "done"
           /\ UNCHANGED <<owner, cancelled, raw, sameReplay, restored, published>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Start \/ Wait \/ Restore \/ Repin \/ Publish \/ Cleanup \/ ChangeOwner \/ Cancel \/ Done
Spec == Init /\ [][Next]_vars
FaithfulSuccess == published => (raw /\ sameReplay /\ restored /\ ~active /\ ~cancelled)
OwnedSuccess == published => owner
NoForeignControl == ~foreignControl
====
