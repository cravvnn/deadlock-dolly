### Deadlock Dolly v0.7.27-beta

**0.7.27-beta**

- **Safer recording recovery.** *Before:* a failed stop or unreadable recorder report could restore runtime overrides while a recording remained active. *After:* Dolly retains timing, render scale, POV and retry ownership until terminal telemetry or confirmed process exit.

- **Bounded update validation.** *Before:* a growing or deeply nested update manifest could escape normal validation. *After:* Dolly bounds the actual read and skips invalid staged manifests through the normal validation path.

- **Reliable updater cleanup.** *Before:* ambiguous journals could authorize workspace deletion, and locked temporary files could hide the original write error. *After:* Dolly retains uncertain recovery files, tolerates malformed legacy records and preserves the primary write failure.

