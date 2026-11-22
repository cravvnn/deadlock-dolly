### Deadlock Dolly v0.7.51-beta

**0.7.51-beta**

- **The camera keeps going through a base cinematic.** *Before:* when the game zoomed its own view during a late base fight, Dolly's native camera shut off and playback could not be resumed, which looked like a crash. *After:* Dolly keeps its camera running through those frames.

- **A blocked cvar unlocker is explained.** *Before:* when Windows security blocked or removed Dolly's cvar unlocker, the game closed at startup and Dolly only quoted "Unable to create interface Source2ServerConfig001". *After:* Dolly says that Windows blocked or removed the file, shows the Windows error, and names what to allow, for example Smart App Control.

- **Diagnostics include crash dumps after a restart.** *Before:* exporting diagnostics right after reopening Dolly left out the game's crash dumps until a replay was launched again. *After:* the export finds them from the saved game location.
