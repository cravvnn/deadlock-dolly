---- MODULE MemoryWatch ----
EXTENDS Naturals, Sequences
\* memory_watch.PausedMemoryWatch.observe, serialized GUI observations.
\* Identity0 = invalid/unpaused; identities1/2 are distinct PID/tick episodes.
\* Reset happens BEFORE warned gate. Unknown-tick transitions and window expiry
\* stay actual-code probe obligations. At most6calls over18s: all within the
\* production60s window. Samples0/1/2GiB;3means unreadable. Defaults min3,
\* threshold2GiB, interval3s. No actual process counters or clock exceptions.
\* Caller _check_paused_memory only consumes the optional warning; GUI text/log
\* adaptation is probed separately. Concurrent observe calls not supported/proved.
\* Production warned latch only; historical baseline/paired traces stay private.
\* badRead is verification history, not a production field. Weak fairness
\* assumes serialized returning observations; Done stutters after Bound calls.
CONSTANT Bound
Empty(k) == [key |-> k, hist |-> <<>>, last |-> 0, warned |-> FALSE]
MinValue(h) == IF Len(h) = 0 THEN 0 ELSE
    CHOOSE v \in {h[i] : i \in 1..Len(h)} : \A j \in 1..Len(h) : v <= h[j]
Step(w,k,t,v) ==
    LET gate == IF k = 0 THEN Empty(0) ELSE IF w.key # k THEN Empty(k) ELSE w
        skip == k = 0 \/ gate.warned \/
                (gate.last # 0 /\ t - gate.last < 3)
    IN IF skip THEN [state |-> gate, read |-> FALSE, signal |-> FALSE]
       ELSE IF v = 3 THEN [state |-> gate, read |-> TRUE, signal |-> FALSE]
       ELSE LET h == Append(gate.hist,v)
                fire == Len(h) >= 3 /\ v - MinValue(h) >= 2
            IN [state |-> [gate EXCEPT !.hist = h, !.last = t,
                           !.warned = gate.warned \/ fire],
                read |-> TRUE, signal |-> fire]
VARIABLES watch, now, calls, signal, badRead, lastKey
vars == <<watch,now,calls,signal,badRead,lastKey>>
API == INSTANCE MemoryWatchApi WITH episode <- watch.key, warned <- watch.warned, signal <- signal
Init == /\ watch = Empty(0) /\ now = 3 /\ calls = 0
        /\ signal = FALSE /\ badRead = FALSE /\ lastKey = 0
Observe(k,dt,v) ==
    /\ calls < Bound
    /\ LET current == Step(watch,k,now + dt,v)
       IN /\ watch' = current.state
          /\ signal' = current.signal
          /\ badRead' = (badRead \/ (watch.warned /\ watch.key = k /\ k # 0 /\ current.read))
    /\ now' = now + dt /\ calls' = calls + 1 /\ lastKey' = k
Run == \E k \in {0,1,2}, dt \in {0,3}, v \in {0,1,2,3} : Observe(k,dt,v)
Done == /\ calls = Bound /\ UNCHANGED vars
Next == Run \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Run)
TypeOK == /\ watch.key \in {0,1,2} /\ watch.warned \in BOOLEAN
          /\ Len(watch.hist) <= Bound /\ watch.last \in 0..(3 + Bound * 3)
          /\ calls \in 0..Bound /\ now \in 3..(3 + Bound * 3)
          /\ signal \in BOOLEAN /\ badRead \in BOOLEAN
WithinWindow == now - 3 < 60
EpisodeIdentity == watch.key = lastKey
NoPostWarningRead == ~badRead
Returns == <>(calls = Bound)
Refines == API!Spec
====
