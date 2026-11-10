---- MODULE MovieCancel ----
EXTENDS Naturals
\* Production-only contract; private baseline comparisons stay in analysis.
\* Minimal active/done API, not whole-caller or whole-program refinement.
\* MovieWave.cancel via ClipAudioCapture.cancel (GUI stop/discard/recovery).
\* One fixed controller/session/output root, take A WAV, returning endmovie
\* success or lost acknowledgement, one acknowledged retry, repeated cancel.
\* A peer B can exist after A acknowledged its stop; a stale global endmovie
\* must not be sent by retired A. This is a helper-contract schedule, not a
\* demonstrated GUI peer interruption. Context.verify_owner checks launch/root
\* identity, not an active movie generation. Replaced roots/controllers are
\* rejected by existing actual code; identities cannot collapse to ownership.
\* No actual recorder, filesystem/console races, hangs, ClipAudio temp lifetime,
\* whole-GUI retry/refinement, waveform validity or liveness is claimed.
\* Cleanup returns; safety-only projection, intentional terminal stuttering,
\* deadlock checking on. Exact WAV/peer-file probes bind the decisive edges.
KeepUnconfirmed == TRUE
RetireAcknowledged == TRUE
VARIABLES pc,ack,wave,finished,peer
vars == <<pc,ack,wave,finished,peer>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE MovieCancelApi WITH phase <- phase
Init == /\ pc = "cancel" /\ ack \in BOOLEAN /\ wave = TRUE
        /\ finished = FALSE /\ peer = FALSE
Cancel == /\ pc = "cancel"
          /\ pc' = (IF ack THEN "peer" ELSE IF KeepUnconfirmed THEN "retry" ELSE "done")
          /\ wave' = (~ack /\ KeepUnconfirmed)
          /\ finished' = (ack /\ RetireAcknowledged) /\ UNCHANGED <<ack,peer>>
Retry == /\ pc = "retry" /\ pc' = "peer" /\ ack' = TRUE
         /\ wave' = FALSE /\ finished' = RetireAcknowledged /\ UNCHANGED peer
Peer == /\ pc = "peer" /\ pc' = "repeat" /\ peer' = TRUE
        /\ UNCHANGED <<ack,wave,finished>>
Repeat == /\ pc = "repeat" /\ pc' = "done"
          /\ peer' = (IF finished THEN peer ELSE FALSE)
          /\ UNCHANGED <<ack,wave,finished>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Cancel \/ Retry \/ Peer \/ Repeat \/ Done
TypeOK == /\ pc \in {"cancel","retry","peer","repeat","done"}
          /\ ack \in BOOLEAN /\ wave \in BOOLEAN
          /\ finished \in BOOLEAN /\ peer \in BOOLEAN
UnconfirmedKept == ~ack => wave
PeerPreserved == (pc = "done" /\ ack) => peer
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
