### Deadlock Dolly v0.7.38-beta

**0.7.38-beta**

- **Finished recordings stay finished.** *Before:* if the game's screen was lost or its resolution changed right after you stopped a recording, Dolly could report the recording as failed while keeping the video, or throw away a finished camera export. *After:* a recording that finished stays completed with its files.

- **Cleaner cleanup after the game closes.** *Before:* when the game closed, two cleanups could run at the same moment and occasionally lose the saved exit result. *After:* they run one after the other.
