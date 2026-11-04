### Deadlock Dolly v0.6.96-alpha

**0.6.96-alpha**

- **Follow player selection.** *Before:* a camera switch could make Dolly reject a valid player with a replay-changed error. *After:* Dolly settles the replay and checks the player again before starting Follow.

- **Keep the chosen replay moment.** *Before:* the spectator switch could advance a paused replay during Follow setup. *After:* Dolly returns to the selected tick before applying the camera rig, while retaining cancellation and player-identity checks.
