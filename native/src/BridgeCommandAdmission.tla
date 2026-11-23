---- MODULE BridgeCommandAdmission ----
EXTENDS Naturals
\* bridge_win worker admission of editor control snapshots (read_control,
\* validate_control, admit_control) and the main-view callback that consumes
\* gCommand and gWorkerError (on_view). The editor publishes up to MaxWrites
\* snapshots: a new command (valid or refused by the header/payload/start
\* checks, codes 31-33), or a flags-only refresh under its current command id
\* (valid or refused). The seqlock is abstracted to an atomic snapshot whose
\* sequence advances on every publication. One worker and one view callback.
\* Not modelled: payload contents, attach resolution, heartbeat/editor exit
\* (gWorkerError 30) and worker exceptions (99), which still fault the view.
MaxWrites == 3
\* A refusal is remembered by its exact snapshot (sequence and command).
RememberRefusal == TRUE
KeyBySequence == TRUE
\* A refusal is reported by leaving the new command unacknowledged, never by
\* the worker error the view treats as a lost control connection.
RefusalFaultsView == FALSE
\* A same-command flags refresh passes the same header checks.
ValidateRefresh == TRUE
VARIABLES writes, seq, slot, accepted, pub, rejected, workerError, parsedSeq, reparsed,
          ack, viewState, badFlags
vars == <<writes, seq, slot, accepted, pub, rejected, workerError, parsedSeq, reparsed,
          ack, viewState, badFlags>>
Cmds == 0..MaxWrites
Flags == 0..MaxWrites
NoSlot == [cmd |-> 0, flags |-> 0, ok |-> TRUE]
NoRefusal == [active |-> FALSE, seq |-> 0, cmd |-> 0]
Init == /\ writes = 0 /\ seq = 0 /\ slot = NoSlot /\ accepted = 0
        /\ pub = [cmd |-> 0, flags |-> 0] /\ rejected = NoRefusal /\ workerError = 0
        /\ parsedSeq = 0 /\ reparsed = FALSE /\ ack = 0 /\ viewState = "probe"
        /\ badFlags = {}
\* Editor: a new command id with fresh flags, valid or refused.
WriteCommand(ok) ==
    /\ writes < MaxWrites
    /\ writes' = writes + 1 /\ seq' = seq + 1
    /\ slot' = [cmd |-> writes + 1, flags |-> writes + 1, ok |-> ok]
    /\ badFlags' = IF ok THEN badFlags ELSE badFlags \cup {writes + 1}
    /\ UNCHANGED <<accepted, pub, rejected, workerError, parsedSeq, reparsed, ack, viewState>>
\* Editor: new flags under its current command id (confetti, seek relief).
WriteFlags(ok) ==
    /\ writes < MaxWrites /\ slot.cmd # 0
    /\ writes' = writes + 1 /\ seq' = seq + 1
    /\ slot' = [cmd |-> slot.cmd, flags |-> writes + 1, ok |-> ok]
    /\ badFlags' = IF ok THEN badFlags ELSE badFlags \cup {writes + 1}
    /\ UNCHANGED <<accepted, pub, rejected, workerError, parsedSeq, reparsed, ack, viewState>>
Remembered == /\ rejected.active
              /\ IF KeyBySequence THEN rejected.seq = seq ELSE TRUE
              /\ rejected.cmd = slot.cmd
\* read_control: an odd/unchanged snapshot, the acknowledged command with
\* unchanged flags, or a remembered refusal is not copied or parsed.
Skip == \/ slot.cmd = 0
        \/ slot.cmd = accepted /\ slot.flags = pub.flags
        \/ Remembered
Refuse == IF slot.cmd = accepted /\ ~ValidateRefresh THEN FALSE ELSE ~slot.ok
Worker ==
    /\ ~Skip
    /\ reparsed' = (reparsed \/ parsedSeq = seq)
    /\ parsedSeq' = seq
    /\ IF Refuse
          THEN /\ rejected' = IF RememberRefusal
                                 THEN [active |-> TRUE, seq |-> seq, cmd |-> slot.cmd]
                                 ELSE rejected
               /\ workerError' = IF RefusalFaultsView THEN 1 ELSE workerError
               /\ UNCHANGED <<accepted, pub>>
          ELSE /\ rejected' = NoRefusal
               /\ IF slot.cmd = accepted
                     THEN /\ pub' = [pub EXCEPT !.flags = slot.flags]
                          /\ UNCHANGED <<accepted, workerError>>
                     ELSE /\ accepted' = slot.cmd
                          /\ pub' = [cmd |-> slot.cmd, flags |-> slot.flags]
                          /\ workerError' = 0
    /\ UNCHANGED <<writes, seq, slot, ack, viewState, badFlags>>
\* on_view: a new command is acknowledged; any worker error releases the
\* camera as "Dolly control connection expired".
View ==
    /\ pub.cmd # 0
    /\ \/ pub.cmd # ack
       \/ workerError # 0 /\ viewState # "fault"
    /\ ack' = pub.cmd
    /\ viewState' = IF workerError # 0 THEN "fault" ELSE "running"
    /\ UNCHANGED <<writes, seq, slot, accepted, pub, rejected, workerError, parsedSeq, reparsed,
                   badFlags>>
Done == /\ writes = MaxWrites /\ UNCHANGED vars
Next == \/ \E ok \in BOOLEAN : WriteCommand(ok) \/ WriteFlags(ok)
        \/ Worker \/ View \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Worker) /\ WF_vars(View)
TypeOK == /\ writes \in 0..MaxWrites /\ seq \in 0..MaxWrites
          /\ slot \in [cmd : Cmds, flags : Flags, ok : BOOLEAN] /\ accepted \in Cmds
          /\ pub \in [cmd : Cmds, flags : Flags]
          /\ rejected \in [active : BOOLEAN, seq : 0..MaxWrites, cmd : Cmds]
          /\ workerError \in {0, 1} /\ parsedSeq \in 0..MaxWrites /\ reparsed \in BOOLEAN
          /\ ack \in Cmds /\ viewState \in {"probe", "running", "fault"}
          /\ badFlags \subseteq Flags
\* No worker failure is modelled, so the view never releases a command it
\* acknowledged: a refused snapshot leaves the running camera alone.
RejectedNeverFaultsAcked == viewState # "fault"
\* Each publication is copied and parsed at most once (bounded by MaxWrites).
RejectionNotReparsed == ~reparsed
\* A flags refresh keeps the admitted command id and never publishes flags
\* that failed the checks.
FlagsRefreshKeepsIdentity == /\ pub.cmd = accepted
                             /\ pub.flags \notin badFlags
\* Once the editor stops, a valid last snapshot is admitted and acknowledged.
Settles == <>[](writes = MaxWrites /\ slot.ok => ack = slot.cmd /\ pub.flags = slot.flags)
====
