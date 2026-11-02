### Deadlock Dolly v0.6.91-alpha

**0.6.91-alpha**

- **Earlier shader tracking for Players.** *Before:* character shaders created while Dolly verified startup could be missed by Players capture. *After:* shader tracking starts before those waits, while the reviewed capture checks remain in place.

- **Clearer Players diagnostics.** *Before:* support reports could omit pixel coverage or show samples from an earlier take. *After:* diagnostics retain bounded RGB and alpha coverage and tag capture reports with their request, helping investigate blank layers without sharing a video.
