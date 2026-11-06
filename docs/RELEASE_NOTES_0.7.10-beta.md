### Deadlock Dolly v0.7.10-beta

**0.7.10-beta**

- **Clear Players export results.** *Before:* a locked temporary movie could hide an export error or cancellation. *After:* Dolly preserves the original result and attempts cleanup of both temporary movies.

- **Safer still-image saves.** *Before:* overlapping still-image saves shared a temporary file, and a failed save could leave it behind. *After:* each save uses its own temporary file and replaces the image only after the PNG is complete.

- **Retryable ReShade preset setup.** *Before:* an interrupted first-time preset write could leave an incomplete preset that blocked a retry. *After:* Dolly finishes each preset before installing it and preserves existing user edits.
