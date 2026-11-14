---- MODULE MediaCloseApi ----
\* Caller-visible media teardown on close (MediaTransport._finish_media_and_unmap
\* via NativeBridge.close -> _close_media): the encoder is asked to stop before
\* the shared mapping is unmapped. Asking may fail (caught); the attempt still
\* precedes unmap. Says nothing about how the take ends: RecorderApi does.
\* Refined by MediaFinalize. Composed by DollyRoot.
VARIABLES stopAsked,unmapped
vars == <<stopAsked,unmapped>>
Init == stopAsked = FALSE /\ unmapped = FALSE
AskStop == /\ unmapped = FALSE /\ stopAsked' = TRUE /\ UNCHANGED unmapped
Unmap == /\ stopAsked /\ unmapped = FALSE /\ unmapped' = TRUE /\ UNCHANGED stopAsked
Spec == Init /\ [][AskStop \/ Unmap]_vars
====
