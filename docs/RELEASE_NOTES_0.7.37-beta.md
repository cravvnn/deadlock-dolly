### Deadlock Dolly v0.7.37-beta

**0.7.37-beta**

- **Faster cleanup when the game closes.** *Before:* if Deadlock closed or crashed while Dolly was opening the paused camera, Dolly could wait up to about 16 seconds before cleaning up. *After:* Dolly notices the game has exited on its next check and cleans up right away.

- **More of Dolly checked together.** *Before:* camera release, Players capture restore and recording shutdown were each checked on their own. *After:* added checks confirm they hold together, including that closing asks a recording to stop before letting go of it.
