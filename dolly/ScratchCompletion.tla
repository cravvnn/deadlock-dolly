---- MODULE ScratchCompletion ----
EXTENDS Naturals
\* Production-only model; private baseline comparisons are not shipped.
\* Completion API is local, not a whole-caller/game or whole-program claim.

\* player_layer.cleanup_capture after sealed bundle conversion, from GUI video
\* and screenshot_ui.finish_player_capture. Known raw intermediates are gone.
\* Four terminal calls: completed manifest update, two unlinks, rmdir.
\* Returning OSError at one chosen point; a foreign sibling skips deletion.
\* Report leftover rather than abort successful caller. Partial metadata and
\* missing ownership after late rmdir failure are allowed, not retry guarantees.
\* No active capture, namespace attack, write durability or non-OSError policy.
VARIABLES pc,fault,foreign,result,kept
vars == <<pc,fault,foreign,result,kept>>
phase == IF pc = 5 THEN "done" ELSE "active"
API == INSTANCE ScratchCompletionApi WITH phase <- phase

Init == /\ pc = 1 /\ fault \in 0..4 /\ foreign \in BOOLEAN
        /\ result = "running" /\ kept = foreign
Terminal == /\ pc \in 1..4
            /\ pc' = IF fault = pc \/ (foreign /\ pc = 2) THEN 5 ELSE pc + 1
            /\ result' = IF fault = pc THEN "leftover"
                          ELSE IF (foreign /\ pc = 2) \/ pc = 4 THEN "ok" ELSE result
            /\ UNCHANGED <<fault,foreign,kept>>
Done == /\ pc = 5 /\ UNCHANGED vars
Next == Terminal \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Terminal)
TypeOK == /\ pc \in 1..5 /\ fault \in 0..4 /\ foreign \in BOOLEAN /\ kept \in BOOLEAN
          /\ result \in {"running","ok","leftover","exception"}
ForeignKept == kept = foreign
ReportCleanup == pc = 5 => result \in {"ok","leftover"}
Returns == <>(pc = 5)

Refines == API!Spec
====
