---- MODULE DollyRoot ----
EXTENDS Naturals
\* Root composition of user-visible promises over component interfaces.
\* Built only on CameraLeaseApi (refined by ReleaseJoin), CameraRetirementApi
\* (refined by CameraSessionJoin), RecorderApi (refined by CameraOnlyCapture and
\* VideoCompleteness), CaptureRestoreApi (refined by CaptureRestoreJoin) and
\* MediaCloseApi (refined by MediaFinalize) and GameinfoInstallApi (refined by
\* GameinfoTransaction, since 2026-11-14); never on implementation specs.
\*
\* Promises (invariants/properties below):
\*  1 NoWriterAfterRelease / NewWriterAfterRetirement: once Dolly reports the
\*    camera released, nothing writes it until a new shot; a new shot never
\*    starts before the previous lease is retired (Controller play ->
\*    _stop_before_new_shot -> acknowledged release before prepare).
\*  2 RestorationOwned: while the game runs, unrestored effects always have an
\*    owner; refusal/timeout keeps the lease (ReleaseJoin, CameraSessionJoin).
\*  3 HonestOutcome: a terminal recording state agrees with the kept files
\*    (no exception: capture failures are ordered before the encoder's
\*    decision since 2026-11-14).
\*  4 CloseReturns / CloseReleases / RecordingSettles: NativeBridge.close returns
\*    (bounded release(0.25), timed joins); afterwards the camera is released
\*    either by the acknowledged Release or by the native heartbeat-expiry fault
\*    (on_view: gWorkerError/2 s heartbeat -> finish(Fault) -> gEffects.restore,
\*    retried every frame), and media detach stops an active take.
\*  5 QuietWhenRetired: a lease retires (and so a new shot starts) only after
\*    the previous playback worker is quiet (_halt -> join -> release).
\*  6 CaptureAfterOutcome: a Players capture's player/effects context is
\*    restored only after the recording's terminal readback and a verified
\*    console; refusal or timeout keeps the originals (CaptureRestoreJoin).
\*  7 StopBeforeUnmap: close asks the encoder to stop before it unmaps the
\*    shared mapping, even when another media step fails (MediaFinalize).
\*  8 PluginWhileRunning / NoDanglingMount / GameFilesSettle: the running game
\*    keeps Dolly's plugin folder; gameinfo.gi never mounts a missing folder;
\*    once the game exits, the user's gameinfo.gi is back and the folder gone
\*    (or an outside change waits for the user's choice) whether Dolly is
\*    open, closing or already closed: the exit watcher cleans while it runs,
\*    the helper started with the game cleans after it (GameinfoTransaction).
\*
\* Environment assumptions (fairness): while the game process lives it keeps
\* rendering main views, so Release/expiry callbacks and restoration retries run,
\* and restoration eventually succeeds; takes are finite. The game may exit at
\* any time (no fairness). Close is the user's choice (no fairness).
\* RestoreMayFail also lets the capture console check time out for good, and
\* JoinMayHang lets a playback worker never stop (then only heartbeat expiry
\* after close releases the camera, and the lease is kept).
\* Bounds: two shot generations, one take. Excludes: engine threading, C++
\* memory model, GPU/pixels, real timing, storage durability, UI queueing,
\* Players/screenshot paths, and the internal steps of each component (they
\* belong to the component specs that refine these interfaces).
CONSTANTS GameMayExit, RestoreMayFail, JoinMayHang
VARIABLES lease, writer, restored, game, outcome, kept, rec, app, link,
          gen, retiredGen, released, worker, ctx, ctxReady, stopAsked, unmapped,
          cfg, plugin
vars == <<lease, writer, restored, game, outcome, kept, rec, app, link,
          gen, retiredGen, released, worker, ctx, ctxReady, stopAsked, unmapped,
          cfg, plugin>>
camVars == <<lease, writer, restored, game>>
recVars == <<outcome, kept>>
ctlVars == <<rec, app, link, gen, retiredGen, released>>
extVars == <<worker, ctx, ctxReady, stopAsked, unmapped>>
giVars == <<cfg, plugin>>
Camera == INSTANCE CameraLeaseApi WITH alive <- game
Recorder == INSTANCE RecorderApi
Retirement == INSTANCE CameraRetirementApi WITH retired <- ~lease,
    quiet <- ~worker, evidence <- ((writer = "none" /\ restored) \/ ~game)
Capture == INSTANCE CaptureRestoreApi WITH pending <- ctx # "restored",
    ack <- outcome \in Recorder!Terminal, context <- ctxReady
Media == INSTANCE MediaCloseApi
Gameinfo == INSTANCE GameinfoInstallApi WITH alive <- game

Init == /\ lease = FALSE /\ writer = "none" /\ restored = TRUE /\ game = TRUE
        /\ outcome = "active" /\ kept = FALSE /\ rec = "none"
        /\ app = "running" /\ link = TRUE
        /\ gen = 0 /\ retiredGen = 0 /\ released = FALSE
        /\ worker = FALSE /\ ctx = "none" /\ ctxReady = FALSE
        /\ stopAsked = FALSE /\ unmapped = FALSE
        /\ cfg = "dolly" /\ plugin = TRUE

\* Controller.play / enter_native_flight: admission requires a retired lease.
StartShot == /\ app = "running" /\ ~lease /\ gen < 2
             /\ Camera!Acquire /\ gen' = gen + 1 /\ released' = FALSE /\ worker' = TRUE
             /\ UNCHANGED <<outcome, kept, rec, app, link, retiredGen, ctx, ctxReady,
                            stopAsked, unmapped>>
\* The playback worker observes _halt and exits; join returns (or hangs).
WorkerJoin == /\ worker /\ worker' = FALSE
              /\ UNCHANGED <<camVars, recVars, ctlVars, ctx, ctxReady, stopAsked, unmapped>>
\* on_view processes the published Release (Stopped, or Fault 36 on failure).
\* The controller publishes Release only after the worker joined.
ReleaseCallback == /\ app \in {"running", "closing"} /\ link /\ game /\ writer = "shot"
                   /\ ~worker
                   /\ Camera!NativeRelease /\ (~RestoreMayFail => restored')
                   /\ UNCHANGED <<recVars, ctlVars, extVars>>
\* Heartbeat stopped (editor closed/crashed): on_view faults and releases.
Expire == /\ ~link /\ game /\ writer = "shot"
          /\ Camera!NativeRelease /\ (~RestoreMayFail => restored')
          /\ UNCHANGED <<recVars, ctlVars, extVars>>
\* finish(Fault/Stopped) retries gEffects.restore on every later frame.
Restore == /\ Camera!Restore /\ UNCHANGED <<recVars, ctlVars, extVars>>
\* _release_native_camera clears the lease only on acknowledged Stopped.
RetireLease == /\ app \in {"running", "closing"} /\ link /\ game
               /\ Camera!Retire /\ retiredGen' = gen /\ released' = TRUE
               /\ UNCHANGED <<recVars, rec, app, link, gen, extVars>>
GameExit == /\ GameMayExit /\ Camera!Exit /\ UNCHANGED <<recVars, ctlVars, extVars>>
\* recover_dead_session: dead process only (enforced by the interface); it
\* halts and joins the worker like every other retirement caller.
RecoverDead == /\ app \in {"running", "closing"} /\ ~worker /\ Camera!Lost
               /\ retiredGen' = gen
               /\ UNCHANGED <<recVars, rec, app, link, gen, released, extVars>>
\* A Players capture take reserves the player/effects context.
StartRecord == /\ app = "running" /\ game /\ rec = "none" /\ rec' = "on"
               /\ ctx' = "held"
               /\ UNCHANGED <<camVars, recVars, app, link, gen, retiredGen, released,
                              worker, ctxReady, stopAsked, unmapped>>
RecStage == /\ rec = "on" /\ Recorder!Stage /\ UNCHANGED <<camVars, ctlVars, extVars>>
\* Stop/cancel/frame cap/failure, or media detach after the editor leaves.
RecFinish == /\ rec = "on" /\ game /\ Recorder!Finish
             /\ UNCHANGED <<camVars, ctlVars, extVars>>
\* VideoExport.stop(capture_only): the console context check after readback.
CtxReady == /\ ctx = "held" /\ game /\ ~ctxReady /\ ctxReady' = TRUE
            /\ UNCHANGED <<camVars, recVars, ctlVars, worker, ctx, stopAsked, unmapped>>
\* Restore the reserved context: only after the terminal readback.
CtxRestore == /\ ctx = "held" /\ game /\ outcome \in Recorder!Terminal /\ ctxReady
              /\ ctx' = "restored"
              /\ UNCHANGED <<camVars, recVars, ctlVars, worker, ctxReady, stopAsked, unmapped>>
\* NativeBridge.close: _close_media, release(0.25) attempt (ReleaseCallback may
\* or may not run first), heartbeat stop, timed join, unmap.
Close == /\ app = "running" /\ app' = "closing"
         /\ UNCHANGED <<camVars, recVars, rec, link, gen, retiredGen, released, extVars>>
\* _close_media -> _finish_media_and_unmap asks the encoder to stop.
MediaStop == /\ app = "closing" /\ Media!AskStop
             /\ UNCHANGED <<camVars, recVars, ctlVars, worker, ctx, ctxReady>>
CloseFinish == /\ app = "closing" /\ app' = "closed" /\ link' = FALSE /\ Media!Unmap
               /\ UNCHANGED <<camVars, recVars, rec, gen, retiredGen, released,
                              worker, ctx, ctxReady>>
\* ---- game-install transaction (GameinfoInstallApi)
others == <<camVars, recVars, ctlVars, extVars>>
\* After the unlocker initializes, or as Controller.close starts.
GiEarlyRestore == /\ game /\ app \in {"running", "closing"} /\ Gameinfo!Restore /\ UNCHANGED others
\* A mod manager or Steam check rewrites Dolly's file.
GiEdit == /\ Gameinfo!Edit /\ UNCHANGED others
\* After the game exits: the exit watcher (Dolly open) or the helper started
\* with the game (Dolly closing or closed).
GiCleanRestore == /\ ~game /\ Gameinfo!Restore /\ UNCHANGED others
GiRemove == /\ ~game /\ Gameinfo!Remove /\ UNCHANGED others
\* The user's choice for an outside change, in an open Dolly.
GiResolve == /\ app = "running" /\ Gameinfo!Resolve /\ UNCHANGED others
Install == GiEarlyRestore \/ GiEdit \/ GiCleanRestore \/ GiRemove \/ GiResolve

Settled == /\ app = "closed"
           /\ (~game \/ (writer = "none" /\ restored /\ (rec = "none" \/ outcome # "active")))
Done == Settled /\ UNCHANGED vars

Session == StartShot \/ WorkerJoin \/ ReleaseCallback \/ Expire \/ Restore \/ RetireLease
           \/ GameExit \/ RecoverDead \/ StartRecord \/ RecStage \/ RecFinish
           \/ CtxReady \/ CtxRestore \/ Close \/ MediaStop \/ CloseFinish
Next == (Session /\ UNCHANGED giVars) \/ Install \/ Done
Fair == /\ WF_vars(ReleaseCallback /\ UNCHANGED giVars) /\ WF_vars(Expire /\ UNCHANGED giVars) /\ WF_vars(Restore /\ UNCHANGED giVars)
        /\ WF_vars(RetireLease /\ UNCHANGED giVars) /\ WF_vars(RecFinish /\ UNCHANGED giVars) /\ WF_vars(CloseFinish /\ UNCHANGED giVars)
        /\ WF_vars(MediaStop /\ UNCHANGED giVars) /\ WF_vars(CtxRestore /\ UNCHANGED giVars)
        /\ (~JoinMayHang => WF_vars(WorkerJoin /\ UNCHANGED giVars))
        /\ (~RestoreMayFail => WF_vars(CtxReady /\ UNCHANGED giVars))
        /\ WF_vars(GiCleanRestore) /\ WF_vars(GiRemove)
Spec == Init /\ [][Next]_vars /\ Fair

TypeOK == /\ lease \in BOOLEAN /\ writer \in {"none", "shot"} /\ restored \in BOOLEAN
          /\ game \in BOOLEAN /\ outcome \in {"active"} \cup Recorder!Terminal
          /\ kept \in BOOLEAN /\ rec \in {"none", "on"}
          /\ app \in {"running", "closing", "closed"} /\ link \in BOOLEAN
          /\ gen \in 0..2 /\ retiredGen \in 0..2 /\ released \in BOOLEAN
          /\ worker \in BOOLEAN /\ ctx \in {"none", "held", "restored"}
          /\ ctxReady \in BOOLEAN /\ stopAsked \in BOOLEAN /\ unmapped \in BOOLEAN
          /\ cfg \in {"original", "dolly", "other", "otherRef"} /\ plugin \in BOOLEAN
NoWriterAfterRelease == released => writer = "none"
NewWriterAfterRetirement == writer = "shot" => retiredGen = gen - 1
RestorationOwned == (game /\ ~restored) => lease
HonestOutcome == outcome \in Recorder!Terminal => ((outcome = "completed") <=> kept)
QuietWhenRetired == ~lease => ~worker
CaptureAfterOutcome == ctx = "restored" => (outcome \in Recorder!Terminal /\ ctxReady)
StopBeforeUnmap == unmapped => stopAsked
PluginWhileRunning == game => plugin
NoDanglingMount == cfg \in {"dolly", "otherRef"} => plugin
CloseReturns == app = "closing" ~> app = "closed"
CloseReleases == app = "closed" ~> (~game \/ (writer = "none" /\ restored))
RecordingSettles == rec = "on" ~> (outcome # "active" \/ ~game)
GameFilesSettle == ~game ~> (cfg \in {"other", "otherRef"} \/ (cfg = "original" /\ ~plugin))
\* Required success (Root-success.cfg): with a live game and returning
\* restoration, every shot's lease is retired unless the app closes first.
ShotRetires == writer = "shot" ~> (released \/ app = "closed")
\* ...and a reserved capture context is restored.
CaptureRestores == ctx = "held" ~> ctx = "restored"
CameraRefined == Camera!Spec
RecorderRefined == Recorder!Spec
RetirementRefined == Retirement!Spec
CaptureRefined == Capture!Spec
MediaRefined == Media!Spec
GameinfoRefined == Gameinfo!Spec
====
