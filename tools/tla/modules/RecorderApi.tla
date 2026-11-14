---- MODULE RecorderApi ----
\* Caller-visible recording outcome: the native recorder's terminal state
\* (dolly_video_win.cpp state store, read by VideoExport/crav_capture) and
\* whether the final artifact (MP4/trace plus sidecars) was kept on disk.
\* Shared module because its refinements and callers live in different
\* directories: CameraOnlyCapture (native/src) refines it; DollyRoot (dolly)
\* composes it. Stage: files may be published or removed while active.
\* Finish: the terminal state agrees with the files (completed <=> kept).
\* GapFinish: the documented exception. A capture-thread fail() (GetBuffer loss,
\* resolution change) can land after the encoder's single decision, outside the
\* producer gate, giving failed beside a kept file. Callers choose whether to
\* admit it via AllowLateCaptureFault; it is not hidden.
CONSTANT AllowLateCaptureFault
VARIABLES outcome, kept
Terminal == {"completed", "cancelled", "failed"}
Init == outcome = "active" /\ kept = FALSE
Stage == /\ outcome = "active" /\ kept' \in BOOLEAN /\ UNCHANGED outcome
Finish == /\ outcome = "active" /\ outcome' \in Terminal
          /\ ((outcome' = "completed") <=> kept) /\ UNCHANGED kept
GapFinish == /\ AllowLateCaptureFault /\ outcome = "active" /\ kept
             /\ outcome' = "failed" /\ UNCHANGED kept
Spec == Init /\ [][Stage \/ Finish \/ GapFinish]_<<outcome, kept>>
====
