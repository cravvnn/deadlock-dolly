### Deadlock Dolly v0.7.43-beta

**0.7.43-beta**

- **The game HUD comes back.** *Before:* pausing and resuming during a shot, a failed POV finish, or a failed Follow pick could leave Deadlock's HUD hidden until the game restarted, and the HUD could show up in a recording. *After:* Dolly puts your HUD back every time and keeps it out of shots that hide it.

- **Faster close after the game exits.** *Before:* with ReShade on, or after a crash while recording, closing Dolly could hang for 5 to 15 seconds. *After:* Dolly closes right away.

- **Sturdier exports.** *Before:* folder names in other alphabets could turn export errors into crashes, long layer exports could time out and leave a broken video, and a failed Finish could leave the game recording audio. *After:* these finish cleanly or report a clear error.

- **Fewer false failures.** *Before:* one slow console reply could stop startup or a shot's replay reload, and Bank edits did nothing on a camera with a roll curve. *After:* Dolly keeps waiting, and Bank edits apply.
