### Deadlock Dolly v0.7.68-beta

**0.7.68-beta**

- **Support for Deadlock build 6880 (Nov 27 2026 update).** *Before:* opening a replay after the build 6880 Deadlock update stopped with an unsupported game build message. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6880.

- **Updated cvar unlocker for build 6880.** *Before:* the previous cvar unlocker rejected build 6880's updated server binary at startup. *After:* Dolly ships an updated cvar unlocker matched to build 6880 while keeping support for earlier reviewed builds.
