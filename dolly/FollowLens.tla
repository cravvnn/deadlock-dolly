---- MODULE FollowLens ----
EXTENDS Naturals
\* FollowTransaction.apply/restore projection: one opaque offset, ADS lens,
\* enable flag; two updates; one write/readback or restoration failure.
\* Callbacks enforce owned session/typed writes; ownership loss/reacquisition
\* abstracts the same original session. No process/game/ConVar implementation
\* or crash durability proof. Controller selection is in FollowSelection.tla.
VARIABLES initial, actual, saved, pending, pc, step, wanted, owned,
          authority, faults, updates
vars == <<initial,actual,saved,pending,pc,step,wanted,owned,authority,faults,updates>>
Init == /\ initial \in {[normal |-> n, ads |-> 45, offset |-> 0, enabled |-> e] : n \in {90,150}, e \in {0,1}}
        /\ actual = initial /\ saved = initial /\ pending = FALSE
        /\ pc = "ready" /\ step = 0 /\ wanted = 90 /\ owned = TRUE
        /\ authority = TRUE /\ faults = 1 /\ updates = 0
Apply == /\ pc \in {"ready","applied"} /\ owned /\ actual.normal <= 120
         /\ updates < 2
         /\ saved' = IF pending THEN saved ELSE actual
         /\ pending' = TRUE /\ wanted' = actual.normal
         /\ pc' = "write" /\ step' = 1 /\ updates' = updates + 1
         /\ UNCHANGED <<initial,actual,owned,authority,faults>>
Reject == /\ pc = "ready" /\ actual.normal > 120 /\ pc' = "done"
          /\ UNCHANGED <<initial,actual,saved,pending,step,wanted,owned,authority,faults,updates>>
WriteOffset == /\ pc = "write" /\ step = 1 /\ owned
               /\ actual' = [actual EXCEPT !.offset = updates]
               /\ authority' = owned /\ step' = 2
               /\ UNCHANGED <<initial,saved,pending,pc,wanted,owned,faults,updates>>
WriteLens == /\ pc = "write" /\ step = 2 /\ owned
             /\ actual' = [actual EXCEPT !.ads = wanted]
             /\ authority' = owned /\ step' = 3
             /\ UNCHANGED <<initial,saved,pending,pc,wanted,owned,faults,updates>>
Enable == /\ pc = "write" /\ step = 3 /\ owned
          /\ actual' = [actual EXCEPT !.enabled = 1]
          /\ authority' = owned /\ pc' = "applied" /\ step' = 0
          /\ UNCHANGED <<initial,saved,pending,wanted,owned,faults,updates>>
StartRestore == /\ pc = "applied" /\ pc' = "restore" /\ step' = 0
                /\ UNCHANGED <<initial,actual,saved,pending,wanted,owned,authority,faults,updates>>
Disable == /\ pc = "restore" /\ step = 0 /\ owned
           /\ actual' = [actual EXCEPT !.enabled = 0]
           /\ authority' = owned /\ step' = 1
           /\ UNCHANGED <<initial,saved,pending,pc,wanted,owned,faults,updates>>
RestoreOffset == /\ pc = "restore" /\ step = 1 /\ owned
                 /\ actual' = [actual EXCEPT !.offset = saved.offset]
                 /\ authority' = owned /\ step' = 2
                 /\ UNCHANGED <<initial,saved,pending,pc,wanted,owned,faults,updates>>
RestoreLens == /\ pc = "restore" /\ step = 2 /\ owned
               /\ actual' = [actual EXCEPT !.ads = saved.ads]
               /\ authority' = owned /\ step' = 3
               /\ UNCHANGED <<initial,saved,pending,pc,wanted,owned,faults,updates>>
RestoreEnable == /\ pc = "restore" /\ step = 3 /\ owned
                 /\ actual' = [actual EXCEPT !.enabled = saved.enabled]
                 /\ pending' = FALSE /\ authority' = owned /\ pc' = "done"
                 /\ UNCHANGED <<initial,saved,step,wanted,owned,faults,updates>>
Failure == /\ pc \in {"write","restore"} /\ faults = 1
           /\ faults' = 0 /\ pc' = "blocked" /\ step' = 0
           /\ pending' = TRUE
           /\ UNCHANGED <<initial,actual,saved,wanted,owned,authority,updates>>
RetryRestore == /\ pc = "blocked" /\ owned /\ pc' = "restore"
                /\ UNCHANGED <<initial,actual,saved,pending,step,wanted,owned,authority,faults,updates>>
LoseOwner == /\ owned /\ owned' = FALSE
             /\ UNCHANGED <<initial,actual,saved,pending,pc,step,wanted,authority,faults,updates>>
RegainOwner == /\ ~owned /\ owned' = TRUE
               /\ UNCHANGED <<initial,actual,saved,pending,pc,step,wanted,authority,faults,updates>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Apply \/ Reject \/ WriteOffset \/ WriteLens \/ Enable \/ StartRestore
        \/ Disable \/ RestoreOffset \/ RestoreLens \/ RestoreEnable \/ Failure
        \/ RetryRestore \/ LoseOwner \/ RegainOwner \/ Done
Spec == Init /\ [][Next]_vars
SavedOriginals == pending => saved = initial
RestoredOrPending == ~pending => actual = initial
CorrectAimedLens == pc = "applied" => actual.ads = actual.normal
ReviewedAdsRange == actual.ads <= 120
WriteAuthority == authority
====
