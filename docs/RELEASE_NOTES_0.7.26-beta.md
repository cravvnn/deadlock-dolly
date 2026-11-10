### Deadlock Dolly v0.7.26-beta

**0.7.26-beta**

- **Reliable audio cleanup results.** *Before:* a locked temporary audio file could make a saved recording appear to fail or obscure the original stop error. *After:* Dolly preserves the recording result and logs temporary files that could not be removed.

- **Correct reused voice selection.** *Before:* a later use of the same voice key could hide an earlier segment that overlapped the selected window. *After:* Dolly keeps the key when any observed lifetime overlaps and handles reordered start/stop records conservatively.

- **Complete resource inventory.** *Before:* resources stored entirely in a VPK entry's preload data could be omitted from listings and material-presence checks. *After:* Dolly includes those readable entries while continuing to skip empty entries and enforce duplicate and count limits.
