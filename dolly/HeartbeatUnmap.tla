---- MODULE HeartbeatUnmap ----
EXTENDS Naturals
\* _beat checks stop/open then writes; close stops and bounded-joins then unmaps.
\* Candidate uses an independent lifetime mutex around each check/write and
\* around final _closed/unmap, not the command lock. One beat, one close.
\* Join may time out while beat is paused. Returning actions assumed; no proof
\* of OS atomic-call termination or bounded mutex acquisition.
CONSTANT Guarded
VARIABLES b,c,locked,stopped,mapped,bad
vars == <<b,c,locked,stopped,mapped,bad>>
Init == /\ b="check" /\ c="stop" /\ locked=FALSE /\ stopped=FALSE /\ mapped=TRUE /\ bad=FALSE
Check == /\ b="check" /\ ~locked
         /\ b' = IF stopped THEN "done" ELSE "store"
         /\ locked'= (Guarded /\ ~stopped)
         /\ UNCHANGED <<c,stopped,mapped,bad>>
Store == /\ b="store" /\ b'="done" /\ bad' = ~mapped
         /\ locked'=FALSE /\ UNCHANGED <<c,stopped,mapped>>
Stop == /\ c="stop" /\ c'="join" /\ stopped'=TRUE
        /\ UNCHANGED <<b,locked,mapped,bad>>
Join == /\ c="join" /\ c'="unmap" /\ UNCHANGED <<b,locked,stopped,mapped,bad>>
Unmap == /\ c="unmap" /\ ~locked /\ c'="done" /\ mapped'=FALSE
         /\ UNCHANGED <<b,locked,stopped,bad>>
Run == Check \/ Store \/ Stop \/ Join \/ Unmap
Done == /\ b="done" /\ c="done" /\ UNCHANGED vars
Spec == Init /\ [][Run \/ Done]_vars /\ WF_vars(Run)
TypeOK == /\ b \in {"check","store","done"} /\ c \in {"stop","join","unmap","done"}
          /\ locked \in BOOLEAN /\ stopped \in BOOLEAN /\ mapped \in BOOLEAN /\ bad \in BOOLEAN
NoWriteAfterUnmap == ~bad
Returns == <>(b="done" /\ c="done")
====
