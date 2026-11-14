---- MODULE StatusSnapshot ----
EXTENDS Naturals
\* Native write_status's serialized odd/body/even publication, read by
\* NativeBridge.status then _wait. Two correlated fields stand for ack/state.
\* One writer publication; the reader polls. One read attempt stands for one
\* status() call: its eight bounded rereads all seeing odd/torn sequences raise
\* NativeStatusBusy. _wait catches only that busy refusal and polls again until
\* its deadline (Expire, an environment step with no fairness), then raises its
\* timeout error. Other status errors (closed, pid, ABI, magic) are not modeled;
\* they still end the wait. Sequence atomicity/coherence assumed; byte-copy
\* fields can interleave. No wrap, additional writers, C++ memory-model legality,
\* real clock duration or real OS proof. Direct status() callers are excluded.
BusyRetries == TRUE
VARIABLES w,r,seq,a,b,first,ra,rb,accepted,result
vars == <<w,r,seq,a,b,first,ra,rb,accepted,result>>
Init == /\ w=0 /\ r=0 /\ seq=0 /\ a=0 /\ b=0
        /\ first=0 /\ ra=0 /\ rb=0 /\ accepted=FALSE /\ result="pending"
Odd == /\ w=0 /\ w'=1 /\ seq'=1 /\ UNCHANGED <<r,a,b,first,ra,rb,accepted,result>>
WriteA == /\ w=1 /\ w'=2 /\ a'=1 /\ UNCHANGED <<r,seq,b,first,ra,rb,accepted,result>>
WriteB == /\ w=2 /\ w'=3 /\ b'=1 /\ UNCHANGED <<r,seq,a,first,ra,rb,accepted,result>>
Even == /\ w=3 /\ w'=4 /\ seq'=2 /\ UNCHANGED <<r,a,b,first,ra,rb,accepted,result>>
First == /\ r=0 /\ result="pending" /\ r'=1 /\ first'=seq
         /\ UNCHANGED <<w,seq,a,b,ra,rb,accepted,result>>
ReadA == /\ r=1 /\ r'=2 /\ ra'=a /\ UNCHANGED <<w,seq,a,b,first,rb,accepted,result>>
ReadB == /\ r=2 /\ r'=3 /\ rb'=b /\ UNCHANGED <<w,seq,a,b,first,ra,accepted,result>>
RequireStable == first=seq
RequireEven == first # 1 /\ seq # 1
Coherent == RequireStable /\ RequireEven
\* status() accepts, or raises NativeStatusBusy; _wait polls again on busy.
Check == /\ r=3 /\ accepted'=Coherent
         /\ IF Coherent THEN r'=4 /\ result'="accepted"
            ELSE IF BusyRetries THEN r'=0 /\ result'="pending"
            ELSE r'=4 /\ result'="busy"
         /\ UNCHANGED <<w,seq,a,b,first,ra,rb>>
\* _wait's remaining <= 0 check after a refused poll raises its timeout error.
Expire == /\ r=0 /\ result="pending" /\ r'=4 /\ result'="timeout"
          /\ UNCHANGED <<w,seq,a,b,first,ra,rb,accepted>>
Write == Odd \/ WriteA \/ WriteB \/ Even
Read == First \/ ReadA \/ ReadB \/ Check
Done == /\ w=4 /\ r=4 /\ UNCHANGED vars
Spec == Init /\ [][Write \/ Read \/ Expire \/ Done]_vars
        /\ WF_vars(Write) /\ WF_vars(Read)
TypeOK == /\ w \in 0..4 /\ r \in 0..4 /\ seq \in 0..2
          /\ a \in 0..1 /\ b \in 0..1 /\ ra \in 0..1 /\ rb \in 0..1
          /\ first \in 0..2 /\ accepted \in BOOLEAN
          /\ result \in {"pending","accepted","busy","timeout"}
CoherentRead == accepted => ra=rb
Returns == <>(w=4 /\ r=4)
\* A writer that finishes yields an accepted snapshot unless the deadline fires.
AcceptsBeforeDeadline == [](result # "timeout") => <>(result = "accepted")
====
