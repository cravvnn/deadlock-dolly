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
  Wine/Proton does not answer those calls, so Dolly reports that the check is
  unavailable and offers its existing manual load instead: keep the game open and
  load the selected replay from Startup controls. Nothing is broken, and the
  replay loads without the preload wait.
- **Health-panel handoff verification.** The same cross-process check also guards
  the replay HUD handoff. Under Wine it cannot run, so Dolly restores the saved
  HUD values directly instead of holding them pending; expect an INFO line, and a
  possible brief HUD flicker when the panel comes back.
- **Hardware encoders.** NVENC, AMF and Quick Sync need driver DLLs that do not
  exist in a Proton prefix, and Windows Media Foundation has no H.264 encoder
  under Wine. Use **Lossless FFV1 (.mkv)**; software H.264 is planned.
- **Self-update.** Replacing the running executable under Wine is untested.
  Choose Later when Dolly offers an update.
- **Steam Deck Gaming Mode.** Desktop Mode uses the same steps but is untested.

## Desktop text

The desktop picks the first family the system actually has (`Segoe UI`, then
`Noto Sans`, `DejaVu Sans`, `Liberation Sans`, `Arial`; `Consolas`, then
`Noto Sans Mono`, `DejaVu Sans Mono`, `Liberation Mono`), so a prefix without
Microsoft's fonts still gets a real sans and mono. `DOLLY_FONT_FAMILY` and
`DOLLY_FONT_MONO` override that choice:

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
- A preserved `citadel_dolly_*` folder in `game/` after a force-kill is
  deliberate: Dolly never deletes a session folder that still holds replay or
  settings data. It does not block a new launch; delete it by hand if you want
  the space back.
- `sh: lsof: not found` in the terminal is not Dolly (it never runs `lsof`); it
  comes from another host component, for example a frame-generation layer.
- A leftover plugin folder blocks a launch: start Dolly again, it recovers
  before launching. Do not delete `gameinfo.gi` or the plugin folder by hand.
- Logs and settings:
  `<Steam>/steamapps/compatdata/1422450/pfx/drive_c/users/steamuser/AppData/Roaming/DeadlockDolly/`
  and `<Dolly folder>/logs`.
