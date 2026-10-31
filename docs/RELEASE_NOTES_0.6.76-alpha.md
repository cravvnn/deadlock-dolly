### Deadlock Dolly v0.6.76-alpha

**0.6.76-alpha**

- **Bone Picker opens from the desktop again.** *Before:* starting the first bone camera from the desktop failed with `start_attached_view() missing 1 required keyword-only argument: 'publish'`. *After:* the desktop camera controls and Cancel button supply the same editor publication callback as the in-game panel, so the Bone Picker opens and closes normally.

- **A refused launch names the process that is blocking it.** *Before:* Dolly only said "Deadlock is already running", even when the visible game was closed and a hidden or different-install process was the cause. *After:* the error reports the PID, executable path, Windows session and visible window, and says when that process is from a different installation than the one selected in Dolly.

- **Stuck effects and earlier fixes remain.** *Before:* changing replay frames could leave Violet's ink, decals and screen post-processing behind, mixed skin packs could be skipped, and the 6819 hotfix blocked normal editing. *After:* **Clear stuck effects**, automatic mixed skin-pack loading, preserved audio levels, the authored campath range and the 6819 camera/roster fixes all remain.
