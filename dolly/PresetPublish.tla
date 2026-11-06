---- MODULE PresetPublish ----
EXTENDS Naturals
\* reshade_setup._install_presets: one preset, caller source bytes already read.
\* prepare_config installs main and optional presets sequentially; outer config
\* writer/GUI/runtime activation NOT covered. Existing presets always preserved.
\* Writes/close/publication/cleanup are separate; no fsync/powerloss promise.
\* One process interruption with one subsequent call uses a fresh stage.
\* Orphan records old stage presence; it is never swept. Final generation other
\* may appear before publication; private namespace replacement excluded.
\* Collision-only config checks failed exclusive open has no cleanup authority.
\* LinkPublication models POSIX hard-link alias, not native POSIX certification.
CONSTANTS LinkPublication, Collision
VARIABLES pc,stage,final,closed,own,result,prior,lost,retries,orphan,bad
vars == <<pc,stage,final,closed,own,result,prior,lost,retries,orphan,bad>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE PresetPublishApi WITH phase <- phase
Init == /\ pc = "entry" /\ stage = IF Collision THEN "intruder" ELSE "none"
        /\ final \in {"none","other"} /\ closed = FALSE /\ own = FALSE
        /\ result = "pending" /\ prior = "pending" /\ lost = FALSE
        /\ retries = 0 /\ orphan = FALSE /\ bad = FALSE
Entry == /\ pc = "entry"
    /\ pc' = (IF final # "none" THEN "done" ELSE "start")
    /\ result' = (IF final # "none" THEN "skip" ELSE result)
    /\ UNCHANGED <<stage,final,closed,own,prior,lost,retries,orphan,bad>>

Open == /\ pc = "start" /\ stage = "none"
    /\ stage' = ("empty")
    /\ final' = (final)
    /\ own' = (TRUE)
    /\ pc' = ("write")
    /\ UNCHANGED <<closed,result,prior,lost,retries,orphan,bad>>

Occupied == /\ pc = "start" /\ stage = "intruder"
    /\ result' = ("error")
    /\ pc' = ("done")
    /\ UNCHANGED <<stage,final,closed,own,prior,lost,retries,orphan,bad>>

Write == /\ pc = "write"
    /\ stage' = ("full")
    /\ final' = (final)
    /\ pc' = ("close")
    /\ UNCHANGED <<closed,own,result,prior,lost,retries,orphan,bad>>

Close == /\ pc = "close"
    /\ closed' = (TRUE)
    /\ pc' = ("publish")
    /\ UNCHANGED <<stage,final,own,result,prior,lost,retries,orphan,bad>>

Publish == /\ pc = "publish" /\ final = "none"
    /\ final' = ("full")
    /\ stage' = (IF LinkPublication THEN stage ELSE "none")
    /\ bad' = (bad \/ ~closed \/ stage # "full")
    /\ pc' = ("return")
    /\ UNCHANGED <<closed,own,result,prior,lost,retries,orphan>>

OtherCreates == /\ final = "none" /\ pc # "done"
    /\ final' = ("other")
    /\ UNCHANGED <<pc,stage,closed,own,result,prior,lost,retries,orphan,bad>>

ExistingAtPublish == /\ pc = "publish" /\ final # "none"
    /\ result' = ("skip")
    /\ prior' = ("skip")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,closed,own,lost,retries,orphan,bad>>

Return == /\ pc = "return"
    /\ result' = ("ok")
    /\ prior' = ("ok")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,closed,own,lost,retries,orphan,bad>>

Error == /\ pc \in {"start","write","close","publish"}
    /\ result' = ("error")
    /\ prior' = ("error")
    /\ pc' = (IF own THEN "cleanup" ELSE "done")
    /\ UNCHANGED <<stage,final,closed,own,lost,retries,orphan,bad>>

Cleanup == /\ pc = "cleanup"
    /\ lost' = (lost \/ stage = "intruder")
    /\ stage' = ("none")
    /\ pc' = ("done")
    /\ UNCHANGED <<final,closed,own,result,prior,retries,orphan,bad>>

CleanupError == /\ pc = "cleanup"
    /\ pc' = ("done")
    /\ UNCHANGED <<stage,final,closed,own,result,prior,lost,retries,orphan,bad>>

Crash == /\ retries = 0 /\ pc \notin {"restart","done"}
    /\ orphan' = (orphan \/ stage # "none")
    /\ pc' = ("restart")
    /\ result' = ("pending")
    /\ prior' = ("pending")
    /\ retries' = (1)
    /\ UNCHANGED <<stage,final,closed,own,lost,bad>>

Restart == /\ pc = "restart"
    /\ stage' = ("none")
    /\ closed' = (FALSE)
    /\ own' = (FALSE)
    /\ pc' = ("entry")
    /\ UNCHANGED <<final,result,prior,lost,retries,orphan,bad>>


Run == Entry \/ Open \/ Occupied \/ Write \/ Close \/ Publish \/ ExistingAtPublish \/ Return \/ Error \/ Cleanup \/ CleanupError \/ Restart
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Run \/ OtherCreates \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ stage \in {"none","empty","full","intruder"}
          /\ final \in {"none","empty","full","other"}
          /\ retries \in {0,1} /\ own \in BOOLEAN /\ lost \in BOOLEAN
          /\ closed \in BOOLEAN /\ orphan \in BOOLEAN
          /\ result \in {"pending","ok","error","skip"}
          /\ prior \in {"pending","ok","error","skip"}
PublishedComplete == final # "empty"
PreserveResult == pc = "done" /\ prior # "pending" => result = prior
ForeignPreserved == ~lost
ClosedComplete == ~bad
Returns == <>(pc = "done")
Refines == API!Spec
====
