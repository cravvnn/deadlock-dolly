---- MODULE EncoderUnwind ----
EXTENDS Naturals
\* player_layer.encode_bundle via player_encode.worker_main/encode_in_worker.
\* One admitted owned child, cancellation monitor admission success/refusal,
\* one work error, locked/unlocked owned partial MOV, returning cleanup steps.
\* A start RuntimeError leaves an allocated but unstarted Thread in baseline;
\* join then raises and skips local retirement. Candidate withdraws that handle.
\* Unlink OSError may replace the primary failure; candidate contains it.
\* Parent ProcessJob kill-on-close is outside this local-owner model: it already
\* provides a second cleanup boundary. No indefinite application leak claim.
\* No success/publication, actual FFmpeg, Windows scheduling, kill/wait failure,
\* I/O nontermination, GPU, native capture or weak memory is modeled. Actual
\* owned-child/Win32-lock probes bind the two decisive edges to real code.
\* Terminals intentionally stutter; deadlock checking stays enabled. No liveness
\* or whole-program proof and no state-reduction claim from different counts.
AdmissionFix == TRUE
UnlinkFix == TRUE
VARIABLES pc,monitor,child,pipe,frames,locked,primary,error
vars == <<pc,monitor,child,pipe,frames,locked,primary,error>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE EncoderUnwindApi WITH phase <- phase
Init == /\ pc = "admit" /\ monitor = "none" /\ child = FALSE
        /\ pipe = FALSE /\ frames = TRUE /\ locked \in BOOLEAN
        /\ primary = "none" /\ error = "none"
Admit == /\ pc = "admit" /\ pc' = "start" /\ monitor' = "allocated"
         /\ child' = TRUE /\ pipe' = TRUE
         /\ UNCHANGED <<frames,locked,primary,error>>
Accept == /\ pc = "start" /\ pc' = "work" /\ monitor' = "started"
          /\ UNCHANGED <<child,pipe,frames,locked,primary,error>>
Refuse == /\ pc = "start" /\ pc' = "join"
          /\ monitor' = (IF AdmissionFix THEN "none" ELSE "allocated")
          /\ primary' = "admission" /\ error' = "admission"
          /\ UNCHANGED <<child,pipe,frames,locked>>
Work == /\ pc = "work" /\ pc' = "join" /\ primary' \in {"pixels","cancel"}
        /\ error' = primary' /\ UNCHANGED <<monitor,child,pipe,frames,locked>>
Join == /\ pc = "join"
        /\ pc' = (IF monitor = "allocated" THEN "done" ELSE "retire")
        /\ error' = (IF monitor = "allocated" THEN "join" ELSE error)
        /\ UNCHANGED <<monitor,child,pipe,frames,locked,primary>>
Retire == /\ pc = "retire" /\ pc' = "unlink" /\ child' = FALSE
          /\ pipe' = FALSE /\ frames' = FALSE
          /\ UNCHANGED <<monitor,locked,primary,error>>
Unlink == /\ pc = "unlink" /\ pc' = "done"
          /\ error' = (IF locked /\ ~UnlinkFix THEN "unlink" ELSE error)
          /\ UNCHANGED <<monitor,child,pipe,frames,locked,primary>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Admit \/ Accept \/ Refuse \/ Work \/ Join \/ Retire \/ Unlink \/ Done
TypeOK == /\ pc \in {"admit","start","work","join","retire","unlink","done"}
          /\ monitor \in {"none","allocated","started"}
          /\ child \in BOOLEAN /\ pipe \in BOOLEAN /\ frames \in BOOLEAN
          /\ locked \in BOOLEAN /\ primary \in {"none","admission","pixels","cancel"}
          /\ error \in {"none","admission","pixels","cancel","join","unlink"}
LocallyRetired == pc = "done" => ~child /\ ~pipe /\ ~frames
PrimaryPreserved == pc = "done" => error = primary
Refines == API!Spec
Spec == Init /\ [][Next]_vars
====
