---- MODULE GameExitClose ----
EXTENDS Naturals
\* L05 process: a game exit while NativeBridge.start_flight holds _operations.
\* start_flight (native_bridge.py) waits for editor input, then for the Armed
\* acknowledgment; each wait polls up to N times (8 s) under _operations. The
\* dolly-game-exit watcher (launcher.py watch) and a user quit both reach
\* Session.close -> NativeBridge.close, which needs _operations. bind_game is
\* given the process's poll(), and both waits check it after each status read,
\* so a dead game ends the wait at its next poll instead of after the timeout.
\* One Poll step is an atomic status read, exit check and sleep: an exit after
\* the check lands before the next step, so it costs at most one poll interval.
\* Controller._op_lock and GUI recovery (recover_dead_session) are not modeled;
\* they wait on the same flight call. Session.close's configuration restore is
\* outside this model. The native ack is only produced while the game lives.
CONSTANT N
VARIABLES game, flight, stage, polls, late, ops, watcher, user, bodies, closed
vars == <<game, flight, stage, polls, late, ops, watcher, user, bodies, closed>>
Exited == game = "dead"

Init == /\ game = "alive" /\ flight = "idle" /\ stage = "editor" /\ polls = 0
        /\ late = 0 /\ ops = "none" /\ watcher = "waiting" /\ user = "open"
        /\ bodies = 0 /\ closed = FALSE

\* NativeBridge.start_flight: _check_open under _operations.
FlightStart == /\ flight = "idle" /\ ops = "none" /\ ~closed
               /\ ops' = "flight" /\ flight' = "waiting" /\ stage' = "editor" /\ polls' = 0
               /\ UNCHANGED <<game, late, watcher, user, bodies, closed>>
\* Editor input becomes ready, then the native callback acknowledges Armed.
Ready == /\ flight = "waiting" /\ game = "alive"
         /\ IF stage = "editor"
               THEN /\ stage' = "ack" /\ polls' = 0 /\ UNCHANGED <<flight, ops>>
               ELSE /\ flight' = "returned" /\ ops' = "none" /\ UNCHANGED <<stage, polls>>
         /\ UNCHANGED <<game, late, watcher, user, bodies, closed>>
\* Not ready: check the process, then sleep one poll interval.
Poll == /\ flight = "waiting" /\ polls < N
        /\ (stage = "editor" => ~Exited)
        /\ (stage = "ack" => ~Exited)
        /\ polls' = polls + 1
        /\ late' = IF Exited THEN late + 1 ELSE late
        /\ UNCHANGED <<game, flight, stage, ops, watcher, user, bodies, closed>>
\* _check_game raises; start_flight unwinds and leaves _operations.
ExitSeen == /\ flight = "waiting" /\ Exited
            /\ flight' = "failed" /\ ops' = "none"
            /\ UNCHANGED <<game, stage, polls, late, watcher, user, bodies, closed>>
Timeout == /\ flight = "waiting" /\ polls = N
           /\ flight' = "failed" /\ ops' = "none"
           /\ UNCHANGED <<game, stage, polls, late, watcher, user, bodies, closed>>

GameExit == /\ game = "alive" /\ game' = "dead"
            /\ UNCHANGED <<flight, stage, polls, late, ops, watcher, user, bodies, closed>>
\* process.wait(0.5) returns in the watcher thread.
Observe == /\ watcher = "waiting" /\ Exited /\ watcher' = "observed"
           /\ UNCHANGED <<game, flight, stage, polls, late, ops, user, bodies, closed>>
WatcherClose == /\ watcher = "observed" /\ ops = "none" /\ ops' = "watcher"
                /\ UNCHANGED <<game, flight, stage, polls, late, watcher, user, bodies, closed>>
UserQuit == /\ user = "open" /\ user' = "quitting"
            /\ UNCHANGED <<game, flight, stage, polls, late, ops, watcher, bodies, closed>>
UserClose == /\ user = "quitting" /\ ops = "none" /\ ops' = "user"
             /\ UNCHANGED <<game, flight, stage, polls, late, watcher, user, bodies, closed>>
\* NativeBridge.close: an already-closed bridge returns at once (_closed).
CloseBody == /\ ops \in {"watcher", "user"}
             /\ bodies' = IF closed THEN bodies ELSE bodies + 1
             /\ closed' = TRUE /\ ops' = "none"
             /\ watcher' = IF ops = "watcher" THEN "done" ELSE watcher
             /\ user' = IF ops = "user" THEN "done" ELSE user
             /\ UNCHANGED <<game, flight, stage, polls, late>>

Wait == Poll \/ ExitSeen \/ Timeout
Closing == Observe \/ WatcherClose \/ UserClose \/ CloseBody
\* Nothing left to do once the flight is not waiting and no close is running.
Done == /\ flight # "waiting" /\ ops = "none" /\ UNCHANGED vars
Next == FlightStart \/ Ready \/ Wait \/ GameExit \/ UserQuit \/ Closing \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Wait) /\ WF_vars(Closing)

TypeOK == /\ game \in {"alive", "dead"}
          /\ flight \in {"idle", "waiting", "returned", "failed"}
          /\ stage \in {"editor", "ack"} /\ polls \in 0..N /\ late \in 0..(2 * N)
          /\ ops \in {"none", "flight", "watcher", "user"}
          /\ watcher \in {"waiting", "observed", "done"}
          /\ user \in {"open", "quitting", "done"}
          /\ bodies \in 0..2 /\ closed \in BOOLEAN
\* After the game exits, a flight wait takes no further timeout poll while it
\* holds _operations (the close-latency fix).
ExitLatency == late = 0
CloseOnce == bodies <= 1
NoFlightAfterClose == closed => ops # "flight"
ExitCloses == Exited ~> closed
QuitCloses == (user = "quitting") ~> (user = "done")
====
