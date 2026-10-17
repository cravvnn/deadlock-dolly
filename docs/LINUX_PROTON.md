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

1. Extract the Windows ZIP. Put `run_dolly_proton.sh` next to `Dolly.exe` if it
   is not already there.
2. From that folder:

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
was last started with. Flatpak Steam is detected at
`~/.var/app/com.valvesoftware.Steam/.local/share/Steam`. If the tool cannot be
identified the script stops and lists what it found; name one with `PROTON=`
rather than starting Dolly under a different Proton.

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

- **Hardware encoders.** NVENC, AMF and Quick Sync need driver DLLs that do not
  exist in a Proton prefix, and Windows Media Foundation has no H.264 encoder
  under Wine. Use **Lossless FFV1 (.mkv)**; software H.264 is planned.
- **Self-update.** Replacing the running executable under Wine is untested.
  Choose Later when Dolly offers an update.
- **Steam Deck Gaming Mode.** Desktop Mode uses the same steps but is untested.

## Troubleshooting

- Launch fails: re-run with `PROTON_LOG=1` and read `~/steam-1422450.log`.
- Dolly reports that Steam is not running: start the desktop Steam client, then
  re-run.
- A leftover plugin folder blocks a launch: start Dolly again, it recovers
  before launching. Do not delete `gameinfo.gi` or the plugin folder by hand.
- Logs and settings:
  `<Steam>/steamapps/compatdata/1422450/pfx/drive_c/users/steamuser/AppData/Roaming/DeadlockDolly/`
  and `<Dolly folder>/logs`.
