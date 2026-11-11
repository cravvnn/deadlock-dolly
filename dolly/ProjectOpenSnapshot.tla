---- MODULE ProjectOpenSnapshot ----
EXTENDS Naturals
\* DollyApp.open -> Project.load/versioned -> Project.from_dict, followed by
\* migration notice. One valid bounded file, two source versions, a single
\* external replacement between accepted parse and notice. Actual GUI owner
\* probes cover BOM/strict invalid input/notice and one real binary read.
\* Baseline parses the path again; candidate returns version with same accepted
\* document and keeps Project.load's result-only API. Transient local metadata,
\* no new persistent flag. File changes during first stream read, modal UI
\* callbacks, project replacement/admission, errors and save history excluded.
\* Each parse assumed returning; Done stutters; deadlock check enabled.
SameSnapshot == TRUE
VARIABLES pc,file,projectVersion,noticeVersion,changed
vars == <<pc,file,projectVersion,noticeVersion,changed>>
Init == /\ pc = "read" /\ file \in {"current","legacy"}
        /\ projectVersion = "none" /\ noticeVersion = "none" /\ changed = FALSE
ReadProject == /\ pc = "read" /\ pc' = "notice" /\ projectVersion' = file
               /\ noticeVersion' = (IF SameSnapshot THEN file ELSE noticeVersion)
               /\ UNCHANGED <<file,changed>>
ReplaceFile == /\ pc = "notice" /\ ~changed /\ changed' = TRUE
               /\ file' = (IF file = "current" THEN "legacy" ELSE "current")
               /\ UNCHANGED <<pc,projectVersion,noticeVersion>>
ReadNotice == /\ pc = "notice" /\ pc' = "done"
              /\ noticeVersion' = (IF SameSnapshot THEN noticeVersion ELSE file)
              /\ UNCHANGED <<file,projectVersion,changed>>
Done == /\ pc = "done" /\ UNCHANGED vars
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE ProjectOpenSnapshotApi WITH phase <- phase
Next == ReadProject \/ ReplaceFile \/ ReadNotice \/ Done
TypeOK == /\ pc \in {"read","notice","done"} /\ file \in {"current","legacy"}
          /\ projectVersion \in {"none","current","legacy"}
          /\ noticeVersion \in {"none","current","legacy"} /\ changed \in BOOLEAN
NoticeMatchesLoaded == pc = "done" => noticeVersion = projectVersion
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
