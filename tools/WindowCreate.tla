---- MODULE WindowCreate ----
EXTENDS Naturals
\* filter_captured_audio_window.filter_window, called by ClipAudioCapture.finish.
\* One writer checks/parses/opens/writes; a distinct peer can create the named
\* output before exclusive open. Open is the atomic OS no-replace primitive.
\* Write refusal may leave an owned partial; no complete publication, namespace
\* replacement after open, symlink attack, durable crash or global lock proof.
\* Local finite safety and completion-shape refinement only; terminal deadlock checking disabled.
VARIABLES pc, owner, peer, clobbered
vars == <<pc, owner, peer, clobbered>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE WindowCreateApi WITH phase <- phase
Init == /\ pc = "check" /\ owner = "none" /\ peer = FALSE /\ clobbered = FALSE
Check == /\ pc = "check" /\ pc' = "parse" /\ UNCHANGED <<owner,peer,clobbered>>
Parse == /\ pc = "parse" /\ pc' = "open" /\ UNCHANGED <<owner,peer,clobbered>>
PeerCreate == /\ pc \in {"parse","open"} /\ owner = "none" /\ ~peer
              /\ owner' = "peer" /\ peer' = TRUE /\ UNCHANGED <<pc,clobbered>>
Open == /\ pc = "open"
        /\ pc' = IF owner = "peer" THEN "done" ELSE "write"
        /\ clobbered' = clobbered
        /\ owner' = IF owner = "peer" THEN owner ELSE "ours-partial"
        /\ UNCHANGED peer
Write == /\ pc = "write" /\ pc' = "done" /\ owner' = "ours-complete" /\ UNCHANGED <<peer,clobbered>>
WriteError == /\ pc = "write" /\ pc' = "done" /\ UNCHANGED <<owner,peer,clobbered>>
Next == Check \/ Parse \/ PeerCreate \/ Open \/ Write \/ WriteError
TypeOK == /\ pc \in {"check","parse","open","write","done"}
          /\ owner \in {"none","peer","ours-partial","ours-complete"}
          /\ peer \in BOOLEAN /\ clobbered \in BOOLEAN
PeerPreserved == ~clobbered
PeerRemains == peer => owner = "peer"
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
