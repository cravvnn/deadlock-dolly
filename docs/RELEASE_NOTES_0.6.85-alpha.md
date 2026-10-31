### Deadlock Dolly v0.6.85-alpha

**0.6.85-alpha**

- **Players stay in sync.** *Before:* a Players export could stop when no players were visible, even with plenty of free storage. *After:* empty views keep their transparent frames, and capture waits for a free frame buffer while preserving the shot timing.

- **More reliable high-FPS exports.** *Before:* longer render work or a game UI warning could interrupt an export or leave restoration pending. *After:* fixed-step exports allow rendering time to finish, and console replies stay readable so preparation and restoration can complete.

- **Save Path during background work.** *Before:* desktop and in-game Save could do nothing while Dolly was busy. *After:* Save preserves the visible path, keeps any newer edits marked as unsaved, and records useful diagnostics if saving fails.

- **Reliable replay startup.** *Before:* Dolly could remain waiting for preload on the current game build. *After:* startup reads the verified hideout state correctly and continues through the normal replay checks.
