---- MODULE ScratchStatus ----
EXTENDS Naturals
\* player_scratch.update only, two status updates on one valid owned take.
\* GUI _finish_player_capture calls captured/cancelled/encode_failed; cleanup
\* calls completed. Values 1/2 stand for complete JSON payloads with distinct
\* statuses, but JSON fields and merge/lost-update behavior aren't modeled.
\* Read/ownership validation/open/partial dump/full dump/close/replace separate.
\* A status write is not fsynced in either implementation: process exit probes
\* supplement this model, but power loss durability is expressly not promised.
\* Initially an old failed/crashed attempt may have left capture.tmp (baseline)
\* or a private orphan at slot3(candidate). Candidate keys1/2 are fresh here;
\* random-name collisions in fault-enabled Init occupy either fresh slot with
\* owner3: exclusive-open error, not permission to unlink. No live replacement
\* of a successfully opened private path by a hostile process is modeled.
\* Existing path/owner/size validation delegated; external hostile namespace
\* replacement, malformed JSON/schema, Windows ACLs/reparse semantics excluded.
\* No status ordering or concurrent field-merge promise; last successful
\* replacement wins. Existing raw payload/output never modified by this owner.
\* GUI encode workflow/native capture are NOT proved; real cleanup adapter and
\* existing tests check consumers. Private leftovers intentionally not swept.
CONSTANTS Faults, Sequential, Collisions
Actors == {1,2}
Slots == {0,1,2,3}
key(i) == i
VARIABLES pc, owners, full, manifest, complete, result, opened, validated, foreignDeleted, prior
vars == <<pc,owners,full,manifest,complete,result,opened,validated,foreignDeleted,prior>>
phase == IF \A i \in Actors : pc[i] = "done" THEN "done" ELSE "active"
API == INSTANCE ScratchStatusApi WITH phase <- phase
Init == /\ pc = [i \in Actors |-> IF Sequential /\ i = 2 THEN "wait" ELSE "read"]
        /\ \E stale \in BOOLEAN, collisions \in (IF Faults /\ Collisions THEN SUBSET Actors ELSE {{}}) :
              /\ owners = [k \in Slots |-> IF (stale /\ k = 3) \/ k \in collisions THEN 3 ELSE 0]
              /\ full = [k \in Slots |-> FALSE]
        /\ manifest = 0 /\ complete = TRUE
        /\ result = [i \in Actors |-> "pending"]
        /\ opened = [i \in Actors |-> FALSE]
        /\ validated = [i \in Actors |-> FALSE]
        /\ foreignDeleted = FALSE /\ prior = [i \in Actors |-> "pending"]
Admit(i) == /\ pc[i] = "wait" /\ pc[1] = "done"
    /\ pc' = ([pc EXCEPT ![i] = "read"])
    /\ UNCHANGED <<owners,full,manifest,complete,result,opened,validated,foreignDeleted,prior>>

Read(i) == /\ pc[i] = "read"
    /\ pc' = ([pc EXCEPT ![i] = "validate"])
    /\ UNCHANGED <<owners,full,manifest,complete,result,opened,validated,foreignDeleted,prior>>

Validate(i) == /\ pc[i] = "validate"
    /\ validated' = ([validated EXCEPT ![i] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "open"])
    /\ UNCHANGED <<owners,full,manifest,complete,result,opened,foreignDeleted,prior>>

Open(i) == /\ pc[i] = "open" /\ owners[key(i)] = 0
    /\ owners' = ([owners EXCEPT ![key(i)] = i])
    /\ full' = ([full EXCEPT ![key(i)] = FALSE])
    /\ opened' = ([opened EXCEPT ![i] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "partial"])
    /\ UNCHANGED <<manifest,complete,result,validated,foreignDeleted,prior>>

Occupied(i) == /\ pc[i] = "open" /\ owners[key(i)] # 0
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ UNCHANGED <<owners,full,manifest,complete,opened,validated,foreignDeleted,prior>>

PartialDump(i) == /\ pc[i] = "partial"
    /\ full' = ([full EXCEPT ![key(i)] = FALSE])
    /\ pc' = ([pc EXCEPT ![i] = "write"])
    /\ UNCHANGED <<owners,manifest,complete,result,opened,validated,foreignDeleted,prior>>

FinishDump(i) == /\ pc[i] = "write"
    /\ full' = ([full EXCEPT ![key(i)] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "close"])
    /\ UNCHANGED <<owners,manifest,complete,result,opened,validated,foreignDeleted,prior>>

Close(i) == /\ pc[i] = "close"
    /\ pc' = ([pc EXCEPT ![i] = "replace"])
    /\ UNCHANGED <<owners,full,manifest,complete,result,opened,validated,foreignDeleted,prior>>

Replace(i) == /\ pc[i] = "replace"
    /\ manifest' = (owners[key(i)])
    /\ complete' = (full[key(i)])
    /\ owners' = ([owners EXCEPT ![key(i)] = 0])
    /\ full' = ([full EXCEPT ![key(i)] = FALSE])
    /\ result' = ([result EXCEPT ![i] = "ok"])
    /\ prior' = ([prior EXCEPT ![i] = "ok"])
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ UNCHANGED <<opened,validated,foreignDeleted>>

EarlyError(i) == /\ Faults /\ pc[i] \in {"read","validate","open"}
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ UNCHANGED <<owners,full,manifest,complete,opened,validated,foreignDeleted,prior>>

WriteError(i) == /\ Faults /\ pc[i] \in {"partial","write","close","replace"}
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ prior' = ([prior EXCEPT ![i] = "error"])
    /\ UNCHANGED <<owners,full,manifest,complete,opened,validated,foreignDeleted>>

Cleanup(i) == /\ pc[i] = "cleanup" /\ opened[i]
    /\ foreignDeleted' = (foreignDeleted \/ (owners[key(i)] # 0 /\ owners[key(i)] # i))
    /\ owners' = ([owners EXCEPT ![key(i)] = 0])
    /\ full' = ([full EXCEPT ![key(i)] = FALSE])
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<manifest,complete,result,opened,validated,prior>>

CleanupError(i) == /\ Faults /\ pc[i] = "cleanup" /\ opened[i]
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<owners,full,manifest,complete,result,opened,validated,foreignDeleted,prior>>

Crash(i) == /\ Faults /\ pc[i] \notin {"wait","done"}
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ result' = ([result EXCEPT ![i] = "crash"])
    /\ UNCHANGED <<owners,full,manifest,complete,opened,validated,foreignDeleted,prior>>


Run == \E i \in Actors : Admit(i) \/ Read(i) \/ Validate(i) \/ Open(i) \/ Occupied(i)
       \/ PartialDump(i) \/ FinishDump(i) \/ Close(i) \/ Replace(i) \/ EarlyError(i)
       \/ WriteError(i) \/ Cleanup(i) \/ CleanupError(i) \/ Crash(i)
Done == /\ phase = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in [Actors -> {"wait","read","validate","open","partial","write","close","replace","cleanup","done"}]
          /\ owners \in [Slots -> {0,1,2,3}] /\ full \in [Slots -> BOOLEAN]
          /\ manifest \in {0,1,2,3} /\ complete \in BOOLEAN
          /\ result \in [Actors -> {"pending","ok","error","crash"}]
          /\ prior \in [Actors -> {"pending","ok","error"}]
          /\ opened \in [Actors -> BOOLEAN] /\ validated \in [Actors -> BOOLEAN]
          /\ foreignDeleted \in BOOLEAN
OnlyCompletePublication == complete
OnlyValidatedPublication == \A i \in Actors : result[i] = "ok" => validated[i]
OwnCleanupOnly == ~foreignDeleted
CleanupPreservesResult == \A i \in Actors : (pc[i] = "done" /\ prior[i] # "pending" /\ result[i] # "crash") => result[i] = prior[i]
FreshAttemptsProgress == (~Faults) => \A i \in Actors : pc[i] = "done" => result[i] = "ok"
Returns == <>(phase = "done")
Refines == API!Spec
====
