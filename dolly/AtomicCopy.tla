---- MODULE AtomicCopy ----
EXTENDS Naturals
\* replace_file source/copy/fsync-temp/replace/finally-unlink, one owned file.
\* Caller source is immutable under the install lock. Target replacement is
\* assumed to expose old or new file after ordinary process interruption.
\* Source or target corruption / machine power loss is not modeled/proved.
\* Crash terminates the helper; a leftover private temp is allowed on abrupt
\* process exit, as finally cannot run then. Caller retry is in Rollback.tla.
CONSTANTS Files, Existing
VARIABLES contents, availableBackups, pc, temporary
vars == <<contents,availableBackups,pc,temporary>>
Disk == INSTANCE FileOpsApi
Init == /\ contents = [f \in Files |-> "new"] /\ availableBackups = Existing
        /\ pc = "create" /\ temporary = "none"
Create == /\ pc = "create" /\ temporary' = "empty" /\ pc' = "copy"
          /\ UNCHANGED <<contents,availableBackups>>
CopyBytes == /\ pc = "copy" /\ temporary' = "old" /\ pc' = "flush"
             /\ UNCHANGED <<contents,availableBackups>>
Flush == /\ pc = "flush" /\ pc' = "replace"
         /\ UNCHANGED <<contents,availableBackups,temporary>>
Replace == /\ pc = "replace"
           /\ contents' = [contents EXCEPT ![1] = temporary]
           /\ UNCHANGED availableBackups
           /\ temporary' = "none" /\ pc' = "done"
Crash == /\ pc \notin {"done","crashed"} /\ pc' = "crashed"
         /\ UNCHANGED <<contents,availableBackups,temporary>>
Done == /\ pc \in {"done","crashed"} /\ UNCHANGED vars
Next == Create \/ CopyBytes \/ Flush \/ Replace \/ Crash \/ Done
Spec == Init /\ [][Next]_vars
SourcePreserved == availableBackups = Existing
PublishedOriginal == pc = "done" => contents[1] = "old"
Refines == Disk!Spec
====
