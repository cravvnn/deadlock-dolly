---- MODULE GameFileCache ----
EXTENDS Naturals
\* Maintained production contract; private baseline comparisons are not shipped.
\* preload.game_file; PreloadMonitor, ReplayCameraMonitor, FollowTargetMonitor and
\* follow capability probes compare its returned digest with reviewed pins.
\* One prior cached generation, one read, up to two replacements with unchanged
\* size/mtime. Full path metadata distinguishes generations; stat is coherent.
\* Size1 fits, size2 exceeds the retained-byte budget1. Real tests cover the
\* multi-entry budget, warm hits and replacement accounting. Bytes/hash remain
\* coupled; no payload/read-memory bound, hostile metadata forgery, identity
\* reuse, parallel callers or module load lock is established.
\* Weak fairness assumes returning steps. Done stutters; completion API is local.
\* preload.game_file, monitors for owned replay startup. One cached generation,
\* one replacement retaining size/mtime, one read, one optional second replacement.
\* Full path stamp changes by generation; hash correctly identifies read bytes.
\* Path stat observations are atomic; not protection from arbitrary timestamp forgery,
\* OS identity reuse, parallel callers, real module load races or post-check replacement.
VARIABLES pc,file,seen,data,result,cache,cacheStamp,cachedBytes,size,changes,hit
vars == <<pc,file,seen,data,result,cache,cacheStamp,cachedBytes,size,changes,hit>>
CheckIdentity == TRUE
RespectBudget == TRUE
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE GameFileCacheApi WITH phase <- phase

Init == /\ pc = "stat" /\ file = 0 /\ seen = 0 /\ data = 0 /\ result = 3
        /\ cache = 0 /\ cacheStamp = 0 /\ cachedBytes = 1 /\ size \in 1..2 /\ changes = 0 /\ hit = FALSE
Replace == /\ pc \in {"stat","read","after"} /\ changes < 2 /\ file' = file+1 /\ changes' = changes+1
           /\ UNCHANGED <<pc,seen,data,result,cache,cacheStamp,cachedBytes,size,hit>>
Stat == /\ pc = "stat" /\ seen' = file /\ pc' = "lookup" /\ UNCHANGED <<file,data,result,cache,cacheStamp,cachedBytes,size,changes,hit>>
Lookup == /\ pc = "lookup" /\ hit' = (~CheckIdentity \/ cacheStamp = seen)
          /\ pc' = IF ~CheckIdentity \/ cacheStamp = seen THEN "hit" ELSE "read"
          /\ UNCHANGED <<file,seen,data,result,cache,cacheStamp,cachedBytes,size,changes>>
Hit == /\ pc = "hit" /\ result' = cache /\ pc' = "done" /\ UNCHANGED <<file,seen,data,cache,cacheStamp,cachedBytes,size,changes,hit>>
Read == /\ pc = "read" /\ data' = file /\ pc' = "after" /\ UNCHANGED <<file,seen,result,cache,cacheStamp,cachedBytes,size,changes,hit>>
After == /\ pc = "after" /\ pc' = "done"
         /\ result' = IF CheckIdentity /\ file # seen THEN 3 ELSE data
         /\ cache' = IF (~CheckIdentity \/ file = seen) /\ (~RespectBudget \/ size = 1) THEN data ELSE cache
         /\ cacheStamp' = IF (~CheckIdentity \/ file = seen) /\ (~RespectBudget \/ size = 1) THEN seen ELSE cacheStamp
         /\ cachedBytes' = IF (~CheckIdentity \/ file = seen) /\ (~RespectBudget \/ size = 1) THEN size ELSE cachedBytes
         /\ UNCHANGED <<file,seen,data,size,changes,hit>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == Replace \/ Stat \/ Lookup \/ Hit \/ Read \/ After \/ Done
TypeOK == /\ pc \in {"stat","lookup","hit","read","after","done"} /\ file \in 0..2 /\ seen \in 0..2
          /\ data \in 0..2 /\ result \in 0..3 /\ cache \in 0..2 /\ cacheStamp \in 0..2
          /\ cachedBytes \in 1..2 /\ size \in 1..2 /\ changes \in 0..2 /\ hit \in BOOLEAN
CorrectGeneration == pc = "done" /\ result # 3 => result = seen
RetentionBound == cachedBytes <= 1
CacheReceipt == cache = cacheStamp
Spec == Init /\ [][Next]_vars /\ WF_vars(Next)
Returns == <>(phase = "done")
Refines == API!Spec

====
