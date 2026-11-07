---- MODULE AssetEnvironment ----
EXTENDS Naturals
\* export_captured_audio._convert_asset; called by export via ClipAudioCapture.finish/_finish_movie.
\* Two child spawns, returning success/refusal. Compose existing runtime context;
\* assume context enter/restore and child operations return. No global import race,
\* converter fidelity, PCM or OS DLL-loader proof.
\* Local finite safety and completion-shape refinement only; terminal deadlock checking disabled.
VARIABLES pc, pass, clean, launches, dirtyLaunch, failed
vars == <<pc, pass, clean, launches, dirtyLaunch, failed>>
UseContext == TRUE
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE AssetEnvironmentApi WITH phase <- phase
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
