### Deadlock Dolly v0.7.41-beta

**0.7.41-beta**

- **No more leftover folders in the game directory.** *Before:* every Dolly session left a `citadel_dolly_...` folder behind in Deadlock's game folder. *After:* the folder is removed when the game closes, and old leftovers are cleared the next time you launch a replay. Folders holding your own files are still kept.

- **Closing Dolly never skips the save prompt.** *Before:* if stopping playback failed while you were closing Dolly, it could exit without asking to save your shot. *After:* it always asks first.

- **Updates recover on their own.** *Before:* a failed update could block Dolly from opening with "another update may be in progress", and each failed download left a large folder behind. *After:* Dolly opens normally when its files are intact, and failed downloads clean up after themselves.

- **Your settings and mods survive more edge cases.** *Before:* a settings file that could not be read once could be replaced by defaults, and a mod file named with an upper-case `.VPK` made Dolly skip all your mods. *After:* the old settings are kept as a backup copy, and those mods load.
