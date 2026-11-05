---- MODULE RecoveryResourcesApi ----
\* A changed installation with a pending recovery marker must retain its
\* original-byte recovery resources. Creation/deletion are internal to the
\* updater workspace; no live installation is acted on by this specification.
VARIABLES needed, resources
vars == <<needed,resources>>
Init == /\ needed = FALSE /\ resources = FALSE
Create == /\ ~resources /\ resources' = TRUE /\ UNCHANGED needed
Protect == /\ ~needed /\ resources /\ needed' = TRUE /\ UNCHANGED resources
Release == /\ needed /\ needed' = FALSE /\ UNCHANGED resources
Remove == /\ ~needed /\ resources /\ resources' = FALSE /\ UNCHANGED needed
Next == Create \/ Protect \/ Release \/ Remove
Spec == Init /\ [][Next]_vars
====
