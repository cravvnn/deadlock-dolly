---- MODULE CameraOnlyCapture ----
EXTENDS Naturals
\* Camera-only branch of video::capture / finish_camera_only.
\* Existing caller: overlay clean_frame, serialized Present/Resize boundary.
\* One admitted callback can overlap worker stop/cancel. Closing ready precedes
\* waiting for that callback; no file publication on the render thread.
\* Two frames represent a bounded take. Valid abstracts actual native clock,
\* pose, fresh-view, dimensions and contiguous-cadence checks, tested in C++.
\* This is raw camera-trace publication, NOT final .crav success. Replay SHA
\* pin/recheck and final no-overwrite publication remain Python obligations.
\* Cancel covers admission/drain here. Cancellation during raw file I/O can
\* leave that raw trace while terminal status is cancelled; no .crav succeeds.
\* Excludes GPU behavior, Windows filesystem semantics and whole-program proof.
VARIABLES ready, stopping, cancelled, failed, callback, count, pc, published
vars == <<ready, stopping, cancelled, failed, callback, count, pc, published>>
Init == /\ ready = TRUE /\ stopping = FALSE /\ cancelled = FALSE
        /\ failed = FALSE /\ callback = "none" /\ count = 0
        /\ pc = "running" /\ published = FALSE
Enter == /\ ready /\ ~stopping /\ callback = "none" /\ count < 2
         /\ callback' \in {"valid", "invalid"}
         /\ UNCHANGED <<ready, stopping, cancelled, failed, count, pc, published>>
FinishFrame == /\ callback # "none"
               /\ count' = IF callback = "valid" THEN count + 1 ELSE count
               /\ failed' = (failed \/ callback = "invalid")
               /\ stopping' = (stopping \/ callback = "invalid" \/ count' = 2)
               /\ callback' = "none"
               /\ UNCHANGED <<ready, cancelled, pc, published>>
Stop == /\ pc = "running" /\ ~stopping /\ stopping' = TRUE
        /\ UNCHANGED <<ready, cancelled, failed, callback, count, pc, published>>
Cancel == /\ pc = "running" /\ ~cancelled
          /\ cancelled' = TRUE /\ stopping' = TRUE
          /\ UNCHANGED <<ready, failed, callback, count, pc, published>>
Fault == /\ pc = "running" /\ ~failed
         /\ failed' = TRUE /\ stopping' = TRUE
         /\ UNCHANGED <<ready, cancelled, callback, count, pc, published>>
Close == /\ pc = "running" /\ stopping
         /\ ready' = FALSE /\ pc' = "draining"
         /\ UNCHANGED <<stopping, cancelled, failed, callback, count, published>>
Drain == /\ pc = "draining" /\ callback = "none" /\ pc' = "publishing"
         /\ UNCHANGED <<ready, stopping, cancelled, failed, callback, count, published>>
Publish == /\ pc = "publishing"
           /\ published' = (~cancelled /\ ~failed /\ count = 2)
           /\ pc' = "done"
           /\ UNCHANGED <<ready, stopping, cancelled, failed, callback, count>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Enter \/ FinishFrame \/ Stop \/ Cancel \/ Fault \/ Close \/ Drain \/ Publish \/ Done
Spec == Init /\ [][Next]_vars
Bounded == count <= 2
QuiescentPublication == published => (~ready /\ callback = "none" /\ stopping)
CompletePublication == published => (count = 2 /\ ~cancelled /\ ~failed)
QuiescentReader == (pc \in {"publishing", "done"}) => (~ready /\ callback = "none")
====
