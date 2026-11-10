---- MODULE AudioWorkRetirement ----
EXTENDS Naturals
\* Production-only settings; matched baseline comparison stays private.
\* API checks minimal returning completion shape, not full caller refinement.
\* ClipAudioCapture.finish/_finish_movie after _mux successfully published, or
\* cancel after an independent source stop error. GUI _stop_video_recording's
\* worker consumes the return/exception after clearing its audio reference.
\* Three paths, returning cleanup, ordinary sharing/permission refusal only.
\* No actual encoder/game/PCM/stop authority, peer namespace, arbitrary crash,
\* successful retry or I/O liveness modeled. Model starts AFTER primary outcome;
\* it does not prove the mux or source stop. Win32 lock/real TemporaryDirectory
\* probes and real PCM fixtures bind the narrow cleanup edge. No new state flag.
\* Deliberate terminal stutter; deadlock checks enabled. Bounded safety projection.
Candidate == TRUE
VARIABLES path,locked,pc,primary,reported,work
vars == <<path,locked,pc,primary,reported,work>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE AudioWorkRetirementApi WITH phase <- phase
Init == /\ path \in {"fixed","loopback","cancel"} /\ locked \in BOOLEAN
        /\ pc = "cleanup" /\ work = TRUE
        /\ primary = (IF path = "cancel" THEN "stop_error" ELSE "completed")
        /\ reported = primary
Cleanup == /\ pc = "cleanup" /\ pc' = "done" /\ work' = locked
           /\ reported' = (IF locked /\ ~Candidate THEN "cleanup_error" ELSE primary)
           /\ UNCHANGED <<path,locked,primary>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Cleanup \/ Done
TypeOK == /\ path \in {"fixed","loopback","cancel"} /\ locked \in BOOLEAN
          /\ pc \in {"cleanup","done"} /\ work \in BOOLEAN
          /\ primary \in {"stop_error","completed"}
          /\ reported \in {"stop_error","completed","cleanup_error"}
PrimaryKept == pc = "done" => reported = primary
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
