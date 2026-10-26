# Running Dolly on Linux (Proton)

Current state: **experimental, community-tested**. Launching, editing, the native
paused camera and cleanup have been tested on Linux; recording and the updater
have not.

Dolly is a Windows program. It runs under Proton inside Deadlock's own prefix
because it reads the game's process, memory and console through Windows APIs. A
non-Steam shortcut, a separate prefix or a different Proton version creates a
different Wine server that cannot see the running game.

## Requirements

- Deadlock installed through Steam and configured to run with **Proton**, not
  the native Linux build.
- The Proton version Deadlock itself uses. Switching Dolly to another version
  rewrites the shared prefix; do not do it.
- Steam running, and Deadlock closed: Dolly starts the game.

## Quick start

The ZIP from a green hosted run of `feat/linux-proton` already carries
`run_dolly_proton.sh` and this guide beside `Dolly.exe`.

1. Extract the ZIP and open a terminal in the extracted `DeadlockDolly` folder.
2. Check detection first:

   ```sh
   bash run_dolly_proton.sh --dry-run
   ```

   Check the printed `Steam:`, `Game:`, `Proton:`, `Runtime:` and `Prefix:`
   lines.
3. Start Dolly:

   ```sh
   bash run_dolly_proton.sh
   ```

   Leave the terminal open. Dolly should appear, find Deadlock and launch it.

Environment overrides: `STEAM_ROOT`, `PROTON`, `PROTON_RUNTIME` and
`DOLLY_COMPAT_TOOL_DIRS`. Custom compatibility tools are found in each
library's `compatibilitytools.d` and in the system folders distributions use
(`/usr/share/steam/compatibilitytools.d`), matched against the tool Deadlock
was last started with: the prefix's recorded name, that name inside the tool's
manifest, its folder name, or the version string some custom builds record
instead — with Steam's own compatibility mapping as a further fallback. Flatpak
Steam is detected at `~/.var/app/com.valvesoftware.Steam/.local/share/Steam`.
If the tool cannot be identified the script stops and lists every candidate
with its layer name and version; name one with `PROTON=` rather than starting
Dolly under a different Proton.

## Test checklist

| # | Check |
| --- | --- |
| 1 | `--dry-run` detects the right library, Proton and prefix |
| 2 | Dolly opens with no error dialog |
| 3 | Dolly finds Deadlock |
| 4 | Launching from Dolly starts the game and loads a replay |
| 5 | Pause, free camera and the in-game Dolly controls respond |
| 6 | After quitting, `gameinfo.gi` is restored and no `citadel_dolly_*` folder is left in `game/citadel` |
| 7 | A short **Lossless FFV1 (.mkv)** recording plays back |
| 8 | A still capture writes its PNG files |

## Not supported yet

- **Preload verification.** Automatic startup normally reads the game's loaded
  modules and memory to verify the reviewed preload before loading the replay.
  Wine/Proton does not answer those calls, so Dolly records that the check is
  unavailable, skips the wait and continues: the replay loads and the paused
  editor opens on their own, without the preload verification (and without the
  dialog Windows shows when a readable preload is slow).
- **Health-panel handoff verification.** The same cross-process check also guards
  the replay HUD handoff. Under Wine it cannot run, so Dolly restores the saved
  HUD values directly instead of holding them pending; expect an INFO line, and a
  possible brief HUD flicker when the panel comes back.
- **Depth capture and capture layers.** The native depth readback fails on the
  first frame under Wine/Proton, so a depth master, EXR sequence or layer pass
  writes nothing (the colour take of a layered run may still land). Dolly now
  refuses a depth take up front with that reason instead of failing after the
  shot. Plain colour takes - **Lossless FFV1 (.mkv)** - work. Screenshots follow
  the same rule: under Proton a still saves `plate.png` only; depth and hero
  cutouts remain Windows-only for now.
- **Recording audio.** Both audio checkboxes are off by default and should stay
  off under Proton. Game audio runs the bundled Windows helper against the game
  process (`DollyGameAudio.exe`), and reconstructed audio downloads the
  Source2Viewer .NET CLI; neither has been tried under Wine. A video-only
  **Lossless FFV1 (.mkv)** take is unaffected. The separate "sound hooks are
  unavailable" line is about the replay pause cue and is harmless.
- **Hardware encoders.** NVENC, AMF and Quick Sync need driver DLLs that do not
  exist in a Proton prefix, and Windows Media Foundation has no H.264 encoder
  under Wine. Use **Lossless FFV1 (.mkv)**; software H.264 is planned.
- **Self-update.** Replacing the running executable under Wine is untested.
  Choose Later when Dolly offers an update.
- **Steam Deck Gaming Mode.** Desktop Mode uses the same steps but is untested.

## Desktop text

On real Windows the desktop asks for `Segoe UI` (mono: `Consolas`). Under Wine
those names are **not trusted**: the prefix lists them for host fonts of its own
choosing — "Segoe UI" resolves to a serif on some systems — so Dolly drops the
Windows-only names and takes the first family the system really has (`Noto Sans`,
`DejaVu Sans`, `Liberation Sans`, `Arial`; mono: `Noto Sans Mono`,
`DejaVu Sans Mono`, `Liberation Mono`). `Dolly.log` records the outcome:

```text
Desktop fonts resolved to family=Noto Sans mono=Noto Sans Mono (Wine prefix; Windows-only families skipped)
```

`DOLLY_FONT_FAMILY` and `DOLLY_FONT_MONO` override that choice (and are the way
to keep a Windows-only name if the prefix really has one):

```sh
DOLLY_FONT_FAMILY="Noto Sans" DOLLY_FONT_MONO="Noto Sans Mono" \
  bash run_dolly_proton.sh
```

Installing Microsoft's fonts into the prefix, and Wine's smoothing settings,
are described in the private tester notes kept with this work.

## Troubleshooting

- Launch fails: re-run with `PROTON_LOG=1` and read `~/steam-1422450.log`.
- Dolly reports that Steam is not running: start the desktop Steam client, then
  re-run.
- The replay never appears in the game: try a **fresh** replay first. A demo
  recorded on an older game build can be rejected by Deadlock itself
  (`Host_EndGame: LinkClasses: unable to match one or more serializers` →
  back to the hideout); that is the game, not Dolly. Under Proton Dolly offers
  the replay first as `replays/<file>.dem`, which is inside the engine's own
  search path, then as the full path, and records each attempt with the
  console's answer in the startup evidence. Export diagnostics (or send
  `<Dolly folder>/logs`) if neither form starts.
- `The sound hooks are unavailable; the replay pause cue will still play` and a
  slightly heavier mouse in the in-game panel are expected under Wine; report
  them if they become unworkable.
- A shot that hangs: press **Stop / restore**. It cancels the running preparation
  or playback instead of asking you to wait for it, and diagnostics export again
  once it unwinds. If the game itself stopped rendering, `Dolly.log` carries one
  dated line — `No new game frames for N s while the paused camera is held (frame,
  tick, paused, effect phase, last frame interval)` — send that line with the log.
  A game that has really frozen must be closed and reopened.
- The in-game panel opens with **F8** (automatic startup goes straight to the
  free camera and no longer flashes it).
- A preserved `citadel_dolly_*` folder in `game/` after a force-kill is
  deliberate: Dolly never deletes a session folder that still holds replay or
  settings data. It does not block a new launch; delete it by hand if you want
  the space back.
- `sh: lsof: not found` in the terminal is not Dolly (it never runs `lsof`); it
  comes from the Steam Linux Runtime (the frame-generation layer also probes it).
  Harmless: ignore it.
- **Keep the replay speed at 1x.** At 4x, some replays make the game print
  `Ignoring CSVCMsg_UserCommands_t` warnings thousands of times while it parses;
  the game keeps playing but its console stops answering commands, so Dolly can
  no longer control the replay. Automatic startup therefore skips its 4x pre-show
  accelerator under Proton and plays the intro at 1x.
- **`The game stopped answering its console`.** The game keeps running — it may
  still be playing and rendering — but its console stopped reading commands. Under
  Proton this happened while a replay played at 4x and the engine flooded the
  console with `CSVCMsg_UserCommands` warnings; automatic startup now skips its
  4x intro accelerator, and playback should stay at 1x. Dolly releases the camera
  and lets Stop/close finish. Press **Connect** once to see whether a fresh console
  connection recovers; otherwise close Deadlock, relaunch it through Dolly, and
  send `Dolly.log`.
- A leftover plugin folder blocks a launch: start Dolly again, it recovers
  before launching. Do not delete `gameinfo.gi` or the plugin folder by hand.
- Logs and settings:
  `<Steam>/steamapps/compatdata/1422450/pfx/drive_c/users/steamuser/AppData/Roaming/DeadlockDolly/`
  and `<Dolly folder>/logs`.
