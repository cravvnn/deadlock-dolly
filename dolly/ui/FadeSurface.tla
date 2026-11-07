---- MODULE FadeSurface ----
EXTENDS Naturals
\* Win32Backend._surface, called by update via ScrollFade.bitmap/_want.
\* One provisional DC/bitmap, checked allocation/bits/selection and handle admission.
\* Rollback coalesces returning DeleteObject/DeleteDC; assume deletion succeeds.
\* No crash during cleanup, dict-update MemoryError, concurrent calls or GPU/game model.
\* Finite safety/completion-shape contract; deadlock checking disabled at terminal states.
VARIABLES pc, dc, bitmap, selected
vars == <<pc, dc, bitmap, selected>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE FadeSurfaceApi WITH phase <- phase
Init == /\ pc = "dc" /\ dc = "none" /\ bitmap = "none" /\ selected = FALSE
DC(ok) == /\ pc = "dc" /\ dc' = (IF ok THEN "local" ELSE "none")
          /\ pc' = (IF ok THEN "bitmap" ELSE "done") /\ UNCHANGED <<bitmap, selected>>
Bitmap(ok) == /\ pc = "bitmap" /\ bitmap' = (IF ok THEN "local" ELSE "none")
              /\ pc' = (IF ok THEN "bits" ELSE "failed") /\ UNCHANGED <<dc, selected>>
Bits(ok) == /\ pc = "bits" /\ pc' = (IF ok THEN "select" ELSE "failed")
            /\ UNCHANGED <<dc, bitmap, selected>>
Select(ok) == /\ pc = "select" /\ selected' = ok
              /\ pc' = IF ok THEN "admit" ELSE "failed"
              /\ UNCHANGED <<dc, bitmap>>
Rollback == /\ pc = "failed" /\ pc' = "done"
            /\ dc' = "none"
            /\ bitmap' = "none"
            /\ UNCHANGED selected
Admit == /\ pc = "admit" /\ pc' = "owned" /\ dc' = "handle" /\ bitmap' = "handle" /\ UNCHANGED selected
Free == /\ pc = "owned" /\ pc' = "done" /\ dc' = "none" /\ bitmap' = "none" /\ selected' = FALSE
Next == DC(TRUE) \/ DC(FALSE) \/ Bitmap(TRUE) \/ Bitmap(FALSE) \/ Bits(TRUE) \/ Bits(FALSE)
        \/ Select(TRUE) \/ Select(FALSE) \/ Rollback \/ Admit \/ Free
TypeOK == /\ pc \in {"dc","bitmap","bits","select","failed","admit","owned","done"}
          /\ dc \in {"none","local","handle"} /\ bitmap \in {"none","local","handle"} /\ selected \in BOOLEAN
NoLeaks == pc = "done" => dc = "none" /\ bitmap = "none"
Usable == pc = "owned" => selected
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
