### Deadlock Dolly v0.7.39-beta

**0.7.39-beta**

- **Your game config comes back even after a crash.** *Before:* if Dolly or the game crashed while starting, Dolly's temporary `gameinfo.gi` could stay in place until your next launch through Dolly. *After:* Dolly puts your original back as soon as it opens again (never while Deadlock is still running).

- **No more stuck launches after a mod manager or Steam check.** *Before:* if something else changed `gameinfo.gi` during a Dolly session, every later launch was blocked. *After:* Dolly asks whether to put your original back or keep the current file.

- **More mods load.** *Before:* sound and animation mods, Deadworks mods and mods in custom folders were left out, and a broken disabled mod in Grimoire or another Deadlock Mod Manager profile could drop all your mods. *After:* they load like skins do. Low-settings and effect packs that change how the game looks are still left out.

- **Graphics profiles hold more.** *Before:* a Dolly graphics profile only kept quality settings. *After:* it also keeps DLSS/upscaler, resolution, refresh rate and window mode, and if you change graphics in-game during a Dolly session, Dolly offers to save them when the game closes. Your normal Deadlock settings are never changed.
