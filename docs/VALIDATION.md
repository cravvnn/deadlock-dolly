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
