### Deadlock Dolly v0.7.42-beta

**0.7.42-beta**

- **Dolly always finishes closing.** *Before:* if Deadlock's `gameinfo.gi` was changed by another program during a session, closing Dolly could get stuck retrying and never exit. *After:* Dolly closes, and asks what to do about the changed file the next time it opens.

- **No false "changed outside Dolly" prompt with two Dolly windows.** *Before:* if you closed one Dolly while the game ran, opened another, and started a replay right as the game closed, Dolly could wrongly report that another program had changed `gameinfo.gi`. *After:* both clean up one at a time, so this cannot happen.

- **Safer updates.** *Before:* an update that turned an app file into a folder could not be undone if it failed, and the reverse change could not install at all. *After:* both install and roll back cleanly.
