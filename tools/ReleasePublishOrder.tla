---- MODULE ReleasePublishOrder ----
\* release_files.release_set: Windows ZIP, source ZIP, then SHA256SUMS.txt.
\* Each ZIP is written to <name>.partial, tested, then os.replace'd onto its
\* final name; the old checksum file is removed first and the new one is
\* written to a .partial and replaced last. A crash may stop at any step and
\* leaves .partial files (finally cannot run); a normal failure (a bad source
\* list or a failed ZIP test) removes its .partial. File contents are
\* abstracted to none/old/new/partial; os.replace is assumed atomic. Disk
\* faults, power loss, concurrent writers and ZIP bytes are not modeled.
\* Local finite safety and checksum-view refinement only.
VARIABLES pc, win, src, sums, winPart, srcPart, sumsPart
vars == <<pc, win, src, sums, winPart, srcPart, sumsPart>>
Content == {"none", "old", "new", "partial"}
Record == [on : BOOLEAN, w : Content, s : Content]
Absent == [on |-> FALSE, w |-> "none", s |-> "none"]
Set(w, s) == [on |-> TRUE, w |-> w, s |-> s]
Matching == Set(win, src)
view == IF ~sums.on THEN "absent"
        ELSE IF sums.w = "old" /\ sums.s = "old" THEN "old"
        ELSE IF sums.w = "new" /\ sums.s = "new" THEN "new"
        ELSE "mixed"
API == INSTANCE ReleasePublishOrderApi WITH sums <- view
Init == /\ pc = "validate" /\ win = "old" /\ src = "old" /\ sums = Set("old", "old")
        /\ winPart = "none" /\ srcPart = "none" /\ sumsPart = Absent
Validate == /\ pc = "validate" /\ pc' \in {"unlinkSums", "failed"}
            /\ UNCHANGED <<win, src, sums, winPart, srcPart, sumsPart>>
UnlinkSums == /\ pc = "unlinkSums" /\ pc' = "winStart" /\ sums' = Absent
              /\ UNCHANGED <<win, src, winPart, srcPart, sumsPart>>
WinStart == /\ pc = "winStart" /\ pc' = "winFinish" /\ winPart' = "writing"
            /\ win' = win
            /\ UNCHANGED <<src, sums, srcPart, sumsPart>>
WinFinish == /\ pc = "winFinish" /\ pc' = "winTest" /\ winPart' = "complete"
             /\ UNCHANGED <<win, src, sums, srcPart, sumsPart>>
WinTestFail == /\ pc = "winTest" /\ pc' = "failed" /\ winPart' = "none"
               /\ UNCHANGED <<win, src, sums, srcPart, sumsPart>>
WinReplace == /\ pc = "winTest" /\ pc' = "srcStart"
              /\ win' = "new" /\ winPart' = "none"
              /\ UNCHANGED <<src, sums, srcPart, sumsPart>>
SrcStart == /\ pc = "srcStart" /\ pc' = "srcFinish" /\ srcPart' = "writing"
            /\ UNCHANGED <<win, src, sums, winPart, sumsPart>>
SrcFinish == /\ pc = "srcFinish" /\ pc' = "srcTest" /\ srcPart' = "complete"
             /\ UNCHANGED <<win, src, sums, winPart, sumsPart>>
SrcTestFail == /\ pc = "srcTest" /\ pc' = "failed" /\ srcPart' = "none"
               /\ UNCHANGED <<win, src, sums, winPart, sumsPart>>
SrcReplace == /\ pc = "srcTest" /\ pc' = "sumsWrite"
              /\ src' = "new" /\ srcPart' = "none"
              /\ UNCHANGED <<win, sums, winPart, sumsPart>>
SumsWrite == /\ pc = "sumsWrite" /\ pc' = "sumsReplace" /\ sumsPart' = Matching
             /\ UNCHANGED <<win, src, sums, winPart, srcPart>>
SumsReplace == /\ pc = "sumsReplace" /\ pc' = "done"
               /\ sums' = sumsPart /\ sumsPart' = Absent
               /\ UNCHANGED <<win, src, winPart, srcPart>>
Crash == /\ pc \notin {"done", "failed", "crashed"} /\ pc' = "crashed"
         /\ UNCHANGED <<win, src, sums, winPart, srcPart, sumsPart>>
Next == \/ Validate \/ UnlinkSums \/ WinStart \/ WinFinish \/ WinTestFail \/ WinReplace
        \/ SrcStart \/ SrcFinish \/ SrcTestFail \/ SrcReplace \/ SumsWrite \/ SumsReplace
        \/ Crash
Spec == Init /\ [][Next]_vars
TypeOK == /\ win \in Content /\ src \in Content /\ sums \in Record /\ sumsPart \in Record
          /\ winPart \in {"none", "writing", "complete"}
          /\ srcPart \in {"none", "writing", "complete"}
\* SHA256SUMS.txt, when present, names exactly the ZIPs at their final names.
SumsMatch == sums.on => sums = Matching
\* A final-name ZIP is never a partially written archive.
NoPartialFinal == win \in {"old", "new"} /\ src \in {"old", "new"}
CompleteRelease == pc = "done" => win = "new" /\ src = "new" /\ sums = Set("new", "new")
FailureClean == pc = "failed" => winPart = "none" /\ srcPart = "none" /\ sumsPart = Absent
Refines == API!Spec
====
