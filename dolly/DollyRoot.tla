---- MODULE DollyRoot ----
EXTENDS Naturals
\* Root composition of four user-visible promises over component interfaces.
\* Built only on CameraLeaseApi (refined by ReleaseJoin) and RecorderApi
\* (refined by CameraOnlyCapture); never on implementation specs.
\*
\* Promises (invariants/properties below):
\*  1 NoWriterAfterRelease / NewWriterAfterRetirement: once Dolly reports the
\*    camera released, nothing writes it until a new shot; a new shot never
\*    starts before the previous lease is retired (Controller play ->
\*    _stop_before_new_shot -> acknowledged release before prepare).
\*  2 RestorationOwned: while the game runs, unrestored effects always have an
\*    owner; refusal/timeout keeps the lease (ReleaseJoin, CameraSessionJoin).
\*  3 HonestOutcome: a terminal recording state agrees with the kept files,
\*    except RecorderApi's documented late capture fault (off by default; the
\*    "known gap" mutant shows the exception is real).
\*  4 CloseReturns / CloseReleases / RecordingSettles: NativeBridge.close returns
\*    (bounded release(0.25), timed joins); afterwards the camera is released
\*    either by the acknowledged Release or by the native heartbeat-expiry fault
\*    (on_view: gWorkerError/2 s heartbeat -> finish(Fault) -> gEffects.restore,
\*    retried every frame), and media detach stops an active take.
\*
\* Environment assumptions (fairness): while the game process lives it keeps
\* rendering main views, so Release/expiry callbacks and restoration retries run,
\* and restoration eventually succeeds; takes are finite. The game may exit at
\* any time (no fairness). Close is the user's choice (no fairness).
\* Bounds: two shot generations, one take. Excludes: engine threading, C++
\* memory model, GPU/pixels, real timing, storage durability, UI queueing,
\* Players/screenshot paths, and the internal steps of each component (they
\* belong to the component specs that refine these interfaces).
CONSTANTS GameMayExit, RestoreMayFail
AllowLateCaptureFault == FALSE
VARIABLES lease, writer, restored, game, outcome, kept, rec, app, link,
          gen, retiredGen, released
vars == <<lease, writer, restored, game, outcome, kept, rec, app, link,
          gen, retiredGen, released>>
camVars == <<lease, writer, restored, game>>
recVars == <<outcome, kept>>
ctlVars == <<rec, app, link, gen, retiredGen, released>>
Camera == INSTANCE CameraLeaseApi WITH alive <- game
Recorder == INSTANCE RecorderApi

Init == /\ lease = FALSE /\ writer = "none" /\ restored = TRUE /\ game = TRUE
        /\ outcome = "active" /\ kept = FALSE /\ rec = "none"
        /\ app = "running" /\ link = TRUE
        /\ gen = 0 /\ retiredGen = 0 /\ released = FALSE

\* Controller.play / enter_native_flight: admission requires a retired lease.
StartShot == /\ app = "running" /\ ~lease /\ gen < 2
             /\ Camera!Acquire /\ gen' = gen + 1 /\ released' = FALSE
             /\ UNCHANGED <<outcome, kept, rec, app, link, retiredGen>>
\* on_view processes the published Release (Stopped, or Fault 36 on failure).
ReleaseCallback == /\ app \in {"running", "closing"} /\ link /\ game /\ writer = "shot"
                   /\ Camera!NativeRelease /\ (~RestoreMayFail => restored')
                   /\ UNCHANGED <<recVars, ctlVars>>
\* Heartbeat stopped (editor closed/crashed): on_view faults and releases.
Expire == /\ ~link /\ game /\ writer = "shot"
          /\ Camera!NativeRelease /\ (~RestoreMayFail => restored')
          /\ UNCHANGED <<recVars, ctlVars>>
\* finish(Fault/Stopped) retries gEffects.restore on every later frame.
Restore == /\ Camera!Restore /\ UNCHANGED <<recVars, ctlVars>>
\* _release_native_camera clears the lease only on acknowledged Stopped.
RetireLease == /\ app \in {"running", "closing"} /\ link /\ game /\ Camera!Retire
               /\ retiredGen' = gen /\ released' = TRUE
               /\ UNCHANGED <<recVars, rec, app, link, gen>>
GameExit == /\ GameMayExit /\ Camera!Exit /\ UNCHANGED <<recVars, ctlVars>>
\* recover_dead_session: dead process only (enforced by the interface).
RecoverDead == /\ app \in {"running", "closing"} /\ Camera!Lost /\ retiredGen' = gen
               /\ UNCHANGED <<recVars, rec, app, link, gen, released>>
StartRecord == /\ app = "running" /\ game /\ rec = "none" /\ rec' = "on"
               /\ UNCHANGED <<camVars, recVars, app, link, gen, retiredGen, released>>
RecStage == /\ rec = "on" /\ Recorder!Stage /\ UNCHANGED <<camVars, ctlVars>>
\* Stop/cancel/frame cap/failure, or media detach after the editor leaves.
RecFinish == /\ rec = "on" /\ game /\ (Recorder!Finish \/ Recorder!GapFinish)
             /\ UNCHANGED <<camVars, ctlVars>>
\* NativeBridge.close: _close_media, release(0.25) attempt (ReleaseCallback may
\* or may not run first), heartbeat stop, timed join, unmap.
Close == /\ app = "running" /\ app' = "closing"
         /\ UNCHANGED <<camVars, recVars, rec, link, gen, retiredGen, released>>
CloseFinish == /\ app = "closing" /\ app' = "closed" /\ link' = FALSE
               /\ UNCHANGED <<camVars, recVars, rec, gen, retiredGen, released>>
Settled == /\ app = "closed"
           /\ (~game \/ (writer = "none" /\ restored /\ (rec = "none" \/ outcome # "active")))
Done == Settled /\ UNCHANGED vars

Next == StartShot \/ ReleaseCallback \/ Expire \/ Restore \/ RetireLease \/ GameExit
        \/ RecoverDead \/ StartRecord \/ RecStage \/ RecFinish \/ Close \/ CloseFinish \/ Done
Fair == /\ WF_vars(ReleaseCallback) /\ WF_vars(Expire) /\ WF_vars(Restore)
        /\ WF_vars(RetireLease) /\ WF_vars(RecFinish) /\ WF_vars(CloseFinish)
Spec == Init /\ [][Next]_vars /\ Fair

TypeOK == /\ lease \in BOOLEAN /\ writer \in {"none", "shot"} /\ restored \in BOOLEAN
          /\ game \in BOOLEAN /\ outcome \in {"active"} \cup Recorder!Terminal
          /\ kept \in BOOLEAN /\ rec \in {"none", "on"}
          /\ app \in {"running", "closing", "closed"} /\ link \in BOOLEAN
          /\ gen \in 0..2 /\ retiredGen \in 0..2 /\ released \in BOOLEAN
NoWriterAfterRelease == released => writer = "none"
NewWriterAfterRetirement == writer = "shot" => retiredGen = gen - 1
RestorationOwned == (game /\ ~restored) => lease
HonestOutcome == outcome \in Recorder!Terminal => ((outcome = "completed") <=> kept)
CloseReturns == app = "closing" ~> app = "closed"
CloseReleases == app = "closed" ~> (~game \/ (writer = "none" /\ restored))
RecordingSettles == rec = "on" ~> (outcome # "active" \/ ~game)
\* Required success (Root-success.cfg): with a live game and returning
\* restoration, every shot's lease is retired unless the app closes first.
ShotRetires == writer = "shot" ~> (released \/ app = "closed")
CameraRefined == Camera!Spec
RecorderRefined == Recorder!Spec
====
