---- MODULE ArchiveCreate ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* _write_diagnostics_archive, called by Controller.export_diagnostics after
\* _diagnostics_destination chooses a name. Check/Parse abstract destination
\* preparation and report construction; Open is ZipFile exclusive creation.
\* One archive and one peer that can create between selection and open.
\* A failed write may leave an owned partial; no ZIP transaction, crash,
\* namespace substitution, process identity or complete diagnostics workflow.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
Exclusive == TRUE
VARIABLES pc, owner, peer, clobbered
vars == <<pc, owner, peer, clobbered>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ArchiveCreateApi WITH phase <- phase

Init == /\ pc = "check" /\ owner = "none" /\ peer = FALSE /\ clobbered = FALSE
Check == /\ pc = "check" /\ pc' = "parse" /\ UNCHANGED <<owner,peer,clobbered>>
Parse == /\ pc = "parse" /\ pc' = "open" /\ UNCHANGED <<owner,peer,clobbered>>
PeerCreate == /\ pc \in {"parse","open"} /\ owner = "none" /\ ~peer
              /\ owner' = "peer" /\ peer' = TRUE /\ UNCHANGED <<pc,clobbered>>
Open == /\ pc = "open"
        /\ pc' = IF Exclusive /\ owner = "peer" THEN "done" ELSE "write"
        /\ clobbered' = (clobbered \/ (~Exclusive /\ owner = "peer"))
        /\ owner' = IF Exclusive /\ owner = "peer" THEN owner ELSE "ours-partial"
        /\ UNCHANGED peer
Write == /\ pc = "write" /\ pc' = "done" /\ owner' = "ours-complete" /\ UNCHANGED <<peer,clobbered>>
WriteError == /\ pc = "write" /\ pc' = "done" /\ UNCHANGED <<owner,peer,clobbered>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Check \/ Parse \/ PeerCreate \/ Open \/ Write \/ WriteError \/ Done
TypeOK == /\ pc \in {"check","parse","open","write","done"}
          /\ owner \in {"none","peer","ours-partial","ours-complete"}
          /\ peer \in BOOLEAN /\ clobbered \in BOOLEAN
PeerPreserved == ~clobbered
PeerRemains == peer => owner = "peer"
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
