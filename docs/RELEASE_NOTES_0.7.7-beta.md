### Deadlock Dolly v0.7.7-beta

**0.7.7-beta**

- **Players export recovery.** *Before:* a busy game console could block the stop request after a failed Players export or leave restoration pending after a brief timeout. *After:* Dolly requests an owned capture stop first, then retries replay verification before restoring settings, preserving raw captures and saved values when recovery cannot be confirmed.

- **Reliable settings errors.** *Before:* an error removing a temporary settings file could hide the original save failure. *After:* Dolly preserves the original result so a cleanup problem does not replace the useful error.

- **Safer update preparation.** *Before:* malformed cached update metadata could interrupt the search for a usable update. *After:* Dolly rejects invalid metadata consistently and can continue checking other prepared updates.
