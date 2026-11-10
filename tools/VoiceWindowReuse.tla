---- MODULE VoiceWindowReuse ----
EXTENDS Naturals
\* Production-only settings; matched baseline comparison stays private.
\* API checks minimal returning completion shape, not full caller refinement.
\* filter_window called by ClipAudioCapture.finish before export_captured_audio.
\* One reused (slot,id), two complete non-overlapping lifetimes in clock order;
\* integer endpoints0..6, reference window(2,4). RecordFirst/RecordSecond abstract
\* parsing/pairing one complete lifetime each. Candidate unions overlap evidence;
\* baseline's last-start/last-stop dictionaries retain only the last lifetime.
\* No file publication, orphan/missing stops, equal timestamps, multiple keys,
\* actual engine-ID reuse frequency, acoustic fidelity or I/O liveness modeled.
\* Actual WAV/CSV/export probes cover orphan/unmatched cases outside this slice.
\* Terminal stuttering intentional, deadlock checks retained. No whole-program proof.
Candidate == TRUE
VARIABLES a,b,c,d,pc,selected
vars == <<a,b,c,d,pc,selected>>
phase == IF pc = "done" THEN "done" ELSE "active"
API == INSTANCE VoiceWindowReuseApi WITH phase <- phase
Overlap(x,y) == x < 4 /\ y > 2
Init == /\ a \in 0..6 /\ b \in 0..6 /\ c \in 0..6 /\ d \in 0..6
        /\ a < b /\ b < c /\ c < d /\ pc = "first" /\ selected = FALSE
RecordFirst == /\ pc = "first" /\ pc' = "second" /\ selected' = Overlap(a,b)
               /\ UNCHANGED <<a,b,c,d>>
RecordSecond == /\ pc = "second" /\ pc' = "done"
                /\ selected' = (Overlap(c,d) \/ (Candidate /\ selected))
                /\ UNCHANGED <<a,b,c,d>>
Done == /\ pc = "done" /\ UNCHANGED vars
Next == RecordFirst \/ RecordSecond \/ Done
TypeOK == /\ a \in 0..6 /\ b \in 0..6 /\ c \in 0..6 /\ d \in 0..6
          /\ pc \in {"first","second","done"} /\ selected \in BOOLEAN
NoLostVoice == (pc = "done" /\ (Overlap(a,b) \/ Overlap(c,d))) => selected
NoFalseSelection == pc = "done" => (selected => (Overlap(a,b) \/ Overlap(c,d)))
Spec == Init /\ [][Next]_vars
Refines == API!Spec
====
