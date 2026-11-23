---- MODULE BonePickerTransaction ----
EXTENDS Naturals
\* L01 desktop Bone Picker transaction (bone_picker.py, editor_publication.py).
\* While app._bone_picker_context is set, DollyApp._submit refuses every
\* operation, including Connect/Disconnect. open_picker submits its start,
\* then ready() stores the context and publishes. configure() may skip that
\* publication (a history refresh, an inactive editor); the attach block then
\* first carries picker=True on a later poll. Native picker_camera latches the
\* first picker=True sequence; a finish is accepted only for that request.
\* A lost console makes configure see an inactive editor; it then cancels the
\* picker locally (bone_picker.discard), since native can no longer finish it.
\* Sequences are abstract counters bounded by MaxSeq; a value of 0 is None.
\* Native rejection of a republished session and game exit are not modeled.
CONSTANT MaxSeq
VARIABLES editor, picker, seq, request, latched
vars == <<editor, picker, seq, request, latched>>

Init == /\ editor = "active" /\ picker = "closed" /\ seq = 0
        /\ request = 0 /\ latched = 0

\* open_picker: the start operation is submitted while the editor is active.
Open == /\ picker = "closed" /\ editor = "active"
        /\ picker' = "opening"
        /\ UNCHANGED <<editor, seq, request, latched>>
\* ready(): the context is stored; its publication may be skipped.
Ready == /\ picker = "opening"
         /\ picker' = "open" /\ request' = 0
         /\ UNCHANGED <<editor, seq, latched>>
\* configure() publishes the attach block with picker=True. Native latches the
\* first such sequence; the desktop stores the sequence this call returned.
Publish == /\ picker = "open" /\ editor = "active" /\ seq < MaxSeq
           /\ seq' = seq + 1
           /\ request' = IF request = 0 THEN seq + 1 ELSE request
           /\ latched' = IF latched = 0 THEN seq + 1 ELSE latched
           /\ UNCHANGED <<editor, picker>>
\* Finish or cancel from the in-game panel.
Close == /\ picker = "open" /\ editor = "active"
         /\ picker' = "closed" /\ request' = 0 /\ latched' = 0
         /\ UNCHANGED <<editor, seq>>
\* The console socket fails while the game runs: connected=False.
Lose == /\ editor = "active" /\ editor' = "lost"
        /\ UNCHANGED <<picker, seq, request, latched>>
\* configure() sees the inactive editor and cancels the picker locally.
Discard == /\ editor = "lost" /\ picker = "open"
           /\ picker' = "closed" /\ request' = 0 /\ latched' = 0
           /\ UNCHANGED <<editor, seq>>
\* Connect is a submitted operation: it runs only while no picker is open.
Reconnect == /\ editor = "lost" /\ picker = "closed"
             /\ editor' = "active"
             /\ UNCHANGED <<picker, seq, request, latched>>
\* _submit refuses Connect while the picker context is set.
Refused == /\ editor = "lost" /\ picker # "closed" /\ UNCHANGED vars

Next == Open \/ Ready \/ Publish \/ Close \/ Lose \/ Discard \/ Reconnect \/ Refused
Spec == Init /\ [][Next]_vars /\ WF_vars(Ready) /\ WF_vars(Discard) /\ WF_vars(Reconnect)

TypeOK == /\ editor \in {"active", "lost"}
          /\ picker \in {"closed", "opening", "open"}
          /\ seq \in 0..MaxSeq /\ request \in 0..MaxSeq /\ latched \in 0..MaxSeq
\* The desktop's stored request is the sequence native latched.
RequestIsFirstPublish == (picker = "open" /\ latched # 0) => request = latched
\* A lost editor never leaves the desktop blocked behind an open picker.
PickerEndsWhenLost == (editor = "lost") ~> (picker = "closed")
LostReconnects == (editor = "lost") ~> (editor = "active")
====
