### Deadlock Dolly v0.7.64-beta

**0.7.64-beta**

- **Support for Deadlock build 6875 (Nov 26 2026 update).** *Before:* opening a replay after the build 6875 Deadlock update stopped with an unsupported game build message and rejected the server binary at startup. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6875 with an updated cvar unlocker.

- **Denser, better-lit rain with splashes, puddles, ripples and wet ground, plus finer blowing sand.** *Before:* rain read thinly over dry streets without splashes or puddles, and sandstorms looked like soft drifting dots rather than wind-driven grains. *After:* rain adds wet-ground sheen, puddles, splash rings and ripples alongside denser, better-lit streaks, and sandstorms render finer blowing sand grains.

- **Per-shot Standard and High weather quality switch.** *Before:* weather always rendered at one fixed detail level with no way to choose higher preview detail per shot. *After:* both the in-game Scene drawer and the desktop Scene page include a Standard/High weather quality switch saved per shot, while video exports always render at High quality.

- **Solomon's chess pieces leave tracks in snow, sand and ash.** *Before:* only heroes left trails in ground cover while Solomon's sliding chess pieces glided across snow, sand and ash without leaving a mark. *After:* when Hero tracks is enabled, Solomon's chess pieces carve clean, narrow drag tracks across snow, sand and ash, stop marking while airborne, and vanish whenever Hero tracks is turned off.
