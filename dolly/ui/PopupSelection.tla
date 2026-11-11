---- MODULE PopupSelection ----
EXTENDS Naturals
\* Finite admission model of dropdown_popup.Popup._choose, installed by kit_styles.
\* Tk's single UI thread captures AB at open, then roster refresh/disable can
\* happen before the user's selection handler. DollyApp._attach_roster_changed
\* is the observed refresh caller; bone/profile comboboxes share the popup.
\* Choose's cget/instate/current calls contain no update/wait event loop; this
\* abstract step covers that synchronous validation + selection boundary.
\* Distinct lists preserve row identity; empty list models Tcl current failure.
\* No ABA identity changes with identical labels, post-enqueue roster updates,
\* focus/grab, actual pixels or Tcl lifetime failures are modeled.
\* Returning handlers and weakly fair progress are assumed, not UI/I/O liveness.
Fresh == TRUE
Enabled == TRUE
VARIABLES pc, row, live, enabled, accepted, value
vars == <<pc,row,live,enabled,accepted,value>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE PopupSelectionApi WITH phase <- phase
Init == /\ pc = "open" /\ row \in 1..2 /\ live = "AB"
        /\ enabled = TRUE /\ accepted = FALSE /\ value = "unchanged"
Refresh == /\ pc = "open" /\ live' \in {"AB","BA","BC","empty"}
           /\ enabled' \in BOOLEAN /\ pc' = "choose"
           /\ UNCHANGED <<row,accepted,value>>
Wanted == IF row = 1 THEN "A" ELSE "B"
Current == IF live = "AB" THEN Wanted
           ELSE IF live = "BA" THEN IF row = 1 THEN "B" ELSE "A"
           ELSE IF row = 1 THEN "B" ELSE "C"
Choose == /\ pc = "choose" /\ pc' = "done"
          /\ accepted' = (live # "empty" /\ (Fresh => live = "AB") /\ (Enabled => enabled))
          /\ value' = IF accepted' THEN Current ELSE value
          /\ UNCHANGED <<row,live,enabled>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Refresh \/ Choose
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"open","choose","done"} /\ row \in 1..2
          /\ live \in {"AB","BA","BC","empty"} /\ enabled \in BOOLEAN
          /\ accepted \in BOOLEAN /\ value \in {"unchanged","A","B","C"}
VisibleChoice == accepted => (live = "AB" /\ value = Wanted)
EnabledChoice == accepted => enabled
Returns == <>(pc = "done")
Refines == API!Spec
====
