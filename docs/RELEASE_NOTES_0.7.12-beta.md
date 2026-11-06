### Deadlock Dolly v0.7.12-beta

**0.7.12-beta**

- **Cleaner failed connections.** *Before:* a failure configuring a new console connection could leave its socket open until later cleanup. *After:* Dolly closes the unpublished socket when setup fails.

- **Retryable operation startup.** *Before:* a failed operation-start notification could leave the interface busy and block retries. *After:* Dolly releases the reservation and preserves the original error so another operation can be submitted.

- **Clear camera export results.** *Before:* failure to remove a temporary camera-export file could hide the original error or report failure after a successful save. *After:* Dolly preserves the export result when temporary-file cleanup fails.
