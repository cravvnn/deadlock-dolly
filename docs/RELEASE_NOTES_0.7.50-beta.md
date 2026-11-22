### Deadlock Dolly v0.7.50-beta

**0.7.50-beta**

- **Takes are never cut short under their real name.** *Before:* a take was written straight to its final filename, so a game crash mid-take left a broken video that looked finished, a long .mp4 take was deleted when FFmpeg needed more than two minutes to finish it, and an FFmpeg that stopped responding could hang Cancel or Finish forever. *After:* a take records as name.partial.mp4 and gets its real name only when it finishes, Dolly waits as long as FFmpeg keeps making progress and keeps the file if it gives up, and Cancel or Finish ends a stuck FFmpeg within about 30 seconds.

- **Auto picks an encoder that works.** *Before:* Auto always chose the graphics card's hardware encoder, even when that encoder could not run, and long or high frame rate RenderLock camera exports always timed out. *After:* Auto test-encodes the hardware encoder once in the background and falls back to the Windows encoder if it fails, and camera exports only time out when frames stop arriving.

- **Settings and game files stay put.** *Before:* a preference such as Discord or DLSS changed while Dolly was saving a launch or ReShade change could be silently undone, a settings file briefly locked by antivirus or OneDrive at startup reset to defaults, and a second Dolly window's recovery could undo the game files of a session that was still launching. *After:* every change keeps the others, briefly locked files are retried, and recovery leaves a launching or running game alone.

- **Console and window fixes.** *Before:* Connect could start while the previous playback was still shutting down and mix its commands into the new connection, starting a recording un-maximized a maximized game window, and the game's assert dialog was missed when its buttons had shortcut letters. *After:* Connect waits for playback to finish, the game window keeps its size, and the assert dialog is handled.
