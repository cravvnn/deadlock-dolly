### Deadlock Dolly v0.7.11-beta

**0.7.11-beta**

- **Bounded compatibility-cache reads.** *Before:* Dolly could read an oversized compatibility cache fully before rejecting it. *After:* reads stop just beyond the existing size limit, and oversized caches remain rejected.

- **Quieter paused-memory monitoring.** *Before:* Dolly continued sampling memory after showing a warning for a paused replay. *After:* monitoring stops for that episode and rearms when playback, the game process or the replay position changes.

- **More resilient launch cleanup.** *Before:* a failure restoring one temporary handle flag could prevent restoring the other. *After:* Dolly attempts both restorations even if one fails.
