# UI cleanup and control inventory

Implemented 2026-09-13 on top of e98c829. The approved desktop navigation and
in-game Camera/Lens/Export layout now ship in Dolly, including the three layer
switches. The interactive wireframe remains a design reference with simulated
files and views; the production UI uses the existing Tk and ImGui renderers.
No export option, camera backend, effect track or recovery action was removed.

## Default desktop and Full editor switch

Default desktop: Library, Export, Settings. Library combines replay selection,
launch, and shot file operations. A prominent Full editor switch adds Cameras and
Effects, including the shot timeline. Changing this switch must only change the
layout: preserve the current shot, edits, settings and game connection. Remember
the layout preference separately from the project. With Console selected, make
Full editor easy to open because its fallback camera controls remain necessary.
Do not automatically select the hidden Cameras page after startup in simple mode.
Keep cancellation during startup, errors, and Stop / restore accessible in both modes.

| Current controls | Current home | Disposition |
| --- | --- | --- |
| Game executable, selected demo, browse, Play replay, startup progress/cancel | Library launch setup; Settings for saved paths | Keep; primary action becomes Open replay |
| Replay folder, search, refresh, selection, browse another file | Library | Merge Home and Replays |
| New, open, save, save as, rename shot, exit | Library/file menu | Keep; avoid treating replay files as shot projects |
| Camera driver Native/Console | Library launch setup and troubleshooting | Keep explicit, chosen before launch |
| Display launch options | Settings / Game | Keep; security and connection arguments remain managed |
| Console link, launch, connect, initialize unlocker, load replay, check camera/build, disconnect, configuration recovery | Settings / Troubleshooting | Keep out of the normal workflow; retain startup ordering |
| Log, diagnostics export | Status/error context and Troubleshooting | Consolidate repeated shortcuts |
| All 26 editor bindings, key/mouse capture, Ctrl/Alt/Shift, presets, save/reset, movement speed and sensitivity; ReShade binding | Settings / Controls | Keep all action bindings; F7 remains reserved |
| Capture hotkey enable and legacy binding editor | Full editor / Capture; Settings / Controls | Keep session-only enable; Native must still disable competing external polling |
| Capture, start path here, replace, delete, saved view selection | Full editor / Cameras and in-game Camera | Keep |
| Replay timing/Timed shot, spacing | Full editor / Capture timing | Keep; spacing only relevant to Timed shot |
| Arrive time, aspect, bank, normal aspect preset/reset, update/preview | Full editor / Camera inspector | Keep |
| X/Y/Z, pitch/yaw, start tick, ticks/sec, position curve, rotation mode, use current tick, add entered camera | Full editor / Coordinates and timing | Keep |
| Framing interpolation and editable curve; top-down path | Full editor / Cameras | Keep actual editable curve |
| Paused-camera start/stop/capture, previous/next, move/turn rates, keyboard flight enable, hold-to-move/look, focus controls | Full editor / Paused camera | Keep for desktop/Console fallback |
| Timeline, exact time, Play shot, Pause, Stop/restore, preview frame, seek replay | Full editor timeline; relevant in-game transport | Keep Stop/restore reachable in simple mode too |
| Playback speed, Hide HUD, Frozen preview | Full editor playback options | Keep; distinguish playback speed from export speed |
| Updates/sec, Console smoothing | Advanced playback options | Keep; hide Console-only smoothing under Native; update rate is monitoring under Native |
| Renderer relief (currently duplicated) | One shared advanced playback setting | Consolidate UI, retain behavior and default |
| Range/Citadel DOF presets, track create/update/remove, variable, interpolation, restore, time/value keys, fixed values | Full editor / Effects | Keep every track and fixed value, even when hidden |
| In-game DOF enable, near/far blurry/crisp, ground tilt | In-game Lens | Move from long Editor page into Lens; preserve pause/availability gates |
| In-game view selection, replace, previous/next, path guides | In-game Camera | Keep full view picker |
| Replay Play/Pause, Play shot, seek +/-1 sec, speed, update rate | In-game Camera / Replay | Keep update rate visible in Free camera |
| Fly camera, Heroes/game UI, movement speed, clear ragdolls | In-game Camera / Free camera | Place Clear ragdolls as a standalone button below Free camera; preserve disabling during playback |
| Output file/browse, FPS 30/60/120/300/600, bitrate 10/20/40, all 12 desktop encoder choices (11 native codec IDs), fixed-step, export speed | Export (both modes), in-game Export where supported | Keep; fold encoder/bitrate into quality disclosure |
| Depth master, optional EXR, World, Players, Effects | Export / Output passes on both surfaces | Keep independently selectable; color remains included |
| Start, finish, discard recording; preview shot | Export | Keep all desktop actions; in-game retains existing record/finish controls |
| FFmpeg executable/browse | Desktop Export / Runtime | Keep advanced; bundle default |
| ReShade DLL/browse, enable, disable session, forget runtime, menu shortcut | Settings / ReShade; in-game Lens menu | Move install/configuration out of Export; preserve session behavior |

## Consolidation

Home and Replays are merged into Library. Keybinds are in Settings / Controls.
The repeated renderer-relief checkbox is consolidated into playback options;
Console smoothing hides under Native. The Console backend, raw track and fixed
value editors, recovery actions, and precision EXR option remain available.
Export recording actions stay visible while its options scroll. Settings version
4 remembers Full editor separately from shots and preserves versions 1-3 values.

## Implemented in-game layer switches

World, Players and Effects now mirror the desktop export variables. Selecting
any layer calls the same desktop fixed-step default helper. Deselecting one does
not clear another or disable fixed-step. Export orchestration is unchanged.
Appended action IDs 54-56 and video_flags bits 3-5 preserve existing IDs, defaults,
config size and ABI. A matching rebuilt Python app and DLL are needed.

## Validation and spacing

Python regression suite: 1063 tests passed, 15 skipped. Native suite: 15 passed.

The Windows bundle self-test opens the actual GUI at its supported minimum size,
checks reachable export and ReShade controls, and switches between simple and
full editor modes. Regression tests cover settings migration, preservation of
unsaved shots and layer choices when switching layout, startup navigation, and
independent layer synchronization. Native tests include synthetic DX11 rendering
of all three tabs. No live replay validation was performed for this UI change.

Selected replay aligns its headings and actions with Replay library without
extra nested detail boxes. Fields and following button groups have separate
spacing. The in-game panel remains floating, draggable and resizable; the
wireframe viewport is only background context. Clear ragdolls sits below Free
camera, Updates / s is visible inside Free camera, and Stop / restore has a
separate footer gap on every tab, including Lens.
