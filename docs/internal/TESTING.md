# Development checks

[Maintainer references](README.md) Â· [Current user documentation](../README.md)

Run the 15 core user-contract tests from the checkout root:

```console
python -m unittest discover -s tests -q
```

The portfolio prioritizes safe startup after a game update, preserving the game
install and user files, and completing native camera/Players recordings honestly.
Keep the 15 core contracts with a bounded set of related failure scenarios;
do not hide the retired suite behind a runner or test implementation text.

- `test_core_install.py` owns P01 reviewed-build admission and additive published
  definitions; P02 launch, journal-before-write and exact restoration; P03
  interrupted-session recovery; P04 deletion of only owned temporary files.
- `test_core_native.py` owns P05 native preparation/playback/watchdogs/handoff;
  P06 owned Players progress and drain-before-restore; P08 verified native seeks;
  P09 rendered-pose/tick capture and replacement.
- `test_core_workflows.py` owns P07 recorder admission and cleanup; P10 authored
  motion/retiming/native parity; P11 USD/AE camera export; P12 Follow/Bone and F9;
  P13 shot persistence/validation; P14 settings preservation/migration; P15
  verified update installation, recovery and explicit consent.

These tests use real product code and owned temporary files, with simulated
processes, console/native telemetry, network responses and deliberate I/O faults.
They check independently known bytes, numeric values, output data and event
order. P01 checks generated compatibility data as part of its normal run. The
standalone equivalent is `python tools/generate_compatibility.py --check`.

P10 compiles the actual C++ path parser/evaluator and checks Python-generated
paths against independent numeric expectations. Windows uses CMake and Visual
Studio 2022 x64, matching the release toolchain; missing tools fail the test.
Linux uses `g++`; without it, that portion warns and Python wire checks still
run. The release native gates remain necessary. If Tk cannot import, the updater's UI imports are isolated
behind test stubs. P15 exercises real consent policy with a fake notice widget;
no window is constructed and those imports are restored afterward.

[CORE_TEST_AUDIT.json](CORE_TEST_AUDIT.json) maps each contract to recent release
fixes, selected decision-function coverage, seeded faults and remaining gaps.
Two or more targeted faults per contract must be detected without parse/import
errors or unrelated invalid fixtures. Coverage is a smell check on those owning
decisions, not a whole-application percentage target or proof of correctness.
The remaining retired detailed suite remains in Git history; its full coverage is not
preserved by these 15 contracts.

Run these fast source checks for runtime changes. Keep live and packaged checks
proportional to the change:

- After each game build, qualify reviewed offsets/hooks in a local replay before
  publishing definitions or a release: ready paused camera, capture, four exact
  seeks, shot playback, F9 and exact file restoration on exit.
- For a release, retain native compilation/ctest, PE/ABI and packaged executable
  gates, plus affected output qualification when recording behavior changes. Verify
  Players/depth/audio/EXR output when their implementation changes.
- When updater/bootstrap ownership changes, qualify the actual Windows process
  handoff and file locks, including one update from the preceding release.
- When UI behavior changes, use packaged GUI smoke and inspect the changed
  interaction/layout. ReShade, Discord, replay repair, DLSS, graphics profiles
  and reconstructed audio receive targeted manual checks when touched.
- When a modeled protocol changes, run its maintained TLA+ checks and update the
  source/spec mapping. A model result does not replace implementation tests.

The source suite does not qualify Windows, live game/render/encoder behavior,
all GUI orchestration, abrupt process death or every protocol interleaving.
The audit records which checks actually ran and which remain unqualified.

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

## Focused regressions and temporary investigations

The core portfolio is a starting point, not a test-count ceiling. The maintained
source suite also retains replay repair, helper setup and Players scratch owner
regressions, plus two real child-process updater/startup schedules. These protect
publication-before-verification, foreign-file deletion, retry blockage and
recovery-resource races that the headless core and abstract models do not establish.
Run all maintained source tests with the discovery command above.

Before adding a permanent test, name its observable contract, plausible regression
and gap in current proof. Prefer the existing owner and independent expected
results. Add a new test only for a distinct risk; demonstrate that it detects the
intended defect. Temporary probes and one-off investigation harnesses belong in
ignored analysis/ directories, outside discovery and source exports. Promote
only durable regression proof. Do not delete valuable proof to meet a quota.

TLA+ remains a bounded protocol/interleaving check. Keep implementation witnesses
for critical model contracts and reconcile their mapping when code changes.
Model mutants establish model sensitivity; they do not establish code sensitivity.
Use targeted checks during development and one normal complete release build;
reuse passing proof while relevant inputs match. Live/recording checks need their
own current authorization and are selected for the changed integration boundary,
not automatically requested for every source-only repackage.


## Output ownership supplement

`test_output_ownership.py` adds three bounded owning contracts to the compact
portfolio: Players cancellation/encoder errors survive denied MOV cleanup and
both files are attempted; still PNG failures preserve unrelated partial files
and matte/preview bytes decode correctly; first-use ReShade preset failures
leave no incomplete final and retries preserve existing user edits. The worker
uses a real owned Python process with a local protocol stand-in, never FFmpeg or
the game. Deletion/row/write refusal boundaries are injected. These are distinct
gaps, not restored retired tests. Together with existing owners the suite has54
test methods. Private exhaustive/fault/process probes remain outside discovery.
