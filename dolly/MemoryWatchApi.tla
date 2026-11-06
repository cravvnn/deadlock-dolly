---- MODULE MemoryWatchApi ----
\* Caller-facing warning episode contract; process counters and GUI rendering
\* are outside this interface. One signal at most until episode reset.
VARIABLES episode, warned, signal
vars == <<episode,warned,signal>>
Init == /\ episode = 0 /\ warned = FALSE /\ signal = FALSE
Observe == /\ episode' \in {0,1,2} /\ signal' \in BOOLEAN
           /\ (episode' = 0 => ~signal')
           /\ (episode' = episode /\ warned => ~signal')
           /\ warned' = IF episode' # episode \/ episode' = 0
                         THEN signal' ELSE warned \/ signal'
Spec == Init /\ [][Observe]_vars
====
