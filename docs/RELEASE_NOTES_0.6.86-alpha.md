### Deadlock Dolly v0.6.86-alpha

**0.6.86-alpha**

- **More reliable replay seeking.** *Before:* jumping to certain parts of a current, otherwise playable replay could crash Deadlock. *After:* Dolly's Native driver guards incomplete minimap updates so affected jumps can finish without changing the replay.

- **Clearer crash details.** *Before:* a seek crash could suggest that the replay was old or damaged without confirming the cause. *After:* Dolly keeps your shot available and shows diagnostic guidance with the replay and game build details.
