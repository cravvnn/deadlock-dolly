# Deadlock Dolly 0.5.40-alpha

## Copy error details from any error dialog

Error dialogs now have a **Copy error details** button beside **OK**. One click
puts the exact report on the clipboard:

```
Deadlock Dolly 0.5.40-alpha | Play shot
Camera position did not settle. Release movement keys and enter replay freecam, then retry. Export diagnostics if it persists.
```

The first line names your Dolly version and the step that failed; the rest is
the error exactly as shown. Paste that text when asking for help instead of
screenshotting the dialog. The dialog keeps the same plain message-box
appearance and OK behavior as before, and closing it is unchanged. Startup
failures that happen before the editor window exists keep their previous dialog.

## Scope and verification

The button covers every editor error dialog: startup and connection failures,
shot playback, camera and export problems, and in-game panel errors. It changes
no game interaction, capture, recording or replay behavior.

A full local release build passed 1362 Python tests (16 skipped), 20 enabled
native checks, the frozen GUI/updater smoke tests and both package/source
audits. No game launch was part of this change, and no game-runtime behavior is
claimed.
