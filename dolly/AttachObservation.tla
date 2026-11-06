---- MODULE AttachObservation ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* editor_session.poll consumes editor_attach_result and invokes the GUI's
\* _attach_result_changed; the latter still checks selected key and request.
\* Two distinct bridges, two nonzero sequences, four serialized polls. Model
\* last-observation deduplication, not a per-bridge history or native protocol.
\* Result parsing/config coherence, GUI edits, zero sequences and reconnect
\* readiness are actual code probes, outside this admission-only model.
Pairs == {<<b,s>> : b \in 1..2,s \in 1..2}
VARIABLES calls,memo,last,actual,expected
vars == <<calls,memo,last,actual,expected>>
phase == IF calls = 4 THEN "done" ELSE "active"
API == INSTANCE AttachObservationApi WITH phase <- phase

Init == /\ calls = 0 /\ memo = <<0,0>>
        /\ last = <<0,0>> /\ actual = FALSE /\ expected = FALSE
Poll(b,s) == /\ calls < 4 /\ calls' = calls + 1
             /\ actual' = (<<b,s>> # memo)
             /\ expected' = (<<b,s>> # last) /\ last' = <<b,s>>
             /\ memo' = <<b,s>>
Run == \E b \in 1..2,s \in 1..2 : Poll(b,s)
Done == /\ calls = 4 /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ calls \in 0..4 /\ memo \in {<<b,s>> : b \in 0..2,s \in 0..2}
          /\ last \in (Pairs \cup {<<0,0>>}) /\ actual \in BOOLEAN /\ expected \in BOOLEAN
CorrectAdmission == actual = expected
Returns == <>(calls = 4)

Refines == API!Spec
====
