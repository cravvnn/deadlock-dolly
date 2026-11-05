---- MODULE PlaybackIdentity ----
EXTENDS Naturals
\* _run_native timeout branch, entered by play/POV playback's monitor thread.
\* One query admitted while unpaced, zero or one prior unpaced miss, and a
\* capture report appearing while the query blocks. Native identity/Stop and
\* frame-clock checks run at the next monitor iteration, outside this projection.
\* Valid abstracts _player_capture_pacing_active's existing request/PID/session/
\* replay/freshness/progress predicate, exercised separately by owner tests.
\* No GPU, console framing, real-time liveness or whole-program proof.
\* ownedCapture is normal sampling progress; ownedFresh is GPU/storage/drain.
\* Both use the same concrete ownership/freshness/progress predicate.
VARIABLES pc, report, misses, outcome
vars == <<pc, report, misses, outcome>>
Valid == report \in {"ownedFresh", "ownedCapture"}
Init == /\ pc = "query" /\ report = "idle" /\ misses \in 0..1 /\ outcome = "none"
CaptureWhileQuery == /\ pc = "query" /\ pc' = "timeout"
                     /\ report' \in {"ownedFresh", "ownedCapture", "idle", "complete", "foreign", "stale", "error"}
                     /\ UNCHANGED <<misses, outcome>>
Timeout == /\ pc = "timeout"
           /\ outcome' = IF Valid THEN "defer" ELSE IF misses = 1 THEN "abort" ELSE "retry"
           /\ pc' = "done" /\ UNCHANGED <<report, misses>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == CaptureWhileQuery \/ Timeout \/ Done
Spec == Init /\ [][Next]_vars
KeepProgressingCapture == (pc = "done" /\ report \in {"ownedFresh", "ownedCapture"}) => outcome = "defer"
RejectUnverifiedWait == (pc = "done" /\ ~Valid) =>
    outcome = (IF misses = 1 THEN "abort" ELSE "retry")
====
