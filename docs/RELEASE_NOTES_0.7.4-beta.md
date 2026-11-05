### Deadlock Dolly v0.7.4-beta

**0.7.4-beta**

- **Natural aiming zoom in Follow.** *Before:* the Follow override could combine its aimed lens with the weapon's own zoom, producing extreme magnification. *After:* Follow applies weapon zoom once with default or custom offsets and restores the original camera settings when stopped.

- **Players exports tolerate capture backpressure.** *Before:* an export could stop when capture became busy during a replay query, even while frame writing continued. *After:* Dolly rechecks owned capture progress after the delayed reply, allowing progressing exports to continue while retaining the normal identity and hang checks.
