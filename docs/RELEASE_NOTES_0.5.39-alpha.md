# PLEASE WAIT: LET DOLLY LOAD THE HIDEOUT, THEN YOUR DEMO

After clicking **Open replay in Dolly**, let Dolly finish the startup sequence.
It advances **click to continue**, loads the hideout, waits for map and shader
preloading to finish, then automatically opens your selected demo. **Do not
manually load a replay or click through the game's menus while Dolly is working.**
Wait until Dolly reports that the replay is ready.

**0.5.39-alpha**

- **Verified map and shader preloading.** *Before:* Dolly could open a replay before background preloading started. *After:* Dolly verifies that preloading has started and finished before opening your demo.
- **Fully automatic startup.** *Before:* Reaching the preload stage could require clicking the game's initial screen. *After:* Dolly handles that screen and continues into your paused replay automatically.
- **Clear startup progress.** *Before:* Hideout readiness did not confirm background preload completion. *After:* Dolly shows its preload progress and stops with an explanation if completion cannot be verified.

## Verification and limits

Hands-off startup was verified locally with both Native and Console camera
modes using the saved replay and graphics settings. The selected replay reached
paused camera readiness after verified preload completion; both processes exited
cleanly and snapshotted configuration/settings were restored exactly.

This is a startup-readiness improvement, not a claim that all replay crashes or
shader hitches are fixed. Verification supports reviewed game builds; an unknown
update can require a Dolly update before automatic startup is available. Manual
troubleshooting controls remain available but bypass automatic preload verification.
The existing hardware encoder and depth-scene smoke-test exclusions remain.
