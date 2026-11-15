---- MODULE RecorderApi ----
\* Caller-visible recording outcome: the native recorder's terminal state
\* (dolly_video_win.cpp state store, read by VideoExport/crav_capture) and
\* whether the final artifact (MP4/trace plus sidecars) was kept on disk.
\* Shared module because its refinements and callers live in different
\* directories: CameraOnlyCapture (native/src) refines it; DollyRoot (dolly)
\* composes it. Stage: files may be published or removed while active.
\* Finish: the terminal state agrees with the files (completed <=> kept).
\* There is no exception: since 2026-11-14 every capture-thread fail() runs
\* inside the producer gate, so it lands before the encoder's single decision
\* or is never observed (capture stops reading the backbuffer once admission
\* closes). The former late-fault exception (GapFinish) was removed.
VARIABLES outcome, kept
Terminal == {"completed", "cancelled", "failed"}
Init == outcome = "active" /\ kept = FALSE
Stage == /\ outcome = "active" /\ kept' \in BOOLEAN /\ UNCHANGED outcome
Finish == /\ outcome = "active" /\ outcome' \in Terminal
          /\ ((outcome' = "completed") <=> kept) /\ UNCHANGED kept
Spec == Init /\ [][Stage \/ Finish]_<<outcome, kept>>
====
