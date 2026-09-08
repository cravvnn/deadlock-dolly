# Validation — 0.3.8 alpha

Date: 8 September 2026.

This is an **experimental native-camera integration**, not a verified cure for
visible jitter. The helper has been compiled for Windows x64 and its path math
and editor integration have been tested here. Deadlock, Windows native callback
execution, packaged EXE startup and rendered visual quality have not been tested
in this Linux workspace. The included GitHub workflow runs the Windows checks
before producing an executable.

## Why this changes the delivery method

The supplied 0.3.7 diagnostics retained positional lag of approximately
155.72 ms with Light smoothing and 154.60 ms with Balanced, beyond each filter's
nominal delay. A single 64 Hz replay tick takes 156.25 ms of real time at 0.1x.
These were different shots, with rounded low-frequency position readback; the
numbers support investigating tick-dependent camera application but do not
prove its internal cause or form a controlled comparison of the filters.

Advancedfx's Source 2 camera path is applied inside the game view setup. Dolly's
new driver follows that architectural approach using independently verified
Deadlock addresses and Dolly's existing curve evaluator. It does not reuse CS2
addresses or repeatedly stream camera targets to the native helper. Sources:
[Advancedfx Source 2 view integration](https://github.com/advancedfx/advancedfx/blob/main/AfxHookSource2/main.cpp),
[Advancedfx camera paths](https://github.com/advancedfx/advancedfx/blob/main/shared/CamPath.cpp).

## Exact-build evidence

| Module | SHA-256 |
| --- | --- |
| client.dll | `c7d068857c617c9c41d2c501865a94d93c52f3081864623ae23146e495f3021b` |
| engine2.dll | `887201acec33837fdb18d73c04f8e0894971d26eebafe992a28a12fada118afb` |

Static disassembly independently verified the main `SetUpView` callback at
client RVA `0x16bcfb0`, its sole direct caller, expected view-render vtable and
main view at `this + 0x10`. The original function runs first; final XYZ, angles,
aspect and the corresponding FOV adjustment are written before the inspected
matrix-building code. Other caller/vtable combinations and the alternate
projection flag are excluded. Installed files are hashed before launch changes;
the native worker checks loaded module fingerprints and function bytes again.
Derived evidence is included in `native/profiles/`. Game DLLs are not included.

Replay time comes from the verified float current-time field at client globals
`+0x30`. It includes the engine's fractional time. The rounded context tick at
`+0x44` must not have interpolation fraction added to it: that would introduce
a full-tick discontinuity. Frozen previews use QueryPerformanceCounter instead.
The current-time scope and its actual cadence at this callback still need a
live probe. Float precision is also finite: near 1,800 replay seconds its
resolution is about 0.122 ms of replay time, or 1.22 ms of real time at 0.1x.

## Implementation checks

- Compile the complete project to an immutable bounded path once. The callback
  evaluates its XYZ, pitch/yaw/bank and aspect at the current phase. Existing
  Hermite/PCHIP, shortest-rotation, linear and step behavior are retained.
- Prepare and acknowledge a held first view, acknowledge Play while still
  paused, then send `demo_resume`. Native playback sends no repeated console
  position, rotation or aspect commands.
- Hold the final camera while the underlying spectator is repositioned once.
  Require three fresh original-view samples within position/angle tolerance
  before release. If it cannot settle, retain the hold and report the problem.
- Release ownership on expired editor heartbeat, replay change, seeking,
  unsupported view or time jump. Keep the hook DLL resident until game exit to
  avoid unloading code still referenced by the engine.
- Preserve the existing paused-flight, capture, seek and calibration methods.
  DOF and other effect cvars remain asynchronous console updates using sampled
  native phase. They are not synchronized to each rendered view.

Path evaluation performs no allocation. The full callback also reads game state,
uses atomic shared ownership and publishes telemetry; it is not claimed to be
lock-free or hard real-time. A native hook does not remove frame pacing, GPU,
recording or game-clock limitations.

## Executed checks and remaining gates

| Check | Result |
| --- | --- |
| Python regression suite, Python 3.12.13/Linux | 590 run: 589 passed, 1 Windows-only shared-memory smoke skipped |
| C++ evaluator compiled and exercised against Python curves | Passed, including random/nonuniform paths, wrapped angles, zoom and malformed payloads |
| Windows x64 native DLL cross-compilation, Zig 0.14.1/clang | Passed |
| PE32+ x64 DLL, required exports and no separate compiler runtime DLL imports | Passed |
| Actual callback synthetic Windows harness cross-compilation | Passed; execution awaits GitHub Windows CTest |
| Windows CTest, shared-memory smoke and packaged EXE smoke | Required by included Windows build workflow; not executed here |
| Live unlocker proxy startup, view cadence, handoff and visible smoothness | Requires the supplied game build and user testing |

The Windows callback harness calls the actual production callback against
synthetic memory. It checks sub-tick changes within one integer tick, initial
paused Play acknowledgement, hold/resume continuity, endpoint handling,
projection transformation, replay changes, time jumps, heartbeat expiry and
release after fault. It loads no Valve DLL and installs no game hook. It tests
callback logic, not that the game invokes that callback once per rendered frame.

All 62 Python/spec files parse with Python 3.10 syntax rules. Seventeen protected
paused-camera, positioning and seek methods are byte-identical to 0.3.7.
Release archives use an explicit 119-file manifest; logs, recordings, game DLLs and build intermediates are
excluded. The update archive is based on the complete 0.3.7 source archive.

## Before publishing

Follow [NATIVE_CAMERA.md](NATIVE_CAMERA.md) for the short comparison procedure.
Use the same saved straight/diagonal-pan shot at 1x and 0.1x, then check native
frozen playback and the handoff back to paused movement. Export diagnostics
immediately after any failure or visible step. A successful GitHub build is
necessary but does not establish smooth in-game rendering.

No GitHub repository or public release was changed by this work.
