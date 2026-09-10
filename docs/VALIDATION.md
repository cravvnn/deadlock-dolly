# Validation — 0.4.0 alpha, Stage 1

Stage 1 is a test candidate for Windows and the reviewed Deadlock builds.
The existing rendered-view path interpolation and typed DOF evaluator are
retained. New code adds native manual flight, DX11 editor rendering, shared
input actions, automatic startup and a reorganized desktop editor.

## Verified in this workspace

- Python regression suite from a clean extraction of the full source ZIP:
  686 tests completed successfully, with one Windows-only skip. Coverage
  includes queued console/game-UI ownership, startup configuration races,
  capture snapshots and Stop recovery.
- Native path, effect and manual-flight evaluator tests run on Linux. Flight
  checks cover frame-rate independence, Z-up movement, diagonal normalization,
  mouse sensitivity, long-frame discard and preserved framing/bank.
- Fresh Windows x64 DollyNative.dll and both Windows test harnesses compile and
  link. PE architecture, required exports, static C++ runtime and the matching
  ABI 3 build metadata/hash are verified.
- The callback harness includes manual seed/hold, replay resume, effect phase,
  capture snapshot/ring overflow and acknowledgement-lock contention cases.
  These Windows callback tests were compiled, not executed here.
- The DX11 WARP harness checks actual panel pixels, context-state restoration,
  resize/backbuffer release and shutdown when executed by Windows CTest.
- All five desktop tabs were rendered at 1000x700 and 1500x1050 with normal and
  150% scaling using Xvfb. The layout fits without page-level scrollbars. This
  checks layout with available Linux fonts, not Windows font rendering.
- Dear ImGui files match their pinned upstream SHA-256 manifest. Full source
  packaging includes all new CMake sources, headers, tests and required notices.
  Supplied game DLLs, private settings, logs and recordings are excluded.

## Required Windows and in-game validation

No Windows executable, DX11 WARP harness, or Deadlock session has been run in
this Linux workspace. Cross-compilation does not establish that native input
routes, foreground focus, console closure, renderer state or ReShade work in
the installed game. The GitHub Windows workflow rebuilds the native helper,
runs CTest and Python tests, verifies the executable icon, and starts the
packaged editor. It does not launch Deadlock or publish a release.

Use STAGE1_TESTING.md for automatic startup, held camera/hero switching,
F7/F8/F9/F10, capture, Alt-Tab, 0.1x paths, repeated shots, supported DOF and
resize tests. Live results should accompany any pre-release. Stage 2 world
markers/full in-game curve editing and Stage 3 video/render-pass export are
not implemented by this candidate.

## Earlier validation records

# Validation — 0.3.13 alpha

Static review covers the supplied updated client, engine2 and tier0 files.
The new engine mapped sections match the previous engine except for debug
entry timestamps and CodeView PDB age. Code, data, exception entries, relocation
entries and image layout are unchanged. For tier0, 3,530 saved instructions
match, including every byte of the complete 633-byte typed setter. The current
lookup and data-accessor functions were disassembled and their ABI/table slots
reviewed; those RVAs and the scalar layout remain unchanged.

The new compatibility profile records these hashes and review evidence.
Startup retains both old and new reviewed module fingerprints. An unknown
build remains blocked, and all mismatching module names are reported together.
No other game DLL is a native-camera compatibility dependency.

The Windows EXE and current game are not executed in this Linux workspace.
GitHub Windows CTest and an in-game camera/DOF playback check remain required.

## Local 0.3.13 checks

- The actual three uploaded game files pass the updated launcher compatibility
  gate using the newly compiled helper and its verified metadata.
- Python suite: 616 tests, OK, one platform-dependent skip.
- Launcher tests cover unknown builds and reporting all changed modules together.
  Profile, launcher and native pins match for all three game modules.
- Windows x64 helper cross-compilation and PE export/import checks passed.
- Native camera callback and DOF writer match 0.3.12 apart from fingerprint
  gates. Camera controller, interpolation and paused navigation are unchanged.
- Full source ZIP checked against SOURCE_FILES.txt and every CMake source path;
  the complete DOF source/header/test files are included.

## Earlier validation

# Validation — 0.3.12 alpha

Static comparison reconstructed both previous camera functions from saved
disassembly and reproduced their recorded SHA-256 hashes. Against the supplied
September 9 client, the only six differing instructions were original call
targets shifted by 0x90. Each new target starts a PE runtime function.
The relocated framing helper has equivalent instructions and identical
absolute RIP-relative data/import targets. The globals setter and view table
matrix entry match. The image size and bridge offsets are unchanged.

The compatibility profile records the candidate hash and reviewed differences.
Engine and tier0 pins remain unchanged. This review does not establish that
the current installed engine/tier0 files match: startup still verifies them.
Current-game playback and the Windows executable are not run in this Linux
workspace; the GitHub Windows build and an in-game test are still needed.

## Local checks for 0.3.12

- Python suite: 615 tests run, OK, one platform-dependent test skipped.
- Native path and effect evaluator tests: passed on Linux.
- Windows x64 DollyNative.dll: cross-compiled, exports/imports and package
  fingerprint verified. Windows callback execution remains a GitHub CTest gate.
- Previous and new approved client fixtures accepted; a third unknown client
  rejected. Launcher, native bridge and profile client pins agree.
- Camera controller, paused movement, interpolation and native DOF sources
  match the 0.3.11 source archive byte-for-byte.

## Previous 0.3.11 validation

# Validation — 0.3.11 alpha

This revision adds native frame-phase DOF tracks to the working native camera.
It is a release candidate pending GitHub Windows gates and an in-game test.

## Verified implementation

The uploaded tier0 SHA-256 is
`b4300eb0abfe73e1e877516ab6b8bdd1a1bdb4ffc47c7515a349d0623b852f69`.
Disassembly identified VEngineCvar007's table and find/data functions, the
16-byte reference layout, scalar type/value fields, and the typed setter at
RVA 0x20ec60. That setter filters, clamps, increments the change count and calls
native change callbacks. Runtime checks enforce the exact module fingerprint,
interface table, entry points, types and flags. The client DOF graph builder
reads scalar values from the same value storage (including focus/aperture and
mode); these reads are recorded in the bundled compatibility profile.

Seven explicit effect IDs are accepted; no command strings, unknown variables,
per-user or server-controlled cvars enter this native writer. Camera and effect
coefficients are compiled once and published together using bridge ABI 2.
Every supported effect uses the camera phase and a typed readback. Failure
stops playback rather than reverting silently to console streaming.

The existing native phase/clock, camera evaluation and aspect conversion block
was compared verbatim against 0.3.10. Camera interpolation source, navigation,
pacing and manual paused movement methods are unchanged. Controller changes are
limited to native Play preparation, monitoring and the held-camera handoff.

## Checks completed here

- Full Python suite: 611 tests run, 610 passed, one Windows-only mapping test
  skipped. This includes the actual C++ path/effect evaluator parity runners.
- Final focused native suite after two additional regressions: 71 tests run,
  70 passed, the same Windows-only mapping test skipped. Added coverage checks
  fixed-value restore overrides and refusing a mismatched effect/camera phase.
- C++ evaluator compared with editor output at 394 times, including subtick
  samples, nonuniform smooth curves, exact step transitions and endpoints.
- Corrupt/truncated shot envelopes, unknown effect IDs, oversized counts,
  unsupported cvars and invalid switch/range values are rejected.
- Native controller checks cover no animated console writes, repeated DOF
  shots, retained final effects, Stop restoration and early rejection.
- Updated Windows x64 DLL and actual callback smoke-test executable compiled
  using Zig/Clang. PE architecture, exports and runtime dependencies checked.
- The Windows callback harness covers native scalar binding, shared phase,
  boolean/integer switches, held phases, wrong types, rejected setters, failed
  restoration/retry, explicit restore values and editor-heartbeat loss.
  It was compiled, NOT executed here. GitHub's CTest gate executes it on Windows.
- Source/update archive allowlists exclude logs, replays, personal settings,
  caches and all supplied game DLLs, including tier0.dll. The update is checked
  to reconstruct the full source from 0.3.10 exactly.

## Not established by these checks

No Windows EXE or game session was run in this workspace. Native cvar readback
is not a pixel-level verification of final GPU output. Neither the synthetic
harness nor static disassembly guarantees all game rendering conditions.
The earlier 0xC0000005 game exit still has no supplied crash stack; this update
is not claimed to fix that separate crash.

Before publishing, build a fresh Windows Actions run from this commit. Use a
focus/aperture pull with the DOF preset at 1x and 0.1x, check Frozen preview,
play the shot to completion three times, and verify Stop restores effects.
Export diagnostics if any phase/readback error appears. See NATIVE_EFFECTS.md
for the exact supported controls and user workflow.
