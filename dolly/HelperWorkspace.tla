---- MODULE HelperWorkspace ----
EXTENDS Naturals
\* source2viewer.ensure_viewer after both callers missed existing cache and the
\* root exists. Actor numbers retain artifact owners; 0 means absent. Bytes are
\* identical pinned payloads, so another verified owner's bytes aren't invalid.
\* Download abstracts full _download with size/hash checks into a private archive.
\* Extract abstracts safe ZIP+CLI verification. Install is a delegated successful
\* per-file publication call; individual DLL moves/partial installs are NOT
\* enumerated and no atomic cache-wide publication is claimed. Notice includes
\* license. Cache CLI/marker are shared; an old marker may exist, and a failed
\* marker write may create it (discovery checks presence, not JSON contents).
\* Download/hash internals and partial failed-download bytes are abstracted;
\* real-file/process probes supplement these operation-return boundaries.
\* Cleanup visits private archive/stage in either order, ignoring OSError.
\* Two returning attempts, no external executable locks/cache tampering or
\* private-workspace replacement, networking/ZIP parser internals/power loss.
CONSTANTS Faults
Actors == {1,2}
key(i) == i
VARIABLES pc,archive,stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed
vars == <<pc,archive,stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>
phase == IF \A i \in Actors : pc[i] = "done" THEN "done" ELSE "active"
API == INSTANCE HelperWorkspaceApi WITH phase <- phase
Init == /\ pc = [i \in Actors |-> "acquire"]
        /\ archive = [i \in Actors |-> 0]
        /\ stage = [i \in Actors |-> 0] /\ workspace = [i \in Actors |-> FALSE]
        /\ cli = FALSE /\ marker \in BOOLEAN /\ outcome = [i \in Actors |-> "pending"]
        /\ foreignDeleted = FALSE
        /\ remaining = [i \in Actors |-> {}]
        /\ beforeCleanup = [i \in Actors |-> "pending"] /\ confirmed = [i \in Actors |-> FALSE]
Acquire(i) == /\ pc[i] = "acquire"
    /\ workspace' = ([workspace EXCEPT ![i] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "download"])
    /\ UNCHANGED <<archive,stage,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Download(i) == /\ pc[i] = "download"
    /\ archive' = ([archive EXCEPT ![key(i)] = i])
    /\ pc' = ([pc EXCEPT ![i] = "extract"])
    /\ UNCHANGED <<stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Extract(i) == /\ pc[i] = "extract" /\ archive[key(i)] # 0
    /\ stage' = ([stage EXCEPT ![key(i)] = i])
    /\ pc' = ([pc EXCEPT ![i] = "install"])
    /\ UNCHANGED <<archive,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Install(i) == /\ pc[i] = "install" /\ (stage[key(i)] # 0 \/ cli)
    /\ cli' = (TRUE)
    /\ stage' = ([stage EXCEPT ![key(i)] = 0])
    /\ pc' = ([pc EXCEPT ![i] = "validate"])
    /\ UNCHANGED <<archive,workspace,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Validate(i) == /\ pc[i] = "validate" /\ cli
    /\ confirmed' = ([confirmed EXCEPT ![i] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "notice"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup>>

Notice(i) == /\ pc[i] = "notice"
    /\ pc' = ([pc EXCEPT ![i] = "marker"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Marker(i) == /\ pc[i] = "marker"
    /\ marker' = (TRUE)
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ outcome' = ([outcome EXCEPT ![i] = "ok"])
    /\ UNCHANGED <<archive,stage,workspace,cli,foreignDeleted,remaining,beforeCleanup,confirmed>>

Missing(i) == /\ (pc[i] = "extract" /\ archive[key(i)] = 0) \/ (pc[i] = "install" /\ stage[key(i)] = 0 /\ ~cli)
    /\ outcome' = ([outcome EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,foreignDeleted,remaining,beforeCleanup,confirmed>>

IOError(i) == /\ Faults /\ pc[i] \in {"acquire","download","extract","install","validate","notice","marker"}
    /\ outcome' = ([outcome EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = IF pc[i] = "acquire" THEN "done" ELSE "cleanup"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,foreignDeleted,remaining,beforeCleanup,confirmed>>

MarkerWriteError(i) == /\ Faults /\ pc[i] = "marker"
    /\ marker' = (TRUE)
    /\ outcome' = ([outcome EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ UNCHANGED <<archive,stage,workspace,cli,foreignDeleted,remaining,beforeCleanup,confirmed>>

CleanupStart(i) == /\ pc[i] = "cleanup"
    /\ remaining' = ([remaining EXCEPT ![i] = {2,3}])
    /\ beforeCleanup' = ([beforeCleanup EXCEPT ![i] = outcome[i]])
    /\ pc' = ([pc EXCEPT ![i] = "cleaning"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,outcome,foreignDeleted,confirmed>>

CleanupArchive(i) == /\ pc[i] = "cleaning" /\ 2 \in remaining[i]
    /\ foreignDeleted' = (foreignDeleted \/ (archive[key(i)] # 0 /\ archive[key(i)] # i))
    /\ archive' = ([archive EXCEPT ![key(i)] = 0])
    /\ remaining' = ([remaining EXCEPT ![i] = @ \ {2}])
    /\ UNCHANGED <<pc,stage,workspace,cli,marker,outcome,beforeCleanup,confirmed>>

DenyArchive(i) == /\ Faults /\ pc[i] = "cleaning" /\ 2 \in remaining[i]
    /\ pc' = ([pc EXCEPT ![i] = pc[i]])
    /\ outcome' = ([outcome EXCEPT ![i] = outcome[i]])
    /\ remaining' = ([remaining EXCEPT ![i] = @ \ {2}])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,foreignDeleted,beforeCleanup,confirmed>>

CleanupStage(i) == /\ pc[i] = "cleaning" /\ 3 \in remaining[i]
    /\ foreignDeleted' = (foreignDeleted \/ (stage[key(i)] # 0 /\ stage[key(i)] # i))
    /\ stage' = ([stage EXCEPT ![key(i)] = 0])
    /\ remaining' = ([remaining EXCEPT ![i] = @ \ {3}])
    /\ UNCHANGED <<pc,archive,workspace,cli,marker,outcome,beforeCleanup,confirmed>>

DenyStage(i) == /\ Faults /\ pc[i] = "cleaning" /\ 3 \in remaining[i]
    /\ pc' = ([pc EXCEPT ![i] = pc[i]])
    /\ outcome' = ([outcome EXCEPT ![i] = outcome[i]])
    /\ remaining' = ([remaining EXCEPT ![i] = @ \ {3}])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,foreignDeleted,beforeCleanup,confirmed>>

CleanupFinish(i) == /\ pc[i] = "cleaning" /\ remaining[i] = {}
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ workspace' = ([workspace EXCEPT ![i] = IF archive[key(i)] = 0 /\ stage[key(i)] = 0 THEN FALSE ELSE @])
    /\ UNCHANGED <<archive,stage,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

DenyDirectory(i) == /\ Faults /\ pc[i] = "cleaning" /\ remaining[i] = {}
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,outcome,foreignDeleted,remaining,beforeCleanup,confirmed>>

Crash(i) == /\ Faults /\ pc[i] # "done"
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ outcome' = ([outcome EXCEPT ![i] = "crashed"])
    /\ UNCHANGED <<archive,stage,workspace,cli,marker,foreignDeleted,remaining,beforeCleanup,confirmed>>
Run == \E i \in Actors : Acquire(i) \/ Download(i) \/ Extract(i) \/ Install(i) \/ Validate(i) \/ Notice(i) \/ Marker(i) \/ Missing(i) \/ IOError(i) \/ MarkerWriteError(i) \/ CleanupStart(i) \/ CleanupArchive(i) \/ DenyArchive(i) \/ CleanupStage(i) \/ DenyStage(i) \/ CleanupFinish(i) \/ DenyDirectory(i) \/ Crash(i)
Done == /\ phase = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in [Actors -> {"acquire","download","extract","install","validate","notice","marker","cleanup","cleaning","done"}]
          /\ archive \in [Actors -> {0,1,2}] /\ stage \in [Actors -> {0,1,2}]
          /\ workspace \in [Actors -> BOOLEAN] /\ cli \in BOOLEAN /\ marker \in BOOLEAN
          /\ outcome \in [Actors -> {"pending","ok","error","crashed"}] /\ foreignDeleted \in BOOLEAN
          /\ remaining \in [Actors -> SUBSET {2,3}]
          /\ beforeCleanup \in [Actors -> {"pending","ok","error","crashed"}] /\ confirmed \in [Actors -> BOOLEAN]
SuccessfulCache == \A i \in Actors : outcome[i] = "ok" => cli /\ marker /\ confirmed[i]
OwnCleanupOnly == ~foreignDeleted
CleanupPreservesResult == (\A i \in Actors : beforeCleanup[i] # "pending" => outcome[i] \in {beforeCleanup[i],"crashed"})
NoFaultSuccess == ~Faults /\ phase = "done" => \A i \in Actors : outcome[i] = "ok"
Returns == <>(phase = "done")
Refines == API!Spec
====
