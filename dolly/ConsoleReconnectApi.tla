---- MODULE ConsoleReconnectApi ----
\* One connect call settles once: connected, failed or refused.
VARIABLE result
Spec == /\ result = "pending"
        /\ [][result = "pending" /\ result' \in {"connected", "failed", "refused"}]_result
====
