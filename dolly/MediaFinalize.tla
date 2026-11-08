---- MODULE MediaFinalize ----
EXTENDS Naturals, FiniteSets
\* MediaTransport._finish_media_and_unmap, via _close_media from NativeBridge.close.
\* BridgeClose covers outer teardown; MediaAdmission covers mapping admission.
\* Serialized close: video is active or pending, audio needs stopping, ReShade
\* needs disabling. Capture pending intent before audio mutates command identity.
\* Audio/video stops can independently refuse with ordinary caught exceptions.
\* Every operation returns; status read and mapping close succeed. ReShade may
\* refuse but its attempt still precedes unmap. A video wait is collapsed to a
\* returning action (success/timeout/read error); no native ack, duration, GPU,
\* heartbeat recovery, abrupt crash, borrowed views or general caller proof.
\* API refinement describes completion shape, not actual game finalization.
VARIABLES pc,pending,intent,command,attempted,failures
vars == <<pc,pending,intent,command,attempted,failures>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE MediaFinalizeApi WITH phase <- phase
Init == /\ pc = "snapshot" /\ pending \in BOOLEAN /\ intent = FALSE
        /\ command = "startVideo" /\ attempted = {}
        /\ failures \in SUBSET {"audio","video"}
Snapshot == /\ pc = "snapshot" /\ intent' = pending /\ pc' = "audio"
            /\ UNCHANGED <<pending,command,attempted,failures>>
Audio == /\ pc = "audio" /\ command' = "stopAudio"
         /\ attempted' = attempted \cup {"audio"} /\ pc' = "video"
         /\ UNCHANGED <<pending,intent,failures>>
VideoRequired == ~pending \/ intent
Video == /\ pc = "video"
         /\ attempted' = (IF VideoRequired THEN attempted \cup {"video"} ELSE attempted)
         /\ pc' = "reshade" /\ UNCHANGED <<pending,intent,command,failures>>
Reshade == /\ pc = "reshade" /\ attempted' = attempted \cup {"reshade"}
           /\ pc' = "unmap" /\ UNCHANGED <<pending,intent,command,failures>>
Unmap == /\ pc = "unmap" /\ attempted' = attempted \cup {"unmap"}
         /\ pc' = "done" /\ UNCHANGED <<pending,intent,command,failures>>
Run == Snapshot \/ Audio \/ Video \/ Reshade \/ Unmap
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"snapshot","audio","video","reshade","unmap","done"}
          /\ pending \in BOOLEAN /\ intent \in BOOLEAN
          /\ command \in {"startVideo","stopAudio"}
          /\ attempted \subseteq {"audio","video","reshade","unmap"}
          /\ failures \subseteq {"audio","video"}
IndependentAttempts == pc = "done" => attempted = {"audio","video","reshade","unmap"}
Returns == <>(pc = "done")
Refines == API!Spec
====
