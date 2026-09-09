# Validation — 0.3.10 alpha

## Observed failure

The supplied 0.3.9 diagnostics show a completed native shot at 1.84375 seconds,
with the demo paused at tick 112274. Across 42 fresh handoff samples, the
underlying spectator remained about 328.44 units from the held final camera;
angle error was only 0.0125 degrees. Both Play and Stop called the same strict
handoff before proceeding, so neither could clear the held view. The user's
character-cycle workaround is consistent with refreshing that spectator state.
The video link could not be retrieved; this diagnosis is based on the logs.

## Scope of correction

An unsettled endpoint remains held without turning successful completion into
an error. Explicit Stop releases the native override with acknowledgement and
invalidates the previous position calibration. Play already calls Stop before
its normal seek, fresh calibration and native publication, so it can restart.
A failed release still blocks all subsequent restart writes. Pause can preserve
an unsettled held view; manual camera entry retains its strict handoff check.
Use Stop first if that entry asks to return control to the game.

No native source or DLL bytes changed from 0.3.9. The render callback, clock,
path interpolation, console path loop and manual paused movement methods are
unchanged. The bundled native metadata still accurately identifies its 0.3.9
build; the existing Windows workflow rebuilds it for the current app version.

## Checks

The Python suite includes repeated native completion/restart with a spectator
that never reaches the target, release-before-seek ordering, Pause then Stop,
settings restoration, failed-release blocking, normal verified handoff and
unchanged manual flight cancellation coverage. Source exports use the explicit
allowlist; the update ZIP is compared against the 0.3.9 source and checked to
reconstruct the complete 0.3.10 source without unrelated files.

Python suite: **602 tests run, 601 passed, 1 Windows-only test skipped**.
Python compilation passed. Native source and DLL byte comparison passed.
The skipped Windows named-memory check remains in the Windows Actions gate.

## Remaining live check

The user reports that native panning is now smooth. This correction has not
been run against Deadlock here, and no Windows EXE was built in this workspace.
Build a fresh Windows Actions run from the updated commit, extract the entire
Windows package, and play the same shot to completion three times at 0.1 speed
without cycling characters. Then test Stop / restore followed by Play once.
Stop can visibly return to the game's spectator view at a different position;
finishing a shot continues to hold its final authored camera.

The same diagnostic session later records game exit 0xC0000005 without a crash
stack. This patch does not establish the cause or fix that access violation.
If it recurs, retain its crash dump along with the exported diagnostics.
