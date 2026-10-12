# Deadlock Dolly 0.6.44-alpha

Skin/model mods no longer break the Bone Picker, and the editor recovers cleanly when a launched game exits early or a previous session ended without Stop / restore.

## Changes

- **Bone Picker works with skin/model mods.** *Before:* a hero whose model is replaced by a mod (for example a Sinclair skin whose model resource is renamed) showed "The selected model resource could not be verified" and listed no joints, so the picker could not be used. *After:* Dolly accepts a renamed model that lives in the same hero folder, so the skeleton and its bones resolve normally.
- **The picker no longer dead-ends.** *Before:* a brief paused-editor ownership change showed "Paused editor ownership changed. Cancel and reopen Bone Picker." and the picker stayed stuck. *After:* it waits and retries, and an empty list now states the real reason.
- **Launching recovers when the game exits early.** *Before:* a game that fataled during startup surfaced a raw "[WinError 10054] An existing connection was forcibly closed by the remote host". *After:* Dolly reports the game's exit and lets you relaunch immediately.
- **A saved shot reopens cleanly.** *Before:* a session that ended without Stop / restore left a pending replay-UI journal, so every later launch warned "restoration remains pending" and could loop. *After:* the stale journal is cleared automatically, so save then reopen just works.

## Validation

The full Python suite passes: 2,014 tests, 22 skipped. The native bridge was rebuilt and its tests pass; a renamed-model fixture is accepted while a genuinely different model is still rejected.

## Limits

Dolly cannot make a game that fatals because of a broken addon boot — for example an outdated `scripts/misc.vdata` from a mod manager. That is the addon, not Dolly. Dolly now reports the game's exit instead of a raw socket error so the cause is clear.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe, or let Dolly update itself. Keep the previous installation until your workflow is checked. Python and Tcl/Tk are bundled.
