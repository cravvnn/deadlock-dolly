### Deadlock Dolly v0.7.62-beta

**0.7.62-beta**

- **Seeking to the start of a replay no longer crashes Deadlock.** *Before:* dragging Dolly's replay timeline to the left edge or stepping back past the start sent `demo_gototick 0`, which triggers a Deadlock engine access violation when reconstructing full packet 1. *After:* Dolly clamps replay seeks to tick 1 — the first recorded packet in Deadlock replays — so scrubbing, stepping, rewinding, and tick-0 shots never send `demo_gototick 0`.

- **In-game Replay timeline starts at tick 1.** *Before:* the in-game Replay scrubber allowed dragging down to tick 0. *After:* the Replay scrubber starts at tick 1 so dragging to the beginning of a match lands cleanly on the first seekable tick.
