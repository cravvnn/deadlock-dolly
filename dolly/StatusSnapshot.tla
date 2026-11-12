---- MODULE StatusSnapshot ----
EXTENDS Naturals
\* Native write_status's serialized odd/body/even publication, read by
\* NativeBridge.status then _wait. Two correlated fields stand for ack/state.
\* One writer publication, one reader attempt; refusal is a valid result.
\* Sequence atomicity/coherence assumed; byte-copy fields can interleave.
\* No wrap, additional writers, C++ memory-model legality or real OS proof.
VARIABLES w,r,seq,a,b,first,ra,rb,accepted
vars == <<w,r,seq,a,b,first,ra,rb,accepted>>
Init == /\ w=0 /\ r=0 /\ seq=0 /\ a=0 /\ b=0
        /\ first=0 /\ ra=0 /\ rb=0 /\ accepted=FALSE
Odd == /\ w=0 /\ w'=1 /\ seq'=1 /\ UNCHANGED <<r,a,b,first,ra,rb,accepted>>
WriteA == /\ w=1 /\ w'=2 /\ a'=1 /\ UNCHANGED <<r,seq,b,first,ra,rb,accepted>>
WriteB == /\ w=2 /\ w'=3 /\ b'=1 /\ UNCHANGED <<r,seq,a,first,ra,rb,accepted>>
Even == /\ w=3 /\ w'=4 /\ seq'=2 /\ UNCHANGED <<r,a,b,first,ra,rb,accepted>>
First == /\ r=0 /\ r'=1 /\ first'=seq /\ UNCHANGED <<w,seq,a,b,ra,rb,accepted>>
ReadA == /\ r=1 /\ r'=2 /\ ra'=a /\ UNCHANGED <<w,seq,a,b,first,rb,accepted>>
ReadB == /\ r=2 /\ r'=3 /\ rb'=b /\ UNCHANGED <<w,seq,a,b,first,ra,accepted>>
RequireStable == first=seq
RequireEven == first # 1 /\ seq # 1
Check == /\ r=3 /\ r'=4 /\ accepted'=(RequireStable /\ RequireEven)
         /\ UNCHANGED <<w,seq,a,b,first,ra,rb>>
Run == Odd \/ WriteA \/ WriteB \/ Even \/ First \/ ReadA \/ ReadB \/ Check
Done == /\ w=4 /\ r=4 /\ UNCHANGED vars
Spec == Init /\ [][Run \/ Done]_vars /\ WF_vars(Run)
TypeOK == /\ w \in 0..4 /\ r \in 0..4 /\ seq \in 0..2
          /\ a \in 0..1 /\ b \in 0..1 /\ ra \in 0..1 /\ rb \in 0..1
          /\ first \in 0..2 /\ accepted \in BOOLEAN
CoherentRead == accepted => ra=rb
Returns == <>(w=4 /\ r=4)
====
