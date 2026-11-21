### Deadlock Dolly v0.7.48-beta

**0.7.48-beta**

- **Self-recorded demos say what is wrong.** *Before:* a demo made with the in-game "record" command opened in Dolly with every hero and model frozen in place, with nothing to explain why. *After:* Dolly recognises that kind of demo before it starts Deadlock and tells you how to record it again with SourceTV (tv_enable 1, load the map, tv_record and tv_stoprecord), which plays and seeks normally.

- **No more replay that closes when you seek.** *Before:* moving backward in a "record" demo ended the replay inside Deadlock and left Dolly showing seek and repair errors. *After:* those demos are stopped at the start, so a session no longer reaches that point; match replays and SourceTV recordings are unchanged.
