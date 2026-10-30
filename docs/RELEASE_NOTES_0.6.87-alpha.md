### Deadlock Dolly v0.6.87-alpha

**0.6.87-alpha**

- **Support for the latest Deadlock update.** *Before:* the October 30 patches prevented Dolly from starting. *After:* updated compatibility checks and a rebuilt unlocker restore support for game build 6833.

- **Startup after repeated updates.** *Before:* several game updates in a row could make Dolly reject unchanged game configuration. *After:* Dolly retains more verified patch history while continuing to reject invalid configuration.

- **Reliable replay jumps.** *Before:* incomplete minimap updates could crash Deadlock during certain replay seeks. *After:* the minimap guard works with the current patch; live startup and four exact replay seeks passed in the tested replay.
