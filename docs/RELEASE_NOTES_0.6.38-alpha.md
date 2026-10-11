# Deadlock Dolly 0.6.38-alpha

Fixes a startup failure on developer (`-dev`) game sessions where Dolly stopped with "This game build did not confirm hideconsole" before the replay opened.

## Changes

- **Startup survives the developer console flood.** *Before:* on a `-dev` build the engine dumps its hidden cvar list at startup, which could delay the `hideconsole` check past its window; Dolly treated the unconfirmed check as a missing command and aborted. *After:* an unconfirmed console check is treated as unknown, not rejected, so the explicit command is issued and startup continues.
- **Console availability checks report unknown instead of false.** *Before:* a probe that could not be completed reported the same result as an explicit "unknown command", so unrelated startup and probe gates could fail on a busy console. *After:* only an explicit console rejection counts as missing; an unconfirmed check no longer blocks hideout readiness, replay recovery, camera support or the console toggle.

## Validation

The full Python suite passes: 1,984 tests, 22 skipped. This is a robustness fix to existing startup paths; no game build compatibility profiles, native code, capture or export behavior changed.

## Limits

This change only affects how an unconfirmed console check is handled. A game build that genuinely removes these console commands still reports them as missing and Dolly refuses the affected action rather than guessing. No live game session was run for this change.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe. Keep the previous installation until your workflow is checked. Python and Tcl/Tk are bundled.