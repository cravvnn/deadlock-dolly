---- MODULE AssetFailure ----
EXTENDS Naturals, FiniteSets
\* export_captured_audio.export per-asset failure, cleanup and report.
\* Two distinct asset paths; first may fail after producing a partial WAV, second
\* returns successfully. Cleanup may refuse and retain the owned partial.
\* No incorrect end-to-end success claim; output-folder admission, hashes, parsing,
\* namespace replacement, process hangs and abrupt crashes excluded.
\* Local finite safety and completion-shape refinement only; terminal deadlock checking disabled.
VARIABLES pc, asset, attempted, primary, result, files, hadError
vars == <<pc, asset, attempted, primary, result, files, hadError>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE AssetFailureApi WITH phase <- phase
Init == /\ pc = "convert" /\ asset = 1 /\ attempted = {} /\ primary = "none"
        /\ result = "none" /\ files = {} /\ hadError = FALSE
Convert(error) == /\ pc = "convert" /\ (~error \/ asset = 1)
                  /\ attempted' = attempted \cup {asset} /\ files' = files \cup {asset}
                  /\ primary' = IF error THEN "decoder" ELSE primary
                  /\ hadError' = (hadError \/ error)
                  /\ pc' = IF error THEN "cleanup" ELSE "next"
                  /\ UNCHANGED <<asset,result>>
Cleanup == /\ pc = "cleanup" /\ pc' = "next" /\ files' = files \ {asset}
           /\ result' = primary /\ UNCHANGED <<asset,attempted,primary,hadError>>
CleanupRefused == /\ pc = "cleanup"
                  /\ pc' = "next"
                  /\ result' = primary
                  /\ UNCHANGED <<asset,attempted,primary,files,hadError>>
Advance == /\ pc = "next" /\ asset = 1 /\ asset' = 2 /\ pc' = "convert"
           /\ UNCHANGED <<attempted,primary,result,files,hadError>>
Manifest == /\ pc = "next" /\ asset = 2 /\ pc' = "done"
            /\ UNCHANGED <<asset,attempted,primary,result,files,hadError>>
Next == Convert(TRUE) \/ Convert(FALSE) \/ Cleanup \/ CleanupRefused \/ Advance \/ Manifest
TypeOK == /\ pc \in {"convert","cleanup","next","done"} /\ asset \in 1..2
          /\ attempted \subseteq {1,2} /\ files \subseteq {1,2} /\ hadError \in BOOLEAN
          /\ primary \in {"none","decoder"} /\ result \in {"none","decoder","cleanup"}
PrimaryPreserved == result # "cleanup"
Continued == pc = "done" => attempted = {1,2}
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
