---- MODULE PlaybackStart ----
EXTENDS Naturals
\* Controller.play_pov: native path already armed; Thread.start refuses.
\* Existing finish_pov_recording calls _halt, joining _thread before release.
\* A retained never-started thread makes join raise before native release.
\* Retire refused thread first. Returning native release assumed.
\* Controller.play has analogous native/console admission, with cleanup then
\* later _halt/retry. Actual-method tests cover all three startup sites.
\* No running worker, readiness, replay restoration, concurrent callers, game,
\* failed native release or post-start errors; not full controller refinement.
\* Production-only bounded contract; baseline experiments stay private.
VARIABLES pc,reader,nativeOwned
vars == <<pc,reader,nativeOwned>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE PlaybackStartApi WITH phase <- phase
Init == /\ pc = "start" /\ reader = TRUE /\ nativeOwned = TRUE
Refuse == /\ pc = "start" /\ pc' = "cleanup" /\ reader' = FALSE /\ UNCHANGED nativeOwned
Finish == /\ pc = "cleanup" /\ pc' = (IF reader THEN "blocked" ELSE "done")
          /\ nativeOwned' = reader /\ UNCHANGED reader
Done == /\ pc \in {"blocked","done"} /\ UNCHANGED vars
Run == Refuse \/ Finish
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ pc \in {"start","cleanup","blocked","done"}
          /\ reader \in BOOLEAN /\ nativeOwned \in BOOLEAN
RefusedRetired == pc \in {"done","blocked"} => ~reader /\ ~nativeOwned /\ pc = "done"
Returns == <>(pc = "done")
Refines == API!Spec
====
