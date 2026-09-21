# Changes

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
