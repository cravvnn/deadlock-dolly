---- MODULE RestoreHandles ----
EXTENDS Naturals
\* game_logging.gui_stdio: two saved flags, enabling, body, ordered restoration.
\* Candidate registers both ExitStack callbacks before mutation; reverse
\* registration preserves original restore order1then2. Callbacks never suppress
\* exceptions. A failing restoration leaves that flag unchanged, and all other
\* callbacks still run. game_logging.spawn is the sole production caller;
\* launcher.launch calls spawn. No child or launcher lifecycle is modeled here.
\* Returning set-handle calls only; flags/handles are separate resources.
\* Inheritance/get/open failures before any flag mutation, context teardown of
\* private stdin, actual child inheritance and global lock contention excluded.
\* When both restorations fail, ExitStack propagates the last callback error;
\* the model abstracts both as restore_error. Body errors may be masked.
\* Weak fairness assumes returning calls; terminal Done deliberately stutters.
Handles == {1,2}
VARIABLES original,flags,faults,body,result,pc,attempted
vars == <<original,flags,faults,body,result,pc,attempted>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE RestoreHandlesApi WITH phase <- phase
Init == /\ original \in [Handles -> BOOLEAN] /\ flags = original
        /\ faults \in SUBSET Handles /\ body \in {"ok","body_error"}
        /\ result = body /\ pc = "enable1" /\ attempted = {}
Enable1 == /\ pc = "enable1" /\ flags' = [flags EXCEPT ![1] = TRUE]
           /\ pc' = "enable2" /\ UNCHANGED <<original,faults,body,result,attempted>>
Enable2 == /\ pc = "enable2" /\ flags' = [flags EXCEPT ![2] = TRUE]
           /\ pc' = "body" /\ UNCHANGED <<original,faults,body,result,attempted>>
EnableError == /\ pc \in {"enable1","enable2"}
               /\ result' = "setup_error" /\ pc' = "restore1"
               /\ UNCHANGED <<original,flags,faults,body,attempted>>
Body == /\ pc = "body" /\ pc' = "restore1"
        /\ UNCHANGED <<original,flags,faults,body,result,attempted>>
Restore1 == /\ pc = "restore1" /\ 1 \notin faults
            /\ flags' = [flags EXCEPT ![1] = original[1]]
            /\ attempted' = attempted \cup {1} /\ pc' = "restore2"
            /\ UNCHANGED <<original,faults,body,result>>
Deny1 == /\ pc = "restore1" /\ 1 \in faults
         /\ result' = "restore_error" /\ attempted' = attempted \cup {1}
         /\ pc' = "restore2"
         /\ UNCHANGED <<original,flags,faults,body>>
Restore2 == /\ pc = "restore2" /\ 2 \notin faults
            /\ flags' = [flags EXCEPT ![2] = original[2]]
            /\ attempted' = attempted \cup {2} /\ pc' = "done"
            /\ UNCHANGED <<original,faults,body,result>>
Deny2 == /\ pc = "restore2" /\ 2 \in faults
         /\ result' = "restore_error" /\ attempted' = attempted \cup {2}
         /\ pc' = "done" /\ UNCHANGED <<original,flags,faults,body>>
Run == Enable1 \/ Enable2 \/ EnableError \/ Body \/ Restore1 \/ Deny1 \/ Restore2 \/ Deny2
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ flags \in [Handles -> BOOLEAN] /\ original \in [Handles -> BOOLEAN]
          /\ faults \subseteq Handles /\ attempted \subseteq Handles
          /\ result \in {"ok","body_error","setup_error","restore_error"}
BothAttempted == (pc = "done") => attempted = Handles
RestorableRestored == (pc = "done") => \A h \in Handles \ faults : flags[h] = original[h]
NoFalseSuccess == (pc = "done" /\ result = "ok") => flags = original
Returns == <>(pc = "done")
Refines == API!Spec
====
