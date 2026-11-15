---- MODULE CameraSessionModes ----
EXTENDS Naturals
\* Controller camera modes around Controller._halt (controller.py), closing
\* CameraSessionJoin's exclusions: free flight (manual, no worker), a played
\* shot whose worker (_run_native) finishes by itself, and Stop/Pause racing
\* that self-finish. One caller thread holds _op_lock for Play, EnterFlight and
\* Halt; the worker never takes it.
\*
\* Worker: _run_native polls until the native path completes (Complete) or
\* _stop_event is set (StopSeen), then _finish_native_playback runs on the
\* worker. A naturally finished shot with resume_free_camera re-arms free
\* flight through start_flight, only while _native_active (FinishRearm);
\* otherwise it holds/hands off the camera (FinishHold).
\* Halt: sets _stop_event, joins the worker for up to 10 s. A timeout raises
\* before self._thread is cleared, so a retry joins the same worker again.
\* Then release (Stop) or hold/handoff (Pause, flight hold). A manual camera
\* whose native path already stopped is released instead of held.
\* Native may stop/fault by itself (NativeDown) while the lease is kept.
\* Release acknowledgement/refusal is ReleaseJoin's; here it is acknowledged.
\* Bounded: two Play/EnterFlight operations. Engine threading, console I/O
\* and the recorder are outside this model.
CONSTANT JoinMayTimeout
VARIABLES wpc, finished, resume, stopEv, thread, lease, manual, writer,
          cpc, action, ops
vars == <<wpc, finished, resume, stopEv, thread, lease, manual, writer, cpc, action, ops>>
Retirement == INSTANCE CameraRetirementApi WITH retired <- ~lease,
    quiet <- wpc \in {"none", "done"}, evidence <- writer = "none"

Init == /\ wpc = "none" /\ finished = FALSE /\ resume = FALSE /\ stopEv = FALSE
        /\ thread = FALSE /\ lease = FALSE /\ manual = FALSE /\ writer = "none"
        /\ cpc = "idle" /\ action = "release" /\ ops = 0

CallerFree == cpc \in {"idle", "done"}
\* play(): _stop_before_new_shot has already retired the previous camera.
Play == /\ CallerFree /\ ops < 2 /\ ~thread /\ wpc \in {"none", "done"}
        /\ ~lease /\ writer = "none"
        /\ ops' = ops + 1 /\ lease' = TRUE /\ writer' = "shot" /\ manual' = FALSE
        /\ wpc' = "playing" /\ thread' = TRUE /\ stopEv' = FALSE /\ finished' = FALSE
        /\ resume' \in BOOLEAN /\ cpc' = "idle"
        /\ UNCHANGED action
\* enter_native_flight from a retired camera: free flight, no worker.
EnterFlight == /\ CallerFree /\ ops < 2 /\ ~thread /\ wpc \in {"none", "done"}
               /\ ~lease /\ writer = "none"
               /\ ops' = ops + 1 /\ lease' = TRUE /\ manual' = TRUE /\ writer' = "manual"
               /\ cpc' = "idle"
               /\ UNCHANGED <<wpc, finished, resume, stopEv, thread, action>>

Complete == /\ wpc = "playing" /\ ~stopEv /\ wpc' = "finishing" /\ finished' = TRUE
            /\ UNCHANGED <<resume, stopEv, thread, lease, manual, writer, cpc, action, ops>>
StopSeen == /\ wpc = "playing" /\ stopEv /\ wpc' = "finishing"
            /\ UNCHANGED <<finished, resume, stopEv, thread, lease, manual, writer, cpc, action, ops>>
FinishRearm == /\ wpc = "finishing" /\ finished /\ resume /\ lease
               /\ writer' = "manual" /\ manual' = TRUE /\ wpc' = "done"
               /\ UNCHANGED <<finished, resume, stopEv, thread, lease, cpc, action, ops>>
FinishHold == /\ wpc = "finishing" /\ ~(finished /\ resume /\ lease)
              /\ writer' = IF writer = "shot" THEN "held" ELSE writer
              /\ wpc' = "done"
              /\ UNCHANGED <<finished, resume, stopEv, thread, lease, manual, cpc, action, ops>>
NativeDown == /\ lease /\ writer # "none" /\ writer' = "none"
              /\ UNCHANGED <<wpc, finished, resume, stopEv, thread, lease, manual, cpc, action, ops>>

HaltStart(a) == /\ cpc \in {"idle", "done", "failed"} /\ (thread \/ lease)
                /\ cpc' = "joining" /\ action' = a /\ stopEv' = TRUE
                /\ UNCHANGED <<wpc, finished, resume, thread, lease, manual, writer, ops>>
JoinOk == /\ cpc = "joining" /\ (~thread \/ wpc = "done")
          /\ thread' = FALSE /\ cpc' = "acting"
          /\ UNCHANGED <<wpc, finished, resume, stopEv, lease, manual, writer, action, ops>>
JoinTimeout == /\ JoinMayTimeout /\ cpc = "joining" /\ thread /\ wpc # "done"
               /\ cpc' = "failed"
               /\ UNCHANGED <<wpc, finished, resume, stopEv, thread, lease, manual, writer, action, ops>>
\* _release_native_camera: the lease ends on the acknowledged release.
ActRelease == /\ cpc = "acting" /\ action = "release"
              /\ writer' = "none" /\ lease' = FALSE /\ manual' = FALSE /\ cpc' = "done"
              /\ UNCHANGED <<wpc, finished, resume, stopEv, thread, action, ops>>
\* native_hold/handoff: a manual camera whose native path stopped is released;
\* otherwise a live camera is held and the lease kept.
ActHold == /\ cpc = "acting" /\ action = "hold" /\ cpc' = "done"
           /\ IF lease /\ manual /\ writer = "none"
                 THEN lease' = FALSE /\ manual' = FALSE /\ UNCHANGED writer
                 ELSE writer' = (IF writer = "none" THEN "none" ELSE "held")
                      /\ UNCHANGED <<lease, manual>>
           /\ UNCHANGED <<wpc, finished, resume, stopEv, thread, action, ops>>

Worker == Complete \/ StopSeen \/ FinishRearm \/ FinishHold
Caller == JoinOk \/ JoinTimeout \/ ActRelease \/ ActHold
Done == /\ CallerFree /\ wpc \in {"none", "done"} /\ UNCHANGED vars
Next == Play \/ EnterFlight \/ Worker \/ NativeDown \/ HaltStart("release")
        \/ HaltStart("hold") \/ Caller \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Worker) /\ WF_vars(Caller)

TypeOK == /\ wpc \in {"none", "playing", "finishing", "done"}
          /\ finished \in BOOLEAN /\ resume \in BOOLEAN /\ stopEv \in BOOLEAN
          /\ thread \in BOOLEAN /\ lease \in BOOLEAN /\ manual \in BOOLEAN
          /\ writer \in {"none", "shot", "manual", "held"}
          /\ cpc \in {"idle", "joining", "acting", "done", "failed"}
          /\ action \in {"release", "hold"} /\ ops \in 0..2
\* No camera writer without the controller's lease.
WriterOwned == writer # "none" => lease
\* A returned Stop leaves no lease, no writer and no live worker.
StopRetires == (cpc = "done" /\ action = "release") =>
               (~lease /\ writer = "none" /\ wpc \in {"none", "done"})
\* A returned hold leaves no moving camera.
HoldFreezes == (cpc = "done" /\ action = "hold") => writer \in {"none", "held"}
\* A lease ends only with a quiet worker.
QuietWhenRetired == ~lease => wpc \in {"none", "done"}
\* Every halt returns or reports the timeout.
HaltReturns == cpc = "joining" ~> cpc \in {"done", "failed"}
\* The worker observes _stop_event and finishes (the join can then succeed).
WorkerQuiets == stopEv ~> wpc \in {"none", "done"}
\* Required success (no join timeout): every Stop completes.
StopCompletes == (cpc = "joining" /\ action = "release") ~> cpc = "done"
RefinesRetirement == Retirement!Spec
====
