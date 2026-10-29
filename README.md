<div align="center">

<img src="assets/dolly.png" width="120" alt="Deadlock Dolly logo">

# Deadlock Dolly

**A camera-path editor for local Deadlock replays.**

[![Download](https://img.shields.io/badge/Download-Releases-2ea44f?logo=github&logoColor=white)](https://github.com/cravvnn/deadlock-dolly/releases)
[![Sponsor](https://img.shields.io/badge/Sponsor-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/cravvnn)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/deadlockediting)
[![Report an issue](https://img.shields.io/badge/Report%20an%20issue-6e7781?logo=github&logoColor=white)](https://github.com/cravvnn/deadlock-dolly/issues)

![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D4)
![License](https://img.shields.io/badge/license-MIT-2ea44f)
[![Latest release](https://img.shields.io/github/v/release/cravvnn/deadlock-dolly?include_prereleases&label=latest%20release&color=orange)](https://github.com/cravvnn/deadlock-dolly/releases/latest)

![Deadlock Dolly demo](assets/dolly-demo.gif)

</div>

Capture views from a local replay, shape a camera path and play it back with
animated framing and effects. Edit through the desktop or directly in the game.

**Current source: 0.6.83-alpha · Windows x64 · DirectX 11.**
Native source changes need a matching rebuilt helper. The portable app includes
Python and Tcl/Tk; no separate installation is needed.

> **Local replays only — use at your own risk.** Dolly injects a native DLL
> into Deadlock. Keep `-insecure` in Deadlock's launch options while using Dolly.
> Close your Dolly editing session before launching Deadlock normally.

[Quick start](#quick-start) · [Features](#features) · [Guides](#guides) ·
[Compatibility](#compatibility) · [Support](#support) · [Contributors](#contributors)

## Quick start

1. Download the **Windows x64 ZIP** from [Releases](https://github.com/cravvnn/deadlock-dolly/releases) and extract it completely. Keep `_internal` beside `Dolly.exe`.
2. Open Steam, close any running Deadlock and keep `-insecure` in the game's launch options.
3. Run **Dolly.exe**, select the game executable and a local `.dem` replay, then click **Open replay in Dolly**.
4. Frame a view and press **Ctrl+Alt+K** to capture a camera. Advance the replay before framing and capturing the next view, then use **F8** to open the in-game editor and play your path.

Start the editing game through Dolly. It launches its own process with
`-dev -insecure -console` and refuses an already running game.

| Default shortcut | Action |
| --- | --- |
| **Ctrl+Alt+K** | Capture a camera |
| **F8** | Show or hide Dolly's in-game panels |
| **F9** | Switch to Deadlock's replay controls |
| **F5** | Play the camera shot |
| **Backspace** | Stop and restore |

Bindings can be changed under **Controls**; existing saved bindings may differ.
See the
[in-game editor guide](docs/UI_REWORK.md) for the full workflow.

## Features

- **Camera paths and curves.** Shared desktop/in-game editing for position,
  rotation and aspect ratio, position tangent handles, Undo/Redo and live path guides.
- **Follow and Bone Picker.** Follow a replay participant with the game's camera,
  adjust framing, or attach a camera to a selected bone. Use the per-camera
  vertical-aim lock and camera-relative movement in attached preview.
  See the [Bone Picker guide](docs/BONE_PICKER.md).
- **Framing and depth of field.** Animate aspect ratio and supported DOF controls
  alongside the camera path.
- **Video and audio.** Real-time or fixed-step exports, 30-600 FPS output settings,
  hardware/software H.264 or HEVC, lossless FFV1, and optional game or
  reconstructed audio in both modes. Reconstructed audio installs its verified
  extractor automatically on first use.
- **Depth and layers.** Paired depth masters, optional float EXRs and per-frame
  camera metadata, plus World, Players and Effects takes with documented alpha limits.
- **High-resolution stills.** Plate, players-only matte and cut-out hero presets
  up to 8K, with optional depth within the documented readback limits.
- **ReShade integration.** Optional color effects and verified scene depth for
  compatible depth-based effects, with a bundled crosire/prod80 shader library.
- **Discord Rich Presence.** Show editing and export activity, a session timer
  and links to Dolly. Turn it off in Settings; replay names and file paths stay private.
  See the [Discord presence guide](docs/DISCORD_PRESENCE.md).
- **Experimental replay repair.** Create a separate repair copy for a specific
  replay manifest problem; the original replay remains untouched. Test the copy
  in Deadlock because repairs are not guaranteed to work for every replay.

## Guides

Start with the [documentation index](docs/README.md) for all current guides.

| What you need | Guide |
| --- | --- |
| Setup, controls, replay preparation and recovery | [User guide](docs/USER_GUIDE.md) |
| Camera, Follow, Bone Picker, curves and saved layouts | [In-game editor](docs/UI_REWORK.md) |
| Recording, encoders, audio and ReShade | [Video and ReShade](docs/VIDEO_AND_RESHADE.md) |
| High-resolution screenshots and mattes | [Screenshots](docs/SCREENSHOTS.md) |
| Depth, isolated layers and capture limits | [Layer export](docs/LAYER_EXPORT.md) |
| Camera metadata for compositing | [Camera bundle](docs/CAMERA_BUNDLE.md) |
| Updates and interrupted-update recovery | [Updating and recovery](docs/UPDATING.md) |

## Compatibility

Native mode requires reviewed builds of Deadlock's `client.dll`, `engine2.dll`
and `tier0.dll`. Dolly checks the installed modules before loading its native
helper. Reviewed build definitions are published separately from releases, so a
client-only update can often be supported without downloading a new Dolly
build. See [published build definitions](docs/DEFINITIONS.md).

Use **Settings → Troubleshooting & recovery → Startup controls → Check game build**
to check your installation. **Console (legacy)** opens the manual Startup controls
when selected. It can remain usable across a client-only update when its separate
configuration and unlocker requirements still pass. It does not provide native
recording or layers, and is not guaranteed to survive every game update.
Camera compatibility does not by itself establish support for Depth or Players
output. See the [user guide](docs/USER_GUIDE.md) for build checks and recovery;
the [maintainer reference](docs/internal/GAME_UPDATES.md) explains compatibility review.

Dolly is in alpha. High FPS and high-resolution output depend on the game,
encoder and GPU; the available settings do not guarantee every workload.
Fixed-step Color exports can carry game or reconstructed audio through the
engine's frame-locked WAV recorder; extra layer takes stay silent. Depth, matte
and replay-seeking limits are covered in the guides above.

## Application updates

Packaged Windows builds check the published Latest release in the background
when automatic checking is enabled. The notice shows **What's changed**,
**Update now** and **Later**; manual checks are under **Settings → Updates**.

Every update requires **Update now**. Nothing downloads or installs before that
click, and **Later** leaves the update available. If Dolly asks you to save work
or close the game, do that and click **Update now** again. Settings, shots and
external tool paths are preserved. See [updating and recovery](docs/UPDATING.md).

Dolly also refreshes the small
[published build definitions](docs/DEFINITIONS.md) file in the background when
automatic checking is enabled. That file only adds game builds that are already
reviewed, is cached beside `Dolly.exe`, and never installs or runs anything.

## Session files

Dolly temporarily mounts its plugins for each editing session, restores the
original `gameinfo.gi` after unlocker initialization and removes its generated
plugin folder when the game exits. If Dolly closes first, its cleanup helper
waits for that game process. Keep logs and recovery backups after an interrupted
session.

With Deadlock closed, use **File → Recover game configuration** to check an
interrupted session. Configuration conflicts and unrecognized files are left
intact. The [user guide](docs/USER_GUIDE.md) covers recovery and conflicting HUD mods.

## Development

Run the source editor with Python 3.10+ and Tcl/Tk:

```console
python -m dolly
```

The basic source editor needs no pip dependencies; native features require the
compiled Windows helper. Full regression checks and optional audio workflows
need their documented dependencies. See [Building](docs/BUILDING.md) for setup
and the pinned Windows toolchain, and [Contributing](CONTRIBUTING.md) for development guidance.

`SOURCE_FILES.txt` defines the clean source export. Logs, personal replays,
settings and build outputs stay out of Git.

## Support

Contact **@Cravvnn on Discord**, join the [Deadlock Editing Discord](https://discord.gg/deadlockediting),
or [report an issue](https://github.com/cravvnn/deadlock-dolly/issues).
Include the Dolly version, the action that failed and the ZIP from
**Export diagnostics**. If the app does not open, include
`logs/Dolly_startup.log` and `logs/Dolly.log` when available.

Review diagnostics before posting publicly; they can contain local paths and
replay names.

## Contributors

- [@ExoCalibra](https://github.com/ExoCalibra) — Bone and Weapon vertical-aim lock and camera-relative movement in attached preview ([PR #29](https://github.com/cravvnn/deadlock-dolly/pull/29)).
- [@SamuelCurrid](https://github.com/SamuelCurrid) — After Effects and USD camera exports, camera tracks and forward-seek undershoot recovery ([PR #25](https://github.com/cravvnn/deadlock-dolly/pull/25), [PR #28](https://github.com/cravvnn/deadlock-dolly/pull/28)).
- [@paralin](https://github.com/paralin) — Linux/Proton Steam discovery, Wine mount-point handling and interrupted plugin-cleanup recovery.
- [@tbhtho](https://github.com/tbhtho) — in-game UI rework and saved panel layouts.
- [@s34532](https://github.com/s34532) — custom particle effects, native confetti rain, game-only audio capture and reconstructed clip audio.
- [@Hesykhia](https://github.com/Hesykhia) — high-resolution screenshot export with players-only mattes; update-control feedback.

See [Credits](CREDITS.md) for the project's creator, each contributor's work,
HLAE and other references, libraries and development tools from Dolly's history.

## License

Dolly source uses the [MIT license](LICENSE.txt). Bundled components retain
their own notices under `third_party/` and `native/vendor/`. Artwork has
separate terms in [assets/README.md](assets/README.md).

## Releases

- [Downloads and published release notes](https://github.com/cravvnn/deadlock-dolly/releases)
- [0.6.83-alpha release notes](docs/RELEASE_NOTES_0.6.83-alpha.md)
- [0.6.81-alpha release notes](docs/RELEASE_NOTES_0.6.81-alpha.md)
- [0.6.80-alpha release notes](docs/RELEASE_NOTES_0.6.80-alpha.md)
- [Complete changelog](docs/CHANGELOG.md)
