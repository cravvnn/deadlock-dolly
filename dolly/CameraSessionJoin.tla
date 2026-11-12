---- MODULE CameraSessionJoin ----
EXTENDS Naturals
\* Controller._halt -> worker.join -> _release_native_camera -> new caller.
\* _finish_native_playback's completed Players branch precedes explicit Stop.
\* Actual callers: stop, play/_stop_before_new_shot, enter_native_flight,
\* disconnect, recover_dead_session. Serialized owner operation, two generations.
\* A join may refuse once; a release may refuse once; retry must retain lease.
\* Native release abstracts a returning matching restoration ack, NOT its proof.
\* Initial worker is non-current-thread: self-halt and concurrent caller races
\* excluded. Process absence is fixed per run; no PID reuse/reconnection modeled.
\* Natural Players completion only: pending readbacks keep restoration deferred.
\* Explicit Stop may cancel; no drain correctness, pixels, GPU or real I/O liveness.
VARIABLES pc,workerLive,lease,alive,ack,attempt,joinFailed,releaseFailed,
          generation,playing,pending,restoreAttempted
vars == <<pc,workerLive,lease,alive,ack,attempt,joinFailed,releaseFailed,
          generation,playing,pending,restoreAttempted>>
API == INSTANCE CameraRetirementApi WITH retired <- ~lease,
       quiet <- ~workerLive,evidence <- ack \/ ~alive
Init == /\ pc = "completion" /\ workerLive = TRUE /\ lease = TRUE
        /\ alive \in BOOLEAN /\ ack = FALSE /\ attempt = 0
        /\ joinFailed = FALSE /\ releaseFailed = FALSE /\ generation = 1
        /\ playing = TRUE /\ pending \in BOOLEAN /\ restoreAttempted = FALSE
Completion == /\ pc = "completion" /\ playing' = FALSE
              /\ restoreAttempted' = ~pending /\ pc' = "join"
              /\ UNCHANGED <<workerLive,lease,alive,ack,attempt,joinFailed,
                             releaseFailed,generation,pending>>
Join == /\ pc = "join" /\ workerLive' \in (IF workerLive THEN BOOLEAN ELSE {FALSE})
        /\ pc' = IF workerLive' THEN "refused" ELSE "release"
        /\ joinFailed' = workerLive'
        /\ UNCHANGED <<lease,alive,ack,attempt,releaseFailed,generation,
                        playing,pending,restoreAttempted>>
Release == /\ pc = "release" /\ ack' \in BOOLEAN
           /\ lease' = (alive /\ ~ack')
           /\ releaseFailed' = lease' /\ pc' = IF lease' THEN "refused" ELSE "replace"
           /\ UNCHANGED <<workerLive,alive,attempt,joinFailed,generation,
                           playing,pending,restoreAttempted>>
Retry == /\ pc = "refused" /\ attempt = 0 /\ attempt' = 1 /\ pc' = "join"
         /\ UNCHANGED <<workerLive,lease,alive,ack,joinFailed,releaseFailed,
                         generation,playing,pending,restoreAttempted>>
Replace == /\ pc = "replace" /\ generation' = 2 /\ pc' = "done"
           /\ UNCHANGED <<workerLive,lease,alive,ack,attempt,joinFailed,
                           releaseFailed,playing,pending,restoreAttempted>>
Done == /\ (pc = "done" \/ (pc = "refused" /\ attempt = 1)) /\ UNCHANGED vars
Run == Completion \/ Join \/ Release \/ Retry \/ Replace
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"completion","join","release","refused","replace","done"}
          /\ workerLive \in BOOLEAN /\ lease \in BOOLEAN /\ alive \in BOOLEAN
          /\ ack \in BOOLEAN /\ attempt \in {0,1} /\ generation \in {1,2}
          /\ joinFailed \in BOOLEAN /\ releaseFailed \in BOOLEAN
          /\ playing \in BOOLEAN /\ pending \in BOOLEAN /\ restoreAttempted \in BOOLEAN
QuietBeforeRelease == ~lease => ~workerLive
ReleaseEvidence == ~lease => (ack \/ ~alive)
NoReplacementWriter == generation = 2 => (~lease /\ ~workerLive)
PendingCaptureHeld == pending => ~restoreAttempted
JoinRefusalRetains == pc = "refused" /\ joinFailed => lease
ReturnsOrRetains == <>(pc = "done" \/ (pc = "refused" /\ attempt = 1))
Refines == API!Spec
\* A required successful environment: join returns quiet and Release succeeds.
\* This companion profile prevents safety-only checks hiding an absent success path.
SuccessfulJoin == Join /\ ~workerLive'
SuccessfulRelease == Release /\ ack'
HappyRun == Completion \/ SuccessfulJoin \/ SuccessfulRelease \/ Replace
HappySpec == Init /\ alive = TRUE /\ [][HappyRun \/ Done]_vars /\ WF_vars(HappyRun)
HappyCompletes == <>(pc = "done" /\ generation = 2)
====
