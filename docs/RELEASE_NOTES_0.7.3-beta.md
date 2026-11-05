### Deadlock Dolly v0.7.3-beta

**0.7.3-beta**

- **Support for the latest Deadlock update.** *Before:* build 6851 was rejected by the camera, Follow and editing compatibility gates. *After:* reviewed client mappings and a rebuilt exact-build unlocker admit the new update while retaining existing build and ownership checks.

- **Safer update recovery.** *Before:* interrupted rollback could consume an original backup, and startup cleanup could race an active update. *After:* Dolly retains backups for retry and shares the updater lock through startup handoff. Existing settings, layouts and authored shots stay outside the update payload.

- **Retryable session cleanup.** *Before:* a late file could make final cleanup fail after its ownership marker was removed, blocking later recovery. *After:* a returning removal failure restores the marker so recovery can preserve the new file and retry safely. The released player-switch repair is retained.
