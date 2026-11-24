### Deadlock Dolly v0.7.55-beta

**0.7.55-beta**

- **Slow exports no longer stop partway.** *Before:* a frame-accurate recording, especially at high frame rates with depth, could stop with "Frame-accurate export writer exceeded the capture wait budget" after a brief disk or encoder slowdown. *After:* the recording waits for the writer as long as it is still making progress, and only stops if it makes no progress for 10 seconds.

- **See how big a take is before you record.** *Before:* there was no way to tell that 600 FPS at 0.25x renders thousands of frames and can take a long time. *After:* the in-game Export drawer and the desktop Recording card show the frames and video length, and how long the take should take once a similar take has finished.

- **Recording progress.** *Before:* a long fixed-step take showed only a frame count and could look frozen. *After:* the Export drawer, the Recording card and the status line show frames done out of the total and the time left.
