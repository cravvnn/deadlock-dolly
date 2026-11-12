---- MODULE NativeSnapshotClose ----
EXTENDS Naturals
\* bridge_win worker atomic_store_release(gCommand) vs setup callback's
\* atomic_load_acquire shared_ptr. Desktop close unmaps its OWN view only;
\* native gMemory/module/hook remain resident until game-process exit.
\* One serialized callback at a time, two command objects: A camera, B Release.
\* Callback may hold A through B publication; no successful Release ack yet.
\* Release ack2 means finish(Stopped) with successful effects restoration.
\* No claim for parallel callback invocations, C++ seqlock/data-race legality,
\* weak-memory semantics, status writer arbitration, effects failure or GPU.
VARIABLES pc,head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite
vars == <<pc,head,local,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
Init == /\ pc="idle" /\ head=1 /\ local=0 /\ published=FALSE
        /\ desktopMapped=TRUE /\ nativeMapped=TRUE /\ oldAlive=TRUE /\ ack=0 /\ badWrite=FALSE
Acquire == /\ pc="idle" /\ local'=head /\ pc'="use"
           /\ UNCHANGED <<head,published,desktopMapped,nativeMapped,oldAlive,ack,badWrite>>
PublishRelease == /\ ~published /\ head'=2 /\ published'=TRUE
                  /\ oldAlive' = (local=1) /\ UNCHANGED <<pc,local,desktopMapped,nativeMapped,ack,badWrite>>
DesktopClose == /\ published /\ desktopMapped /\ desktopMapped'=FALSE
                /\ nativeMapped'=TRUE /\ UNCHANGED <<pc,head,local,published,oldAlive,ack,badWrite>>
Use == /\ pc="use" /\ pc'="return"
       /\ ack'= IF local=2 THEN 2 ELSE ack
       /\ badWrite'=(badWrite \/ (local=1 /\ ack=2))
       /\ UNCHANGED <<head,local,published,desktopMapped,nativeMapped,oldAlive>>
Return == /\ pc="return" /\ pc'="idle" /\ local'=0
          /\ oldAlive' = ~published
          /\ UNCHANGED <<head,published,desktopMapped,nativeMapped,ack,badWrite>>
Run == Acquire \/ PublishRelease \/ DesktopClose \/ Use \/ Return
Spec == Init /\ [][Run]_vars
TypeOK == /\ pc \in {"idle","use","return"} /\ head \in {1,2} /\ local \in {0,1,2}
          /\ published \in BOOLEAN /\ desktopMapped \in BOOLEAN /\ nativeMapped \in BOOLEAN
          /\ oldAlive \in BOOLEAN /\ ack \in {0,2} /\ badWrite \in BOOLEAN
SnapshotAlive == pc \in {"use","return"} /\ local=1 => oldAlive
NativeViewAlive == nativeMapped
NoWriteAfterSuccessfulRelease == ~badWrite
====
