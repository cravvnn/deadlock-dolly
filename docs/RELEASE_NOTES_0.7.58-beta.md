### Deadlock Dolly v0.7.58-beta

**0.7.58-beta**

- **New Track camera curve.** *Before:* Smooth and Spline could swing the camera wide or make it loop when two cameras were close together but far apart in time. *After:* pick **Track** under **Interpolation** for a smooth rail through your cameras that never loops or goes backwards; camera timing only changes the speed along it. Every camera is still reached at its time. Smooth stays the default, and existing shots play exactly as before.

- **Fixed-step exports with game audio work again.** *Before:* exporting with game audio on the latest Deadlock update stopped with "This Dolly native build does not support fixed-step audio". *After:* fixed-step exports record the game audio in sync again.

- **Escape opens Deadlock's menu from the camera.** *Before:* once the editor was hidden, Escape did nothing, so leaving the game meant switching to the game UI first. *After:* press **Escape** while flying the camera to open Deadlock's own menu; press **Escape** again or click **Resume** to go straight back to the same camera.

- **The replay library remembers its sort.** *Before:* the replay list went back to sorting by name every time Dolly opened. *After:* it starts with the newest replays first and keeps whichever sort you choose.
