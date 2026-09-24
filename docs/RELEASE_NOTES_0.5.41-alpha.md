# Deadlock Dolly 0.5.41-alpha

- **Reliable error dialogs.** *Before:* An export error could lead to Dolly closing unexpectedly. *After:* Error dialogs remain responsive and retain Copy error details.
- **Recover after a failed export pass.** *Before:* A failed layer handoff could leave camera control held. *After:* Dolly attempts to restore control and stops queued passes while retaining completed videos.
- **Earlier Players space checks.** *Before:* Low temporary space could first be reported after other passes finished. *After:* Dolly estimates the space needed before starting and checks again before Players.

Players capture still stores a temporary uncompressed RGBA half-float bundle on
the game drive. After a successful MOV encode, Dolly removes that bundle.
The production encode path pipes frames to FFmpeg without a PNG sequence;
this release does not change its storage requirements or color conversion.

The error-dialog crash was reproduced and checked in an isolated Windows/Tk
process. Handoff cleanup and space checks have automated regression coverage.
The original full recording has not been re-exported in-game for this fix.
