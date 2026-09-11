# Deadlock updates and Dolly

## Compatibility scanner and generator — 0.5.3

`native/profiles/manifest.json` is the single source of truth for the reviewed
builds. It lists the accepted SHA-256 pin for every reviewed `client.dll`,
`engine2.dll` and `tier0.dll`, plus the date each module was last reviewed.
The Python launcher, the native bridge and `tests/test_native_packaging.py` all
read the same pins, so they cannot drift apart.

On every Native launch Dolly re-hashes the installed modules and classifies the
build:

| Result | Meaning |
| --- | --- |
| Supported | Every installed module hash is listed for this Dolly release. |
| Unsupported | A module is not listed. The message names each module, the observed hash, the reviewed date and whether the file is newer than the review. |
| Incomplete | A required module is missing from the install. |

No network request is made; the manifest is bundled. **Home → Troubleshooting →
Check game build** runs the same scan on demand.

### The AOB fallback

A new game build is normally blocked until its profile is reviewed. When a build
is not listed, the native bridge may still recognize it if a reviewed profile's
wildcard signature matches exactly one location in `.text`. A signature is the
reviewed function body with every RIP-relative displacement and call/jump
operand wildcarded, so it matches only code that is byte-identical modulo
relocated addresses. On a match the bridge re-derives every other camera symbol
from the image (the direct caller, the `CViewRender` vtable through its RTTI
locator, the `SetGlobals` globals pointer and the aspect-source interface
pointer). If any symbol is ambiguous or missing, or the reviewed 32-byte
prologue changed, the build stays blocked. Layout changes still require a new
reviewed profile.

### Generating a profile after a game update

1. Run `python tools/generate_profile.py --game-dir <Deadlock> --previous
   <last-complete-profile> --label <YYYY-MM-DD> --update-manifest`.
   It requires `pefile` and `capstone` (see `requirements-build.txt`).
2. Inspect the generated profile and header diff. Confirm the main view setup,
   caller, vtable, globals and engine-client addresses and that field offsets are
   unchanged.
3. Rebuild `DollyNative.dll` on Windows x64 (see
   [BUILDING.md](BUILDING.md)) and ship a release that lists the new build.
4. Play a short path in the updated client and Stop / restore before publishing.

## 0.3.13 compatibility

This release supports the supplied September 9 client.dll, engine2.dll and tier0.dll.
The previous September 7 client remains supported. Both use the same native
camera hook, view layout, clock access and DOF bridge. Camera movement and
interpolation code were not changed.

The native loader verifies complete file fingerprints. A familiar function
prologue alone is not enough to approve a changed game. In this update, both
camera functions were reconstructed from the saved previous disassembly and
compared against the new file; the only changes were six calls to relocated
helpers. The relocated framing helper was separately checked for equivalence.
The bundled new compatibility profile records this static review.

The new engine2 has identical executable code, data, relocation and exception
sections. The mapped-section differences are debug timestamps and PDB age.
The saved tier0 setter/callback disassembly matches (3,530 instructions), and
its current lookup/data-accessor ABI and interface slots were reviewed.
All three game modules retain exact fingerprint checks. The game's server.dll
is not a native-camera compatibility dependency and is not bundled.

## Install this complete update

1. Extract Deadlock_Dolly_0.3.13_Source.zip. This is the complete repository
   source, including native/src/dolly_effects.cpp and native/tests/effect_tests.cpp.
2. Upload its contents at the GitHub repository root, preserving folder paths.
   For browser uploads use batches: native first, dolly and tests next, then
   remaining folders/root files. Commit every batch before building.
3. Start a new Build Windows app run on the updated branch. Re-running an old
   failed run uses its old checkout and will not include the new commits.
4. Download the successful artifact and extract its Windows_x64 ZIP into a
   fresh directory. Keep Dolly.exe and the complete _internal folder together.
5. Check native startup, play a short path twice, animate DOF, and Stop / restore.

## Future updates

This version does not automatically download or install Dolly updates.
The release page is https://github.com/cravvnn/deadlock-dolly/releases.

An update checker can notify users when a matching reviewed Dolly release
is available. Automatic delivery is separate from compatibility validation:
an updater cannot prove that changed game code still uses the same fields,
interfaces, calling conventions or rendering order.

A future implementation can detect installed module fingerprints, compare
them to compatibility metadata attached to a published release, and offer
that complete Windows package. Unrecognized builds should remain blocked in
Native mode until reviewed. Pattern scanning can help maintainers locate
relocated functions, but must not automatically approve an unknown layout.

Console mode remains a temporary alternative when Native is unavailable.
It uses console delivery and does not provide native per-render-frame camera
and DOF synchronization. The bundled unlocker must still work with that build.
