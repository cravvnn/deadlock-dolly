### Deadlock Dolly v0.7.28-beta

- **Reliable shot opening.** *Before:* a valid shot could be rejected or receive an incorrect migration notice after Dolly read the file twice. *After:* the shot and its source version come from the same bounded read.

- **Consistent build definitions.** *Before:* concurrent startup reads and a definitions refresh could expose empty or outdated support data. *After:* Dolly serializes publication and keeps completed refreshes and withdrawals intact.

- **Stronger take audits.** *Before:* malformed camera data could abort an audit, and missing or empty video evidence could receive an OK result. *After:* Dolly reports those problems, bounds camera reads and continues the remaining checks.

