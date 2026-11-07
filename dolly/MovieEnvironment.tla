---- MODULE MovieEnvironment ----
EXTENDS Naturals
\* MovieWave.finish mapped resampling and final conversion; caller ClipAudioCapture._finish_movie.
\* Two returning subprocess calls, each may fail; compose runtime.external_program_environment.
\* Assume existing context serializes and enters/restores successfully; no FFmpeg,
\* PCM, global import races, process hangs or Windows kernel implementation model.
\* Finite safety/completion-shape contract; deadlock checking disabled at terminal states.
VARIABLES pc, pass, clean, launches, dirtyLaunch, failed
vars == <<pc, pass, clean, launches, dirtyLaunch, failed>>
UseContext == TRUE
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE MovieEnvironmentApi WITH phase <- phase
Init == /\ pc = "enter" /\ pass = 1 /\ clean = FALSE /\ launches = 0 /\ dirtyLaunch = FALSE /\ failed = FALSE
Enter == /\ pc = "enter" /\ clean' = UseContext /\ pc' = "spawn"
         /\ UNCHANGED <<pass, launches, dirtyLaunch, failed>>
Spawn(ok) == /\ pc = "spawn" /\ pc' = "leave" /\ failed' = ~ok
             /\ launches' = launches + 1 /\ dirtyLaunch' = (dirtyLaunch \/ ~clean)
             /\ UNCHANGED <<pass, clean>>
Leave == /\ pc = "leave" /\ clean' = FALSE
         /\ pc' = IF failed \/ pass = 2 THEN "done" ELSE "enter"
         /\ pass' = IF failed \/ pass = 2 THEN pass ELSE pass + 1
         /\ UNCHANGED <<launches, dirtyLaunch, failed>>
Next == Enter \/ Spawn(TRUE) \/ Spawn(FALSE) \/ Leave
TypeOK == /\ pc \in {"enter","spawn","leave","done"} /\ pass \in 1..2
          /\ launches \in 0..2 /\ clean \in BOOLEAN /\ dirtyLaunch \in BOOLEAN /\ failed \in BOOLEAN
Sanitized == ~dirtyLaunch
Restored == pc = "done" => ~clean
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
