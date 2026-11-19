### Deadlock Dolly v0.7.46-beta

**0.7.46-beta**

- **Takes finish on their own, and Stop keeps them.** *Before:* a take could stay open after its shot ended until you pressed Stop recording, and pressing Stop in the middle of a frame-accurate or FFmpeg recording could throw the whole take away with "Export lost frames and was discarded". *After:* a take closes as soon as its shot ends, and Stop saves every frame recorded so far.

- **Sturdier Players exports.** *Before:* after one Players export, hiding your own body in first person stopped working until Deadlock restarted, a nearly full C: drive could fail a good take, and running out of memory could crash the game. *After:* hiding keeps working, the take no longer depends on a diagnostic file, exports run faster, and a memory shortage stops the take with a clear message.

- **Updates that finish.** *Before:* a downloaded update left waiting for over an hour could be deleted so that "Update now" kept failing, a read-only file could make every update roll back, and the message could blame Deadlock when another Dolly window was the cause. *After:* the download is kept, read-only files are handled, and Dolly names the real reason.

- **In-game panel and shot names.** *Before:* after the desktop app stalled for a couple of seconds, the in-game panel and free camera stayed off until you pressed F8, and shot names in other alphabets or with emoji got scrambled in the suggested filename. *After:* the panel comes back on its own and names are kept as typed. When Deadlock stops because its own files are damaged, Dolly now shows the game's reason and the Steam repair steps.
