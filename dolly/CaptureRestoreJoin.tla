---- MODULE CaptureRestoreJoin ----
EXTENDS Naturals
\* VideoExport.stop(capture_only): _confirm_player_stop then
\* _wait_player_restore_context then capture_only=False/mark_recording_pending.
\* DollyApp finish worker calls this; P06 exercises real owner/pinned sidecars.
\* Ack is validated terminal capture readback, NOT camera path completion.
\* Two composite session/replay/request identities. Switch once while waiting;
\* post-readback check refuses replacement. Context fixed after that check.
\* Three returning console attempts; timeout exhaustion retains all reservations.
\* Live process only; no late check/write race, arbitrary concurrent Stop calls,
\* actual GPU drain, byte/codec correctness, process exit or clock liveness proof.
VARIABLES pc,current,ack,verified,pending,timing,attempt
vars == <<pc,current,ack,verified,pending,timing,attempt>>
API == INSTANCE CaptureRestoreApi WITH pending <- pending,ack <- ack,context <- verified
Init == /\ pc = "ack" /\ current = 1 /\ ack = FALSE /\ verified = FALSE
        /\ pending = TRUE /\ timing = TRUE /\ attempt = 0
Wait == /\ pc = "ack" /\ ack' \in BOOLEAN /\ current' \in {1,2}
        /\ pc' = IF ack' THEN "context" ELSE "retained"
        /\ UNCHANGED <<verified,pending,timing,attempt>>
Context == /\ pc = "context"
           /\ pc' = IF current = 1 THEN "console" ELSE "retained"
           /\ UNCHANGED <<current,ack,verified,pending,timing,attempt>>
ConsoleReady == /\ pc = "console" /\ verified' = TRUE /\ pc' = "retire"
                /\ UNCHANGED <<current,ack,pending,timing,attempt>>
ConsoleTimeout == /\ pc = "console" /\ attempt' = attempt + 1
                  /\ pc' = IF attempt < 2 THEN "context" ELSE "retained"
                  /\ UNCHANGED <<current,ack,verified,pending,timing>>
Retire == /\ pc = "retire" /\ pending' = FALSE /\ pc' = "restore"
          /\ UNCHANGED <<current,ack,verified,timing,attempt>>
Restore == /\ pc = "restore" /\ timing' = FALSE /\ pc' = "done"
           /\ UNCHANGED <<current,ack,verified,pending,attempt>>
Done == /\ pc \in {"done","retained"} /\ UNCHANGED vars
Run == Wait \/ Context \/ ConsoleReady \/ ConsoleTimeout \/ Retire \/ Restore
Spec == Init /\ [][Run \/ Done]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"ack","context","console","retire","restore","retained","done"}
          /\ current \in {1,2} /\ ack \in BOOLEAN /\ verified \in BOOLEAN
          /\ pending \in BOOLEAN /\ timing \in BOOLEAN /\ attempt \in 0..3
AckBeforeRetirement == ~pending => ack
SameOwner == ~pending => current = 1
ReadyBeforeRetirement == ~pending => verified
RetainedOriginals == pending => timing
ReturnsOrRetains == <>(pc \in {"done","retained"})
Refines == API!Spec
====
