### Deadlock Dolly v0.7.22-beta

**0.7.22-beta**

- **Safer playback retries.** *Before:* a failed background worker start could leave cleanup or the next connection attempt stuck on a worker that never started. *After:* Dolly clears the refused worker and releases its connection or playback ownership so cleanup and retry can proceed.

- **Preserved read-only configuration.** *Before:* a failed configuration replacement could leave the original file writable. *After:* Dolly attempts to restore its original permissions while preserving the replacement error if restoration also fails.

- **More reliable recording audits.** *Before:* invalid color frame counts could pass an audit, and malformed audit files could escape normal error reporting. *After:* invalid counts become findings, audit reads are bounded, and deeply nested input is reported as unreadable.
