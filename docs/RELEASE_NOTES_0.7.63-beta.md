### Deadlock Dolly v0.7.63-beta

**0.7.63-beta**

- **Support for Deadlock build 6872 (Nov 26 2026 update).** *Before:* opening a replay after the build 6872 Deadlock update stopped with an unsupported game build message. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6872.

- **Updated cvar unlocker for build 6872.** *Before:* the previous cvar unlocker rejected build 6872's updated server binary at startup. *After:* Dolly ships an updated cvar unlocker matched to build 6872 while keeping support for earlier reviewed builds.
