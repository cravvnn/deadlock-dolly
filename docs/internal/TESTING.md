# Development checks

[Maintainer references](README.md) · [Current user documentation](../README.md)

Run the 15 core Python source regression tests from the checkout root:

```console
python -m unittest discover -s tests -q
```

These automated, headless checks cover core source behavior. They do not qualify
an actual Windows build, game integration, or rendered output. Select targeted
manual checks for the behavior changed and record any limits. Add or change a
regression test when it materially protects an important behavior.

The suite covers project persistence and validation, camera interpolation and
rotation, replay timing and seeking, controller restoration, native path data,
settings preservation, game configuration recovery, camera export, and update
installation and rollback. Console/process responses are simulated. When Tk is
unavailable, the updater's UI imports are stubbed to exercise its file transaction;
this does not test the updater window. The retired detailed suites remain in Git
history. Prefer improving these core workflows over adding checks for every
implementation detail.

For a Windows release, use the normal builder with the qualified Python
environment described in [BUILDING.md](../BUILDING.md):

```powershell
& .\.venv\Scripts\python.exe tools/build_windows.py
```

The builder runs the source suite and retains its existing native compilation,
model checks, and packaged executable smoke gates. Those gates are separate from
the Python source tests. An explicit `--output-dir <new-folder>` preserves
an older candidate with the same version; it refuses to overwrite an existing
release archive or checksum.

The source-side TLA+ models can also be checked independently with Node 20 or
newer and the pinned npm dependencies:

```console
npm ci --prefix tools/tla --ignore-scripts
python tools/check_tla.py
```

[SPECS.md](../../SPECS.md) describes their scope and bounds. Passing a model
check does not establish source correspondence or in-game behavior.

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
