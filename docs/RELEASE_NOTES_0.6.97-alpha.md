### Deadlock Dolly v0.6.97-alpha

**0.6.97-alpha**

- **Game audio stays with the shot.** *Before:* fixed-step exports could play sounds late because audio included preparation and skipped frames. *After:* Dolly removes that extra audio and follows the recorded frames, including slow-motion takes.

- **Safer audio recovery.** *Before:* incomplete audio could be padded and attached without verifying its timing against the video. *After:* Dolly checks the alignment and keeps the original audio and silent video if it cannot verify the soundtrack.
