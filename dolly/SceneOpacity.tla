---- MODULE SceneOpacity ----
EXTENDS Naturals, Sequences
\* Controller.near_player_opacity_fix / _restore_near_player_opacity;
\* called by editor_dispatch and disconnect. Snapshot before enable; publish
\* ownership only after verified enable. Failed off readback retains originals.
\* Retry is bounded to two attempts. Disconnect uses the same restore helper.
\* Models two asymmetric values as 0/1, not float precision or the game engine.
\* Enable failure rollback uses existing _write_look_values/_look_restore;
\* treated as terminal without claiming successful cleanup of partial writes.
\* editor_view/wire/native checkbox is a stateless projection tested in code.
VARIABLES pc, original, saved, game, active, attempts
vars == <<pc, original, saved, game, active, attempts>>
Init == /\ pc = "enable" /\ original \in {<<0,0>>, <<0,1>>, <<1,0>>, <<1,1>>}
        /\ saved = <<>> /\ game = original /\ active = FALSE /\ attempts = 0
Enable == /\ pc = "enable" /\ pc' = "on" /\ saved' = original
          /\ game' = <<1,1>> /\ active' = TRUE /\ UNCHANGED <<original, attempts>>
EnableError == /\ pc = "enable" /\ pc' = "failed"
               /\ UNCHANGED <<original, saved, game, active, attempts>>
RestoreFirst == /\ pc = "on" /\ pc' = "partial" /\ attempts' = attempts + 1
                /\ game' = <<saved[1], game[2]>> /\ UNCHANGED <<original,saved,active>>
RestoreSecond == /\ pc = "partial" /\ pc' = "verify" /\ game' = saved
                 /\ UNCHANGED <<original,saved,active,attempts>>
Verify == /\ pc = "verify" /\ pc' = "off" /\ saved' = <<>> /\ active' = FALSE
          /\ UNCHANGED <<original,game,attempts>>
RestoreError == /\ pc \in {"partial", "verify"}
                /\ pc' = IF attempts < 2 THEN "on" ELSE "pending"
                /\ UNCHANGED <<original,saved,game,active,attempts>>
Done == /\ pc \in {"off","failed","pending"} /\ UNCHANGED vars
Next == Enable \/ EnableError \/ RestoreFirst \/ RestoreSecond \/ Verify \/ RestoreError \/ Done
Spec == Init /\ [][Next]_vars
TypeOK == /\ pc \in {"enable","on","partial","verify","off","failed","pending"}
          /\ active \in BOOLEAN /\ attempts \in 0..2
Ownership == active => saved = original
NoFalseOff == pc = "off" => /\ game = original /\ ~active /\ saved = <<>>
RetryEvidence == pc = "pending" => active /\ saved = original
====
