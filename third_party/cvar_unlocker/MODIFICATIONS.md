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

No cvar definitions, game offsets, camera behavior, rendering or assertion
handling are changed. The plugin still performs the same cvar unhiding through
the same official mechanism.

## Build

Built from the patched sources with MSVC 19.38.33134.0, x64 Release, static
CRT, C++20, against the pinned SDK commit above. The result is pinned by
SHA-256 in `THIRD_PARTY.json` and verified by the launcher and native bridge
before use. The unmodified upstream release binary hash is recorded there for
provenance.

## License

The upstream MIT license and copyright notice are preserved in `LICENSE.md`.
