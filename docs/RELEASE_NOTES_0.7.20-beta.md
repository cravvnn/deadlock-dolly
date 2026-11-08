### Deadlock Dolly v0.7.20-beta

**0.7.20-beta**

- **More resilient recording cleanup.** *Before:* an audio stop error could prevent video and effect cleanup, and pending video could be overlooked. *After:* Dolly retains pending-video intent and attempts later cleanup independently. Native failures can still prevent a recording from finishing.

- **Safer graphics profile errors.** *Before:* deeply nested graphics profile files could escape normal error reporting. *After:* Dolly reports the read error and leaves the original file untouched.

- **Replay loading failure reporting.** *Before:* deeply nested replay-helper data could leave the reader showing a loading state. *After:* the reader reports a failure while still rejecting results from an older replay.
