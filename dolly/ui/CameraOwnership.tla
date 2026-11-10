---- MODULE CameraOwnership ----
EXTENDS Naturals
\* shot_commands.commit_camera via DollyApp._commit_camera (_add_key uses old keys).
\* One retained mutable camera, one mutation of returned shot after publication.
\* Original scalar starts0; copying graph breaks cross-shot aliases. New-key
\* isolation, iterable conversion, options and validation stay in actual code probes.
\* No whole editor/history/caller refinement or performance/state-reduction claim.
UseFix == TRUE
VARIABLES pc,original,result,shared
vars == <<pc,original,result,shared>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE CameraOwnershipApi WITH phase <- phase
Init == /\ pc = "copy" /\ original = 0 /\ result = 0 /\ shared = FALSE
Copy == /\ pc = "copy" /\ pc' = "published" /\ shared' = ~UseFix
        /\ UNCHANGED <<original,result>>
Mutate == /\ pc = "published" /\ pc' = "done" /\ result' = 1
          /\ original' = (IF shared THEN 1 ELSE original) /\ UNCHANGED shared
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Copy \/ Mutate \/ Done
TypeOK == /\ pc \in {"copy","published","done"}
          /\ original \in 0..1 /\ result \in 0..1 /\ shared \in BOOLEAN
OriginalPreserved == original = 0
Refines == API!Spec
Spec == Init /\ [][Next]_vars
====
