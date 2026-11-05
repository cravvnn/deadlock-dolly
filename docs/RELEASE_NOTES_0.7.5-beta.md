### Deadlock Dolly v0.7.5-beta

**0.7.5-beta**

- **Players exports recognize normal capture progress.** *Before:* replay queries could time out and stop a Players export even while capture was progressing normally. *After:* Dolly recognizes progress from its active capture and avoids redundant replay queries, retaining ownership, freshness and hang checks.

- **Safer configuration writes.** *Before:* a file-mode failure could occur after new configuration bytes were published, and a cleanup access error could hide the original write result. *After:* Dolly prepares the requested mode before replacement and preserves the original result when temporary-file cleanup encounters an access error.

- **Complete backups of invalid settings.** *Before:* an interrupted settings backup could leave a partial file with a completed-backup name. *After:* Dolly publishes the backup only after the entire original has been copied and synced, and refuses to overwrite an existing backup.
