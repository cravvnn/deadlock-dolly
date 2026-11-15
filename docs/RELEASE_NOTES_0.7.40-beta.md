### Deadlock Dolly v0.7.40-beta

**0.7.40-beta**

- **The save-graphics prompt only lists what you changed.** *Before:* after changing a graphics setting in-game, Dolly's prompt also listed settings you never touched (render scale, DLSS preset, FSR sharpness), and saving stored your resolution and window mode in the profile too. *After:* it lists and saves only the settings you really changed.

- **Game files are put back even if Dolly crashes.** *Before:* if Dolly crashed or was force-closed while Deadlock was open, its temporary game files stayed until you opened Dolly again. *After:* a small background helper puts everything back as soon as that game closes.

- **Unpacked low-settings mods stay out.** *Before:* a post-processing or effects mod installed as loose files could still load in a Dolly session. *After:* it is left out, the same as packed ones. Skin, sound and animation mods still load.
