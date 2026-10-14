# Deadlock Dolly 0.6.47-alpha

Compatibility update for Deadlock build 6782 (October 14, 2026).

## Changes

- **Current game support.** *Before:* the latest game update could prevent Dolly from preparing the selected replay and camera editor. *After:* updated compatibility checks support replay startup, camera paths, Follow, Player POV, HUD controls, and bone selection on build 6782.
- **Export and effect compatibility.** *Before:* changed game modules could block capture and audio features. *After:* Color, Players, Depth, float-depth EXRs, game audio, sound-event collection, weather, and camera effects use the reviewed build 6782 compatibility data.

The previous release's conversion controls, custom scratch folders, remembered export destinations, and capture reliability fixes remain included. Saved-shot queuing remains deferred.

## Validation

All 29 enabled native checks and the 2,043-test Python suite pass. The hardware encoder stress check remains excluded. Bounded local replay checks verified camera preview, Follow/POV ownership, HUD readbacks, bones, weather, 30-frame Color and Players captures, Players transparency and temporary-file cleanup, and a 30-frame paired Color/Depth capture with 30 float EXRs and matching sample/timing metadata.

Audio checks verified a one-second game-audio WAV, nonzero audio in the exported video, and 17 recorded sound starts without dropped events or write errors. Completed sessions restored all 43 saved configurations, released pending runtime settings, removed their deployments, and exited normally. The previously observed Steam networking shutdown assertion remains present; no new game crash or assertion appeared.

## Known limitations

Some replay characters and nearby scenery still appear dark or black in the Color reference and Players output. This release does not establish the cause or certify textured appearance in that scene. Repeated seeking can also leave effects stacked on characters; that separate investigation is still pending.

Color and Players are separate capture passes. The bounded checks do not certify every hero or replay.

## Updating

Extract the Windows x64 ZIP into a fresh folder and run Dolly.exe, or use Dolly's updater once this release is published. Python, Tcl/Tk, and the encoder are bundled.
