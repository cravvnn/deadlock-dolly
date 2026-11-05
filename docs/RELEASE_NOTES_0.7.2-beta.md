### Deadlock Dolly v0.7.2-beta

**0.7.2-beta**

- **Reliable player switching.** *Before:* selecting the next player near the start of a replay could fail while Deadlock was still blending to the hero camera. *After:* Dolly lets the stock transition finish before confirming Follow.

- **Keep your timeline position.** *Before:* a player-camera handoff could advance the paused replay or interrupt a transition with an unnecessary seek. *After:* Dolly restores your chosen paused tick and keeps your playback speed unchanged when switching players.
