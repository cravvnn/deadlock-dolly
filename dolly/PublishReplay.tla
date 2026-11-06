---- MODULE PublishReplay ----
EXTENDS Naturals
\* replay_repair.repair_replay output transaction after source scan and patch
\* preparation. Create includes the preflight/owned-file allocation boundary;
\* Header and Copy keep empty/partial/full bytes distinct. Flush/fsync/close,
\* source checks, packet_index, no-replace publication and cleanup are separate.
\* Caller GUI worker receives return/error. Windows rename consumes the stage;
\* POSIX link leaves a private alias until cleanup. OS publication is atomic.
\* Another owner may create final before commit, or replace our final during
\* error cleanup; cleanup never touches final. Distinct generations retained.
\* Private staging namespace replacement, arbitrary post-success mutation,
\* I/O that never returns, power-loss/directory durability and live game excluded.
CONSTANTS LinkPublication
VARIABLES pc,stage,final,contents,synced,verified,sourceOk,outcome,lostOther
vars == <<pc,stage,final,contents,synced,verified,sourceOk,outcome,lostOther>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE PublishReplayApi WITH phase <- phase
Init == /\ pc = "create" /\ stage = FALSE /\ final = "absent"
        /\ contents = "none" /\ synced = FALSE /\ verified = FALSE /\ sourceOk = TRUE
        /\ outcome = "pending" /\ lostOther = FALSE
Create == /\ pc = "create" /\ final = "absent"
    /\ stage' = (TRUE)
    /\ final' = (final)
    /\ contents' = ("empty")
    /\ pc' = ("header")
    /\ UNCHANGED <<synced,verified,sourceOk,outcome,lostOther>>

Header == /\ pc = "header"
    /\ contents' = ("partial")
    /\ pc' = ("copy")
    /\ UNCHANGED <<stage,final,synced,verified,sourceOk,outcome,lostOther>>

Copy == /\ pc = "copy"
    /\ contents' = ("full")
    /\ pc' = ("flush")
    /\ UNCHANGED <<stage,final,synced,verified,sourceOk,outcome,lostOther>>

Flush == /\ pc = "flush"
    /\ pc' = ("sync")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,outcome,lostOther>>

Sync == /\ pc = "sync"
    /\ synced' = (TRUE)
    /\ pc' = ("close")
    /\ UNCHANGED <<stage,final,contents,verified,sourceOk,outcome,lostOther>>

Close == /\ pc = "close"
    /\ pc' = ("source")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,outcome,lostOther>>

SourceCheck == /\ pc = "source"
    /\ pc' = ("verify")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,outcome,lostOther>>

SourceRefused == /\ pc = "source"
    /\ sourceOk' = (FALSE)
    /\ outcome' = ("error")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,contents,synced,verified,lostOther>>

Verify == /\ pc = "verify"
    /\ verified' = (TRUE)
    /\ pc' = ("publish")
    /\ UNCHANGED <<stage,final,contents,synced,sourceOk,outcome,lostOther>>

Publish == /\ pc = "publish" /\ final = "absent"
    /\ final' = ("ours")
    /\ stage' = (IF LinkPublication THEN stage ELSE FALSE)
    /\ pc' = ("return")
    /\ UNCHANGED <<contents,synced,verified,sourceOk,outcome,lostOther>>

OtherCreates == /\ final = "absent" /\ pc # "done"
    /\ final' = ("other")
    /\ UNCHANGED <<pc,stage,contents,synced,verified,sourceOk,outcome,lostOther>>

RefuseExisting == /\ pc \in {"create","publish"} /\ final = "other"
    /\ outcome' = ("error")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,lostOther>>

Return == /\ pc = "return"
    /\ outcome' = ("ok")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,lostOther>>

IOError == /\ pc \in {"create","header","copy","flush","sync","close","source","verify","publish"}
    /\ outcome' = ("error")
    /\ pc' = ("cleanup")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,lostOther>>

Cleanup == /\ pc = "cleanup"
    /\ stage' = (FALSE)
    /\ pc' = ("done")
    /\ UNCHANGED <<final,contents,synced,verified,sourceOk,outcome,lostOther>>

CleanupError == /\ pc \in {"cleanup"}
    /\ pc' = ("done")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,outcome,lostOther>>

Crash == /\ pc # "done"
    /\ outcome' = ("crashed")
    /\ pc' = ("done")
    /\ UNCHANGED <<stage,final,contents,synced,verified,sourceOk,lostOther>>
Done == /\ pc = "done" /\ UNCHANGED vars
Run == Create \/ Header \/ Copy \/ Flush \/ Sync \/ Close \/ SourceCheck \/ SourceRefused \/ Verify \/ Publish \/ RefuseExisting \/ Return \/ IOError \/ Cleanup \/ CleanupError
Next == Run \/ OtherCreates \/ Crash \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ stage \in BOOLEAN /\ final \in {"absent","ours","other"}
          /\ contents \in {"none","empty","partial","full"} /\ synced \in BOOLEAN /\ verified \in BOOLEAN
          /\ sourceOk \in BOOLEAN
          /\ outcome \in {"pending","ok","error","crashed"} /\ lostOther \in BOOLEAN
PublishedComplete == final # "ours" \/ (contents = "full" /\ synced /\ verified /\ sourceOk)
SuccessValid == outcome = "ok" => final = "ours" /\ contents = "full" /\ synced /\ verified /\ sourceOk
ForeignPreserved == ~lostOther
Returns == <>(pc = "done")
Refines == API!Spec
====
