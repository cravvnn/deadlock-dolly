### Deadlock Dolly v0.7.69-beta

**0.7.69-beta**

- **Support for Deadlock build 6882 (Nov 27 2026 update).** *Before:* opening a replay after the build 6882 Deadlock update stopped with an unsupported game build message. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6882.

- **Updated cvar unlocker for build 6882.** *Before:* the previous cvar unlocker rejected build 6882's updated server binary at startup. *After:* Dolly ships an updated cvar unlocker matched to build 6882 while keeping support for earlier reviewed builds.
