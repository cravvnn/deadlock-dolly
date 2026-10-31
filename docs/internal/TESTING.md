# Development checks

[Maintainer references](README.md) · [Current user documentation](../README.md)

Choose checks for the behavior being changed, then run its neighboring consumers.
Run from the product checkout root and select its qualified Python environment
explicitly. For the standard setup in [BUILDING.md](../BUILDING.md):

```powershell
$py = '.\.venv\Scripts\python.exe'
```

If a linked worktree uses a shared environment, set `$py` to the interpreter
identified by the active project instructions and keep the product worktree as
the current directory. For example, update-flow checks are:

```powershell
& $py -m unittest discover -s tests -p 'test_update*.py' -q
& $py -m unittest discover -s tests -p 'test_release_launcher.py' -q
& $py -m unittest discover -s tests -p 'test_release_notes.py' -q
```

These are the update-flow checks, not a replacement release command. For another
component, substitute its owning test file and include consumers of the changed
contract. Tests import sibling fixtures by bare names: do not use `-t .`.

| Changed area | Include in the selected checks |
|---|---|
| Update discovery, consent or worker | Updater, consent, notice, release launcher and notes; settings when persistence changes |
| Desktop component | Component tests plus its real GUI adapter, layout/theme and input consumers affected by the change |
| Controller, path, settings or shared test fixture | Owning tests during iteration; full source suite before completing a shared-contract change |
| Launch, configuration or recovery | Launcher, graphics profiles, session cleanup/recovery, atomic files and relevant controller consumers |
| Native compatibility, protocol or packaging | Generated compatibility, Python/native agreement, launcher and package tests; affected native targets when their inputs change |
| Documentation only | Changed commands/links and source-export membership; runtime tests only if executable behavior is affected |

If the affected consumers are unclear, run the full source suite. Search fixture
imports before changing a helper shared by other test files. Preserve independent
transport, lifecycle, platform and packaging checks even when their assertions
look similar. Reuse a passing result only while its relevant source, fixtures,
generated inputs and environment remain unchanged; record that identity.

The full source check for shared or unclear changes is:

```powershell
& $py -m unittest discover -s tests -q
```

For a separately requested Windows release, retain the full local source/native
and packaged-executable qualification in the normal builder. It includes the
full source suite, so reuse that proof when its relevant inputs match. Its gates
and forced native relink remain unchanged. Use the intended release version and
outputs, preserving published artifacts. No new skip flags or hosted builds are
part of this workflow.

```powershell
& $py tools/build_windows.py
```

When an older local candidate already uses the intended version number, pass
`--output-dir <new-folder>` to keep both candidates. An explicit output folder
refuses to overwrite an existing release archive or checksum file.

For new regressions, name the observable contract, a credible failing change,
and why existing proof misses it. Prefer one strong owning test with independent
expected values. Before consolidating, carry every unique case into its keeper
and demonstrate that the keeper fails for the intended regression. Avoid new
production seams used only by tests. Use virtual clocks for simulated waiting;
keep real timing, process and filesystem proof in the tests that own it.

Record elapsed time for changed suites and preserve failed attempts. Do not add
retries, broaden mocks or remove failure checks merely to get a passing run.

For FFmpeg color changes on Windows, build the small WARP recorder fixture and
provide the pinned FFmpeg path to the native integration test. This checks real
H.264 and lossless recordings, including the audio remux; it does not launch the
game. The Players and video-export suites also check alpha masters when bundled
FFmpeg is available. To include the Auto H.264 and HEVC NVENC paths on a system
with NVIDIA encoding available, set `DOLLY_TEST_NVENC=1` before the color test.

```powershell
cmake -S native -B build/color-check -DBUILD_TESTING=ON
cmake --build build/color-check --config Release --target video_color_smoke --parallel 2
$env:DOLLY_TEST_VIDEO_COLOR_SMOKE = (Resolve-Path build/color-check/Release/video_color_smoke.exe).Path
$env:DOLLY_TEST_FFMPEG = (Resolve-Path third_party/ffmpeg/bin/ffmpeg.exe).Path
& $py -m unittest discover -s tests -p 'test_video_color_integration.py' -q
& $py -m unittest discover -s tests -p 'test_player_layer.py' -q
& $py -m unittest discover -s tests -p 'test_video_export.py' -q
Remove-Item Env:DOLLY_TEST_VIDEO_COLOR_SMOKE, Env:DOLLY_TEST_FFMPEG
if (Test-Path Env:DOLLY_TEST_NVENC) { Remove-Item Env:DOLLY_TEST_NVENC }
```

## Manual integration checks

Select the checks for the behavior changed. Record the tested commit, Dolly
and game versions, driver, result and any limits. Automated checks and a
packaged editor launch do not establish in-game behavior. Use a local replay
launched through Dolly with its normal development and insecure-mode gates.

| Changed workflow | Observe in the actual integration |
| --- | --- |
| Startup and recovery | Selected replay reaches a ready paused camera; F7/F8/F9 preserve input ownership; normal exit restores session configuration. |
| Camera capture and selection | Capture/Replace retain the rendered pose and tick. In-game capture keeps playback running by default; enabling Pause after capture pauses it. Desktop capture waits for a paused view. |
| Replay and camera seeking | In Replay, select another saved camera and use Seek camera; verify its saved time and view. Typed replay seeks target replay ticks. In Campaths, Seek here uses shot time. |
| Path and curve editing | Desktop and in-game changes agree; handles and live guides reflect edits; Play shot, frozen preview and Stop / restore behave as documented. |
| Follow and Bone Picker | Change replay participants, check framing and motion, then return to Fly camera. Verify attachment identity, bone selection and any saved binding affected by the change. |
| Video, stills and layers | Verify completion, decoded frame counts, output layout and the affected depth/matte/audio behavior using the limits in the current export guides. |
| Updates | Verify the packaged notice, explicit Update now consent, busy-state refusal, restart and interrupted-transaction recovery without losing user files. |

Use the [current user guides](../README.md) for exact labels and options.
The Stage 1/2 checklists are historical integration records and may contain
retired UI, defaults and build instructions. Untested behavior remains
untested; do not carry a result to a different binary or game build.
