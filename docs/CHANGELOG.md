# Changes

## 0.6.37-alpha

- Support Deadlock build 6774 with reviewed client profiles and an updated exact-build unlocker.
- Recover confirmed replay seeks that stop up to four ticks late, retaining one correction, cancellation, the existing deadline and exact-target verification.

## 0.6.36 alpha - Fresh package version for the October 9 update

**0.6.36-alpha**

- **Distinct update version.** *Before:* the 6769 compatibility package reused the already-published 0.6.35 version. *After:* the package and updater identify this build as 0.6.36-alpha. It includes the same reviewed 6769 compatibility, hero mappings, Hyperline handling and FFmpeg fixes.

Full details: [0.6.36 release notes](RELEASE_NOTES_0.6.36-alpha.md).

## 0.6.35 alpha - October 9 update compatibility

**0.6.35-alpha**

- **October 9 update support (6769).** *Before:* Dolly refused the updated game modules. *After:* reviewed compatibility profiles and a rebuilt unlocker allow replay startup and camera editing on 6769.
- **Current hero names and portraits.** *Before:* Baba, Solomon and Rat King were missing from Dolly's hero mappings. *After:* their names and portrait paths match the installed game assets.
- **Hyperline mounts survive editor startup.** *Before:* Dolly's temporary editing configuration could omit Hyperline content mounts. *After:* those mounts are carried into the editing session, with verified configuration replacement and recovery.
- **Hidden FFmpeg output steps.** *Before:* still assembly, Players/layer encoding, audio muxing and depth previews could open console windows or inherit the editor's DLL search path. *After:* these FFmpeg steps run hidden in a clean environment.

Full details: [0.6.35 release notes](RELEASE_NOTES_0.6.35-alpha.md).

## 0.6.34 alpha - Native Depth of Field without the game's shader compiler

**0.6.34-alpha**

- **Native Depth of Field works on installs that showed the black checkerboard.** *Before:* applying Native DOF forced the game to recompile its DOF shader, which fails on many installs, so the engine drew its magenta/black error material over the view. *After:* Dolly skips that forced recompile and the engine renders Native DOF from the shader it already ships, so the effect works and the checkerboard is gone.

Full details: [0.6.34 release notes](RELEASE_NOTES_0.6.34-alpha.md).

## 0.6.33 alpha - October 8 hotfix (6766)

**0.6.33-alpha**

- **Works with the October 8 Deadlock hotfix (6766).** *Before:* the updated client changed `client.dll` and `server.dll`, and Dolly refused the build so the replay editor would not start. *After:* native camera, replay startup, Game Follow and Players/Depth work on 6766, and the bundled cvar unlocker is rebuilt for the new server.
- **A Players layer export no longer leaves a stuck command window.** *Before:* starting a Players layer export could pop up a command window that stayed open, and the encoder could fail if it picked up the wrong libraries. *After:* the bundled encoder runs hidden in its own clean environment and reports a clear error instead of leaving a window open.

Full details: [0.6.33 release notes](RELEASE_NOTES_0.6.33-alpha.md).

## 0.6.32 alpha - The health/ability panel restores after editing

**0.6.32-alpha**

- **The health/ability panel restores after editing.** *Before:* after Stop, Dolly could report the health panel as "pending" and leave the ability panel hidden until you re-selected a hero. *After:* Dolly reads the player-pawn predicate straight from the reviewed pawn vtable, so the panel restores on Stop.

Full details: [0.6.32 release notes](RELEASE_NOTES_0.6.32-alpha.md).

## 0.6.31 alpha - October 8 hotfix (6765) and follow-camera export without the replay HUD

**0.6.31-alpha**

- **Works with the October 8 Deadlock hotfix (6765).** *Before:* the updated client changed `client.dll` and `server.dll`, and Dolly refused the build so the replay editor would not start. *After:* native camera, replay startup, Game Follow and Players/Depth work on 6765, and the bundled cvar unlocker is rebuilt for the new server.
- **Export a follow camera without turning on the replay HUD.** *Before:* recording a Player POV (follow camera) required pressing F9 and selecting a hero, and starting the export stopped the Game Follow and brought the replay HUD back. *After:* pick a hero with Game Follow (the replay HUD stays hidden) and record or export straight away — the follow keeps running while you record.
- **Busier fights no longer break a Players capture.** *Before:* a Players layer capture failed with "failed player layer exceeded 64 draws per image; incomplete output rejected" when a frame contained more than 64 player draws. *After:* the per-image budget is doubled to 128 draws, so crowded moments capture completely.

Full details: [0.6.31 release notes](RELEASE_NOTES_0.6.31-alpha.md).

## 0.6.30 alpha - High-resolution stills and a Players fix for the October 7 builds

**0.6.30-alpha**

- **High-resolution stills (plate + players matte + depth).** New: capture a high-resolution still of the paused view from the in-game panel or the desktop Export tab. It records the color plate, a players-only matte and a depth pass; the capture resolution ceiling is raised to 8K.
- **Players capture works on the October 7 builds.** *Before:* the reviewed scene-system vtable was pinned to the wrong address on 6757/6759 and the 6762/6763 hotfixes, so a Players capture could not resolve the scene. *After:* the CSceneSystem vtable is corrected and Players capture resolves.
- **Clearer message when a camera is captured before the shot start.** *Before:* capturing a view with the replay positioned before the shot's start failed with "Camera keyframes timestamps must be nonnegative". *After:* Dolly explains the replay is before the shot's start and how to fix it.
- **The held health panel is surfaced.** *Before:* a health/ability panel that could not be restored after Stop produced no prompt. *After:* Dolly shows the "Restore the game health panel" guidance for every camera backend.

Full details: [0.6.30 release notes](RELEASE_NOTES_0.6.30-alpha.md).

## 0.6.29 alpha - More reliable automatic updates

**0.6.29-alpha**

- **Automatic updates are more reliable.** *Before:* on some installs the update failed with "[WinError 5] Access is denied" while replacing a file, so the update never finished (your previous files were kept). *After:* Dolly clears a read-only attribute left by ZIP extraction, retries the file move briefly, and if Windows still blocks it, explains what to do (move Dolly out of a protected folder such as Downloads, or allow it in Controlled Folder Access) instead of just failing.

Full details: [0.6.29 release notes](RELEASE_NOTES_0.6.29-alpha.md).

## 0.6.28 alpha - Support for the October 7 Deadlock hotfix (6763)

**0.6.28-alpha**

- **Works with the October 7 Deadlock hotfix (6763).** *Before:* the updated client changed `client.dll`, and Dolly refused the build so the replay editor would not start. *After:* native camera, replay startup, Game Follow, the health panel and Players/Depth work on 6763, and the bundled cvar unlocker is rebuilt for the new server.

Full details: [0.6.28 release notes](RELEASE_NOTES_0.6.28-alpha.md).

## 0.6.27 alpha - Support for the October 7 Deadlock update (6762)

**0.6.27-alpha**

- **Works with the October 7 Deadlock update (6762).** *Before:* the updated client changed `client.dll`, and Dolly refused the build so the replay editor would not start. *After:* native camera, replay startup, Game Follow, the health panel and Players/Depth work on 6762, and the bundled cvar unlocker is rebuilt for the new server.
- **Citadel Depth of Field keeps working after a Native DOF problem.** *Before:* once Native DOF failed on a game install, every depth-of-field control (including Citadel DOF) was blocked and errored. *After:* only the Native DOF pass is held back, and Citadel DOF plus the rest of the editor keep working.
- **Dolly no longer freezes on a repeated error.** *Before:* a repeated failure (for example moving a DOF slider after a Native DOF error) could stack error dialogs and leave Dolly needing a hard close. *After:* Dolly shows one clear message and stays responsive.
- **The Native DOF checkerboard guard is more reliable.** *Before:* the guard depended on the exact engine wording for a failed shader compiler, so some installs could still show the magenta/black checkerboard. *After:* the guard recognizes the failure regardless of wording and leaves Native DOF off with an explanation.

Full details: [0.6.27 release notes](RELEASE_NOTES_0.6.27-alpha.md).

## 0.6.26 alpha - Citadel DOF stays usable and error popups stop stacking

**0.6.26-alpha**

- **Citadel Depth of Field keeps working after a Native DOF problem.** *Before:* once Native DOF failed on a game install, every depth-of-field control (including Citadel DOF) was blocked and errored. *After:* only the Native DOF pass is held back, and Citadel DOF plus the rest of the editor keep working.
- **Dolly no longer freezes on a repeated error.** *Before:* a repeated failure (for example moving a DOF slider after a Native DOF error) could stack error dialogs and leave Dolly needing a hard close. *After:* Dolly shows one clear message and stays responsive.
- **The Native DOF checkerboard guard is more reliable.** *Before:* the guard depended on the exact engine wording for a failed shader compiler, so some installs could still show the magenta/black checkerboard. *After:* the guard recognizes the failure regardless of wording and leaves Native DOF off with an explanation.

Full details: [0.6.26 release notes](RELEASE_NOTES_0.6.26-alpha.md).

## 0.6.25 alpha - October 6 Deadlock updates and health-panel hardening

**0.6.25-alpha**

- **Works with the October 6 Deadlock updates (6757 and 6759).** *Before:* both October 6 game patches changed the client and server, and Dolly refused the updated client. *After:* native camera, replay startup, Game Follow, the health panel and Players/Depth work on 6757 and 6759, and the console unlocker is rebuilt for each server. Reconstructed clip audio stays disabled until its soundsystem review lands.
- **Deleting the first camera now starts the shot there.** *Before:* removing the leading camera left the shot anchored to the old start, so a front-end mistake could not be corrected. *After:* the shot rebases onto the new first camera, effects shift with it, and Undo restores the original start.
- **Export and F9 no longer stall on the health panel.** *Before:* a stale player-predicate value made the health-panel handoff retry for five seconds, so F9 was slow and exports were blocked with a pending-restoration error. *After:* the predicate is corrected, the handoff fails fast instead of retrying, and a cosmetic panel can no longer block export.

Full details: [0.6.25 release notes](RELEASE_NOTES_0.6.25-alpha.md).

## 0.6.24 alpha - Playback no longer dead-ends on health-panel restoration

**0.6.24-alpha**

- **Playback continues when the game's health panel cannot be verified.** *Before:* on a game build whose spectator player context could not be verified, the hidden own-health panel stayed pending and Play refused with "Previous settings still need restoration" with no way past it. *After:* playback proceeds with the panel left safely hidden, and the panel is restored on a later verified hero view.

Full details: [0.6.24 release notes](RELEASE_NOTES_0.6.24-alpha.md).

## 0.6.23 alpha - Native DOF guard against the checkerboard crash

**0.6.23-alpha**

- **Native DOF no longer shows the checkerboard or crashes when the game's vfx compiler is missing.** *Before:* enabling Native Depth of Field (`r_dof_override`) on a game install whose vfx shader compiler could not load made the engine render its magenta/black error material and could crash Deadlock. *After:* Dolly detects the failure before applying the pass, leaves Native DOF off for the session, and tells you to verify the game files; Native DOF still works normally on healthy installs.

Full details: [0.6.23 release notes](RELEASE_NOTES_0.6.23-alpha.md).

## 0.6.22 alpha - Vendor-aware encoder, camera feel and clean recordings

**0.6.22-alpha**

- **Auto encoder picks your GPU's encoder.** *Before:* Auto always chose NVIDIA NVENC, so AMD and Intel users' recordings failed even though their cards support hardware encoding. *After:* Dolly detects the installed GPU and picks NVIDIA NVENC, AMD AMF or Intel Quick Sync automatically, falls back safely on unknown hardware, and shows the chosen encoder in the recording status.
- **Camera feel chooser.** *Before:* the free-camera mouse speed was a small number box hidden in the keybinds tab, and high-DPI mice made the camera spin. *After:* Settings has a Camera feel card with a slider and Very slow / Slow / Normal / Fast / Very fast presets, kept in sync with the keybinds field.
- **No more debug overlays over recordings.** *Before:* every Dolly session showed Deadlock's faint client-status logo mark and the match ID / server CPU debug text, even with the HUD hidden. *After:* both are hidden for the whole Dolly session, in the editor and while recording, and the rest of the replay HUD still works normally.

Full details: [0.6.22 release notes](RELEASE_NOTES_0.6.22-alpha.md).

## 0.6.21 alpha - Support for the October 5 Deadlock build (6753)

**0.6.21-alpha**

- **Works with the new game build.** *Before:* Dolly could not attach to the October 5 Deadlock update. *After:* startup, the unlocker, replay loading, camera editing and Players/audio work on build 6753.
- **Rebuilt unlocker and updated module support.** *Before:* the console unlocker and the Players/audio module pins matched the previous server and engine builds. *After:* the unlocker is rebuilt for the new server, and the new scene, sound and renderer builds are recognized.

Full details: [0.6.21 release notes](RELEASE_NOTES_0.6.21-alpha.md).

## 0.6.20 alpha - Smoother desktop and in-game UI

**0.6.20-alpha**

- **Smoother desktop editor.** *Before:* dropdowns, sliders and scrolling could feel sticky even while idle. *After:* the interface stays quiet when nothing is happening and redraws only when something changes.
- **Smooth page scrolling.** *Before:* precision touchpads and high-resolution wheels dropped scroll steps. *After:* scrolling accumulates those small movements into consistent motion.
- **Zoom and pan the in-game Shot timeline.** *Before:* a timeline with many cameras was crowded and hard to work with. *After:* the mouse wheel zooms around the pointer, right-drag pans, and Alt+drag still retimes a camera tick.
- **No more panel flicker.** *Before:* moving the mouse quickly over the in-game panel could make it blink off and back. *After:* the overlay no longer skips a frame during fast mouse movement.
- **Paused camera retired.** *Before:* the desktop Paused camera button duplicated features the game now provides. *After:* it is gone from the Camera tab and File menu.

Full details: [0.6.20 release notes](RELEASE_NOTES_0.6.20-alpha.md).

## 0.6.19 alpha - Timeline scrubbing and the smooth default

**0.6.19-alpha**

- **Scrub the Shot timeline without grabbing a camera.** *Before:* a press near a camera mark started moving it, so scrubbing was hard on a busy track. *After:* a plain drag always scrubs; hold **Alt** and drag a mark to retime that view, with the mark highlighted and its arrival time shown.
- **Smooth is the default curve again.** *Before:* new shots and captures started on spline. *After:* they start on smooth, and spline is one click away in the new **Path curve** selector in the Camera tab header; saved shots keep the curve they were saved with.

Full details: [0.6.19 release notes](RELEASE_NOTES_0.6.19-alpha.md).

## 0.6.18 alpha - ReShade effect library setup

**0.6.18-alpha**

- **ReShade finds its shaders automatically.** *Before:* ReShade's menu could report no effect files and stay pointed at the game folder, so Dolly's bundled effects never loaded. *After:* Dolly registers the bundled shaders and any shader folder kept next to the selected runtime, so they load on the next ReShade start.
- **No more silent failure.** *Before:* Dolly could still say the shader library was ready while ReShade found nothing. *After:* it reports when no library was found, and **Browse FX library...** points at any other folder.

Full details: [0.6.18 release notes](RELEASE_NOTES_0.6.18-alpha.md).

## 0.6.17 alpha - In-game camera editing, framing guide and spline motion

**0.6.17-alpha**

- **Edit camera views in the overlay list.** *Before:* retiming or adjusting a view meant switching back to the desktop. *After:* change a camera's time and bank directly in the in-game camera list, with edits streaming while you drag.
- **Drag camera ticks on the Shot timeline.** *Before:* retiming a shot meant typing times or nudging step by step. *After:* drag camera markers along the overlay timeline to place views where you want them.
- **Framing guide on a hotkey.** *Before:* nothing helped you check thirds or centering while composing. *After:* a clean thirds/cross grid toggles in the panel (Alt+G by default), and Escape closes the panel like F8.
- **Spline camera motion, now the default.** *Before:* moves could feel mechanical, and rotation channels could flatten against a key. *After:* a smooth spline runs through your keys by default, for new captures and for shots saved before this update; deliberate Linear choices stay Linear.
- **ReShade FX library recovery.** *Before:* if ReShade could not find its effect folder, its menu asked you to fix it and Dolly offered no way to. *After:* the bundled shaders and a shader folder beside the selected runtime are registered automatically, **Browse FX library...** covers anything else, and a missing library is reported instead of claiming it is ready.

Full details: [0.6.17 release notes](RELEASE_NOTES_0.6.17-alpha.md).

## 0.6.15 alpha - Session lifecycle maintenance

**0.6.15-alpha**

- **Session maintenance.** *Before:* Launching, configuration recovery and cleanup shared tightly connected code. *After:* These responsibilities are separated and regression-tested, preserving the existing editing workflow.
- **Windows cleanup checks.** *Before:* A linked-folder safety test could be skipped without Windows symlink privileges. *After:* It also runs with a Windows junction, checking that cleanup leaves linked files untouched.

Full details: [0.6.15 release notes](RELEASE_NOTES_0.6.15-alpha.md).

## 0.6.14 alpha - Editor maintenance

**0.6.14-alpha**

- **Familiar controls, easier upkeep.** *Before:* Shared controls and editor actions were maintained in several large, connected files. *After:* They have clearer shared owners, while your layouts, shortcuts, camera tools and saved settings stay the same.
- **In-game tools kept together.** *Before:* Panel and Bone Picker drawing shared a file with the graphics hook lifecycle. *After:* Their presentation is separated, with the existing input, resizing and cleanup behavior retained and checked against the previous version.

Full details: [0.6.14 release notes](RELEASE_NOTES_0.6.14-alpha.md).

## 0.6.13 alpha - Mods load with Dolly again

**0.6.13-alpha**

- **Mods load with Dolly again.** *Before:* launching through Dolly replaced `gameinfo.gi` with Dolly's own configuration and dropped every mod mount, so skin and HUD mods were missing for the whole session. *After:* Dolly carries your installed mod mounts (Deadlock Mod Manager, Grimoire or a manual setup) into its temporary configuration, and your original file is still restored exactly on exit.
- **Uncompiled HUD files are still caught early.** *Before:* a stale loose Panorama file inside a mounted mod folder could crash the game before Dolly connected. *After:* Dolly names the exact file and refuses before changing anything.

Full details: [0.6.13 release notes](RELEASE_NOTES_0.6.13-alpha.md).

## 0.6.12 alpha - F9 startup, panel timing and display scaling

**0.6.12-alpha**

- **F9 replay controls return after startup.** *Before:* Dolly could pause while Deadlock's opening sequence still hid the replay timeline and hero controls. *After:* Startup waits for the game's HUD transition to finish before pausing, so F9 can show the controls immediately.
- **A cleaner startup.** *Before:* Closing the startup console or resetting the editor while loading could open Dolly's panel too early. *After:* The panel stays closed until camera support is verified, while recovery shortcuts remain available.
- **The in-game panel scales correctly.** *Before:* Different rendering and window sizes could clip or misplace parts of the panel. *After:* Dolly scales its drawing and clipping to the actual render surface, keeping the panel correctly positioned without changing your resolution.

Full details: [0.6.12 release notes](RELEASE_NOTES_0.6.12-alpha.md).

## 0.6.11 alpha — Replay graphics profiles, capture fixes and compatibility hardening

**0.6.11-alpha**

- **Choose recording graphics for Dolly sessions.** *Before:* Changing between gameplay and recording quality required manual settings changes. *After:* Save, import, preview and select named graphics profiles, with the actual previous settings backed up and restored after the game exits.
- **More reliable camera startup and editor recovery.** *Before:* Console camera startup could stall on an unnecessary refresh, and returning to the editor could hide the original readiness failure behind an obsolete instruction. *After:* Dolly first checks camera response, guides safe health-panel restoration and retains actionable readiness errors without prematurely closing the console.
- **Players capture and complete Depth exports.** *Before:* The current game's Players producer could be rejected, and automatically completed Depth takes could skip final output work or lose pending timing restoration. *After:* A reviewed scene/renderer profile fixes producer selection; export completion and verified restoration finish through the normal workflow. Players remains alpha with known appearance limits.
- **Stronger compatibility and release checks.** *Before:* Optional feature failures could block independent camera functionality, compatibility facts were duplicated, and source archives omitted native files. *After:* Feature failures are better isolated, reviewed definitions are generated consistently, and complete source packages are checked with a clean local build.

Full overnight changes, usage and validation limits: [0.6.11 release notes](RELEASE_NOTES_0.6.11-alpha.md).

## 0.6.10 alpha — Game Follow and shot playback on build 6745

**0.6.10-alpha**

- **Game Follow reads the current player view correctly.** *Before:* selecting a hero could fail with "observer services object type differs (got null)." *After:* Dolly uses the verified observer-services field for build 6745 while retaining its player and replay checks.
- **Fix the health-panel check that blocked shot playback.** *Before:* the same outdated field left health-panel restoration pending, and starting a shot reported "Previous settings still need restoration." *After:* the check uses the current game layout so a valid hero view can finish restoration and unblock playback.

## 0.6.9 alpha — Deadlock build 6745, audio restored

**0.6.9-alpha**

- **Support for the newest Deadlock update.** *Before:* after Deadlock updated to build 6745, Dolly refused the new build. *After:* the native camera, automatic replay loading, Game Follow, particles, the damage/death filter and the cvar unlocker are reviewed for build 6745.
- **Game audio works again on the current build.** *Before:* clip audio was disabled after the soundsystem changed. *After:* the reviewed audio hooks are relocated and enabled for the current soundsystem.
- **Renderer diagnostics follow the current renderer.** *Before:* the optional renderer-pressure diagnostics stopped on the newer renderer. *After:* the reviewed layout is mapped for the current rendersystemdx11.

## 0.6.8 alpha — Game Follow for every hero

**0.6.8-alpha**

- **Game Follow works for every hero again.** *Before:* starting Game Follow on some heroes (for example Chrono/Paradox or Rat King) stopped with "observer object type differs" because their selected pawn uses the reviewed familiar/clone pawn class. *After:* the whole reviewed pawn family is accepted, so Follow attaches to those heroes too.
- **Clearer Game Follow errors.** *Before:* every object mismatch reported the same sentence. *After:* the message names the failing object (controller, pawn, services or selected player) and the observed class, so a genuine build mismatch is actionable.

## 0.6.7 alpha — Deadlock build 6742 and clean damage/death effects

**0.6.7-alpha**

- **Support for the newest Deadlock update.** *Before:* after Deadlock updated to build 6742, Dolly refused the new build. *After:* the native camera, automatic replay loading, Game Follow and health verification, the particle engine and the cvar unlocker are reviewed and enabled for build 6742.
- **Particles and clean damage/death effects on 6742.** *Before:* particle effects could be unavailable, and selecting a hero could carry their death grayscale and red damage vignette into Dolly's camera on the new build. *After:* effects render normally and the selected-player damage/death overlays stay out of Dolly's camera while it owns the view.

## 0.6.6 alpha — particle effects return and the F9 handoff keeps the replay UI

**0.6.6-alpha**

- **Particle effects show again on the current Deadlock build.** *Before:* effects like confetti could stop appearing after Deadlock updated to build 6731. *After:* they render normally again.
- **F9 keeps the replay controls available when the health display cannot be verified.** *Before:* pressing F9 could leave you without the replay timeline, HUD or cursor when the game view could not be checked. *After:* the replay UI and cursor return so you can pick a hero, and only the health display that cannot be verified stays safely hidden until it can be shown.

## 0.6.5 alpha — F9 stays reliable when the game view cannot be verified

**0.6.5-alpha**

- **F9 no longer stops on an unverifiable stock hero handoff.** *Before:* returning to the game with F9 could show "Saved spectator hero handoff remains pending" or a health-HUD camera error when the game's own spectator view was not a settled gameplay camera, for example while a replay target change was still settling. *After:* the replay UI and cursor return normally, the health panel stays safely hidden, and the saved hero is restored on a later handoff once a valid hero view exists. No HUD reveal is forced and original settings are retained.

## 0.6.4 alpha — Player lists, the replay timeline and the newest Deadlock update

**0.6.4-alpha**

- **Support for the newest Deadlock update.** *Before:* after Deadlock updated again, Dolly refused the new build and asked for a newer release. *After:* the native camera, automatic replay loading and the cvar unlocker are reviewed and enabled for Deadlock build 6731 (Steam build 25658155).
- **Bone Picker and Game Follow read player IDs again.** *Before:* the player list came up empty, so those cameras could not select a hero on the new build. *After:* the reviewed spectator-to-player chain is relocated for build 6731 and both cameras resolve the selected player again.
- **F9 brings the replay timeline back.** *Before:* pressing F9 returned the camera to the game but the demo overlay stayed hidden, so the timeline could not be scrubbed. *After:* F9 reopens Deadlock's replay UI and mouse cursor, and Stop still restores your original settings.

## 0.6.3 alpha — Sessions keep going through Deadlock's own assertion dialogs

**0.6.3-alpha**

- **No frozen session when Deadlock shows its own assertion dialog.** *Before:* the game's development build could stop behind a modal assertion window while a replay loaded or exited, which looked like Dolly had broken. *After:* Dolly quietly chooses the dialog's ignore action for its own launched game, and the session continues. The assertion text still reaches the game console and the session's stdout log.
- **Native depth of field checks the engine's shader support.** *Before:* the engine's native depth-of-field pass could use the missing-texture fallback when its shaders were disabled or stale. *After:* Dolly verifies the shader setting when native depth of field is first used, reloads the depth-of-field shaders when it must change that setting, restores the setting when you disconnect, and warns you when the game itself cannot compile the effect (which shows as a magenta/black checkerboard).
- **Support for the October 1 Deadlock update.** *Before:* after Deadlock updated, Dolly refused the new build and asked for a newer release. *After:* the native camera, automatic replay loading and the cvar unlocker are reviewed and enabled for Deadlock build 6728 (Steam build 25658155).

## 0.6.2 alpha — Launch recovery for protected gameinfo files and stale local UI files

**0.6.2-alpha**

- **Launch when `gameinfo.gi` is read-only.** *Before:* Steam verification or a modding guide could mark the file read-only, and Dolly's launch failed with "Access is denied" before the game started. *After:* Dolly swaps the file in place and restores the original bytes with the read-only attribute intact.
- **Launch after a Deadlock update with local UI files.** *Before:* the editing configuration still mounted retired addon paths, so a stale loose file such as an old HUD `hud.xml` could crash the updated game during startup, before Dolly could connect. *After:* the editing configuration matches the official mount list, and a launch names any uncompiled Panorama files still in the game folder instead of only reporting an exit code.

## 0.6.1 alpha — Anchored camera markers and a startup fallback for a missing preload

**0.6.1-alpha**

- **Steady saved-camera markers.** *Before:* camera boxes could deform and drift as framing, aspect or lens changed. *After:* markers keep the same anchored position and a consistent shape at any view, including path preview.
- **Startup when Deadlock's intro never appears.** *Before:* if the game never ran its hideout intro, no map/shader preload could start, Dolly waited out the full timeout and no replay loaded. *After:* Dolly stops the automatic check early, explains what happened and offers to load the selected replay without the preload check; the game session stays open.

## 0.6.0 alpha — In-game camera editing, Undo/Redo and current Deadlock support

**0.6.0-alpha**

- **Edit and recover cameras in-game.** *Before:* managing saved cameras meant returning to the desktop, and accidental edits were harder to recover. *After:* select, double-click to view, delete, Undo and Redo from Dolly's in-game camera list, with shared desktop history.
- **Keep the framing you captured.** *Before:* a custom lens could change after replay recovery even when camera coordinates matched. *After:* new captures retain lens metadata through playback, save/reopen and shot history.
- **Updated replay and camera controls.** *Before:* recent game updates could leave player lists empty or block a Follow-to-Bone handoff. *After:* reviewed build 6726 support restores the tested player lists and lets paused Follow transfer directly into Bone Picker.
- **Cleaner views and complete recordings.** *Before:* health-bar controls could be unavailable, repeated replay-clock values could create capture gaps, and adding audio could lose the final frame. *After:* updated health controls, corrected real-time frame admission and video-preserving audio muxing address those cases.

### Camera editing and history

The CAMERA tab now includes a selectable list of saved cameras. A single click
selects for editing without moving the view; a double-click jumps to that camera.
Delete removes only the selected camera. Undo brings it back, and Redo reapplies
the deletion. Desktop and in-game editing share shot history, including camera,
lens, attachment and effect data. New edits invalidate the redo branch; save/reopen
and dirty-state behavior are covered by regression checks.

Use Ctrl+Z to undo and Ctrl+Y or Ctrl+Shift+Z to redo while Dolly's panel is open.
History is for shot edits, not replay transport or external game configuration.
Playback, recording and active picker operations retain their editing guards.

### Framing, Follow and Bone cameras

Captured views now store lens information instead of relying on whatever lens
the game chooses after a replay restart. That information follows the shot through
preview, path interpolation, save/reopen and Undo/Redo. Existing shots remain
loadable, but an older custom-lens shot may need recapture to gain this metadata.

The recent Game Follow workflow remains in its own FOLLOW tab: choose a hero,
follow their aim, and adjust distance, shoulder and height. Right-click resets
the sliders, and the HUD option controls whether the native game UI is visible.
Follow and Bone are separate modes. Paused Follow can now hand directly into
Bone Picker without requiring Restore rig first. These changes build on the
Follow motion and HUD work shipped in the preceding releases.

Regular camera paths still hold their final view on completion. Explicit F9 or
Stop / restore uses the spectator handoff. Stop retains the selected slow replay
speed, and error dialogs once again request the standard Windows error sound.

### Deadlock updates, local demos and restoration

Reviewed native and Python compatibility profiles cover Deadlock build 6726
(Steam build 25639407). Unknown builds remain blocked. Automatic startup retains
the normal hideout, unlocker, preload and replay-camera handoff checks.

Player discovery was updated for the tested local `tv_record` replay, restoring
the Follow and Bone player lists. The editing session uses the reviewed editing
configuration while preserving the user's original `gameinfo.gi` byte-for-byte.
Separate video settings and autoexec files are not blanket-reset.

Read-only module verification now handles Windows' documented transient module
snapshot error with a bounded retry. Other failures still stop verification and
report the Windows error code. HUD restoration continues to require a verified
handoff rather than exposing an unsafe player panel.

### Look controls and particles

The floating health-bar toggle uses the current health-panel controls and restores
the saved values. The tilted hero health/ability panel remains a separate HUD
concern. Look-control readbacks and rollback handling were strengthened.

All eight particle presets and the tested quality-setting combinations looked
normal locally. Native DOF, Citadel DOF, Native DOF with Confetti and final effect
disable were visually checked. The reported black/purple checkerboard was not
reproduced on the affected user's exact installation or GPU; this release does
not claim a universal particle-material fix or reset users' graphics presets.

Glow remains available, but enabling it does not force outlines when the game
does not consider them eligible. Its visual effect was inconclusive in the
comparison scene and is not presented as a verified visual improvement.

### Video, audio and export fixes

Real-time capture no longer rejects an otherwise valid rendered frame solely
because the replay clock repeats. Fixed-step capture, backward-time rejection
and completed-path endpoint handling keep their separate safeguards. Genuine
missed timing slots are still counted; the fix does not invent replacement frames.

Adding game audio no longer uses a shortest-stream cutoff that could remove
the last video frame. Audio coverage checks still reject incomplete source audio.
The final three-second Color/audio test retained all 180 frames, reported zero
missed slots, preserved identical video pixels and timestamps through muxing,
and was visually accepted as smooth with aligned sound.

Color, World, Depth, Effects and Players were checked with bounded two-frame
takes. Players coverage and alpha output were inspected. These short checks do
not certify long recordings, every hero combination, every GPU or 4K performance.
Fixed-step bundled audio remains unsupported; use real-time audio recording and
export silent fixed-step layers separately.

### Validation and upgrade notes

The candidate passed 1,628 Python tests (16 skipped), 25 native tests, a relocated
portable-app startup check, and updater installation/rollback checks. Clean test
sessions restored original runtime/configuration state. The README, user-guide
introduction and quick-start documents now describe the current workflows.

Download the Windows x64 ZIP, extract the entire package and launch Dolly.exe.
Keep `_internal` beside it. Preserve shots and pending recovery backups when
upgrading. This remains an alpha for local replay editing; keep `-insecure` in
Deadlock's launch options while using Dolly. The repeated intro during shot
preparation stays visible until a true skip has been verified.

## 0.5.46 alpha — Automatic take validation and a complete source export

- Finished layered exports are validated automatically: Dolly checks the take
  folder for missing or zero-frame depth output, incomplete manifests, missing
  masters and decoded-audit mismatches, and logs either a clear pass or the
  exact problem. The check is read-only and never changes the export result.
- The source export now rejects an incomplete manifest: every shipped
  `dolly/*.py`, test, tool and packaging file must be listed, and the missing
  `tests/test_layer_modes.py` was restored to the archive.

## 0.5.45 alpha — Deadlock update 2026-09-25 (build 6701) compatibility

- The native camera accepts the 2026-09-25 Deadlock update: the client was
  re-reviewed offline, every reviewed camera function is unchanged, and the
  native bridge recognizes the new build by hash and signature.
- Automatic startup preload verification was re-reviewed for the new client.
  All reviewed code spans are byte-identical to the previous build, and one
  bounded live session confirmed the paused native editor, native camera
  recovery cycles and a clean exit with exact configuration restoration.
- The editing configuration review is refreshed for the new client hash, so
  one-click startup continues to work after the update.

## 0.5.44 alpha — Stability hardening and crash diagnostics

- Dolly now warns when a paused replay keeps committing memory, so a long
  paused edit can be saved and the session restarted before the engine runs
  out of memory.
- A console read failure now fails the affected request immediately with a
  clear reason instead of making later commands time out for their full
  duration.
- The interface keeps updating after an unexpected UI-callback error, and a
  burst of log messages can no longer grow the event queue without bound.
- If Deadlock is already running when Dolly fails to attach, closing the game
  still restores the original configuration automatically. Empty take folders
  and updater temporaries are cleaned up when a step fails.
- Cancelled layered exports finish cleanly instead of raising a confusing
  "lost the recording options" error after the run was torn down.
- A one-time runtime renderer check records the active renderer and warns when a
  native session is not actually DirectX 11.
- Added a read-only take validator: `python -m dolly.export_audit <folder>`
  flags zero-frame depth output (the missing/ALWAYS signature), incomplete
  manifests, missing masters and decoded-audit frame/timestamp failures.

## 0.5.43 alpha — Camera recovery and DOF controls

- Return to the editor after native camera release or an attach failure without
  getting stuck behind repeated camera-unavailable errors. Recovery clears only
  the live attach preview and preserves saved cameras and bone settings.
- Right-click values in either in-game Looks DOF card to restore Dolly defaults.
- Citadel DOF now starts with a usable aperture when none is authored. Switching
  DOF modes prevents range overrides from masking Citadel blur, preserves saved
  range curves, and keeps the shared master switch enabled for the active mode.

## 0.5.40 alpha — Copy error details from error dialogs

- Error dialogs now include a **Copy error details** button next to **OK**. One
  click copies the Dolly version, the failed operation and the exact error text,
  ready to paste into a support message instead of retaking screenshots.
- The dialog keeps its previous message-box layout and behavior; unused, nothing
  changes. Startup failures that occur before the editor window exists still use
  the original dialog.

## 0.5.39 alpha — Automatic hideout and verified preload startup

- Dolly advances the initial click-to-continue screen automatically, verifies
  that dashboard map/shader preloading has started and completed, then opens
  the selected demo through the existing replay and camera safeguards.
- Startup progress explains the preload stage. Missing evidence, unsupported
  game builds and timeouts cannot silently mark preloading complete.
- Hands-off startup passed local Native and Console tests. Users should let
  Dolly finish loading the hideout and demo without manually navigating menus
  or loading a replay. No frame-time or crash-rate improvement is claimed.

## 0.5.37 alpha — Select DirectX 11 for the Native editor

- Native replay launches now explicitly select DirectX 11. Previously a game
  using Vulkan could load the replay but time out waiting for the DX11 panel.
  The renderer flag applies to Dolly's local replay process; saved graphics
  settings are not edited. Console-backend launch behavior is unchanged.
- Verified locally: a Vulkan launch reproduced the reported panel timeout;
  the fixed launcher selected DX11 and opened the paused editor with input ready.
  This does not address unrelated memory-exhaustion or depth-export failures.

## 0.5.34 alpha — Crash reports that explain themselves

- Deadlock crash dumps are now included with diagnostics. Before, a crash wrote
  its dump beside the game executable, but the support ZIP searched only the
  game folder, so reports arrived without the dump. The newest dumps are now
  found and attached automatically.
- A crash while a replay is loading now reports the exit code in decimal and
  hex and explains that the replay may be one this game build cannot
  reconstruct, instead of only saying the game closed. The selected replay's
  map and recorded build are named when its header could be read.
- Diagnostics include the selected replay's header: recorded map, game build,
  patch version, demo version, and server/client names.
- Known limit: moving player-layer takes at 120 FPS with 1x export speed can
  miss shot frames on a busy machine. Dolly refuses the take instead of writing
  a bad layer; retry at a lower FPS or at 0.5x export speed.

## 0.5.33 alpha — Selecting a ReShade runtime works immediately

- Selecting a runtime DLL now takes effect immediately: the card remembers the
  path, so the next session can enable it automatically, and the status names
  the one remaining step (launch a replay, then press Enable ReShade). Before,
  browsing filled only the text box — the path was never saved, the status
  stayed "Choose a runtime DLL", and Enable stayed greyed until a live replay,
  which made a perfectly good DLL look rejected.
- **Enable ReShade** is pressable as soon as a valid runtime is selected, even
  before launching; pressing it without a session explains that a DirectX 11
  replay is required.
- Reported by a user whose runtime was not recognized: 0.5.32 shipped the
  diagnostics below, but the path was still only remembered after a successful
  enable, so the card never acknowledged the selection.

## 0.5.32 alpha — ReShade runtime diagnostics

- The ReShade card now says why a selected runtime cannot be used. When the
  saved DLL is gone — moved, cleaned, quarantined, or replaced with the folder
  it lived in — Dolly names the missing path and states that it never moves or
  deletes the runtime. Before, the card only said "Choose the 64-bit ReShade
  runtime DLL", which read as if Dolly had removed it.
- A runtime kept inside the Dolly folder is refused with instructions to move
  it somewhere of its own (for example `Documents\Dolly-ReShade`) and select it
  again: a portable update replaces the Dolly folder, so a DLL inside it can
  disappear between versions even though Dolly itself never touches it.
- Reported by a user whose runtime stopped being recognized after updating.

## 0.5.31 alpha — Play a shot that starts at a recorded-packet boundary

- **Play shot no longer fails when the first camera tick sits one tick after
  the replay's recorded packet.** Reported with a 45-minute replay: a camera
  captured at tick 52121 (one past the paused position) made Play wait 15
  seconds and abort with "the renderer has not confirmed a paused replay view",
  on both attempts. The engine's paused skip had reported "Demo Skipping ...
  from full packet 49921" and settled at tick 52120, one tick short of the
  request, while Dolly's renderer gate demanded a tick at or after the target
  and could never confirm.
- A skipped seek now nudges playback across the recorded-packet boundary once
  (resume then pause) and re-requires the exact tick. If the tick is still
  unreachable, Dolly starts at the recorded tick one frame earlier, holds the
  first camera over that instant and says so, instead of failing the whole
  playback. The renderer gate now only proves a fresh paused rendered view; the
  exact tick stays enforced by the console settle policy, and transport
  hiccups during reconstruction are retried inside the existing 15-second
  budget without swallowing identity errors.
- A seek that still cannot settle now names the tick the replay reached before
  reporting the renderer detail, so a stuck replay is distinguishable from a
  window that is not rendering.

## 0.5.30 alpha — Depth and layer exports at 4K

- Depth exports now work when the game renders the scene below the window size.
  With upscaling or resolution scaling enabled, the game's scene target is
  smaller than the recording (for example 2560x1440 for a 3840x2160 window);
  the scene selector used to require the recording size, skipped every frame
  and ended the take with `Recording ended before any game frame was captured`.
  The selector now verifies the reviewed scene target at its own resolution and
  captures the paired depth data at that size.
- The depth manifest records both sizes: `width`/`height` are the depth
  master's own resolution, and the new `color_width`/`color_height` fields are
  the paired color video's, so a compositor knows to scale. The depth preview
  keeps its matching half-resolution stream.
- The depth encoder starts with the first verified depth frame, so its raw
  input size follows the scene target instead of assuming the color size.
- A depth take that still captures nothing says so and names the scene it
  observed: the error reports the recording size, the verified scene size and
  the reason a reviewed target was rejected (for example multisampling or an
  unusable depth view).
- The scene selector keeps its failure-closed rules: within one frame the
  largest reviewed target wins, a smaller reviewed target cannot replace the
  chosen scene, and two different targets of the same size stay ambiguous.
- Reported case: a 3840x2160 depth take skipped every rendered frame
  (`scene-skip=256`). Every verified depth take so far ran at 2560x1440; the
  4K take now captures depth at the game's internal scene resolution with the
  color recording unchanged. Live 4K verification in the game remains
  outstanding.

## 0.5.29 alpha — Confetti rain and player POV export

- Add **confetti rain driven by the game's native particle engine**. Enable it
  in the Export tab or the in-game card, choose a spawn height between 100 and
  1500 units, and optionally have pieces despawn when they touch the ground.
  The camera-following volume uses Dolly's own packaged effect assets and
  leaves the game's files unchanged.
- Add **Player POV** export. Choose the game's spectator view, pause at the
  desired start, then record a bounded POV take with the shared export settings
  without replacing the shot's authored camera path. Color and paired depth
  takes are both supported.
- Fix the rain vanishing on longer shots. The replay clock's sub-frame wobble
  no longer resets the effects, so the rain stays continuous through pauses,
  seeks, and long camera moves.
- Double the close rain coverage (depth and width) at the same particle
  density, and widen the high-altitude volume so long camera paths stay filled
  with rain. All effects are released when a take ends or Stop is pressed.
- Verified in local replays: continuous rain across three stationary cycles
  and a long moving path, pause/resume, ground-contact mode, and one bounded
  5-second color+depth camera-path export with no lingering particles.
- Thanks to s34532 for the native confetti rain implementation.

## 0.5.28 alpha — Editing configuration and in-game camera reset

- Launch with Dolly's reviewed editing gameinfo so competitive framing and
  rendering overrides do not carry into replay editing. Back up the user's
  original bytes and restore them after unlocker initialization, with existing
  exit/crash recovery and external-change protection retained.
- Verify the editing configuration against the installed game build. After a
  game update, Dolly needs a matching reviewed editing configuration before
  launching; separate autoexec and saved video settings remain unchanged.
- Add **Reset camera path** to the in-game Camera card. Confirm **Reset here**
  to replace the camera views with the current view and replay start tick while
  keeping lens and depth-of-field tracks. The confirmation stays inside the game.
- Verified one local replay session with the supplied competitive configuration:
  aspect and model-detail overrides reset, the user-triggered native reset event
  preserved the current pose and DOF track, and exact configuration/preferences
  restoration and temporary deployment cleanup passed.

## 0.5.26 alpha — More complete player-layer exports

- Player-layer capture matches character parts to the data actually submitted
  for rendering, rather than data being prepared for a later frame. Recycled
  instance slots and mismatched ownership are rejected instead of silently
  producing missing or incorrect parts.
- Preserve the first selected character pass and supported material passes
  without a depth attachment. Start shader discovery before replay loading.
- Increase the shared player capture limit from 24 to 64 rendering passes per
  image. All detected player heroes remain eligible; exceeding the limit now
  rejects the incomplete output with an explicit error.
- Ownership checks copy 36 bytes per pass instead of entire multi-megabyte
  buffers. Captures retain exact identity and record validation.
- Check player-layer length limits before starting the color recording, and
  retain small capture reports in diagnostic ZIPs after game cleanup.
- Verified bounded local Rem and Seven captures, including automatic player
  detection and Seven alpha MOV encoding. Full moving-shot validation, the
  original user replays, and Rem's summoned companions remain follow-up work.

## 0.5.25 alpha — Hero bone cameras and attachment recovery

- Head and right-hand camera selection, replay playback and detach were checked
  on Holliday, Abrams, Rem, Yamato, Mo and Krill, Drifter, Lash, Graves, Wraith,
  Calico, Vindicta and Warden. Other heroes and bones remain experimental.
- Bone cameras resolve skeleton names from the selected hero's own model resource,
  including models with generated cloth joints. Other models' nearby name tables
  can no longer be selected by the old pointer walk. Model and pose-buffer changes
  are checked before applying the camera. Entity handles are rechecked so a
  recycled entity cannot silently replace the selected target.
- Eyes attachment no longer performs optional bone discovery. Native heartbeat
  monitoring remains responsive independently of model discovery work.
- Detach restores a usable free camera after an attachment failure. Failed camera
  entry clears stale ownership, and an explicit retry refreshes target resolution.
- Read skeleton count fields at their actual 32-bit width so adjacent bytes no
  longer cause intermittent valid-model rejection.
- Fixed a buffer overread in bone-name decoding. Diagnostic exports now include
  attachment selection, roster and bone catalog details.

## 0.5.24 alpha — One camera workspace and precise replay stepping

- In-game Camera combines replay playback, camera selection, a marked shot
  timeline, Seek here and path guides. The separate Replay tab and Between
  cameras card are removed; desktop replay management remains unchanged.
- Choose 1, 2, 5, 10 or 25 ticks, then step backward or forward while the
  current camera stays fixed and the panel stays open. The actual replay tick
  is shown. If a requested tick is absent from the recording, stepping uses
  the nearest recorded tick in that direction and reports the actual movement.

## 0.5.23 alpha — In-between shot editing and shared UI

- In-game **Camera > Between cameras** adds a shot-time slider and **Seek here**.
  Choose a time, then seek once to apply the replay time and complete shot view.
- Desktop **Seek replay** and in-game **Seek here** use the native pose when
  available, including rotation curves and framing, after the replay settles.
  The console backend retains its existing guarded fallback. Native seeking
  waits for fresh paused render frames before requesting console confirmation,
  avoiding requests lost during replay reconstruction.
- Both interfaces share dark cards, mint actions and compact attachment
  controls, with extra rotation and transition settings available on demand.

- Source transitions can ease into a saved camera over an adjustable duration,
  ending at its key time. Zero keeps the existing cut behavior. Both desktop
  and in-game Camera controls expose the setting.
- Shot-only and fixed-step recording drain their last pending frame even when
  the replay clock stops advancing at the shot endpoint.
- Weapon and named-bone selection reject reduced skeletons and incomplete name
  lists instead of silently treating their indices as render bones. Unverified
  rigs can be unavailable; Eyes remains the fallback.
- Body hiding rejects mixed instance batches and invalid identity offsets;
  incomplete hero draw coverage remains under investigation.

### Rotation framing curve and a scrollable inspector

- The desktop **FRAMING CURVE** card is now **FRAMING CURVES**: an editable
  **rotation** graph for **Pitch**, **Yaw** and **Roll** sits above the
  aspect-ratio graph. Dragging a point writes a per-camera override for that
  channel, the dashed line keeps the authored camera motion visible, hollow
  points mark cameras without an override, and **Reset** clears the channel's
  overrides. Saved shots keep the overrides, and preview, console playback and
  native playback apply them exactly like authored angles.
- Both graphs share one timeline: the mouse wheel zooms around the pointer,
  a right-drag pans, and a double-click returns to the whole shot, so a
  rotation move and the framing change at the same moment line up at any zoom.
- The Cameras inspector scrolls, so the camera-source, DOF and path cards stay
  reachable on smaller windows instead of falling below the bottom.

## 0.5.22 alpha — Layer takes stay independent

- Recording a World layer first hides the skinned objects, and the players
  capture never reset those flags: it armed against a scene with no characters,
  sealed no frames, and a failed take could leave the game showing only the
  world. The players capture now restores every scene class before it arms, and
  Stop / restore always shows the classes again; a failed restore raises instead
  of passing silently.
- Verified live by reproducing that order exactly: with the World hiding applied
  first, the players capture sealed 57 frames and encoded its alpha master.

## 0.5.21 alpha — Players-only layer export

- The **Players** layer records real players plus their weapons, attachments and
  carried objects, with NPCs and creeps left out, while the untouched world still
  occludes them and the layer keeps real coverage alpha in one fixed-step pass.
  The native bridge classifies every scene object's owner through the scene graph
  (scene object -> scene node -> owner pawn) with the game's live schema and
  records exactly the draws those objects produce.
- Supports the current September 17 client builds: the update re-tagged character
  input layouts and moved the draw/submit timing, so the capture pairs draws
  through the identity-buffer binding, accepts a one-frame submit lag, and
  validates every recorded draw against its owner's record read back from the
  GPU -- a draw whose record moved is dropped instead of trusted.
- A take that ends early (a replay returning to the hideout) or a stop request
  finishes the capture with the frames it already sealed instead of waiting out
  its whole budget.
- Verified live on the current build: a 57-frame fixed-step take captured 16-24
  validated draws per frame, previews show both players with their equipment and
  no world, and the native and Python suites pass (16/16 and 1157 tests).
- Re-verified against the September 18 client hotfix (Steam buildid 25379491):
  the pinned client, engine and tier0 modules are unchanged, so the reviewed
  17b profile still reports the build as supported and a fresh live take
  captured and encoded cleanly.

## 0.5.20 alpha — September 17 game compatibility

- Adds the reviewed compatibility profile for the September 17 client builds
  (buildids 25376188 and the 25379260 hotfix, client `d1ee16fc…`). Engine modules
  (engine2, tier0, scenesystem, rendersystemdx11) are unchanged between the two
  builds, and the scanner reports the game as supported. Capture, playback and
  export were re-verified live on the current build.
- The players-only layer export implementation is included but not enabled yet:
  the game update moved the render-side draw records its selection gates read, so
  the **Players layer** option stays disabled until those gates are re-derived and
  verified live. The world and effects layers are unchanged. Per-player (single
  pawn) selection is not part of this release.
- The ownership capture itself remains in the native bridge: it selects the exact
  draws produced by scene objects owned by player pawns, so a layer contains real
  players and their equipment (weapons, attachments and carried objects) while
  NPCs and creeps stay out, the world stays in the scene so scenery occlusion is
  preserved, and real coverage alpha arrives in one fixed-step pass. The research
  path behind it was proven end-to-end with ownership-verified, alpha-exact MOV
  proofs across 96, 192 and 256-frame fixed-step takes for several heroes,
  including the largest and smallest skeletons and an attachment-carrying phasing
  hero, with every captured frame paired against the color take's authored
  timeline.
- Verification behind this release: the capture is part of the native bridge
  binary (16/16 native tests) and the full Python suite passes (1156 tests).

## 0.5.18 alpha — Replay tick-rate alignment

- Replay-timed cameras now use the open replay's own tick rate from its
  `CDemoFileInfo`. Matchmaking replays are recorded at 32 ticks/second as well
  as 64; a 32-tick replay used to be treated as 64, so a shot's cameras arrived
  at the wrong replay moments. Reported case: a shot at 0.1x playback whose last
  camera missed the action it framed because the replay reached only half the
  authored ticks.
- A new shot adopts the detected rate. A saved shot that disagrees is offered a
  one-step retime when the replay is checked or opened, and **Retime to replay**
  in shot timing does it at any time. A capture that would mix two clocks is
  refused with instructions instead of writing a camera against the wrong one.
- Playback of a mismatched shot warns in the status line, and diagnostics report
  the detected replay tick rate.

## 0.5.17 alpha — Depth takes skip transition frames

- A depth take now skips presented frames that have no verified scene pass yet
  — for example a cleared scene target or a full-viewport `ALWAYS` depth write
  during a loading/transition frame — instead of failing on the first frame
  with `scene result=missing why=depth function=ALWAYS`. The skip is bounded
  and stops at the first captured frame; any scene failure after that still
  stops the take closed. Reported case: a 1920x1080 60 FPS take failed on its
  first frame (`frames_written=0`).
- Zero-frame diagnostics now include the transition-frame skip count
  (`scene-skip=` in the capture summary).

## 0.5.16 alpha — Depth guard and encoder diagnostics

- Keep a verified scene-depth frame when a full-viewport `EQUAL` depth write
  targets the chosen scene texture. An EQUAL test with writes can only store
  the depth value it compared against, so the exported depth cannot change;
  the frame stays ready with its verified calibration. A frame with no
  supported main pass still fails closed. This addresses the reported
  `why=depth function=EQUAL` depth-take rejection.
- Name the failing FFmpeg write stage (color pipe, depth sequence or depth
  pipe) and append a bounded tail of that process's stderr to the media error,
  so a stopped take keeps its cause after the temporary FFmpeg logs are
  removed during cleanup.
- Keep the Citadel glow choice across replay resets: the toggle remembers the
  disabled state and re-asserts it after the recovery that recording
  preparation performs, so a take started with glow off stays off.

## 0.5.15 alpha — September 16 Deadlock build support

- Support the September 16, 2026 Deadlock client (`client.dll`
  `472dad57…`): refresh the reviewed compatibility profile, the bundled manifest
  and the generated native profile table, and rebuild the native helper against
  it. `server.dll` changed too; `scenesystem.dll`, `rendersystemdx11.dll`,
  `tier0.dll`, `engine2.dll` and `gameinfo.gi` are unchanged by this update.
- Re-verify the reviewed camera path on the new client: the main-view setup
  prologue, the sole caller, the `CViewRender` vtable, the globals pointer and
  the aspect source all re-resolve, the relocated-call list is unchanged in
  shape, and every view and field offset is identical; only addresses moved.
- Keep the reviewed `globals+0x30` clock fallback for the new client until a
  render-fraction observation is recorded for it, matching the rule that a
  clock-field review is never inherited across client hashes.

## 0.5.14 alpha — Package privacy and depth diagnostics

- Build the Windows package from an explicit file list that never includes
  runtime `logs/`, demos, recordings or staged update folders, so a folder that
  was run in place cannot distribute session journals; the source export already
  enforced the same rule.
- Name the observed depth comparison in the scene-depth rejection diagnostic
  (for example `why=depth function=LESS`), so a failed depth take identifies the
  offending pass from its own message instead of only reporting "depth function".

## 0.5.13 alpha — Safe health-bar toggle

- Restore the health-bar toggle to the two live-verified switches
  (`citadel_healthbars_enabled` and `citadel_unit_status_use_new`), written one
  command at a time. Writing `citadel_unit_status_enabled 0` or
  `citadel_hud_objective_health_enabled 0` while a replay renders hung the game
  with a DX11 device error (Windows logged an Application Hang and Dolly then
  reported that the build did not accept the switches), so those master
  switches are never touched. Exact prior values are still snapshotted and
  restored on the next press.

## 0.5.12 alpha — In-folder updates and smoother recovery

- Keep update downloads, staging and backups in a `.dolly-update-*` folder
  inside the Dolly folder instead of beside it; the next launch removes the
  folder once the update completes or rolls back safely, including legacy
  folders that older releases staged next to the installation.
- Reuse an already-downloaded verified package when an update is deferred or
  retried, so the same release is not downloaded again.
- Back up and replace a locally modified managed application file (for example
  a hand-built native DLL) instead of refusing the update; a failed startup
  check still restores it through rollback.

## 0.5.11 alpha — Live replay speed and health-bar master

- Make **Playback speed** apply immediately: changing it in-game or on the
  desktop updates the replay's demo timescale while a replay is playing or
  paused, so heavy-effect shots can be reviewed in slow motion without
  restarting. Stop / restore still returns a Dolly-owned speed to 1×, and the
  update rate still requires a stopped shot.
- Rework the health-bar button as **Toggle health bars**, a master hide/restore
  for unit, HUD and objective bars (`citadel_unit_status_enabled`,
  `citadel_healthbars_enabled`, `citadel_hud_objective_health_enabled`). It
  snapshots the exact prior values, verifies the readback and restores them on
  the next press. The old style switches are no longer touched and bar glow
  stays with **Toggle Citadel glow**.

## 0.5.10 alpha — Citadel controls

- Add in-game Camera-tab buttons under Clear ragdolls: **Toggle Citadel glow**
  (boss/player/trooper glow switches plus health-bar glow), **Healthbar
  Toggle** (health bars and the new unit-status mode) and **Near player
  opacity fix** (full opacity on the near-player camera fades). Each toggle
  reads the current value before flipping.
- Rename the native DOF card to **Native Depth of Field** and add a **Citadel
  Depth of Field** card to the in-game Lens tab: an Enable DOF switch
  (`r_citadel_depthoffield_enable` with `r_depth_of_field`) and sensor-size
  and focus-distance sliders on the engine's documented bounds (0.5–3, 0–10000
  with a logarithmic focus scale).
- Mirror the Citadel DOF card on the desktop Camera tab. Sliders author the
  shot at the playhead and apply it through the existing paused preview, so
  values save to Effects and play back through the native effect binder.
- Keep ReShade depth effects working while the game is online. ReShade pauses
  its add-on events and depth detection after it sees network traffic; Dolly
  now publishes its own verified scene depth from its Present path instead of
  the gated event, so MXAO and similar shaders keep working without renaming
  the game executable. While events are paused, capture waits and the ReShade
  status explains why instead of stopping the runtime.

## 0.5.9 alpha — Export flow wording and launcher notes

- Clarify the layered export flow in the Export panel: the color video records
  first, ticked passes are extra takes that follow automatically, and the
  status line announces that handoff. Refresh the user guide's export section
  and its availability notes.
- Put the **In-game capture** switch on the Library page beside **Open replay
  in Dolly** as well as the Cameras toolbar, so it no longer requires the Full
  editor switch.
- Clarify launch-safety wording: Dolly refuses to launch or connect unless it
  started the game process itself and always passes `-dev -insecure -console`.
  The `-insecure` launch-option advice protects the temporary plugin-mount
  window, not a supported way to attach to a manually launched game.

## 0.5.8 alpha — Export fixes, ReShade depth and crash dumps

- Keep each new export take beside the take tree instead of nesting it inside
  the previous take folder, and log the alpha master path when a layer combine
  finishes.
- Collapse duplicate and stale bundled ReShade search paths so the effect list
  stops showing each bundled shader several times.
- Publish the verified scene depth to ReShade's `DEPTH` semantic while its
  runtime is active, so depth-based effects such as MXAO can work. The bundled
  presets set the reversed-projection definitions.
- Compact the native scene observation history per target instead of dropping
  the oldest events when the per-frame budget fills, keep the frame's verified
  calibration when a supported scene pass has no per-view constants instead of
  failing the take, and report the rejection reason, last event and scene
  target count for the frame being captured rather than a stale reason.
- Include the newest Deadlock breakpad minidumps in exported diagnostics.

## 0.5.7 alpha — Wireframe-matched controls and manual builds

- Match the desktop and in-game panels to the wireframe layout.
- Keep Windows builds manual and fix updater handling for short install paths.

## 0.5.6 alpha — Embedded updates and launcher-first desktop

- Show the startup update check from Dolly.exe itself.
- Move the desktop to the launcher-first layout with Camera, Lens and Export
  panels.

## 0.5.5 alpha — Depth master, layer takes and verified updates

- Record an optional paired depth master beside the color video: a 10-bit
  ProRes `.mov`, an optional float EXR sequence and a normalized preview video.
- Record isolated world, players and effects layer takes. Players and effects
  use black and white matte passes combined into an RGBA alpha master.
- Add verified public-release updates that preserve settings, shots and
  external tool paths.
- Fix scroll-wheel framing anchoring and show a dialog when a background take
  fails.

## 0.5.4 alpha — Recorded-shot metadata and stability

- Write `<video>.shot.json` next to a recording that contains a native shot:
  exact first/last encoded frame, replay time, fps, fixed-step flag and frame
  counts. Purely additive metadata; the video is untouched.
- Wait for the recorder to report recording before a prepared native shot
  starts, and stop cleanly on timeout, cancellation or a terminal state.
- Bundle a locally built cvar unlocker that removes its two owned commands on
  Disconnect and fixes SDK interface-storage clearing; the normal-quit access
  violation is removed. MIT notice, patches and provenance are included.
- Add the tested live-depth ownership contract (default-off) ahead of the
  opt-in depth export.

## 0.5.3 alpha — Compatibility scanner and game-update profiles

- Add `native/profiles/manifest.json` as the single source of truth for
  accepted `client.dll`, `engine2.dll` and `tier0.dll` hashes, shared by the
  Python launcher, the native bridge and the packaging tests.
- Add `dolly/compatibility.py`: every launch hashes the installed modules and
  classifies the build as supported, incomplete or unsupported, with the
  observed hash, the reviewed date and a newer-than-reviewed hint. No network
  access and no pip dependency at runtime.
- Report an unrecognized build through `_verified_native` before any game file
  or process is touched, and add a **Check game build** action to the Advanced
  launch dialog.
- Add `native/src/dolly_compat_generated.hpp` (generated profile table) and an
  AOB wildcard fallback in the native bridge. Exact SHA-256 matches stay the
  fast path. A signature match must be unique, all camera symbols must
  re-derive (caller, CViewRender RTTI vtable, SetGlobals pointer, aspect-source
  interface) and the reviewed prologue must survive, or the build stays blocked.
- Add reviewed support for the September 11 `client.dll`. Main view setup,
  caller, vtable, globals and engine-client pointer all relocated by consistent
  deltas with byte-identical view field offsets; `engine2.dll` and `tier0.dll`
  are unchanged.
- Add `tools/generate_profile.py` (the packer): it locates every client symbol
  from the installed binaries, carries forward engine/tier0/effects, writes a
  reviewed profile, refreshes the manifest and emits the native header.
- Add portable wildcard-scanner tests and compatibility scanner tests. Retain
  the camera hook, interpolation, recording and ReShade behavior.

## 0.5.2 alpha — 120 FPS recording

- Added 120 FPS alongside 30/60 in Export, command transport and the native encoder.
- Added 120 FPS cadence, protocol, controller and Windows MP4 metadata regressions.
- Corrected Export help text: returning to desktop controls does not finish recording.
- Retains the 0.5.1 recording handoff fix and existing camera/ReShade integration.
- Depth, hero-only and world-only exports require further renderer integration and are not shipped in this build.

## 0.5.1 alpha — Recording handoff

- Recording follows the verified native session, independently of manual camera input.
- Transient camera readiness changes and returning to desktop controls no longer finish an active MP4.
- Finish recording saves the file; disconnect and expired session heartbeat retain automatic finalization. Resizing still finishes recording.
- Added a Windows Present/encoder regression for camera handoffs and focus changes.
- Camera movement, interpolation and ReShade API remain unchanged.

## 0.5.0 alpha — MP4 and ReShade

- Real-time, video-only H.264 MP4 capture at 30/60 FPS and current SDR game resolution.
- Bounded GPU/CPU queues, encoder worker, missed-slot reporting, exclusive output creation and finalization on stop/focus loss.
- In-game recording controls; desktop output path, frame rate and bitrate.
- Optional ReShade manual runtime, color effects, clean capture before its UI, and configurable F11 menu access.
- ReShade loading runs outside the camera worker; configuration stays outside the game.
- REPLAY home heading. Camera hook, interpolation and timing remain unchanged.
- Fixed-step rendering, audio, depth-dependent ReShade shaders and separate layer export are not included.

## 0.4.8 alpha — Recorded packets and frozen native preview

- Read a bounded, cached index of completed Source 2 packet records. Native
  shot starts between packets explicitly seek the next indexed packet and
  retain the existing exact pause/tick checks. Unknown, incomplete or changing
  files keep strict seeking. Tick-zero behavior remains unchanged.
- Evaluate camera and effects at the actual start on the saved timeline and
  report the skipped fraction. Reject starts beyond the authored shot end.
- Prepare frozen native previews directly from confirmed paused render
  telemetry, with no same-tick seek or console position calibration.
- Retain at most 120 view observations outside playback too, using the same
  monotonic clock as input/graphics diagnostics. No additional game reads.
- Document the camera-cache consistency lead, optional ReShade integration
  and Stage 3 export design. The native DLL/hook is unchanged; the renderer
  slowdown and overflow are not fixed here. General legacy calibration remains
  strict, and deterministic export still needs preroll for sparse recordings.

## 0.4.7 alpha — Paused flight return and replay names

- Closing F8 on a paused held/completed camera requests the acknowledged Flight
  handoff. It no longer claims movement input while manual flight is inactive.
  Active paths and frozen previews keep playing; busy transitions keep the panel.
- Wait for two fresh, stable paused views before positioning the spectator at a
  finished shot. Delayed pause acknowledgement no longer uses a stale running
  tick as its baseline. A later replay change still blocks the handoff.
- Match custom replay names with or without their final .dem suffix throughout
  startup and native playback, preserving dots. Other suffixes cannot alias the
  selected replay. The reported tv_record-specific rejection is not reproduced.
- Timestamp bounded input and graphics observations using one monotonic clock,
  retaining them after game closure for comparison with future slowdowns.
- The supplied particle/material DLLs match the earlier crash dump. They do not
  establish a safe renderer fix; severe slowdown and buffer overflow remain
  unresolved. Camera interpolation and render-time evaluation are unchanged.

## 0.4.6 alpha — Startup mouse handoff and source cleanup

- Apply the same explicit game HUD/cursor handoff on first flight and F9 return,
  preserving the pre-edit values for Stop / restore.
- Reconcile cursor confinement when the first camera view becomes ready, from
  the control worker. Preserve held movement and accumulated mouse input.
- Add optional raw-mouse/cursor diagnostics and distinguish guide/panel drawing
  time from time spent inside the original game Present call.
- Format owned C++ sources consistently with a pinned formatter, protected
  token/literal checks and an idempotence check. Vendor sources stay unchanged.
- The reported DX11 vertex-buffer overflow remains unresolved. No renderer
  allocation limit, camera interpolation or playback clock is changed.

## 0.4.5 alpha — Path guides, playback controls and range DOF

- Add numbered, projected camera guides and the authored spline in paused
  flight. Highlight the selected camera and hide guides during playback,
  console use, the game UI and loss of focus. F8 toggles Show path guides.
- Share in-game playback speed and Updates / s with the desktop controls.
  Native camera/effects still update at render cadence.
- Clear dropdown commit highlighting while preserving text editing and focus.
- Add synchronized r_dof_override_ranges and the related override controls,
  with four-value keys/restoration and a Range DOF preset.
- Include a readable/JSON supported-camera-cvar list in the portable package.
- Keep the established camera clock, interpolation and paused flight behavior.
  Expanded in-game curves remain Stage 2; export remains Stage 3.

## 0.4.4 alpha — Windows build correction

- Write BOM-bearing cleanup test fixtures explicitly as UTF-8 and compare the
  helper's normalized Windows path, including temporary folders with 8.3 names.
- Wait for the test cleanup helper to exit before removing its temporary
  directory. Application behavior and the native DLL are unchanged.

## 0.4.4 alpha — Session cleanup and crash diagnostics

- Remove generated plugin folders after the game exits, including when Dolly
  closes first. A background helper waits on the exact launched process and
  exits after cleanup; it does not load plugins or start another game.
- Retry cleanup for restored sessions and marked leftovers from older portable
  folders. Preserve current game search paths, external edits, unknown files,
  links, diagnostic logs and recovery backups.
- Add bounded, read-only DX11 retirement-queue and overlay observations to
  diagnostics. Retain the latest samples when the game exits or crashes.
  Unknown renderer builds skip the private probe without disabling the camera.
- Add sustained graphics-resource lifetime checks to the Windows build gate.
- The reported DX11 vertex-buffer overflow is still unresolved. Camera paths,
  render timing, paused movement and mouse handling are unchanged.

## 0.4.3 alpha — HUD visibility and overlay input

- Returning from F9 hides both the replay controls and the character HUD.
  Opening F9 shows them again with game mouse interaction. Stop restores the
  original HUD/cursor values.
- Read original UI settings once and apply each return transition once,
  removing repeated console writes during flight entry.
- Keep Win32 mouse-capture/cursor changes out of the Present input queue.
  Hidden Dolly panels no longer receive mouse releases intended for Deadlock.
- Preserve the existing native camera interpolation and frame-synced effects.

## 0.4.2 alpha — Replay UI, capture and restart fixes

- F9 explicitly shows the replay HUD and releases the game cursor. Returning
  hides the replay controls before native flight takes over. Unsupported
  console commands are no longer mistaken for confirmed capabilities.
- F7 returns from the console to the previous UI. F8 returns to Dolly's panel.
  Alt-tab preserves input ownership; Stop restores the original HUD/cursor.
- Capture while replay time advances keeps the in-game keypress pose and tick.
  Desktop capture waits for a fresh paused rendered view. Both leave the
  replay paused, and capture after P restores native manual movement.
- Restart shots beginning at tick 0 when the replay explicitly identifies
  tick 1 as its first seekable packet. Camera and effect time start at the
  corresponding fraction of a second; saved keyframes are unchanged.
- Keep native path interpolation, frame timing and DOF evaluation unchanged.

## 0.4.1 alpha — Native editor startup and controls

- Wait for the paused replay view, DX11 panel and native input before entering
  flight, including when the launcher is in the foreground. A failed entry
  keeps the panel's console, Stop and retry actions live.
- Preserve held-key state when switching input ownership, preventing repeated
  F7/F8 toggles from one press or duplicate input messages.
- Match the in-game panel to the desktop palette with proportional text,
  grouped controls, a prominent capture action and a compact header.
- Select Replay timing by default and disable interval spacing until Timed
  shot is selected. Capture still uses the native press-time pose and tick.
- Keep the source packaging independent of local authoring context files.

## 0.4.0 alpha — Stage 1 connected editing

- Fix the Windows build's startup-test path comparison for short TEMP names
  such as RUNNER~1. The test checks the resolved, quoted replay path and keeps
  unlocker-before-replay ordering checks. Application behavior is unchanged.
- Add Home, Replays and Keybinds pages while retaining Dolly's existing style,
  camera/effect editing and supplied logo.
- Add one-click Play replay: connect to the launched process, require rendered
  pre-replay readiness and unlocker registration, confirm cvar_unhide once,
  then load the selected demo, pause it and enter native editing. Retain manual
  startup actions under Troubleshooting and provide cancellation/progress.
- Add saved action bindings, keyboard/mouse assignment, conflict checks,
  movement speed and mouse sensitivity. Migrate prior capture preferences;
  reserve F7 for console access. Validate additive display launch options while
  retaining -dev -insecure -console and the managed startup sequence.
- Add native paused movement and mouse look in the rendered camera callback,
  avoiding repeated console position writes and paused spectator-height
  calibration. Capture from the native event's displayed pose; taking a key
  does not stop manual flight.
- Add a DX11 in-game panel sharing the desktop project: capture/replace, saved
  views, replay and path controls, movement speed and input-mode switching.
  F8 opens the panel, F9 returns to the original game UI, F10 enters flight,
  and F7 gives the console input ownership before typing begins.
- Add input focus/lifecycle handling, event acknowledgment, retained camera
  poses during relative seek and explicit release before replay seeks.
  Preserve existing authored path interpolation and native DOF phase behavior.
- Introduce native bridge ABI 3. Build and extract a complete matching Windows
  package; earlier helper/editor combinations are not compatible.
- Include a Stage 1 manual test checklist. The new Windows/Deadlock workflow
  and ReShade coexistence remain unverified in the Linux development environment.
  In-world path markers, expanded in-game curves, video export and separate
  depth/world/hero/effect passes remain later stages and are not included.

## 0.3.13 alpha — complete September 9 module compatibility

- Add the supplied engine2.dll and tier0.dll builds to the reviewed fingerprints.
  Existing reviewed modules remain supported; unknown binaries remain blocked.
- Verify engine mapped sections differ only in debug metadata. Verify 3,530
  saved tier0 instructions, including the full typed setter, and inspect the
  current lookup/data-accessor ABI and unchanged interface slots.
- Report all mismatching game module names together in the launcher.
- Keep native camera and DOF timing, movement and interpolation unchanged.
- Distribute a complete source package, including native DOF sources/tests;
  this update does not depend on applying earlier incremental ZIPs.

## 0.3.12 alpha — September 9 client compatibility

- Support the reviewed September 9 client.dll alongside the September 7 build.
- Verify both complete camera functions against the previous disassembly: only
  six original calls moved to relocated helpers. Verify the relocated aspect
  helper retains its instructions and absolute import/data targets.
- Keep native camera/DOF timing, interpolation, offsets and ABI 2 unchanged.
- Keep exact engine2.dll and tier0.dll checks; unknown builds still stop before
  game configuration is modified. See GAME_UPDATES.md for update guidance.

## 0.3.11 alpha — native DOF curves

- Compile seven verified DOF controls with the camera path; evaluate them at
  the same main-view phase using typed native setters and immediate readback.
- Support fixed values, smooth/linear numeric curves, stepped switches and
  restore overrides. Reject unsupported native effect tracks before playback.
- Keep final camera/DOF held together until Play or Stop. Restore native effect
  snapshots on release, fault or expired editor heartbeat.
- Add tier0 fingerprint/interface checks and bridge ABI 2. Rebuild the complete
  Windows package; old editor/helper combinations are rejected.
- Add parser/parity, transport, replay lifecycle and native callback regressions.
  Camera clock/curve math, manual paused movement and unlocker startup remain.

## 0.3.10 alpha — restart a finished native shot

- Keep an unsettled final spectator handoff as a held view, not a failed shot.
- Play and Stop release the old native override with acknowledgement before
  seeking or restoring settings; neither requires that frozen spectator to
  converge. Reset stale position calibration on explicit release.
- Preserve strict handoff checks for competing manual camera writers.
- Add repeated-play, pause/stop and failed-release regressions.
- Native DLL, render callback, interpolation and paused movement are unchanged.

## 0.3.9 alpha — Windows native connection build fix

- Fix the confirmed Windows-only test/startup error: Python looked up
  `InterlockedExchange` as a kernel32 DLL export, which was unavailable.
  Export and call three compiled atomic wrappers from DollyNative.dll instead.
  Keep atomic memory barriers and shared-memory ABI 1.
- Verify the helper hash, x64 DLL format and protocol before using its exports;
  report incomplete or mixed-version packages clearly. Loading the helper in
  the editor does not call its game factory or install camera hooks.
- Require the atomic exports in the Windows packaging check. Retain and extend
  the real Windows named-memory test; add portable binding/error regressions
  and C++ atomic return-value/high-bit checks.
- Show a bounded test-log tail in Actions when Python regressions fail, while
  keeping the complete diagnostic artifact and stopping the build on failure.
- Camera paths, native timing, hook locations, manual paused movement, unlocker
  sequence and game compatibility fingerprints are unchanged from 0.3.8.

## 0.3.8 alpha — experimental native main-view camera

- Add a Native (experimental) camera driver selected before launch. Evaluate
  the complete saved path in the game's main-view callback, after normal camera
  setup and before the view matrices. XYZ, rotation and aspect zoom no longer
  rely on repeated console camera commands during native shot playback.
- Use the verified fractional game clock for replay shots and a high-resolution
  elapsed clock for frozen previews. Preserve existing spline shapes, shortest
  rotation, zoom interpolation and exact endpoints. The optional external
  smoothing filter remains available with the Console driver.
- Support only the exact client.dll and engine2.dll builds supplied for this
  investigation. Fingerprints, function bytes and view identity are checked
  before camera ownership. A game update requires a verified native profile;
  Console remains selectable before launch. No Valve DLL is distributed.
- Arm the native path before demo_resume. Hold the last view while verifying
  that the underlying spectator camera has caught up before releasing control.
  Manual paused flight, capture, preview and position calibration are unchanged.
- Keep the unlocker-before-replay startup, -dev/-insecure requirements and
  recoverable game configuration. Stop overriding if the editor disappears,
  the replay changes, the view is unsupported or replay time jumps.
- Add native telemetry, shared-memory protocol checks, C++/Python curve parity
  tests and a Windows callback smoke test to the executable build workflow.
  DOF and other effect cvars still use console updates at sampled native phase.
- Native helper cross-compiles for Windows x64. Actual game rendering,
  responsiveness and visual smoothness remain unverified pending user testing;
  this is an experimental alpha, not a demonstrated jitter-free release.

## 0.3.7 alpha — experimental shared playback smoothing

- Add Off, Light, Balanced and Strong smoothing under Shot playback. Balanced
  is the default for each editor session; the choice is not saved in settings
  or project files. Choose before Play shot; a running shot keeps its choice.
- Smooth the shared playback time over real-time windows of 0/80/160/280 ms.
  Steady motion adds approximately 0/40/80/140 ms of camera delay relative to
  replay action. Position, rotation, aspect and DOF/cvar tracks evaluate
  together on the authored path; Step tracks remain discrete.
- Let the filtered camera finish smoothly at the endpoint. Off retains the
  0.3.6 command sequence and endpoint fix. This update is cumulative for 0.3.5
  users; manual paused-camera movement, preview and capture remain unchanged.
- Add filter, playback and GUI regression coverage and smoothing diagnostics.
  Native improvement is not yet verified; compare the same 0.1-speed, 120 Hz
  shot with Off, Balanced and Strong before publishing. The console filter
  cannot guarantee smooth delivery inside Deadlock's renderer.

## 0.3.6 alpha — smooth the last fraction of path playback

- Remove a forced final-key jump when the replay reaches the end tick before
  the continuous camera clock reaches the final key. Complete both before
  normal pause/HUD restoration, with a bounded wait for the remaining camera
  movement. The supplied 0.3.5 recording showed about a fourfold final yaw step.
- Preserve the exact final camera/framing values and stop on a failed finish,
  cancellation, replay identity change or an externally initiated large seek.
- Keep paused-camera preparation, movement, focus handling and the verified
  0.3.5 seek correction unchanged. The low paused-update average in diagnostics
  includes idle time and does not establish slow active movement after Alt-Tab.
- Keep the authored position/rotation/framing curves and normal playback clock.
  This change targets the measured endpoint bump; it does not establish a fix
  for all mid-path renderer or recording judder. Native verification is pending.

## 0.3.5 alpha — recover from a paused refresh landing two ticks late

- Correct the 0.3.4 paused-controls startup failure seen in all five supplied
  attempts. The backward refresh succeeded, but its forward return stopped two
  ticks late, preventing original-view restoration and all manual translation.
- After three unchanged observations one or two ticks past a seek target,
  reassert pause, confirm the replay and position, and retry that exact target
  once. From the overshoot this is a backward seek. Keep the original polling
  deadline and require six exact target readings across a further pause.
- Ignore transient ahead readings; do not retry large or alternating offsets.
  Cancellation, changed demos and unexpected movement during confirmation stop
  recovery. A failed retry or weak camera response still blocks camera controls.
- Preserve correction evidence in diagnostics, including the original samples,
  observed overshoot and requested target. Keep 0.3.4's high-resolution motion
  clock and the existing camera paths, bindings, logo and Windows build recipe.
- Add transport-level regression tests for the observed seek behavior, original
  view restoration, saved-camera switching and movement in both directions on
  every axis. Native 0.3.5 behavior still needs a rebuilt Windows EXE and game test.

## 0.3.4 alpha — paused camera recovery and precise rotation timing

- Use the high-resolution performance clock for every camera movement,
  replay-clock sample and frame deadline. Windows Python 3.12's coarse clock
  produced repeated poses followed by 15/16 ms steps in the supplied EXE log,
  even with the high-resolution wait timer enabled.
- Refresh paused camera state with one adjacent-tick seek and a verified
  return to the original tick. Preserve the captured or selected camera view,
  then require the existing direct XYZ movement and return check. A same-tick
  seek alone did not reset the weak spectator response in the diagnostics.
- Allow one such recovery for a failed position check after normal Play/Seek.
  Cancellation, changed demos and lens errors do not trigger retries. A camera
  that still moves only partway remains blocked; correction bounds are retained.
- Let the first Capture after an external replay seek measure the new view.
  Retire stale paused-movement preparation and require fresh calibration before
  further manual movement. Reject a replay changing during the capture itself.
- Export motion-clock implementation/resolution and both refresh seeks in
  diagnostics. Retain authored path/rotation/framing interpolation and the
  previous Windows build-test corrections. Native 0.3.4 verification is pending.

## 0.3.3 alpha — build fix 1

- Correct six test failures caused by Windows expanding temporary-directory
  short names such as `RUNNER~1` into their canonical long paths. Continue
  checking the complete expected log paths and replay command.
- Exercise separator rejection without trying to create filenames containing
  quotes or control characters that Windows forbids. Keep real-file command
  checks for semicolons and plus signs; also cover carriage return and NUL.
- Pass all 460 local tests. A separate filesystem-assumption reproduction
  fails with the original six failures/one error and passes all seven repaired
  cases. This is a Linux simulation, not a completed Windows executable build.
- Change four test files and two documentation files only. Retain the existing
  Windows test/build/icon/GUI gates, executable version, application and assets.

## 0.3.3 alpha — executable packaging

- Add a Windows x64 PyInstaller recipe for direct `Dolly.exe` startup, with
  embedded supplied logo, version resources and bundled Python/Tcl/Tk.
- Keep portable logs and recovery journals beside the executable; resolve
  bundled resources separately. Own windowed startup logging in the same
  process, avoiding a recursive Python bootstrap or an extra console window.
- Isolate the external game's DLL search environment from the frozen runtime.
- Add File-menu recovery and `Dolly.exe --recover`, using existing recovery guards.
- Add a Windows Actions build with source regressions, PE/icon verification,
  and actual relocated bundle/editor smoke checks before producing ZIPs.
- Organize the source for GitHub with a concise README, user/build guides,
  explicit source archive list and ignored runtime/build data.
- Keep 0.3.2 camera timing, curves, paused movement and bindings. The native
  Windows build is prepared but was not executed in the Linux authoring workspace.

## 0.3.2 alpha

- Replace tick-edge camera-clock snaps with gradual phase correction shared by
  position, rotation, aspect and cvar curves. Retain bounded prediction and
  explicitly apply the final key when the replay reaches the shot end.
- Wait for repeated exact seek-tick observations before and after reasserting
  pause. A transient adjacent tick no longer causes the observed start failure.
- Refresh the current replay tick before paused camera preparation, retaining
  the visible pose captured before that refresh. Require a direct XYZ response
  and verified return; reject a weak paused response before continuous flight.
- Read back visible position periodically during paths and manual movement.
  Stop sustained large drift rather than accumulating an unseen camera target.
  Preserve independent startup calibration and bounded frame traces in exports.
- Use a dedicated high-resolution Windows frame timer when available, with
  bounded cancellation and normal event-wait fallback.
- Preserve saved shot timing/curves, capture bindings, logo, startup repair and
  the dev/insecure launcher. Pass 441 automated tests. Native Deadlock
  verification remains pending.

## 0.3.1 alpha

- Fix the reported Windows `Permission denied` error opening Dolly_startup.log.
  End the batch launcher's log redirection before starting the Python bootstrap,
  so it can open the output file and pass its handle to the hidden editor.
- Keep bootstrap launch errors recorded with a best-effort append after handle
  cleanup; retain the visible console error if the log is genuinely unwritable.
- Add regression coverage for both batch interpreter branches and log-write
  failure. Extend the existing error test to verify persistence.
- Retain 0.3.0 camera controls, saved shots, bindings, framing and supplied logo.
- Pass 407 automated tests. The full native Windows launch needs a local retry.

## 0.3.0 alpha

- Add a nonmodal Paused camera panel for switching saved views at the current
  replay moment. Apply camera pose, aspect and cvar-track values without seeking
  to the camera's authored arrival time or resuming the replay.
- Add manual camera movement independent of replay time, with GUI movement
  buttons and optional Windows in-game keyboard flight. WASD follows the camera,
  Space/Ctrl changes world-Z height, arrow keys turn, Shift boosts translation
  fourfold and Escape ends flight. Expose move and turn speeds in the panel.
- Check the selected replay and fixed tick before and after camera updates;
  stop if the tick changes or its state becomes unreadable. Reuse the paused
  position calibration rather than running it for every motion sample.
- Keep one camera writer and bounded movement steps after console stalls.
  Clear held GUI input on focus loss and suppress held game keys when enabling,
  returning to the game or replacing its process. Observe input without hooks
  or changing the user's game bindings.
- Cancel initial calibration when the panel closes or Stop controls is used;
  a pending console reply may finish before cancellation takes effect.
- Preserve the capture/replace workflow, automatic aspect restoration,
  `r_aspectratio` framing graph, supplied icon and dev/insecure launcher.
- Use keyboard look in this implementation. HLAE's native mouse and render
  hooks are documented as reference work, not copied as Deadlock offsets.
- Pass 405 automated tests, including simulated flight, input, pending-console
  ownership and GUI lifecycle checks. Native Deadlock verification is pending.

## 0.2.2 alpha

- Use the exact reattached film-reel image, preserving the source bytes.
- Encode every Windows ICO size as a 32-bit DIB with a complete AND mask,
  avoiding PNG-frame parsing problems in older Tk 8.6 Windows icon readers.
- Set the root window icon explicitly and the future-dialog default; only
  fall back to PNG if the Windows ICO fails, avoiding competing icon setters.
- Launch the GUI with the validated interpreter and CREATE_NO_WINDOW so the
  batch/Python console does not retain a separate taskbar button. Keep startup
  logging and show a Windows error dialog on GUI or logger initialization failure.
- Retain all camera, framing, capture-binding and saved-preference behavior.
- Pass 329 tests, including icon-format and console-free startup regressions.


## 0.2.1 alpha

- Add a saved Capture binding dialog for keyboard keys, Mouse4, Mouse5 and
  MiddleMouse, with optional required Ctrl/Alt/Shift modifiers. Ctrl+Alt+K
  remains the default; bare mouse buttons also work with movement modifiers.
- Store the binding per user outside the extracted program folder. Keep the
  enable switch off on startup. Validate settings and save atomically; preserve
  malformed settings before explicitly replacing them.
- Observe the selected input through Windows key-state polling without
  consuming game input. Require a fresh main-key press, suppress repeats and
  held-on-enable/focus-return captures, and scope capture to the launched game.
- Discard queued capture events after disabling or changing the binding, and
  retain busy/playback/modal guards.
- Apply the supplied logo as the window/dialog and taskbar icon, with a light
  backing for contrast, multiple Windows icon sizes and a PNG fallback.
  Keep an unchanged copy of the source artwork.
- Report unexpected input-listener shutdown in the status/log and uncheck the
  capture switch instead of leaving an apparently enabled but stopped listener.
- Retain all 0.2.0 framing, camera-path and development-launcher behavior.
- Pass 315 automated tests. Verify the changed toolbar/dialog in 10 actual Tk
  views at two DPI settings, including saved binding reload.


## 0.2.0 alpha

- Replace degree-based FOV control with `r_aspectratio` framing. Camera frames
  write the aspect ratio; capture and preview read it back. The old Camera FOV
  and Spectator FOV controls are no longer queried or written.
- Add a framing graph with camera selection, vertical value dragging, keyboard
  adjustments and a visible normal-aspect reference. Framing has its own Smooth,
  Linear or Step interpolation. Smooth uses a shape-preserving cubic curve that
  does not overshoot between key values. The graph uses a fixed 0.5–4.0 editing
  range; these are editor limits, not established native cvar limits.
- Default normal framing to 16:9, with presets and a custom standard. Capture
  keeps an explicit positive override. Automatic 0 resolves to the launched
  game's visible client aspect, or the shot's normal aspect if unavailable,
  and records the source. Zero is never a curve endpoint. Stop restores the
  exact original `r_aspectratio`, including automatic 0.
- Save version-2 projects with aspect keys, the normal aspect and framing
  interpolation. Import version-1 camera poses, timing and other tracks without
  recapture, retaining old FOV values as inactive metadata. Imported framing
  starts at 16:9 and must be authored again; no FOV-to-aspect conversion is guessed.
  Reject the former Camera FOV/Spectator FOV controls and duplicate aspect
  tracks at playback validation.
- Redesign the desktop interface around Session, Cameras and Camera variables.
  Keep capture actions, camera list, framing graph, selected-camera controls and
  path overview in fixed panels with a persistent compact playback bar. Move
  raw coordinates/timing to a separate dialog, remove whole-page horizontal
  scrolling and hide the activity log by default.
- Preserve paused position calibration, replay-synchronized playback, automatic
  HUD handling, native DOF tracks and the existing development-only launcher.
- Treat a normal game exit as Game closed instead of a startup failure.
- Pass 261 automated tests and render the actual Tk editor in 24 views covering
  compact/large windows, two DPI settings and the auxiliary dialogs.

The user verified the visible effect of `r_aspectratio` in freecam and requested
this replacement after the previous FOV controls failed to change that view.
That establishes the control's usefulness in the reported game session; it does
not establish an equivalent FOV-degree conversion or validate this release's
full animated transition. See VALIDATION.md for the release's checks and limits.

## 0.1.6 alpha

- Allow up to 12 bounded position-correction passes while checking progress,
  including holding an unchanged command so the camera can settle. This avoids
  the previous three-pass cutoff rejecting a preview whose horizontal position
  was still approaching the requested view.
- Accept a meaningful height response followed by a verified return to the
  requested position. The temporary height probe no longer requires the engine
  to move exactly one unit for every unit commanded.
- Apply the selected view's lens settings before measuring its position
  correction, and read back the selected FOV cvar. This includes FOV values
  changed with **Update view** in the editor.
- Keep the last successfully verified correction as the starting estimate after
  a failed check. Every subsequent Preview, Seek replay, or Play shot still
  verifies the position before completing its positioning step.
- Include the full requested preview frame, FOV and action error/status in
  diagnostics. Saved keyframes need no coordinate edits or recapture.

The user's 0.1.5 diagnostics show that the editor saved and sent the requested
75-to-40 FOV change. The reported errors came from the position check: one
horizontal residual decreased from -5.8 to -4.2 to -3.1 units before the old
attempt limit rejected it, with height already within 0.2 units. Another height
probe moved 11.3 units for a 16-unit command and returned to the requested
position, but the old response test rejected it. These checks are corrected;
the resulting preview behavior still needs confirmation in Deadlock. The
position correction remains a measurement at the initial view, reused during
playback; this does not establish correct compensation for every animated
camera-offset or lens setting throughout a shot.

The complete suite passes **215 tests**, including seven new preview-response
regressions and a UI edit-to-preview FOV regression.

## 0.1.5 alpha

- Measure the position difference between a requested camera view and the
  game's `spec_pos` readback while the replay is paused. Compensate outgoing
  XYZ coordinates for a stable measured offset and verify the corrected view
  before Preview, Seek replay, or Play shot completes its positioning step.
- Check height response with a brief camera movement so that a fixed-height
  camera cannot pass simply because its starting position happens to match.
  Use bounded checks and correction attempts; inconsistent or unreadable
  positions stop playback before `demo_resume` instead of using a guessed offset.
- Reuse the verified correction for all updates in that shot, retaining one
  camera/status round trip per playback update. Check it again on each preview,
  seek, or play action and reset it when the game connection changes.
- Preserve saved keyframes and captured freecam coordinates. Existing shots
  need no manual height adjustment or recapture.
- Include camera-position requests, readbacks, correction and verification
  results in diagnostic exports to make further game-specific issues traceable.
- Add 16 camera-position regressions. The complete suite passes 207 tests.

The user's readback changed from `240.1 3816.2 421.3` to
`239.0 3815.0 478.7` after sending the exact reported `spec_goto` command: a
57.4-unit height increase with unchanged pitch and yaw, plus a small horizontal
shift. This confirms a position round-trip mismatch; it does not establish a
universal fixed offset or its native engine cause. The correction measures the
current session instead of hardcoding that observed value. This release still
requires in-game validation of the corrected camera height.

## 0.1.4 alpha

- Make **Play shot** start at zero every time, seek to the first captured replay
  tick, apply the camera/lens/cvar settings, then resume the demo in the same
  ordered command batch. Normal replay playback is the default. The old frozen
  mode remains available explicitly as **Frozen preview**.
- Automatically set `citadel_hud_visible 0` before playback and `1` afterward,
  including pause, stop, startup failure and playback error. If exposed, hide
  the replay controls too and restore their prior setting. Keep failed cleanup
  pending for retry after reconnecting. Automatic hiding can be disabled.
- Temporarily set the supported `engine_no_focus_sleep` to zero during a shot
  and restore its previous value afterward to avoid background-window throttling.
- Reduce playback to one acknowledged camera/status round trip per update in
  builds with the confirmed live status format. Send console delimiters and
  commands together in one socket write. Never queue unbounded camera updates.
- Estimate fractional replay ticks for smoother slow motion, capped at one tick
  ahead of the latest acknowledged position. Hold at that boundary if ticks stop;
  reset immediately on a reported rewind. The HUD remains hidden until the actual
  replay reaches the path end. This is not render-frame synchronization.
- Preserve continuous equivalent yaw/roll output across +/-180 degrees instead
  of introducing a numeric wrap jump into camera commands.
- Export the last playback project, selected speed/mode, and measured command
  rates/latency. Keep only the latest camera command/response in the diagnostic
  dictionary instead of retaining a new coordinate-named entry every update.

The user's 0.1.3 diagnostics confirm live ticks and all existing camera support
checks. They also show Frozen mode on every recorded play, variable console
cadence, and a roughly 4-second shot taking 40 real seconds, consistent with 0.1x.
Sampled video frames show stepped camera movement during a paused scene. These
fixes address identified software issues. Subsequent user feedback reports mostly
smoother paths and working replay playback, with a remaining camera-height
mismatch addressed in 0.1.5. Native camera update cadence can remain a limit of
this command-driven backend.

## 0.1.3 alpha

- Fix step 5 rejecting the actual `demo_info` metadata response. Replay identity
  and total duration are now parsed separately from the current playback tick.
  `playback_ticks` is never used as the live clock.
- Query bare `demo_goto` for current playback position. Timed captures, camera
  previews and frozen playback can also use verified replay metadata without a
  live tick. Replay-timed captures, seeking and normal playback require one.
- Make freecam capture the main workflow: Start path here, Add camera here and
  Replace selected camera. Timed shots default to three seconds between views
  and smooth spline interpolation. Existing coordinates and timing settings are
  collapsed; FOV, bank and arrival time stay visible.
- Add optional Ctrl+Alt+K capture while the launched game has foreground focus.
  The shortcut skips busy operations, path playback and modal dialogs. Capture
  works from a paused or playing replay and leaves it paused for the snapshot.
- Preserve an existing path on failed or canceled capture. Replacing a selected
  view retains its timestamp. Play path restarts at zero when the selected time
  is at the end of the path.
- Add metadata, live tick, capture workflow and shortcut regression coverage.
  The complete suite passes 162 tests.

The user's 0.1.2 export confirms unlocker completion for 626 commands and 2,896
cvars before replay loading. The new live-tick query, visible camera controls
and native Windows shortcut still require a local game test.

## 0.1.2 alpha

- Correct initialization order: game launch opens the pre-lobby/hideout without
  `+playdemo`. The editor exposes Connect, Initialize unlocker in hideout, Load
  replay and Check camera support as separate ordered steps.
- Require both cvar_unhide completion summaries before enabling replay loading.
  Wait for their actual completion messages, including delayed output after the
  echo acknowledgment, up to the initialization request deadline.
  Checking camera support no longer executes cvar_unhide after the demo loads.
- Use Netconsole by default, matching the successful connection in the user's
  0.1.1 diagnostics. Keep VConsole selectable for separate testing.
- Treat missing unlocker output as an unconfirmed result, not proof of a missing
  DLL. Retain bounded raw console history to expose delayed or unrelated output.
- Include recent sessions in diagnostic exports and record game exit codes/times
  in new journals. A later successful launch no longer hides older launch logs.
- Preserve -dev/-insecure and gameinfo backup/restoration. Initialization restores
  the original gameinfo on disk before the selected replay is dispatched.

The first recorded 0.1.1 failure was the Steam-running check, before process
creation. A later VConsole process was created, but its exit details were absent
from the attached diagnostic ZIP; the reported game crash is not yet diagnosed.
Netconsole echo was successful, while version and cvar_unhide returned empty.
The corrected startup order and actual camera effects still need a local test.

## 0.1.1 alpha

- Fix the launcher rejecting installations whose executable is `deadlock.exe`.
  The user's explicitly selected supported executable is retained. Folder and
  Steam discovery support both `deadlock.exe` and older `citadel.exe` layouts.
- Update running-game checks to recognize both names, including the recovery
  and pre-launch checks. Required `-dev -insecure` launch options remain enforced.
- Report missing executable, gameinfo and server files more precisely.
- Retain launch inputs and the error in diagnostics even when startup fails
  before a session is created. Background UI failures now reach the disk log.

The reported 0.1.0 failure occurred during installation validation, before any
game process was started or any game configuration was modified. Use the same
selected `deadlock.exe` and replay paths in 0.1.1. A non-C: Steam library is valid.

This fix does not establish Windows game startup, unlocker compatibility, or
visible camera behavior. Those remain first-run checks described in README.md.

## 0.1.0 alpha

Initial camera-path editor, replay controller, console transport, cvar tracks,
development launcher and recoverable unlocker mount.
