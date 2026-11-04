### Deadlock Dolly v0.6.94-alpha

**0.6.94-alpha**

- **Automatic pacing for Players.** *Before:* temporary GPU or storage delays could stop a high-FPS Players export. *After:* Dolly waits for capture progress while keeping the selected output FPS, so slower hardware can take longer to finish.

- **Safer export cleanup.** *Before:* Dolly could try to restore the replay UI while Players was still finishing its writes, causing console timeouts or pending restoration. *After:* capture finishes before replay settings are restored, with clearer progress and storage error messages.
