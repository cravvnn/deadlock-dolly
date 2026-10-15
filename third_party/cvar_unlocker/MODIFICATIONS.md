# Bundled cvar unlocker modifications

The bundled `bin/win64/server.dll` is a locally built modification of
[cvar-unhide-s2-citadel v0.5.2](https://github.com/Artemon121/cvar-unhide-s2-citadel),
which is MIT licensed (see `LICENSE.md`). The upstream project is not
affiliated with Deadlock Dolly and does not endorse this build.

## Why

The upstream plugin registers two of its own console commands during
`Connect` but never removes them on `Disconnect`. During process teardown, the
game's command registry can then reach a command object whose SDK reference was
already invalidated, producing an access violation on normal game exit.

## Changes

- `unlocker-build-6785.patch` (apply after the 6782 patch): adds the reviewed
  build6785 server and its configuration interface. Builds6782 and6785 use
  the same tier0 binary, so both exact server pairs remain supported. Unknown
  and crossed pairs are refused. Existing secure-mode and shutdown code is
  unchanged. Offline checks passed; live acceptance is recorded separately.

- `unlocker-build-6753.patch` (apply after the 6746 patch): adds the exact
  October5 server hash; the reviewed Source2ServerConfig001 secondary table
  moved +0x4240 while all three method bodies and the this-8 thunks are
  unchanged from 6746. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6753 live acceptance pending.

- `unlocker-build-6746.patch` (apply after the 6745 patch): adds the exact
  October4 server hash; the reviewed Source2ServerConfig001 secondary table
  moved +0x1000 while all three method bodies and the this-8 thunks are
  unchanged from 6745. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6746 live acceptance pending.

- `unlocker-build-6745.patch` (apply after the 6742 patch): adds the exact
  October2 hotfix server hash; the reviewed Source2ServerConfig001 vtable and
  all three method bodies are pure relocations of 6742 (slots +0x70). Connect
  and Disconnect keep the this-8 thunk ABI and the tick method still returns a
  float. Unknown module identities remain refused. Import, offline ABI and
  lifecycle checks pass; build6745 live acceptance pending.

- `unlocker-build-6742.patch` (apply after the 6739 patch): adds the exact
  October2 hotfix server hash; the reviewed Source2ServerConfig001 vtable and
  all three method bodies are unchanged from 6739. Connect/Disconnect keep the
  this-8 thunk ABI and the tick method still returns a float. Unknown module
  identities remain refused. Import, offline ABI and lifecycle checks pass;
  build6742 live acceptance pending.

- `unlocker-build-6739.patch` (apply after the 6731 patch): adds the exact
  October2 server hash and its relocated Source2ServerConfig001 vtable and
  methods (all three reviewed method bodies relocate byte-identically).
  Connect/Disconnect keep the this-8 thunk ABI and the tick method still
  returns a float. Unknown module identities remain refused. Import, offline
  ABI and lifecycle checks pass; build6739 live acceptance pending.

- `unlocker-build-6731.patch` (apply after the 6728 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6731 live acceptance pending.

- `unlocker-build-6730.patch` (apply after the 6728 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6730 live acceptance pending.

- `unlocker-build-6728.patch` (apply after the 6726 patch): adds the exact
  October1 server hash and its relocated Source2ServerConfig001 vtable and
  methods. Connect/Disconnect keep the this-8 thunk ABI and the tick method
  still returns a float. Unknown module identities remain refused. Import,
  offline ABI and lifecycle checks pass; build6728 live acceptance pending.

- `unlocker-build-6723.patch` (apply after the 6722 patch): adds the exact
  September30 server hash and reviewed secondary configuration interface.
  Connect/Disconnect retain their this-8 thunk ABI and the tick method still
  returns a float. Unknown module identities remain refused. The current
  tier0 imports, offline ABI and lifecycle tests pass; build6723 live startup
  and replay/shutdown acceptance are pending.

- `unlocker-build-6722.patch` (apply after the 6712 patch): retain the exact
  ICvar adapter and add the reviewed hotfix server hash/config vtable tuple.
  Version6722 preserves the Connect/Disconnect this-8 thunk ABI and float tick
  interval method. Unknown server hashes still refuse before registration.
  Dolly also checks the editing profile's server fingerprint before mounting,
  so a future server-only update produces a launcher error before game startup.

- `unlocker-shutdown.patch` (upstream commit
  `505547d58e4b66001406a8fa618548364f7cdb4d`): track a successful `Connect`,
  avoid registering after a failed one, hook the server-config `Disconnect` to
  unregister the two commands owned by this exact source while `ICvar` is still
  connected, invalidate those same objects, and pair `ConVar_Unregister` and
  `DisconnectInterfaces` with startup.
- `sdk-disconnect.patch` (alliedmodders `hl2sdk` commit
  `7ae489c1722dfb117ff080d6403d85e01e9c53c9`): `DisconnectInterfaces` clears
  the pointed-to interface storage instead of overwriting its own bookkeeping
  pointer.

- `unlocker-build-6712.patch` (apply after the source shutdown patch): adapt the
  pinned SDK to Deadlock build 6712's changed ICvar layout. Only the unlocker
  DLL's own interface pointers use the adapter; the engine's ICvar object and
  vtable remain intact. Exact tier0 and server hashes, the server-config vtable,
  and the used ICvar target addresses must match before registration. The
  existing reviewed legacy tier0 builds retain their original SDK path.
- The same patch removes a mandatory import of the deleted optional
  `g_bUpdateStringTokenDatabase` export. Legacy builds resolve their original
  flag by name; the reviewed new build disables that optional debug database
  tracking. Token hashing and cvar definitions are unchanged.
- Preserve the owned-command shutdown fix and secure-mode rejection; reject
  unknown layouts, clean up after adapter failure, avoid hooking repeated
  requests for the same server-config object, and protect the exact vtable
  slot address when installing the existing config hooks.

The plugin retains the same cvar unhiding operation. Camera and rendering
behavior are unchanged. Static layout facts are deliberately tied to the
reviewed module hashes; they are not a general compatibility fallback.

## Build

Built from the patched sources with MSVC 19.38.33134.0, x64 Release, static
CRT, C++20, with MASM for the adapter tail transfers, against the pinned SDK
commit above. The result is pinned by
SHA-256 in `THIRD_PARTY.json` and verified by the launcher and native bridge
before use. The unmodified upstream release binary hash is recorded there for
provenance.

Apply `unlocker-shutdown.patch` to the pinned upstream source and
`sdk-disconnect.patch` to its pinned SDK first, then apply the build patches
in the order listed in `THIRD_PARTY.json`, through `unlocker-build-6785.patch`.
The 6712 patch includes `CMakeLists.txt`
and offline tests. With the SDK at `sdk/`:

```text
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --parallel 1
ctest --test-dir build -C Release --output-on-failure
```

The resulting `build/Release/unlocker_shutdown_candidate.dll` is bundled as
`bin/win64/server.dll`. Compiler/linker output is validated before the manifest
and launcher/native hash pins are updated; never change pins to accept an
unreviewed binary.

Offline build, actual assembled adapter ABI tests, synthetic shutdown/lifecycle
tests and an installed-file PE import/export audit passed for this candidate.
Build 6712 startup, command registration and game shutdown remain pending a
bounded Dolly-owned replay test. No live compatibility claim follows from the
offline results.

## License

The upstream MIT license and copyright notice are preserved in `LICENSE.md`.

- `unlocker-build-6726.patch` follows6723. Adds the exact6726 tier0/server pair and secondary interface tuple. Unknown or mixed6726 pairs fail closed; existing reviewed builds retain their paths.
