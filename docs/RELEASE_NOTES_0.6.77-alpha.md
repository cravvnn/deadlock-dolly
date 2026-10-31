### Deadlock Dolly v0.6.77-alpha

**0.6.77-alpha**

- **Game updates can sometimes skip a new Dolly download.** *Before:* every Deadlock update broke Native camera until a matching Dolly build shipped. *After:* Dolly fetches small reviewed build definitions from the project's GitHub page in the background and accepts a client-only update from that file. A changed `server.dll` still needs a release because the cvar unlocker is built from it.

- **Support is additive and offline-safe.** *Before:* build support was fixed at release time. *After:* published definitions can only add newly reviewed builds, never remove or override what shipped in the release, and a missing, failed or malformed download leaves the installed build behaving exactly as before.

- **The startup build check reports the merged result.** *Before:* **Check game build** only knew about the installed release. *After:* it also reports support that arrived through published definitions, including the editing-configuration pins and whether the cvar unlocker still needs a matching release.

- **Players layer exports no longer fail when the scratch drive falls behind.** *Before:* a 1440p Players capture could stop with "missed shot frames" after a brief write stall, even though the replay never missed a frame. *After:* the fixed-step capture waits for the scratch drive to catch up and keeps every frame, and a genuine stop reports the drive instead of blaming the shot cadence.
