---- MODULE PickerGizmoSession ----
EXTENDS Naturals
\* Object Picker gizmo drag (dolly_object_picker_ui.cpp draw_object_picker_controls).
\* A drag grabs the object at an index; ObjectTransform carries only that index
\* (dolly/editor_dispatch.py _object_transform has no revision), so a commit must
\* target the grabbed object, from the picker session the drag started in.
\* Draw is one drawn picker frame: a gap since the last drawn frame (Close)
\* starts a new session and drops the drag; a drag whose object changed under
\* it (undo, delete, load) is dropped; mouse-up commits once. Press is a
\* left-button click on the selected object, picked up by the next frame even
\* if already released (ImGui trickles queued input); the user clicks again
\* only once the previous drag has ended.
\* Object identity abstracts the code's shape+transform comparison; a
\* replacement identical in shape and transform is not distinguished (it is
\* then the same edit). Bounds: 2 slots, 3 clicks, 1 list edit, 1 close.
\* Excludes ImGui hit testing, gizmo math, the editor queue and Python.
N == 2
MaxClicks == 3
MaxEdits == 1
MaxCloses == 1
UseSessionReset == TRUE
UseTargetCheck == TRUE
VARIABLES open, gap, session, objs, count, nextId, down, pressed, active, gIndex, gId,
          gSession, clicks, edits, closes, commits, badTarget, badSession
vars == <<open, gap, session, objs, count, nextId, down, pressed, active, gIndex, gId,
          gSession, clicks, edits, closes, commits, badTarget, badSession>>
env == <<objs, count, nextId, edits>>
Init == /\ open = TRUE /\ gap = FALSE /\ session = 1
        /\ objs = [i \in 1..N |-> i] /\ count = N /\ nextId = N + 1
        /\ down = FALSE /\ pressed = FALSE
        /\ active = FALSE /\ gIndex = 1 /\ gId = 0 /\ gSession = 0
        /\ clicks = 0 /\ edits = 0 /\ closes = 0 /\ commits = 0
        /\ badTarget = FALSE /\ badSession = FALSE
Close == /\ open /\ closes < MaxCloses
         /\ open' = FALSE /\ gap' = TRUE /\ closes' = closes + 1
         /\ UNCHANGED <<session, down, pressed, active, gIndex, gId, gSession, clicks, commits,
                        badTarget, badSession>> /\ UNCHANGED env
Open == /\ ~open /\ open' = TRUE /\ session' = session + 1
        /\ UNCHANGED <<gap, down, pressed, active, gIndex, gId, gSession, clicks, closes, commits,
                       badTarget, badSession>> /\ UNCHANGED env
Press == /\ open /\ ~down /\ ~active /\ ~pressed /\ clicks < MaxClicks
         /\ down' = TRUE /\ pressed' = TRUE /\ clicks' = clicks + 1
         /\ UNCHANGED <<open, gap, session, active, gIndex, gId, gSession, closes, commits,
                        badTarget, badSession>> /\ UNCHANGED env
Release == /\ down /\ down' = FALSE
           /\ UNCHANGED <<open, gap, session, pressed, active, gIndex, gId, gSession, clicks,
                          closes, commits, badTarget, badSession>> /\ UNCHANGED env
\* Python replaces an object (undo/load) or deletes the last one.
Edit == /\ edits < MaxEdits /\ edits' = edits + 1
        /\ \/ \E i \in 1..count : /\ objs' = [objs EXCEPT ![i] = nextId]
                                  /\ nextId' = nextId + 1 /\ count' = count
           \/ /\ count > 0 /\ objs' = [objs EXCEPT ![count] = 0]
              /\ count' = count - 1 /\ nextId' = nextId
        /\ UNCHANGED <<open, gap, session, down, pressed, active, gIndex, gId, gSession, clicks,
                       closes, commits, badTarget, badSession>>
Unchanged == gIndex <= count /\ objs[gIndex] = gId
Draw == /\ open
        /\ LET kept == active /\ ~(gap /\ UseSessionReset) /\ (Unchanged \/ ~UseTargetCheck)
           IN IF kept /\ ~down
              THEN /\ active' = FALSE /\ commits' = commits + 1
                   /\ badTarget' = (badTarget \/ ~Unchanged)
                   /\ badSession' = (badSession \/ gSession # session)
                   /\ UNCHANGED <<gIndex, gId, gSession>>
              ELSE IF kept
              THEN UNCHANGED <<active, gIndex, gId, gSession, commits, badTarget, badSession>>
              ELSE IF pressed /\ count > 0
              THEN /\ \E i \in 1..count : /\ gIndex' = i /\ gId' = objs[i]
                   /\ active' = TRUE /\ gSession' = session
                   /\ UNCHANGED <<commits, badTarget, badSession>>
              ELSE /\ active' = FALSE
                   /\ UNCHANGED <<gIndex, gId, gSession, commits, badTarget, badSession>>
        /\ gap' = FALSE /\ pressed' = FALSE
        /\ UNCHANGED <<open, session, down, clicks, closes>> /\ UNCHANGED env
Next == Close \/ Open \/ Press \/ Release \/ Edit \/ Draw
Spec == Init /\ [][Next]_vars /\ WF_vars(Open) /\ WF_vars(Draw) /\ WF_vars(Press)
        /\ WF_vars(Release)
TypeOK == /\ open \in BOOLEAN /\ gap \in BOOLEAN /\ session \in 1..(MaxCloses + 1)
          /\ count \in 0..N /\ objs \in [1..N -> 0..(N + MaxEdits)]
          /\ active \in BOOLEAN /\ gIndex \in 1..N /\ commits \in 0..MaxClicks
\* Every ObjectTransform lands on the object the drag grabbed.
CommitTargetsGrabbed == ~badTarget
\* A drag never commits after its picker session ended.
CommitInSession == ~badSession
\* Non-vacuity: with list edits and closes bounded below the clicks, some drag
\* survives to a commit.
DragCommits == <>(commits > 0)
====
