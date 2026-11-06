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

The 10 motion tests and five safety tests exercise project persistence and
validation; nonlinear camera curves, retiming and replay transport; playback and
Follow/F9 restoration; bone-camera persistence; native path parity; camera and
video export; settings preservation; journal validation; and verified updates
with partial-write and health-check rollback. Named scenarios within those
workflows cover important failures without restoring the retired detailed suites.

Console/process responses are simulated. With `g++` available, the path test
compiles the real C++ parser/evaluator and checks generated paths against
independent numeric expectations. Without it, that portion emits a warning;
Python wire assertions still run. When Tk cannot import, only the updater's UI
imports are stubbed; its file transaction code is real and its window is untested.

The [coverage and fault audit](CORE_TEST_AUDIT.json) records the comparison with
`c2f2381`, the 15 workflows, and known losses. The revised suite detected 29
deliberate faults. One additional single-guard mutation survived because a
second guard still blocked the operation; removing both was detected. These
are targeted checks, not an exhaustive mutation score.

The 15 workflows do **not** retain full-suite coverage. The headless measurement
covered 11.4% of Python application statements, versus 7.6% for the initial
reduction and 59.5% for the retired suite. The retired baseline had six failure
records, 38 error records and 594 skips on this host. Detailed GUI/editor, audio,
layer-processing and compatibility coverage remains outside these 15 tests;
the audit lists those limits. Important transaction functions have much stronger
coverage than that whole-application percentage, but executed lines alone do
not establish correctness. The retired tests remain in Git history.

Keep exactly 15 core workflows. Strengthen their independently checked scenarios
when an important regression is found rather than adding checks for every
implementation detail. A green core suite is not a Windows or in-game release
qualification.

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
