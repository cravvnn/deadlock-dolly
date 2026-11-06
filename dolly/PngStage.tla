---- MODULE PngStage ----
EXTENDS Naturals
\* screenshot._png, two same-output writes after row validation by callers.
\* Windows model: Python open handles deny rename until all handles on a stage
\* close. Open(wb) can truncate shared file; exclusive private open cannot.
\* Close abstracts flushing one complete PNG buffer into the stage. Individual
\* chunks/partial mixed bytes NOT modeled; real independent PNG CRC/decode
\* probes supplement this abstraction. No POSIX claim or payload-atomic Close
\* implementation claim. Same final may be intentionally replaced by either
\* successful attempt. GUI/write_hero_stills multi-image bundle not atomic/proved.
\* Collision stress separately permits foreign owner3 in slot1. No hostile
\* replacement after exclusive ownership, crashes, never-returning I/O or power
\* loss. Candidate cleanup faults may leave own private files. No fsync policy.
CONSTANTS Faults, Collision
Actors == {1,2}
key(i) == i
VARIABLES pc,stage,handles,owned,result,published,lost
vars == <<pc,stage,handles,owned,result,published,lost>>
phase == IF \A i \in Actors : pc[i] = "done" THEN "done" ELSE "active"
API == INSTANCE PngStageApi WITH phase <- phase
Init == /\ pc = [i \in Actors |-> "open"]
        /\ stage = [i \in Actors |-> IF Collision /\ i = 1 THEN 3 ELSE 0]
        /\ handles = {} /\ owned = [i \in Actors |-> FALSE]
        /\ result = [i \in Actors |-> "pending"]
        /\ published = [i \in Actors |-> 0] /\ lost = FALSE
Open(i) == /\ pc[i] = "open" /\ stage[key(i)] = 0
    /\ stage' = ([stage EXCEPT ![key(i)] = i])
    /\ handles' = (handles \cup {i})
    /\ owned' = ([owned EXCEPT ![i] = TRUE])
    /\ pc' = ([pc EXCEPT ![i] = "stream"])
    /\ UNCHANGED <<result,published,lost>>

Occupied(i) == /\ pc[i] = "open" /\ stage[key(i)] # 0
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<stage,handles,owned,published,lost>>

Stream(i) == /\ pc[i] = "stream"
    /\ pc' = ([pc EXCEPT ![i] = "close"])
    /\ UNCHANGED <<stage,handles,owned,result,published,lost>>

Close(i) == /\ pc[i] = "close"
    /\ stage' = ([stage EXCEPT ![key(i)] = i])
    /\ handles' = (handles \ {i})
    /\ pc' = ([pc EXCEPT ![i] = "publish"])
    /\ UNCHANGED <<owned,result,published,lost>>

Publish(i) == /\ pc[i] = "publish" /\ stage[key(i)] # 0 /\ ~\E j \in handles : key(j) = key(i)
    /\ published' = ([published EXCEPT ![i] = stage[key(i)]])
    /\ stage' = ([stage EXCEPT ![key(i)] = 0])
    /\ result' = ([result EXCEPT ![i] = "ok"])
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ UNCHANGED <<handles,owned,lost>>

Blocked(i) == /\ pc[i] = "publish" /\ (stage[key(i)] = 0 \/ (\E j \in handles : key(j) = key(i)))
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = "cleanup"])
    /\ UNCHANGED <<stage,handles,owned,published,lost>>

IOError(i) == /\ Faults /\ pc[i] \in {"open","stream","close","publish"}
    /\ handles' = (handles \ {i})
    /\ result' = ([result EXCEPT ![i] = "error"])
    /\ pc' = ([pc EXCEPT ![i] = IF owned[i] THEN "cleanup" ELSE "done"])
    /\ UNCHANGED <<stage,owned,published,lost>>

Cleanup(i) == /\ pc[i] = "cleanup"
    /\ lost' = (lost \/ (stage[key(i)] # 0 /\ stage[key(i)] # i))
    /\ stage' = ([stage EXCEPT ![key(i)] = 0])
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<handles,owned,result,published>>

CleanupError(i) == /\ Faults /\ pc[i] = "cleanup"
    /\ pc' = ([pc EXCEPT ![i] = "done"])
    /\ UNCHANGED <<stage,handles,owned,result,published,lost>>


Run == \E i \in Actors : Open(i) \/ Occupied(i) \/ Stream(i) \/ Close(i) \/ Publish(i) \/ Blocked(i) \/ IOError(i) \/ Cleanup(i) \/ CleanupError(i)
Done == /\ \A i \in Actors : pc[i] = "done" /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ stage \in [Actors -> {0,1,2,3}] /\ handles \subseteq Actors
          /\ owned \in [Actors -> BOOLEAN] /\ lost \in BOOLEAN
          /\ result \in [Actors -> {"pending","ok","error"}]
          /\ published \in [Actors -> {0,1,2,3}]
OwnCleanup == ~lost
OwnPayload == \A i \in Actors : result[i] = "ok" => published[i] = i
PreserveSuccess == \A i \in Actors : published[i] # 0 => result[i] = "ok"
HealthyProgress == (~Faults /\ ~Collision) => \A i \in Actors : pc[i] = "done" => result[i] = "ok"
Returns == <>(\A i \in Actors : pc[i] = "done")
Refines == API!Spec
====
