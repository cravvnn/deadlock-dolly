---- MODULE DefinitionsFirstRead ----
EXTENDS Naturals
\* definitions.current: LoadStart enters lock, Decode performs bounded local
\* load/decode, LoadPublish assigns and returns then unlocks. Background UpdateUI
\* definitions.refresh has already fetched/validated; BAdopt calls adopt then
\* BCache writes cache outside lock, as real refresh does. Launcher game_pins
\* can call current while that refresh runs. Actual two-thread probes bind this.
\* One first reader and a second lookup/refresh/withdrawal; one old/new/empty
\* cache, no second refresh, reset race, failed I/O or mutable returned docs.
\* Production has no separate loaded flag: None represents not loaded;
\* EMPTY is an already loaded empty document. Baseline comparison is private.
\* Real bounded cache I/O holds Python Lock; network/decode of refresh does not.
\* Full compatibility/admission/native forwarding/atomic cache writes excluded.
\* Synchronous operations assumed to return; terminal stutter, deadlock enabled.
SerialRead == TRUE
SerialAdopt == TRUE
VARIABLES a,b,kind,active,locked,disk,decoded,aResult,bResult
vars == <<a,b,kind,active,locked,disk,decoded,aResult,bResult>>
Target == IF kind = "withdraw" THEN "empty" ELSE "new"
Init == /\ a = "start" /\ b = "start" /\ kind \in {"lookup","refresh","withdraw"}
        /\ active = "none" /\ locked = FALSE /\ disk = "old"
        /\ decoded = "none" /\ aResult = "pending" /\ bResult = "pending"
LoadStart == /\ a = "start" /\ a' = "decode" /\ locked' = SerialRead
             /\ UNCHANGED <<b,kind,active,disk,decoded,aResult,bResult>>
Decode == /\ a = "decode" /\ a' = "publish" /\ decoded' = disk
          /\ UNCHANGED <<b,kind,active,locked,disk,aResult,bResult>>
LoadPublish == /\ a = "publish" /\ a' = "done" /\ active' = decoded
               /\ aResult' = decoded /\ locked' = FALSE
               /\ UNCHANGED <<b,kind,disk,decoded,bResult>>
Lookup == /\ b = "start" /\ kind = "lookup" /\ a # "start"
          /\ (~SerialRead \/ ~locked) /\ b' = "done"
          /\ bResult' = (IF active = "none" THEN "empty" ELSE active)
          /\ UNCHANGED <<a,kind,active,locked,disk,decoded,aResult>>
BAdopt == /\ b = "start" /\ kind # "lookup" /\ a # "start"
          /\ (~SerialAdopt \/ ~locked) /\ active' = Target /\ b' = "cache"
          /\ bResult' = Target /\ UNCHANGED <<a,kind,locked,disk,decoded,aResult>>
BCache == /\ b = "cache" /\ b' = "done" /\ disk' = Target
          /\ UNCHANGED <<a,kind,active,locked,decoded,aResult,bResult>>
Done == /\ a = "done" /\ b = "done" /\ UNCHANGED vars
phase == IF a = "done" /\ b = "done" THEN "done" ELSE "active"
API == INSTANCE DefinitionsFirstReadApi WITH phase <- phase
Next == LoadStart \/ Decode \/ LoadPublish \/ Lookup \/ BAdopt \/ BCache \/ Done
TypeOK == /\ a \in {"start","decode","publish","done"} /\ b \in {"start","cache","done"}
          /\ kind \in {"lookup","refresh","withdraw"} /\ locked \in BOOLEAN
          /\ active \in {"none","old","new","empty"} /\ disk \in {"old","new","empty"}
          /\ decoded \in {"none","old","new","empty"} /\ aResult \in {"pending","old","new","empty"}
          /\ bResult \in {"pending","old","new","empty"}
NoProvisionalEmpty == (kind = "lookup" /\ b = "done") => bResult = "old"
RefreshWins == (kind # "lookup" /\ a = "done" /\ b = "done") => active = Target
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
