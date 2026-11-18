### Deadlock Dolly v0.7.44-beta

**0.7.44-beta**

- **Works with the November 18 Deadlock update.** *Before:* after the update, Dolly refused to start a replay because the new game build had not been reviewed. *After:* Dolly supports the new build again, including the camera, Follow, exports with Depth and Players, and replay audio.

- **Problem reports show why the game closed.** *Before:* if Deadlock closed by itself while starting, the exported diagnostics did not include what the game printed, unless NVIDIA DLSS was turned on. *After:* every session keeps the game's own console log for diagnostics, and Dolly keeps only the logs from your 8 most recent sessions.
