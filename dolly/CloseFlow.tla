---- MODULE CloseFlow ----
\* L04 desktop: closing Dolly. gui.DollyApp._on_close (the window's X, or
\* UpdateUI.restart with discard_confirmed after its own _allow_discard),
\* _finish_close, _close_after_failure (reached from _poll_once's error branch
\* while _closing), and controller.close on the worker.
\* Close: if playing, Stop runs on the worker first; then the unsaved-changes
\* question (_allow_discard) unless already answered for this close; then
\* controller.close on the worker, whose completion destroys the window.
\* A failed Stop goes to _close_after_failure, which asks the same question if
\* it was not asked yet. A failed controller.close also lands there: it can
\* fail every time (a pending gameinfo conflict raises on every restore), so
\* once it has run Dolly exits instead of resubmitting it (_close_submitted);
\* its cleanup handoff already ran in its finally block.
\* The question: a clean shot passes; otherwise Save (succeeds or fails or is
\* cancelled), Don't save, or Cancel. Cancel or a failed save keeps Dolly open.
\* Abstractions: Stop and close outcomes are fixed per behavior; the user can
\* edit the shot while idle or while Stop runs, not after consent while the
\* window tears down. A rejected submit's direct close is the same as a
\* worker close. Busy refusal, dialogs and timers are outside this model.
VARIABLES ui, closing, asked, submitted, dirty, consent, playing, stopFails, closeFails, failedOnce
vars == <<ui, closing, asked, submitted, dirty, consent, playing, stopFails, closeFails, failedOnce>>

Init == /\ ui = "idle" /\ closing = FALSE /\ asked = FALSE /\ submitted = FALSE
        /\ dirty \in BOOLEAN /\ consent = FALSE /\ playing \in BOOLEAN
        /\ stopFails \in BOOLEAN /\ closeFails \in {"never", "once", "always"} /\ failedOnce = FALSE

\* The user edits the shot.
Edit == /\ ui \in {"idle", "stopping"} /\ dirty' = TRUE /\ consent' = FALSE
        /\ UNCHANGED <<ui, closing, asked, submitted, playing, stopFails, closeFails, failedOnce>>
\* The window's X.
ClickClose == /\ ui = "idle" /\ closing' = TRUE /\ asked' = FALSE /\ submitted' = FALSE
              /\ ui' = IF playing THEN "stopping" ELSE "finish"
              /\ UNCHANGED <<dirty, consent, playing, stopFails, closeFails, failedOnce>>
\* UpdateUI.restart: its own _allow_discard (refused while playing) answers
\* first; only a yes starts the close, with the answer carried along.
RestartClose == /\ ui = "idle" /\ ~playing
                /\ \/ /\ ~dirty /\ UNCHANGED <<dirty, consent>>
                   \/ /\ dirty /\ consent' = TRUE /\ UNCHANGED dirty
                   \/ /\ dirty /\ dirty' = FALSE /\ UNCHANGED consent
                /\ closing' = TRUE /\ asked' = TRUE /\ submitted' = FALSE /\ ui' = "finish"
                /\ UNCHANGED <<playing, stopFails, closeFails, failedOnce>>
StopDone == /\ ui = "stopping" /\ playing' = FALSE
            /\ ui' = IF stopFails THEN "failed" ELSE "finish"
            /\ UNCHANGED <<closing, asked, submitted, dirty, consent, stopFails, closeFails, failedOnce>>
SubmitClose == /\ asked' = TRUE /\ submitted' = TRUE /\ ui' = "closing"
\* _finish_close.
Finish == /\ ui = "finish" /\ playing' = FALSE
          /\ IF asked THEN SubmitClose /\ UNCHANGED <<dirty, consent, closing>>
             ELSE /\ ui' = "prompt" /\ UNCHANGED <<asked, submitted, dirty, consent, closing>>
          /\ UNCHANGED <<stopFails, closeFails, failedOnce>>
\* _allow_discard's answer, then the close is submitted (or Dolly stays open).
Prompt == /\ ui = "prompt"
          /\ \/ /\ ~dirty /\ SubmitClose /\ UNCHANGED <<dirty, consent, closing>>
             \/ /\ dirty /\ consent' = TRUE /\ SubmitClose /\ UNCHANGED <<dirty, closing>>
             \/ /\ dirty /\ dirty' = FALSE /\ SubmitClose /\ UNCHANGED <<consent, closing>>
             \/ /\ dirty /\ closing' = FALSE /\ ui' = "idle"
                /\ UNCHANGED <<asked, submitted, dirty, consent>>
          /\ UNCHANGED <<playing, stopFails, closeFails, failedOnce>>
\* controller.close on the worker.
CloseRun == /\ ui = "closing"
            /\ IF closeFails = "always" \/ (closeFails = "once" /\ ~failedOnce)
               THEN failedOnce' = TRUE /\ ui' = "failed"
               ELSE ui' = "destroyed" /\ UNCHANGED failedOnce
            /\ UNCHANGED <<closing, asked, submitted, dirty, consent, playing, stopFails, closeFails>>
\* _close_after_failure.
Failed == /\ ui = "failed" /\ playing' = FALSE
          /\ IF ~asked THEN ui' = "prompt" /\ UNCHANGED submitted
             ELSE IF submitted THEN ui' = "destroyed" /\ UNCHANGED submitted
             ELSE ui' = "closing" /\ submitted' = TRUE
          /\ UNCHANGED <<closing, asked, dirty, consent, stopFails, closeFails, failedOnce>>
Done == /\ ui = "destroyed" /\ UNCHANGED vars
Idle == /\ ui = "idle" /\ UNCHANGED vars
Step == StopDone \/ Finish \/ Prompt \/ CloseRun \/ Failed
Next == Edit \/ ClickClose \/ RestartClose \/ Step \/ Done \/ Idle
Spec == Init /\ [][Next]_vars /\ WF_vars(Step)

TypeOK == /\ ui \in {"idle", "stopping", "finish", "prompt", "closing", "failed", "destroyed"}
          /\ closing \in BOOLEAN /\ asked \in BOOLEAN /\ submitted \in BOOLEAN
          /\ dirty \in BOOLEAN /\ consent \in BOOLEAN /\ playing \in BOOLEAN
          /\ stopFails \in BOOLEAN /\ closeFails \in {"never", "once", "always"} /\ failedOnce \in BOOLEAN
\* Dolly never exits over an unsaved shot the user did not agree to discard.
NoSilentLoss == ui = "destroyed" => (~dirty \/ consent)
\* Once the user agreed, Dolly does exit, even if every close attempt fails.
CloseEnds == (closing /\ asked) ~> (ui = "destroyed")
\* Every close ends: Dolly exits, or stays open because the user chose so.
CloseSettles == closing ~> (ui = "destroyed" \/ ui = "idle")
====
