---- MODULE ConsoleReconnect ----
EXTENDS Naturals
\* L05 process: Controller.connect under _op_lock while a playback worker
\* (Controller._thread, console or native) may still be unwinding and either
\* console may fail. connect calls stop(): _halt sets _stop_event and joins the
\* worker for 10 s, then playback/UI restores run. A stop failure is re-raised
\* while the old console is connected or the worker is still alive; otherwise
\* it is swallowed (the restore stays pending). Then the old client is closed,
\* port ownership is checked, a new ConsoleClient connects and is installed,
\* and the ready echo runs; any failure there closes it and leaves _console
\* None. The worker's finally (_finish_playback) talks to whatever _console
\* holds, without _op_lock, so it must be gone before a new client is
\* installed. ConsoleClient retires a socket on a send or read failure (socket
\* cleared and shut down, reader exits) only while it is still current; close()
\* always records "connection closed". One connect call, one worker, the old and
\* new client, one failure each; join timeout, restore failure, port check, open
\* and echo outcomes are nondeterministic. stop_game_follow, native bridge
\* setup, _capture_game_build and the request protocol are not modeled.
VARIABLES pc, console, oldOpen, oldWhy, newOpen, newWhy, worker
vars == <<pc, console, oldOpen, oldWhy, newOpen, newWhy, worker>>
Terminal == {"done", "failed", "refused"}
result == CASE pc = "done" -> "connected"
            [] pc \in {"failed", "refused"} -> pc
            [] OTHER -> "pending"
API == INSTANCE ConsoleReconnectApi WITH result <- result
OldConnected == console = "old" /\ oldOpen /\ oldWhy = "none"

Init == /\ pc = "halt" /\ worker \in {"running", "done"}
        /\ \/ console = "none" /\ oldOpen = FALSE /\ oldWhy = "none"
           \/ console = "old" /\ oldOpen = TRUE /\ oldWhy = "none"
           \/ console = "old" /\ oldOpen = FALSE /\ oldWhy \in {"failed", "closed"}
        /\ newOpen = FALSE /\ newWhy = "unused"

\* _halt: set _stop_event, then join the worker.
Halt == /\ pc = "halt" /\ pc' = "join"
        /\ worker' = IF worker = "running" THEN "finishing" ELSE worker
        /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy>>
JoinOk == /\ pc = "join" /\ worker = "done" /\ pc' = "restore"
          /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
\* join(timeout=10) returns with the worker alive: "Playback is still waiting".
JoinTimeout == /\ pc = "join" /\ worker # "done" /\ pc' = "stopFailed"
               /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
\* HUD, playback and UI restores after the join; any may fail.
Restore == /\ pc = "restore" /\ pc' \in {"closeOld", "stopFailed"}
           /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
\* connect's except branch.
Reraise == /\ pc = "stopFailed" /\ (OldConnected \/ worker # "done")
           /\ pc' = "refused"
           /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
Swallow == /\ pc = "stopFailed" /\ ~OldConnected /\ worker = "done"
           /\ pc' = "closeOld"
           /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
\* if self._console: self._console.close()
CloseOld == /\ pc = "closeOld" /\ pc' = "port"
            /\ oldOpen' = FALSE
            /\ oldWhy' = IF console = "old" THEN "closed" ELSE oldWhy
            /\ UNCHANGED <<console, newOpen, newWhy, worker>>
PortCheck == /\ pc = "port"
             /\ \/ pc' = "open" /\ UNCHANGED console
                \/ pc' = "failed" /\ console' = "none"
             /\ UNCHANGED <<oldOpen, oldWhy, newOpen, newWhy, worker>>
\* ConsoleClient.connect; installed only after it returns.
Open == /\ pc = "open"
        /\ \/ pc' = "echo" /\ console' = "new" /\ newOpen' = TRUE /\ newWhy' = "none"
           \/ pc' = "failed" /\ console' = "none" /\ newOpen' = FALSE /\ newWhy' = "closed"
        /\ UNCHANGED <<oldOpen, oldWhy, worker>>
Echo == /\ pc = "echo"
        /\ \/ newOpen /\ newWhy = "none" /\ pc' = "done"
           \/ pc' = "rollback"
        /\ UNCHANGED <<console, oldOpen, oldWhy, newOpen, newWhy, worker>>
\* console.close(); self._console = None
Rollback == /\ pc = "rollback" /\ pc' = "failed"
            /\ console' = "none" /\ newOpen' = FALSE /\ newWhy' = "closed"
            /\ UNCHANGED <<oldOpen, oldWhy, worker>>

\* The worker leaves its loop on its own (shot finished, console error) or
\* after _stop_event, then runs _finish_playback through self._console.
WorkerStop == /\ worker = "running" /\ worker' = "finishing"
              /\ UNCHANGED <<pc, console, oldOpen, oldWhy, newOpen, newWhy>>
WorkerDone == /\ worker = "finishing" /\ worker' = "done"
              /\ UNCHANGED <<pc, console, oldOpen, oldWhy, newOpen, newWhy>>
\* A send or read failure on the current socket retires it (_fail).
OldFail == /\ oldOpen /\ oldWhy = "none"
           /\ oldOpen' = FALSE /\ oldWhy' = "failed"
           /\ UNCHANGED <<pc, console, newOpen, newWhy, worker>>
NewFail == /\ newOpen /\ newWhy = "none"
           /\ newOpen' = FALSE /\ newWhy' = "failed"
           /\ UNCHANGED <<pc, console, oldOpen, oldWhy, worker>>

Connect == Halt \/ JoinOk \/ JoinTimeout \/ Restore \/ Reraise \/ Swallow
           \/ CloseOld \/ PortCheck \/ Open \/ Echo \/ Rollback
Worker == WorkerStop \/ WorkerDone
Done == /\ pc \in Terminal /\ UNCHANGED vars
Next == Connect \/ Worker \/ OldFail \/ NewFail \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Connect) /\ WF_vars(WorkerDone)

TypeOK == /\ pc \in {"halt", "join", "restore", "stopFailed", "closeOld", "port",
                     "open", "echo", "rollback"} \cup Terminal
          /\ console \in {"old", "none", "new"}
          /\ oldOpen \in BOOLEAN /\ newOpen \in BOOLEAN
          /\ oldWhy \in {"none", "failed", "closed"}
          /\ newWhy \in {"unused", "none", "failed", "closed"}
          /\ worker \in {"running", "finishing", "done"}
OneOpen == ~(oldOpen /\ newOpen)
FailedClean == pc = "failed" => console = "none" /\ ~newOpen /\ ~oldOpen
RefusedUntouched == pc = "refused" => newWhy = "unused" /\ console # "new"
\* The unwinding worker never reaches the new connection.
WorkerQuiet == console = "new" => worker = "done"
Retired == /\ (oldWhy = "failed" => ~oldOpen)
           /\ (newWhy = "failed" => ~newOpen)
\* A failure on a socket that was already closed keeps "connection closed".
CloseReason == pc = "failed" => newWhy # "failed"
Returns == <>(pc \in Terminal)
WorkerEnds == (worker = "finishing") ~> (worker = "done")
Refines == API!Spec
====
