---- MODULE ReleasePublishOrderApi ----
\* What a downloader of dist/ can see of SHA256SUMS.txt: the previous
\* matching set, no checksum file, or the new matching set, in that order.
VARIABLE sums
Spec == /\ sums = "old"
        /\ [][\/ sums = "old" /\ sums' = "absent"
              \/ sums = "absent" /\ sums' = "new"]_sums
====
