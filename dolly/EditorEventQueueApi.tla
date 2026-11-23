---- MODULE EditorEventQueueApi ----
EXTENDS Naturals
\* Ordered acknowledgement shape only: each step acknowledges the next action
\* of one bridge's queue. Not a whole-caller, handler or game claim.
CONSTANT B
VARIABLE acked
Spec == /\ acked = [b \in 1..B |-> 0]
        /\ [][\E b \in 1..B : acked' = [acked EXCEPT ![b] = @ + 1]]_acked
====
