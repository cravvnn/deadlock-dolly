### Deadlock Dolly v0.7.61-beta

**0.7.61-beta**

- **Support for Deadlock build 6871.** *Before:* opening a replay after the November 26 Deadlock update stopped with an unsupported game build message. *After:* Dolly launches, follows heroes, and exports color, depth, EXR and player layers on build 6871.

- **Session-only Debug view dropdown in Look.** *Before:* switching to albedo, normals, roughness, wireframe, visibility grid or particle overlays meant entering console commands by hand and resetting them yourself. *After:* pick a **Debug view** on the desktop **Look** page or in the in-game **Look** drawer to inspect views cleanly for the current session; it is never saved to your shots or preferences. Visibility grid and Particle overlays make the game and exports very slow, so Dolly pauses them while seeking and turns them back on once the shot is in position.
