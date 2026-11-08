---- MODULE ReaderTerminal ----
EXTENDS Naturals
\* ReplayEventReader._read, worker created by snapshot and consumed by
\* editor_publication.configure. ReaderStart separately covers worker admission.
\* One current identity, one returning decode: success, ValueError or
\* RecursionError. Decode and terminal publication are separate actions.
\* Helper I/O, arbitrary exceptions, identity replacement, hangs/crashes and
\* successful payload validation are excluded; actual owner tests cover bytes
\* and stale-result rejection. API refinement is completion shape only.
VARIABLES pc,kind,result,status
vars == <<pc,kind,result,status>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ReaderTerminalApi WITH phase <- phase
Init == /\ pc = "decode" /\ kind \in {"success","value","recursion"}
        /\ result = "none" /\ status = "loading"
Decode == /\ pc = "decode" /\ pc' = "publish"
          /\ result' = (IF kind = "success" THEN "complete" ELSE "failed")
          /\ UNCHANGED <<kind,status>>
Publish == /\ pc = "publish" /\ pc' = "done" /\ status' = result
           /\ UNCHANGED <<kind,result>>
Run == Decode \/ Publish
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"decode","publish","done"}
          /\ kind \in {"success","value","recursion"}
          /\ result \in {"none","complete","failed"}
          /\ status \in {"loading","complete","failed"}
TerminalResult == pc = "done" => status = (IF kind = "success" THEN "complete" ELSE "failed")
Returns == <>(pc = "done")
Refines == API!Spec
====
