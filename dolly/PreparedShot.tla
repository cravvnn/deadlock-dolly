---- MODULE PreparedShot ----
EXTENDS Naturals
\* Controller._prepare_native_shot_replay consumes one reserved token before
\* _wait_for_recorder_ready. NativeWorkflows probes exercise actual owner calls.
\* Two prepared/current identities abstract session/replay/tick/paused checks;
\* identity is fixed during this operation under the controller operation lock.
\* Ordinary recorder only; Players marker-ack branch excluded. No GPU frames,
\* native callback or recorder failure AFTER admission. Deadline/cancel modeled
\* as returning refusal, with fair monotonic timeout; no actual I/O liveness.
CheckIdentity == TRUE
CheckReady == TRUE
VARIABLES pc, identity, recorder, consumed
vars == <<pc,identity,recorder,consumed>>
phase == IF pc = "admitted" THEN "started" ELSE IF pc = "refused" THEN "refused" ELSE "pending"
started == pc = "admitted"
permitted == identity = "current" /\ recorder = "recording"
API == INSTANCE PreparedShotApi WITH phase <- phase, started <- started, permitted <- permitted
Init == /\ pc = "check" /\ identity \in {"current","foreign"}
        /\ recorder = "starting" /\ consumed = FALSE
Consume == /\ pc = "check" /\ consumed' = TRUE
           /\ pc' = IF CheckIdentity /\ identity # "current" THEN "refused" ELSE "wait"
           /\ UNCHANGED <<identity,recorder>>
RecorderObservation == /\ pc = "wait" /\ recorder = "starting"
                       /\ recorder' \in {"recording","failed","idle"}
                       /\ UNCHANGED <<pc,identity,consumed>>
Admit == /\ pc = "wait" /\ (CheckReady => recorder = "recording")
         /\ pc' = "admitted" /\ UNCHANGED <<identity,recorder,consumed>>
Fail == /\ pc = "wait" /\ recorder \in {"failed","idle"}
        /\ pc' = "refused" /\ UNCHANGED <<identity,recorder,consumed>>
TimeoutCancel == /\ pc = "wait" /\ pc' = "refused" /\ UNCHANGED <<identity,recorder,consumed>>
Done == /\ pc \in {"admitted","refused"} /\ UNCHANGED vars
Next == Consume \/ RecorderObservation \/ Admit \/ Fail \/ TimeoutCancel \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Consume) /\ WF_vars(RecorderObservation)
        /\ WF_vars(Admit) /\ WF_vars(Fail) /\ WF_vars(TimeoutCancel)
TypeOK == /\ pc \in {"check","wait","admitted","refused"}
          /\ identity \in {"current","foreign"} /\ recorder \in {"starting","recording","failed","idle"}
          /\ consumed \in BOOLEAN
OwnPreparedTake == pc = "admitted" => identity = "current"
ReadyBeforeStart == pc = "admitted" => recorder = "recording"
ConsumedBeforeWait == pc # "check" => consumed
Returns == <>(pc \in {"admitted","refused"})
Refines == API!Spec
====
