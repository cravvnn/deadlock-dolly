---- MODULE CloseFlow ----
EXTENDS Naturals
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
\* Other work: the question is a nested modal, so the poll keeps running and
\* can start one other operation (the closed-game cleanup) while it is open,
\* or before the close while Dolly is idle (the X then waits). It may fail.
\* Only the close's own steps (CLOSE_STEPS) route a failure to
\* _close_after_failure; RouteAll is the old rule that routed every failure.
\* A close submit refused because that work is still running waits for it
\* (_retry_close) instead of being dropped or run beside it.
\* Abstractions: Stop and close outcomes are fixed per behavior; the user can
\* edit the shot while idle or while Stop runs, not after consent while the
\* window tears down. One other operation per behavior. A submit is refused
\* only while other work runs. Dialogs and timers are outside this model.
RouteAll == FALSE
VARIABLES ui, closing, asked, submitted, dirty, consent, playing, stopFails, closeFails, failedOnce,
          other, otherFails, prompts, closeRan, cancelled
vars == <<ui, closing, asked, submitted, dirty, consent, playing, stopFails, closeFails, failedOnce,
          other, otherFails, prompts, closeRan, cancelled>>
work == <<other, otherFails>>

Init == /\ ui = "idle" /\ closing = FALSE /\ asked = FALSE /\ submitted = FALSE
        /\ dirty \in BOOLEAN /\ consent = FALSE /\ playing \in BOOLEAN
        /\ stopFails \in BOOLEAN /\ closeFails \in {"never", "once", "always"} /\ failedOnce = FALSE
        /\ other = "none" /\ otherFails \in BOOLEAN /\ prompts = 0 /\ closeRan = FALSE /\ cancelled = FALSE

\* The user edits the shot.
Edit == /\ ui \in {"idle", "stopping"} /\ dirty' = TRUE /\ consent' = FALSE
        /\ UNCHANGED <<ui, closing, asked, submitted, playing, stopFails, closeFails, failedOnce>>
        /\ UNCHANGED <<work, prompts, closeRan, cancelled>>
\* The window's X (refused while an operation runs).
ClickClose == /\ ui = "idle" /\ other # "running"
              /\ closing' = TRUE /\ asked' = FALSE /\ submitted' = FALSE /\ cancelled' = FALSE
              /\ ui' = IF playing THEN "stopping" ELSE "finish"
              /\ UNCHANGED <<dirty, consent, playing, stopFails, closeFails, failedOnce>>
              /\ UNCHANGED <<work, prompts, closeRan>>
\* UpdateUI.restart: its own _allow_discard (refused while playing) answers
\* first; only a yes starts the close, with the answer carried along.
RestartClose == /\ ui = "idle" /\ ~playing /\ other # "running"
                /\ \/ /\ ~dirty /\ UNCHANGED <<dirty, consent>>
                   \/ /\ dirty /\ consent' = TRUE /\ UNCHANGED dirty
                   \/ /\ dirty /\ dirty' = FALSE /\ UNCHANGED consent
                /\ closing' = TRUE /\ asked' = TRUE /\ submitted' = FALSE /\ ui' = "finish"
                /\ cancelled' = FALSE
                /\ UNCHANGED <<playing, stopFails, closeFails, failedOnce>>
                /\ UNCHANGED <<work, prompts, closeRan>>
StopDone == /\ ui = "stopping" /\ playing' = FALSE
            /\ ui' = IF stopFails THEN "failed" ELSE "finish"
            /\ UNCHANGED <<closing, asked, submitted, dirty, consent, stopFails, closeFails, failedOnce>>
            /\ UNCHANGED <<work, prompts, closeRan, cancelled>>
\* Submit controller.close; refused while other work runs, then wait for it.
SubmitClose == /\ asked' = TRUE
               /\ IF other = "running"
                  THEN ui' = "waiting" /\ UNCHANGED submitted
                  ELSE ui' = "closing" /\ submitted' = TRUE
\* _finish_close.
Finish == /\ ui = "finish" /\ playing' = FALSE
          /\ IF asked THEN SubmitClose /\ UNCHANGED <<dirty, consent, closing, prompts>>
             ELSE /\ ui' = "prompt" /\ prompts' = prompts + 1
                  /\ UNCHANGED <<asked, submitted, dirty, consent, closing>>
          /\ UNCHANGED <<stopFails, closeFails, failedOnce, work, closeRan, cancelled>>
\* _allow_discard's answer, then the close is submitted (or Dolly stays open).
Prompt == /\ ui = "prompt" /\ prompts' = prompts - 1
          /\ \/ /\ ~dirty /\ SubmitClose /\ UNCHANGED <<dirty, consent, closing, cancelled>>
             \/ /\ dirty /\ consent' = TRUE /\ SubmitClose /\ UNCHANGED <<dirty, closing, cancelled>>
             \/ /\ dirty /\ dirty' = FALSE /\ SubmitClose /\ UNCHANGED <<consent, closing, cancelled>>
             \/ /\ dirty /\ closing' = FALSE /\ ui' = "idle" /\ cancelled' = TRUE
                /\ UNCHANGED <<asked, submitted, dirty, consent>>
          /\ UNCHANGED <<playing, stopFails, closeFails, failedOnce, work, closeRan>>
\* An outer question still open after an inner one was answered (only when a
\* second question was opened over the first).
OuterPrompt == /\ prompts > 0 /\ ui # "prompt" /\ ui # "destroyed" /\ prompts' = prompts - 1
               /\ \/ /\ closing' = FALSE /\ cancelled' = TRUE /\ UNCHANGED <<ui, consent>>
                  \/ /\ consent' = TRUE /\ UNCHANGED <<closing, cancelled>>
                     /\ IF submitted THEN ui' = "destroyed" ELSE UNCHANGED ui
               /\ UNCHANGED <<asked, submitted, dirty, playing, stopFails, closeFails, failedOnce, work, closeRan>>
\* _retry_close: once the other work ends, finish the close (or drop it if
\* Dolly is staying open).
Retry == /\ ui = "waiting" /\ other # "running"
         /\ ui' = IF closing THEN "finish" ELSE "idle"
         /\ UNCHANGED <<closing, asked, submitted, dirty, consent, playing, stopFails, closeFails, failedOnce>>
         /\ UNCHANGED <<work, prompts, closeRan, cancelled>>
\* controller.close on the worker.
CloseRun == /\ ui = "closing" /\ closeRan' = TRUE
            /\ IF closeFails = "always" \/ (closeFails = "once" /\ ~failedOnce)
               THEN failedOnce' = TRUE /\ ui' = "failed"
               ELSE ui' = "destroyed" /\ UNCHANGED failedOnce
            /\ UNCHANGED <<closing, asked, submitted, dirty, consent, playing, stopFails, closeFails>>
            /\ UNCHANGED <<work, prompts, cancelled>>
\* _close_after_failure.
Failed == /\ ui = "failed" /\ playing' = FALSE
          /\ IF ~asked THEN ui' = "prompt" /\ UNCHANGED submitted /\ prompts' = prompts + 1 /\ UNCHANGED asked
             ELSE IF submitted THEN ui' = "destroyed" /\ UNCHANGED submitted /\ UNCHANGED <<asked, prompts>>
             ELSE SubmitClose /\ UNCHANGED prompts
          /\ UNCHANGED <<closing, dirty, consent, stopFails, closeFails, failedOnce>>
          /\ UNCHANGED <<work, closeRan, cancelled>>
\* The poll starts other work while the question is open, or while idle.
OtherStart == /\ other = "none" /\ ui \in {"idle", "prompt"} /\ other' = "running"
              /\ UNCHANGED <<ui, closing, asked, submitted, dirty, consent, playing, stopFails, closeFails>>
              /\ UNCHANGED <<failedOnce, otherFails, prompts, closeRan, cancelled>>
\* It ends. A failure is reported as usual, unless (RouteAll) it is taken for
\* a close step: _close_after_failure runs over whatever is showing.
OtherEnd == /\ other = "running" /\ other' = "done"
            /\ IF otherFails /\ closing /\ RouteAll
               THEN /\ playing' = FALSE
                    /\ IF ~asked THEN ui' = "prompt" /\ prompts' = prompts + 1 /\ UNCHANGED <<asked, submitted>>
                       ELSE IF submitted THEN ui' = "destroyed" /\ UNCHANGED <<asked, submitted, prompts>>
                       ELSE ui' = "closing" /\ submitted' = TRUE /\ UNCHANGED <<asked, prompts>>
               ELSE UNCHANGED <<ui, asked, submitted, playing, prompts>>
            /\ UNCHANGED <<closing, dirty, consent, stopFails, closeFails, failedOnce, otherFails, closeRan, cancelled>>
Done == /\ ui = "destroyed" /\ UNCHANGED vars
Idle == /\ ui = "idle" /\ UNCHANGED vars
Step == StopDone \/ Finish \/ Prompt \/ OuterPrompt \/ Retry \/ CloseRun \/ Failed
Next == Edit \/ ClickClose \/ RestartClose \/ Step \/ OtherStart \/ OtherEnd \/ Done \/ Idle
Spec == Init /\ [][Next]_vars /\ WF_vars(Step) /\ WF_vars(OtherEnd)

TypeOK == /\ ui \in {"idle", "stopping", "finish", "prompt", "waiting", "closing", "failed", "destroyed"}
          /\ closing \in BOOLEAN /\ asked \in BOOLEAN /\ submitted \in BOOLEAN
          /\ dirty \in BOOLEAN /\ consent \in BOOLEAN /\ playing \in BOOLEAN
          /\ stopFails \in BOOLEAN /\ closeFails \in {"never", "once", "always"} /\ failedOnce \in BOOLEAN
          /\ other \in {"none", "running", "done"} /\ otherFails \in BOOLEAN /\ prompts \in 0..2
          /\ closeRan \in BOOLEAN /\ cancelled \in BOOLEAN
\* Dolly never exits over an unsaved shot the user did not agree to discard.
NoSilentLoss == ui = "destroyed" => (~dirty \/ consent)
\* Cancel on the save question keeps Dolly open for that close.
CancelNeverDestroys == ui = "destroyed" => ~cancelled
\* Never two save questions at once.
AtMostOneDiscardPrompt == prompts <= 1
\* The window closes only after controller.close ran (and finished or failed).
DestroyImpliesCloseRanOrFailed == ui = "destroyed" => closeRan
\* controller.close never runs beside other work on the UI thread.
CloseRunsAlone == ui = "closing" => other # "running"
\* Once the user agreed, Dolly does exit, even if every close attempt fails.
CloseEnds == (closing /\ asked) ~> (ui = "destroyed")
\* Every close ends: Dolly exits, or stays open because the user chose so.
CloseSettles == closing ~> (ui = "destroyed" \/ ui = "idle")
====
