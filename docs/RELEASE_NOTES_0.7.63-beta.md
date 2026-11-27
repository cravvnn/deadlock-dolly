### Deadlock Dolly v0.7.63-beta

**0.7.63-beta**

- **Support for Deadlock build 6873 (Nov 26 2026 updates).** *Before:* opening a replay after the November 26 Deadlock updates stopped with an unsupported game build message. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6873 (and build 6872).

- **Updated cvar unlocker for build 6873.** *Before:* the previous cvar unlocker rejected build 6873's updated server binary at startup. *After:* Dolly ships an updated cvar unlocker matched to build 6873 while keeping support for earlier reviewed builds.
