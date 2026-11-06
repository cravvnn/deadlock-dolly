### Deadlock Dolly v0.7.13-beta

**0.7.13-beta**

- **Retryable layer operations.** *Before:* a refused layer operation could lose its pending work or scene restoration. *After:* Dolly retains that work for a later attempt when the operation queue refuses it.

- **Clear worker startup failures.** *Before:* a replay reader or update worker that could not start could leave a reading or busy status behind. *After:* Dolly reports the startup failure, and update actions can be retried explicitly.

- **Safer file checks.** *Before:* malformed camera bundles, depth channel descriptors or crash dumps could pass incomplete checks, and unreadable take files could interrupt an audit. *After:* Dolly rejects unsupported data more precisely and reports file-access failures in take audits.

- **More reliable cleanup and history.** *Before:* a cleanup failure could hide the original result or prevent other cleanup attempts, and resetting shot history made a redundant project copy. *After:* scratch and ReShade cleanup preserve the primary result, while history reset reuses its private saved snapshot.
