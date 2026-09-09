# Validation — 0.3.9 alpha

Date: 8 September 2026.

This update fixes the confirmed Windows shared-memory startup error in 0.3.8.
It does not change camera movement, curves or timing. The correction has passed
local regression checks and Windows cross-compilation; a fresh GitHub Windows
build must execute the updated native and shared-memory tests.

## Confirmed failure in the supplied GitHub artifact

The supplied `native-build.log` shows successful MSVC 19.44 compilation and
both native CTest tests passing: `native_path_tests` and
`native_bridge_callback_smoke`. The subsequent `tests.log` reports 590 tests
run with exactly one error:

```
ERROR: test_windows_mapping_is_readable_by_a_second_handle_and_heartbeat_advances
native_bridge.py:107
self._atomic32 = kernel.InterlockedExchange
AttributeError: function 'InterlockedExchange' not found
```

Other logged camera exceptions occurred during negative regression cases; they
are not additional failed tests. The failing test exposed a real startup bug:
the production native connection uses the same Windows-only initialization.
The executable packaging stage was not reached.

## Correction

DollyNative.dll now exports `DollyAtomicExchange32`, `DollyAtomicExchange64`
and `DollyAtomicCompareExchange32`. These wrap the compiler's Windows atomic
operations, preserving memory barriers and return values. Python binds to
these explicit exports rather than assuming kernel32 exposes the operations.
The shared-memory layout and ABI remain version 1. See Microsoft's
[Interlocked intrinsic documentation](https://learn.microsoft.com/en-us/cpp/intrinsics/interlockedexchange-intrinsic-functions?view=msvc-170).

Before loading the helper, the editor checks its SHA-256, ABI metadata and x64
DLL format, then verifies its exported protocol. It uses an absolute DLL path
and restricted Windows dependency search. Loading the helper in the editor
does not call `CreateInterface` or start its game worker/hooks. Missing atomic
exports produce a clear incomplete/old-package error.

The original real Windows mapping test stays enabled on Windows. It now checks
all three functions, unsigned sequence values and a heartbeat beyond 32 bits.
Its heartbeat wait is bounded and polls for actual progress. Native CTest also
checks exchange/CAS return values and signed/high-bit behavior.

The build script still stops when any test fails. It now prints the final 150
lines of the test log in Actions, while retaining the full UTF-8 log in the
Windows-build-diagnostics artifact.

## Completed verification

- **599 Python tests run: 598 passed, one Windows-only test skipped on Linux.**
  Six new binding/loader tests exercise real anonymous mapped memory with
  C-callable fixtures, typed 32/64-bit arguments, high bits, missing exports,
  bad hashes/ABI/PE format, load failures and mapping cleanup.
- Two build-log tests run real child unittest processes, checking that failures
  remain fatal, the traceback is visible, output stays bounded, the entire log
  is saved, and UTF-8 survives an inherited ASCII environment.
- The packaging test rejects each missing atomic export individually.
- Updated DollyNative.dll and callback/atomic harness cross-compile for Windows
  x64 using Zig 0.14.1/clang. PE inspection confirms all five exports and no
  separate compiler runtime DLL dependency.
- Disassembly of the compiled wrappers shows 32/64-bit memory `xchg` and
  `lock cmpxchg` instructions, with no kernel32 Interlocked import lookup.
- Source comparison against 0.3.8 confirms the entire controller and the native
  view callback, clock, worker and game loader functions are unchanged.

The original 0.3.8 native CTests passed on the user's Windows runner. The
updated atomic checks, actual Windows ctypes loading, named mapping and final
packaged EXE startup still require the fresh GitHub workflow. Cross-compilation
and portable tests do not substitute for that execution. Live Deadlock visual
smoothness remains unverified, as documented for the native-camera preview.

## Applying the update

Apply `Deadlock_Dolly_0.3.9_GitHub_Update.zip` over the existing 0.3.8 source
repository and commit its contents. Start **Actions → Build Windows app → Run
workflow** on the updated branch. Re-running the old job would use its old
commit. Extract the complete resulting Windows package, including `_internal`.

The helper DLL and Python bindings must be updated together. Saved shots and
capture preferences do not need conversion. The source/archive manifest excludes
logs, recordings, game binaries and build intermediates. No GitHub repository
or public release was modified by this work.
