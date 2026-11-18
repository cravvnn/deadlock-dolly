---- MODULE HudOwnership ----
EXTENDS Naturals
\* Who owns the game HUD cvars (citadel_hud_visible and friends) in
\* dolly/controller.py and dolly/game_ui_handoff.py:
\*   pb  = _playback_restore     (_prepare_playback_settings / _restore_playback_settings)
\*   rh  = _replay_hud_restore   (_apply_replay_hud / _restore_replay_hud)
\*   ui  = _game_ui_restore      (_remember_game_ui_settings / _restore_game_ui_settings)
\* Covers Play with or without Hide HUD, pause/resume during a shot
\* (toggle_replay), the shot's own finish (_finish_playback), POV finish and
\* its retry (finish_pov_recording, read by video_export), a failed roster
\* Follow selection (start_selected_game_follow), and Stop (_halt + stop).
\* The HUD is abstracted to whose value it shows: "user" (the user's own
\* settings), "hidden" (Dolly hid it) or "shown" (Dolly showed the replay UI
\* or the replay HUD). A journal is "none" or the value it saved. One console
\* failure is allowed per run; health-panel deferral and the cursor are not
\* modelled separately.
VARIABLES pc, game, pb, rh, ui, worker, hide, showHud, pov, uiShown, fails, stopErr
vars == <<pc, game, pb, rh, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>

Values == {"user", "hidden", "shown"}
Journal == {"none", "user", "hidden", "shown"}
StopPhases == {"halt", "restorePb", "restoreUi"}
Restore(j) == IF j = "none" THEN game ELSE j
Keep(j) == IF j = "none" THEN game ELSE j
Clean == pb = "none" /\ rh = "none" /\ ui = "none"
\* toggle_replay: a live shot whose restore journal holds the HUD keys owns them.
OwnsHud == worker /\ pb # "none"

Init == /\ pc = "idle" /\ game = "user" /\ pb = "none" /\ rh = "none" /\ ui = "none"
        /\ worker = FALSE /\ hide = FALSE /\ showHud \in BOOLEAN /\ pov = FALSE
        /\ uiShown = FALSE /\ fails = 0 /\ stopErr = FALSE

\* play(): Stop first, then hide the HUD if asked and start the worker.
Play == /\ pc \in {"idle", "stopped"} /\ Clean /\ ~worker
        /\ hide' \in BOOLEAN
        /\ pb' = IF hide' THEN game ELSE "none"
        /\ game' = IF hide' THEN "hidden" ELSE game
        /\ worker' = TRUE /\ pc' = "shot"
        /\ UNCHANGED <<rh, ui, showHud, pov, uiShown, fails, stopErr>>

PauseShot == /\ pc = "shot" /\ pc' = "shotPaused"
             /\ (IF OwnsHud
                 THEN UNCHANGED <<game, rh>>
                 ELSE /\ game' = Restore(rh) /\ rh' = "none")
             /\ UNCHANGED <<pb, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>

ResumeShot == /\ pc = "shotPaused" /\ pc' = "shot"
              /\ (IF OwnsHud
                  THEN UNCHANGED <<game, rh>>
                  ELSE /\ rh' = Keep(rh)
                       /\ game' = IF showHud THEN "shown" ELSE "hidden")
              /\ UNCHANGED <<pb, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>

\* _finish_playback on the worker: replay HUD first, then the playback journal.
Finish == /\ pc = "shot" /\ pc' = "finished" /\ worker' = FALSE
          /\ game' = IF pb # "none" THEN pb ELSE Restore(rh)
          /\ pb' = "none" /\ rh' = "none"
          /\ UNCHANGED <<ui, hide, showHud, pov, uiShown, fails, stopErr>>
FinishFail == /\ pc = "shot" /\ pb # "none" /\ fails = 0 /\ fails' = 1
              /\ pc' = "finished" /\ worker' = FALSE
              /\ game' = Restore(rh) /\ rh' = "none"
              /\ UNCHANGED <<pb, ui, hide, showHud, pov, uiShown, stopErr>>

\* prepare_pov_recording: remember the UI once, then hide it for recording.
PovStart == /\ pc \in {"idle", "stopped", "gameUi"} /\ pb = "none" /\ rh = "none" /\ ~worker
            /\ ui' = Keep(ui) /\ game' = "hidden" /\ pov' = TRUE /\ uiShown' = TRUE
            /\ pc' = "pov"
            /\ UNCHANGED <<pb, rh, worker, hide, showHud, fails, stopErr>>
PovFinish == /\ pc \in {"pov", "povPending"} /\ pov
             /\ game' = Restore(ui) /\ ui' = "none" /\ pov' = FALSE /\ uiShown' = TRUE
             /\ pc' = "idle"
             /\ UNCHANGED <<pb, rh, worker, hide, showHud, fails, stopErr>>
PovFinishFail == /\ pc \in {"pov", "povPending"} /\ pov /\ fails = 0 /\ fails' = 1
                 /\ pov' = pov /\ pc' = "povPending"
                 /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, uiShown, stopErr>>
\* A finish with nothing pending returns at once and reports success.
PovNothingPending == /\ pc = "povPending" /\ ~pov /\ pc' = "idle"
                     /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>

\* start_selected_game_follow: hand over to the replay UI, then hide it.
FollowOk == /\ pc \in {"idle", "stopped", "gameUi"} /\ pb = "none" /\ rh = "none" /\ ~worker
            /\ ui' = Keep(ui) /\ game' = "hidden" /\ uiShown' = FALSE /\ pc' = "follow"
            /\ UNCHANGED <<pb, rh, worker, hide, showHud, pov, fails, stopErr>>
FollowFail == /\ pc \in {"idle", "stopped", "gameUi"} /\ pb = "none" /\ rh = "none" /\ ~worker
              /\ ui' = Keep(ui) /\ game' = "shown" /\ uiShown' = TRUE /\ pc' = "gameUi"
              /\ UNCHANGED <<pb, rh, worker, hide, showHud, pov, fails, stopErr>>
FollowFailHidden == /\ pc \in {"idle", "stopped", "gameUi"} /\ pb = "none" /\ rh = "none" /\ ~worker
                    /\ fails = 0 /\ fails' = 1
                    /\ ui' = Keep(ui) /\ game' = "hidden" /\ uiShown' = FALSE /\ pc' = "gameUi"
                    /\ UNCHANGED <<pb, rh, worker, hide, showHud, pov, stopErr>>

\* stop(): _halt joins the worker, then each journal is restored in turn. A
\* failed step keeps its journal and Stop continues.
RequestStop == /\ pc \notin StopPhases /\ pc' = "halt" /\ stopErr' = FALSE
               /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, pov, uiShown, fails>>
HaltWorker == /\ pc = "halt" /\ worker /\ worker' = FALSE
              /\ UNCHANGED <<pc, game, pb, rh, ui, hide, showHud, pov, uiShown, fails, stopErr>>
HaltHud == /\ pc = "halt" /\ ~worker /\ game' = Restore(rh) /\ rh' = "none" /\ pc' = "restorePb"
           /\ UNCHANGED <<pb, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>
HaltHudFail == /\ pc = "halt" /\ worker = FALSE /\ fails = 0 /\ fails' = 1 /\ stopErr' = TRUE /\ pc' = "restorePb"
               /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, pov, uiShown>>
RestorePb == /\ pc = "restorePb" /\ game' = Restore(pb) /\ pb' = "none" /\ pc' = "restoreUi"
             /\ UNCHANGED <<rh, ui, worker, hide, showHud, pov, uiShown, fails, stopErr>>
RestorePbFail == /\ pc = "restorePb" /\ fails = 0 /\ fails' = 1 /\ stopErr' = TRUE /\ pc' = "restoreUi"
                 /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, pov, uiShown>>
RestoreUi == /\ pc = "restoreUi" /\ game' = Restore(ui) /\ ui' = "none" /\ pc' = "stopped"
             /\ uiShown' = (uiShown /\ ui = "none")
             /\ UNCHANGED <<pb, rh, worker, hide, showHud, pov, fails, stopErr>>
RestoreUiFail == /\ pc = "restoreUi" /\ fails = 0 /\ fails' = 1 /\ stopErr' = TRUE /\ pc' = "stopped"
                 /\ UNCHANGED <<game, pb, rh, ui, worker, hide, showHud, pov, uiShown>>

Next == \/ Play \/ PauseShot \/ ResumeShot \/ Finish \/ FinishFail
        \/ PovStart \/ PovFinish \/ PovFinishFail \/ PovNothingPending
        \/ FollowOk \/ FollowFail \/ FollowFailHidden
        \/ RequestStop \/ HaltWorker \/ HaltHud \/ HaltHudFail
        \/ RestorePb \/ RestorePbFail \/ RestoreUi \/ RestoreUiFail
Spec == Init /\ [][Next]_vars

TypeOK == /\ pc \in {"idle", "shot", "shotPaused", "finished", "pov", "povPending",
                     "follow", "gameUi", "stopped"} \cup StopPhases
          /\ game \in Values /\ pb \in Journal /\ rh \in Journal /\ ui \in Journal
          /\ worker \in BOOLEAN /\ hide \in BOOLEAN /\ showHud \in BOOLEAN
          /\ pov \in BOOLEAN /\ uiShown \in BOOLEAN /\ fails \in 0..1 /\ stopErr \in BOOLEAN
\* No journal ever saves one of Dolly's own values as the user's original.
NoDollyOriginal == /\ pb \in {"none", "user"} /\ rh \in {"none", "user"} /\ ui \in {"none", "user"}
\* The user's HUD is on screen or still saved for a restore, even after a failure.
UserRecoverable == game = "user" \/ pb = "user" \/ rh = "user" \/ ui = "user"
\* A shot that hides the HUD never records it.
ShotHidesHud == pc = "shot" /\ hide => game = "hidden"
\* A clean shot end gives the HUD back.
ShotEndReturnsHud == pc = "finished" /\ fails = 0 => game = "user" /\ pb = "none" /\ rh = "none"
\* A POV finish that reports success has restored the user's HUD.
ReportedDone == pc = "idle" => game = "user"
\* A failed POV restore stays pending with its journal for the retry.
RetryKeepsJournal == pc = "povPending" => pov /\ ui = "user"
\* After a failed Follow selection Dolly never claims a hidden HUD is shown.
FollowFailureHonest == pc = "gameUi" /\ uiShown => game # "hidden"
\* Stop joins the worker before it ends.
StopJoinsWorker == pc \in {"restorePb", "restoreUi", "stopped"} => ~worker
\* A Stop with no failures leaves the user's HUD and no journals.
StopSettles == pc = "stopped" /\ ~stopErr => game = "user" /\ Clean
====
