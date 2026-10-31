# Published build definitions

Dolly normally learns how to drive a Deadlock build from data compiled into a
release: the compatibility manifest, the native camera profile table, and the
engine/tier0 identities in `DollyNative.dll`. A game update used to mean a new
Dolly build every time.

Build definitions replace that loop for ordinary updates. One small JSON file
is published on the default branch of the repository:

```
https://raw.githubusercontent.com/cravvnn/deadlock-dolly/main/definitions.json
```

Installed copies fetch it in the background at start-up, validate it, cache it
beside `Dolly.exe` as `DOLLY_DEFINITIONS.json`, and use it for the next launch
and every launch after that. Nothing is downloaded when automatic update
checking is off, and a failed fetch changes nothing.

## What the file may change

Definitions are **additive**. A published entry can add an accepted build; it
can never remove or reinterpret something that shipped in the release.

| Gate | Published field | Effect |
| --- | --- | --- |
| Native camera hook | `client.profiles` | full reviewed camera record: hash, image size, RVAs, prologue, optional AOB signature |
| Compatibility scan | `modules` | extra accepted SHA-256 per game module |
| Replay layout | `engine.profiles` + `engine.layouts` | accepts a new `engine2.dll` that reuses a named layout |
| ConVar bridge | `tier0.profiles` + `tier0.layouts` | accepts a new `tier0.dll` that reuses a named layout |
| Editing configuration | `editing.configuration_modules` | extra accepted consumer hashes |
| Stock cfg provenance | `editing.stock_provenance_files` | extra accepted `(bytes, sha1)` per reviewed stock path |

The cvar unlocker is deliberately **not** in the document. Dolly's bundled
unlocker is a patched copy of one exact `server.dll`, so accepting a new server
identity from the feed would let a stale unlocker run. A `server.dll` change
always requires a rebuilt unlocker and therefore a Dolly release; see
[What still needs a release](#what-still-needs-a-release).

Everything else stays release-locked on purpose: follow-anchor, clip-audio,
particles and prop-creation tables are reviewed per exact build and are not in
the published document, and a published tier0/engine entry cannot widen the
movie-audio capture gate. The bundled stock-video pin in
`assets/editing/stock-provenance.json` is also still required, because it is a
content review rather than a binary identity.

## What still needs a release

Publishing definitions is not a universal bypass. A normal Dolly release is
still required when:

- **`server.dll` changed.** The bundled cvar unlocker is a patched copy of that
  exact binary, so it must be rebuilt for the new server. `tools/check_game_update.py`
  reports this as `CHANGED server.dll`.
- **The reviewed camera code shape changed.** If the camera wrapper, its field
  layout or the cache decoder no longer match the reviewed code, the native
  bridge needs new code (`camera_layout_revision`), not new data.
- **You need Follow, clip audio, particles, prop creation or movie audio for
  the new build.** Those tables are compiled and reviewed per exact build.
- **The stock video resources changed.** The pinned `cfg/video_defaults_*.txt`
  payloads inside `pak01.dll` are bundled, not published.

A client-only hotfix, or an engine/tier0 change that keeps the reviewed replay
and ConVar layouts, is the sweet spot: those need only the published file.

## Guarantees

- **Additive only.** Reviewed release pins are checked first and always stay
  accepted. Duplicate hashes are ignored rather than replaced.
- **Fail-closed.** An oversized, malformed, wrong-ABI or partially valid
  document is rejected whole. The bridge then keeps its compiled table.
- **No execution.** The document is data. It is never interpreted as code and
  never contains file names, URLs or commands.
- **HTTPS only.** The pinned `raw.githubusercontent.com` host is required, and
  redirects to any other host are refused.
- **Bounded.** 512 KiB, fixed table sizes, strict field types and exact key
  sets. The native parser has its own smaller limits.
- **Offline-safe.** No cache, no network or a bad document all behave exactly
  like the previous release.

## File format

```json
{
  "format": 1,
  "native_abi": 4,
  "updated": "2026-10-25T00:00:00Z",
  "notes": "human-readable note",
  "modules": {
    "bin/win64/engine2.dll": {"accepted": ["<sha256>"]}
  },
  "engine": {
    "layouts": {"6728": {"demo_global": "0x5b92b0", "...": "0x0"}},
    "profiles": [{"sha256": "<sha256>", "image_size": "0x906000", "layout": "6728"}]
  },
  "tier0": {
    "layouts": {"modern_6801": {"table": "0x313208", "setter": "0x20fea0",
                                "find": "0x6c6b0", "data": "0x6c5f0",
                                "data_slot": 41, "extended": true}},
    "profiles": [{"sha256": "<sha256>", "image_size": "0x401000",
                  "layout": "modern_6801"}]
  },
  "client": {
    "profiles": [{
      "sha256": "<client.dll sha256>",
      "image_size": "0x43bf000",
      "main_view_setup": "0x19e7770",
      "main_view_caller": "0x19e1194",
      "primary_vtable": "0x2782d90",
      "globals": "0x34331c0",
      "engine_client": "0x3e600d8",
      "render_fraction": "0x0",
      "camera_layout_revision": 1,
      "view_setup_delegate": "0x5e2ce0",
      "setup_prologue": "<64 hex characters>",
      "signature": "<optional lowercase hex>",
      "signature_mask": "<optional 0/1 bytes, same length>"
    }]
  },
  "editing": {
    "configuration_modules": {"bin/win64/engine2.dll": ["<sha256>"]},
    "stock_provenance_files": {
      "game/citadel/gameinfo.gi": [{"bytes": 1234, "sha1": "<sha1>"}]
    }
  }
}
```

Integers may be JSON numbers or `0x` hexadecimal strings. Unknown fields,
unknown module paths and unknown layout names are rejected.

The optional `signature` is only an identity check: it must match uniquely at
`main_view_setup`, and every offset still comes from the record. It lets a
build whose hash changed but whose reviewed function bytes and RVAs did not
keep working without re-reviewing the layout.

## Maintainer workflow

```powershell
# 0. Triage: what changed, and is this a data-only update?
python tools/check_game_update.py

# 1. Review the update as usual and generate the profile.
python tools/generate_profile.py --game-dir "B:/SteamLibrary/steamapps/common/Deadlock" `
    --previous native/profiles/<previous>-complete.json --label 2026-10-26

# 2. Append the reviewed build to the published document.
python tools/publish_definitions.py `
    --profile native/profiles/deadlock-2026-10-26-NNNN-complete.json `
    --editing --stock

# 3. Validate, review the diff, commit and push to the default branch.
python tools/publish_definitions.py --check
```

`--editing` copies the current `assets/editing/profile.json` consumer pins;
`--stock` copies `assets/editing/stock-provenance.json`. Both are optional and
only additive to what Dolly already accepts. The engine and tier0 layouts are
derived from the reviewed profile and matched against the published layouts; a
new layout is added automatically when the accessors moved.

Pushing the file is publishing build support. There is no server, account or
upload step. A rebuild of Dolly is still required for new *features*, and for
any build whose code layout changed enough that the reviewed record itself must
be regenerated by the normal native test cycle.

To withdraw published support, remove the entry and push again; existing
installs fall back to the release pins on their next successful fetch.

## Diagnosing

- Run `python tools/check_game_update.py` after every update: it merges the
  release pins with this document and says whether a data-only publish is
  possible or a release is required.
- The maintainer procedure is summarized in
  [the update reference](internal/GAME_UPDATES.md#published-build-definitions).
- **Settings → Troubleshooting & recovery → Startup controls → Check game
  build** reports the merged review state for the installed build.
- `DOLLY_DEFINITIONS.json` beside `Dolly.exe` is the last accepted document.
  Delete it to fall back to the release-only pins.
- `dolly_definitions.txt` is written into the session overlay beside the
  injected DLL. It is generated, removed with the session, and never user data.
