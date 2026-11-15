---- MODULE DialogRule ----
EXTENDS Naturals, FiniteSets
\* L04 desktop: one dialog at a time (gui.DollyApp._error_dialog_open).
\* Requesters and their policy when another dialog is open:
\*  err      _error: log and show in the status line only (drop).
\*  note     _background_dialog with retry (recovery done, replay copy saved,
\*           DOF report, startup notices): try again after a second.
\*  capture  _background_dialog without retry (replace the camera path?):
\*           drop, the user captures again.
\*  offer    _offer_session_graphics: try again after a second.
\*  conflict _ask_gameinfo_conflict: drop; Dolly asks again at the next launch.
\* Each sets the flag, shows its dialog, and clears the flag in a finally, so
\* a dialog that raises (TclError) still clears it. A modal dialog runs a
\* nested event loop: timers and poll callbacks keep firing while it is open,
\* so any requester can try at any time. After close nothing new opens.
\* Dialogs opened by a click (save prompt, busy notice) are outside this model:
\* an open modal holds the input grab, so the main window takes no clicks.
\* Bounds: each requester asks once; retries are unbounded.
CONSTANT Kinds
Retry == {"note", "offer"}
VARIABLES flag, open, closed, asked, waiting, shown, openedAfterClose
vars == <<flag, open, closed, asked, waiting, shown, openedAfterClose>>

Init == /\ flag = FALSE /\ open = {} /\ closed = FALSE /\ asked = {}
        /\ waiting = {} /\ shown = {} /\ openedAfterClose = FALSE

\* The result arrives (a worker completion, timer or poll event).
Arrive(k) == /\ k \notin asked /\ asked' = asked \cup {k} /\ waiting' = waiting \cup {k}
             /\ UNCHANGED <<flag, open, closed, shown, openedAfterClose>>
\* The requester runs: show, defer or drop according to its policy.
Try(k) == /\ k \in waiting
          /\ IF closed THEN /\ waiting' = waiting \ {k} /\ UNCHANGED <<flag, open, shown, openedAfterClose>>
             ELSE IF flag
                  THEN /\ waiting' = (IF k \in Retry THEN waiting ELSE waiting \ {k})
                       /\ UNCHANGED <<flag, open, shown, openedAfterClose>>
                  ELSE /\ flag' = TRUE /\ open' = open \cup {k} /\ shown' = shown \cup {k}
                       /\ waiting' = waiting \ {k}
                       /\ openedAfterClose' = (openedAfterClose \/ closed)
          /\ UNCHANGED <<closed, asked>>
\* The user answers it, or showing it raised: the finally clears the flag.
Dismiss(k) == /\ k \in open /\ open' = open \ {k} /\ flag' = FALSE
              /\ UNCHANGED <<closed, asked, waiting, shown, openedAfterClose>>
CloseApp == /\ ~closed /\ closed' = TRUE
            /\ UNCHANGED <<flag, open, asked, waiting, shown, openedAfterClose>>
Done == /\ waiting = {} /\ open = {} /\ UNCHANGED vars
Next == \/ \E k \in Kinds: Arrive(k) \/ Try(k) \/ Dismiss(k)
        \/ CloseApp \/ Done
\* Dialogs get answered and retry timers fire; results may never arrive and
\* the user may never close Dolly.
Spec == Init /\ [][Next]_vars /\ \A k \in Kinds: WF_vars(Try(k)) /\ WF_vars(Dismiss(k))

TypeOK == /\ flag \in BOOLEAN /\ open \subseteq Kinds /\ closed \in BOOLEAN
          /\ asked \subseteq Kinds /\ waiting \subseteq Kinds /\ shown \subseteq Kinds
          /\ openedAfterClose \in BOOLEAN
\* Never two dialogs at once (stacked native modals could leave the window disabled).
AtMostOne == Cardinality(open) <= 1
\* The flag says exactly whether a dialog is open; it never sticks.
FlagHonest == flag = (open # {})
\* Nothing opens after Dolly started closing.
NoneAfterClose == ~openedAfterClose
\* A notice that waits for an open dialog is shown once that closes, unless
\* Dolly closes first.
RetriesShow == \A k \in Retry \cap Kinds: (k \in waiting) ~> (k \in shown \/ closed)
====
