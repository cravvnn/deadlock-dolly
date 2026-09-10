# Stage 1 manual test checklist — 0.4.4-alpha

This is the Windows/Deadlock acceptance check for the new native-input and DX11
panel workflow. Automated tests, cross compilation and an editor startup check
are useful evidence, but none proves this integration in a running game.
**These manual checks have not been performed in the Linux workspace.**

Keep the previous working package and a copy of your saved shots. Use a fully
extracted, newly built 0.4.4-alpha Windows folder with its matching ABI 3 helper.
Record the source commit, BUILD_INFO.json version and supported game build with
results. Mark each result PASS, FAIL or NOT TESTED; do not treat an untested
check as a pass.

## 1. Packaging and launch

- [ ] The GitHub Windows workflow completes, including native tests, Python
      tests, logo verification and packaged-editor startup. No checks disabled.
- [ ] Dolly.exe opens from a folder with spaces, with the supplied logo.
      `_internal` remains beside it and no separate Python installation is used.
- [ ] Open Steam, close Deadlock and choose **DirectX 11**. Begin without
      ReShade or additional capture overlays so failures have a clear baseline.
- [ ] Home detects/accepts the installation. Replays refreshes `.dem` files,
      selection reaches Home, and a replay on another drive or spaced path works.
- [ ] Play replay reaches the hideout first. Unlocker confirmation occurs before
      `playdemo`; the chosen demo then loads and pauses ready for native flight.
- [ ] F7 console still works after automatic startup. There is no flight input
      while the console is visible.
- [ ] Cancelling during loading stops further automatic startup steps. It does
      not kill the game. Closing the game permits a clean new launch.
- [ ] A missing/unsupported helper or game fingerprint gives a useful error.
      Do not change fingerprints or substitute a game DLL to force this test.

## 2. Native paused movement and capture

- [ ] At a paused tick, WASD moves relative to view direction; Space/Ctrl changes
      height. Mouse look and arrow look are smooth on diagonal pans.
- [ ] Shift/Alt changes movement speed; Q/E changes bank. Mouse sensitivity and
      movement speed saved on Keybinds have the expected effect.
- [ ] Move low to the ground, capture, move high, capture, and preview both.
      Positions match the actual rendered view without a player-eye height jump.
- [ ] Capture a camera with its keybind while moving. The captured pose matches
      the keypress view, and flight continues afterward without another setup.
- [ ] Hold the capture key: it adds only one key. Replace changes the selected
      key at its existing time, rather than adding a duplicate.
- [ ] Replay timing is the default and spacing is disabled. Timed shot remains
  available for several views at one paused tick. Replay timing records
      distinct replay moments and rejects duplicate times with a clear message.
- [ ] Pause/resume replay time with P, then resume paused camera movement. Check
      that the manual camera does not jump when replay time begins advancing.
- [ ] Press P, then capture while time advances, using the keybind and desktop
      Capture separately. Each adds a camera and leaves the replay paused.
      Continue moving without reopening the paused-camera controls.

## 3. Input ownership and UI

- [ ] Repeat F9 open/close with both the replay controls and a spectated hero's
      HUD visible. Returning to Dolly hides both; F9 shows them again.
- [ ] Drag a replay control while the game UI owns input, release the mouse,
      and return to Dolly. The game's mouse interaction remains usable.
- [ ] Alternate F7/F8/F9, click and drag Dolly controls, and move the mouse while
      paused. FPS stays comparable to the same scene before opening the panel.
      Check diagnostics for repeated QueuePresentAndWait warnings if it drops.


- [ ] F8 opens the Dolly panel with a usable mouse pointer. Clicking a button
      does not also switch heroes or send a gameplay action underneath it.
- [ ] Panel capture/replace, saved-camera selection, replay controls and speed
      use the same shot and settings visible in the desktop application.
- [ ] PageUp/PageDown previews cameras at the CURRENT paused replay tick.
      F10 continues flying from that displayed camera without a vertical jump.
- [ ] F7 opens the console. Type commands containing W/A/S/D/P/Q/E and the
      capture key; camera movement, replay toggling and capture remain suspended.
- [ ] Close the console with F7 and separately with Escape. It stays closed;
      no duplicate toggle reopens it, and the previous UI returns. F8 resumes
      camera input; F10 enters native flight if the camera was released.
- [ ] F9 gives control to the original replay UI. Click to select a hero, then
      return through F8/F9. The new view seeds flight instead of the old shot.
- [ ] Repeat F9 → F7 → F7 and F9 → F7 → F8. Console closure returns to the
      expected UI, hero selection has a working cursor, and Dolly panel clicks
      do not reach Deadlock underneath. Repeat after Alt-Tab.
- [ ] Alt-tab while holding a movement key, release it outside the game, return
      and move again. No stuck movement, mouse delta burst or teleport occurs.
- [ ] Change capture to Mouse4/Mouse5 and an ordinary keyboard chord in turn.
      Restart Dolly and verify saved settings. F7 and exact duplicate bindings
      cannot be assigned to other actions.
- [ ] Resize/window/fullscreen transitions leave the panel usable. Test the
      display mode actually used for filming; record any input or render issue.

## 4. Existing camera paths and effects

- [ ] Open a previous saved shot. Key timing, position, rotation and aspect keys
      are retained. Desktop edits appear in the shared in-game camera selection.
- [ ] Reopen Dolly and replay a saved shot beginning at tick 0. If the game
      reports first full packet 1, playback starts at tick 1 without deleting
      cameras; all saved key times and later effect/camera phases stay intact.
- [ ] Play a straight path and a diagonal rotation path at 1x and 0.1x. Compare
      with the previous working native build using the same recording settings.
- [ ] Pause partway, stop/restore, play to the end, then play again at least
      three times. No hero cycling is required to restart the shot.
- [ ] Preview two different aspect ratios, then fly from the selected view.
      Framing is retained. Test a supported DOF focus/aperture curve and verify
      preview/flight transition, playback phase and restoration visually.
- [ ] HUD hides during Play shot when enabled, and returns after completion,
      Pause, Stop or a handled error. F9 restores original replay interaction.
- [ ] Comma/Period seeks from the current tick using the project's correct
      ticks/second. The displayed camera position is retained after each seek.
- [ ] Frozen preview still moves along a path without resuming the scene.
      Stop it before resuming replay time through the normal replay control.

## 5. Failure recovery and ReShade coexistence

- [ ] Close Deadlock during editing. Dolly stops issuing game actions and
      reports the closed session. Reopen a fresh editing session successfully.
- [ ] After a failed launch, close Deadlock and use File → Recover game
      configuration. Confirm the original installation remains usable.
- [ ] Export diagnostics after a failed action. Include the action, input mode,
      replay pause state and a short recording showing the problem.
- [ ] Only after clean DX11 tests pass, repeat startup, F7/F8/F9/F10, resize and
      representative shot playback with the intended ReShade version/preset.
      Record the exact version, configuration and result. This is a coexistence
      test, not a claim that Dolly supports all ReShade configurations.

Stage 2 world markers/splines and expanded in-game curve editors, and Stage 3
video/depth/world/hero/effect export are outside this test candidate. Do not
advertise them as available in its release notes or tutorial.

Publish the complete Windows ZIP as a **pre-release** only after the relevant
checks pass. Keep any untested configuration explicitly unverified. Send the
Windows-build-diagnostics artifact for build errors; use Dolly's Export
diagnostics for in-game errors. Game DLLs and replay videos are not included in
normal source or release packages.

## 0.4.4 cleanup and crash investigation

- [ ] Close Deadlock first: its citadel_dolly session folder disappears and
      the original game configuration remains restored.
- [ ] Close Dolly first: the session folder remains while Deadlock uses its
      DLLs, then disappears after Deadlock exits. The cleanup helper exits too.
- [ ] With Deadlock closed, Recover game configuration removes marked older
      session folders whose plugins are no longer mounted. Keep any reported
      conflict's backup; recovery must not overwrite external gameinfo edits.
- [ ] Reproduce paused movement and F7/F8/F9 switching. Export diagnostics
      before restarting Dolly if frame rate falls or Deadlock crashes.
      native_editor_runtime.graphics contains the latest sample and history.
      A supported renderer probe reports queue counts, frame markers and
      overlay counters; unsupported/unreadable/racing states are observations,
      not proof that the renderer is broken or that the camera is unsupported.
- [ ] Treat the vertex-buffer overflow as unresolved until the runtime evidence
      identifies its cause and a targeted change passes the same reproduction.
