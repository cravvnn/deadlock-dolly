# Deadlock updates and Dolly

Maintainer reference. The current review procedure comes first; the later
build-specific sections preserve historical evidence and pending checks at
the time of each review. For setup and checking your installed build, use the
[user guide](../USER_GUIDE.md) and [current documentation](../README.md).

## Published build definitions

Ordinary build support no longer requires a new Dolly release. Reviewed data
for one build can be appended to `definitions.json` at the repository root and
pushed to the default branch; installed copies fetch it from the GitHub raw
endpoint, cache it beside `Dolly.exe` and use it on the next launch. See
[published build definitions](../DEFINITIONS.md) for the schema and the exact
publish command (`tools/publish_definitions.py`).

The published document covers binary identity and reviewed offsets only: the
client camera record, engine and Tier0 identities with their named layouts,
extra compatibility module pins, editing-configuration consumers and additive
stock-cfg provenance rows. It is strictly additive, so a release pin is never
revoked or reinterpreted.

Keep these release-locked unless the corresponding reviews are rerun:

- **The cvar unlocker.** Dolly's bundled `server.dll` is a patched copy of one
  exact game server binary, so a new `server.dll` always needs a rebuilt
  unlocker and a release. The feed deliberately cannot name a new server pin.
- Follow-anchor, clip-audio, particle and prop-creation tables, plus movie-audio
  capture, which stay gated to their exact reviewed identities.
- The bundled `assets/editing/stock-provenance.json` video-resource pins and its
  own integrity hash. They describe stock content, not a binary identity.
- Any new layout, field or call-chain change that still needs the normal native
  test cycle. Publishing a hash alone does not qualify a build.

## Shared reviewed contracts

`native/profiles/manifest.json` controls accepted module fingerprints and the
narrower feature pins. `native/profiles/runtime-contracts.json` now holds the
shared current preload, replay-camera, Follow and attachment definitions. Its
module identities reference existing reviewed profiles where available. Named
schema fields retain their evidence, stock getter bytes and audited code spans;
attachment field names/types and wire order are shared by Python and native code.
Older Follow correction profiles keep their own layouts and exact checked bytes.
Player capture profiles also keep the scene fingerprint, producer address and
calling convention, record layout, and exact renderer pairing together. This
avoids enabling a new scene build with an older producer entry point.

### Build 6821 (2026-10-26)

The October 26 update changes the client and server; the reviewed engine,
Tier0, scene, sound, resource, renderer and filesystem modules are unchanged.
The camera, replay, Follow, observer-services schema, attachment and preload
contracts use the current reviewed addresses and bytes. One stock startup
function changed after the section Dolly reads; its saved startup section and
containing instruction block are independently equivalent, without changing
the readiness conditions.

The named death-effect controller moved again. Its factory, RTTI vtable,
weight/update functions and named death-event consumer identify the current
object. The screen-option hook and complete relevant caller fragment retain
their instruction structure. Particle wrappers and their stock callees are
reviewed separately. The server interface has a new table and method tuple;
the rebuilt unlocker passes 737 actual-function pair/interface checks, with
the old implementation correctly rejecting the new pair. Stock cfg and video
resources match the current Steam depot content. These are offline proofs;
bounded runtime and rendered-output qualification remain separate.

### Build 6819 (2026-10-22)

The 6819 hotfix changes `client.dll` and `server.dll`; the reviewed engine,
Tier0, scene, sound, resource and renderer files are unchanged from 6817. The
6817-to-6819 complete-owner map relocates all 100 required owners (deltas 0 or
`+0xA0`). Camera wrapper/matrix, preload, Follow, schema, gameplay-effect,
confetti and attachment consumers were regenerated from that map. The server
Source2ServerConfig001 table and all three method bodies are byte-identical to
6817, so the unlocker is an additive exact-hash gate over the retained tuple
(`0x2b5a1b0`; slots `0x99d9e4/0x99d9f0/0x99ab40`), with 712 actual-function
pair/table checks. Offline verification passes; no 6819 live claim yet.

### Build 6807 (2026-10-20)

The next hotfix changes both client and server images. Required camera, startup,
Follow, gameplay and server-interface consumers are reviewed against the 6806
checkpoint. Updated code, named schema records and data-table addresses remain
separate from unchanged field offsets and interface layouts. Earlier profiles
keep their individual checked bytes and admissions; new hashes alone do not
qualify a build. The stock engine, Tier0, filesystem, renderer, scene, sound and
resource files are unchanged.

Stock configuration payloads remain unchanged; provenance records the complete
current Steam depot map. The exact current unlocker pair and generated Python
and C++ artifact pins must agree. Console retains its independent configuration
and server checks while avoiding Native client-layout dependencies.

### Build 6806 (2026-10-20)

The Nurse Harrow update series changed both client and server code. The current
6806 profile adds reviewed camera, startup, replay, schema and Follow contracts,
plus separate gameplay-filter and weather entries. Earlier profiles retain their
own offsets and checked bytes. Scene, sound, resource, renderer, engine and Tier0
files are unchanged from the reviewed 6805 checkpoint; their admissions remain
unchanged. The unlocker adds the exact reviewed server/Tier0 pair and retains its
named interface, table and lifecycle checks.

Stock gameinfo, cfg and video payloads remain unchanged from the 6801 baseline.
The provenance metadata adds the complete installed 6806 Steam depot map.
Configuration dependencies are checked independently of Native camera support,
so Console Legacy can retain its manual route when only the native client layout
is unsupported. It still requires reviewed configuration and unlocker dependencies.
The specific early-development warning is disabled only in Dolly's editing
session; hero announcements and all ordinary readiness checks remain in place.

Offline layout and package checks do not certify runtime output or every new
hero effect. Bounded replay qualification must identify the actual installed
build, replay recording build, outputs and restoration result separately.

### Build 6796 (2026-10-19)

Build 6796 changed `client.dll` and the unlocker target `server.dll`;
`engine2.dll`, `tier0.dll`, `soundsystem.dll`, `scenesystem.dll`,
`resourcesystem.dll` and `rendersystemdx11.dll` are unchanged. All reviewed
client code/data anchors are byte-identical at their 6793 RVAs. The 6796 client
profile, Follow anchor, runtime contract and manifest pin were added. The
reviewed `Source2ServerConfig001` table and method bodies are unchanged from
6793, so the unlocker accepts 6796 through a hash-only patch of the reviewed
6793 binary. Live validation on 6796 covered Native camera startup, the cvar
unlocker, a 0.5x fixed-step game-audio take and a 0.5x reconstructed-audio take;
no live acceptance is claimed beyond those probes.

Use the unified generator from the repository root:

```powershell
python tools/generate_compatibility.py --check
python tools/generate_compatibility.py --write
python tools/generate_compatibility.py --check --verify-game-dir "<Deadlock install>"
```

`--check` is the default and detects stale generated files without writing.
`--write` validates saved reviews and regenerates the Python runtime constants,
native runtime definitions, Follow correction table, player scene table, camera
header and sound header. It needs no installed game and never changes accepted fingerprints or
approves a new build. Native builds run the check before removing the previous
DLL. Edit the reviewed inputs and generator, not generated files.

The optional `--verify-game-dir` reads installed PE files; it never launches or
attaches to Deadlock. It checks the current runtime contract's exact client,
tier0 and resource identities, saved code spans, and named observer-services
field against both stock getters. It also checks the installed scene/renderer
pair, player producer entry bytes and its saved complete code span where present.
It needs the build dependencies used by the
profile tools. This is offline evidence, not an in-game or output certification.

For a game update, first review the changed subsystems and retain the old
profiles. The existing client and sound profile tools remain discovery tools;
their output still needs review. Update the relevant saved contracts, evidence
and manifest pins, then regenerate all six outputs with the unified command.
Inspect the diff, run the Python/native checks, and verify the actual affected
workflow in a bounded owned replay before claiming new runtime support.
Never widen preload or an optional feature's support merely because camera
code was accepted.

Generation preserves each reviewed profile's values and feature gates; it does
not automatically discover private layouts or make Dolly update-proof. Historical
attachment branches and Depth target/calibration contracts remain independently
reviewed. The 6745 player scene candidate uses its reviewed producer at `0x5c8f0`
with the retained ten-argument ABI; older profiles keep their original entries.
Offline review and test passes do not certify live Players or Depth output.

## Historical review: Version6722 hotfix and Follow slide correction

Steam build25614556 uses client44a50bc2.../image40f5000. Exact original6712
and6722 PE files were compared privately; startup, replay, observer, camera,
entity resolver and effect wrapper RVAs/types are reviewed and updated. Other
six reviewed game modules and original gameinfo are unchanged. The separate
hotfix camera profile preserves older profiles and exact-build admission.

The stock normal crouch camera composition still initializes one intermediate
world anchor to zero; custom overrides leave it zero. Blending scales the hero's
world anchor toward map origin. Dolly's guarded correction scopes the actual
current ThirdPerson camera and full observer/tracked-pawn identity, then passes
a private input descriptor with the valid anchor into the exact faulty blend.
Angles, offsets, crouch weight, later ADS/sequence effects and collision remain
stock. No persistent game memory or installed game files are patched. Exact
module and live function bytes gate the resident detours. Missing ownership,
changed config/command, nonfinite data or another callsite passes through.
Optional diagnostics report scopes/blends/corrections/rejections for live proof.
This correction still needs bounded runtime and visual acceptance.

## Historical review: Build 6712 replay startup and schema printing

Production startup now waits for the reviewed normal replay camera handoff
before pausing. The selected replay and first-packet gates remain intact;
read-only, exact-build camera checks replace no existing safety checks.
The state-based starting view and responsive controls were visually accepted.

The stock detailed pawn-schema printer crashes while recursively expanding an
inline structure in this build (schemasystem+0xa4e3, null record). Attachment
queries now use `schema_dump_binding C_CitadelPlayerPawn`, whose reviewed
nonrecursive mode retains typed top-level rows. Existing presence, type and
offset validation remains; the other three schema queries are unchanged.
Bounded live verification returned m_angEyeAngles at4352 as QAngle and exited
normally. Roster/bone and export compatibility checks remain incomplete.

The entity-system global also moved outside the legacy attach-roster scan
window. The new exact-client path verifies initializer20049f0 and resolves
global3cfc7c0, requiring the reviewed CGameEntitySystem primary RTTI vtable on
each lookup. Existing player list, identity and handle validation is retained.
Unrecognized legacy clients retain their existing resolver; a recognized new
client with invalid initializer/type data refuses resolution.

Source-audio event capture worked, but the start-parameter structure shifted:
the initial amplitude/envelope float moved0x24->0x20 and the legacy rate/fade
parameter float moved0x30->0x2c. These offsets now belong to the selected sound
profile, including signature fallback; missing metadata refuses resolution.
The field at new0x30 is integer pitch and must not be read as the old float.
CSV columns retain their meanings; source_volume is not final mixed loudness,
and source_rate_parameter is not asserted to be pitch. Runtime retest pending.

## Historical review: Build 6712 offline compatibility (2026-09-29)

The September 29 profiles cover the camera wrapper and changed view fields,
engine replay accessors, ICvar interface, ten-argument scene producer, renderer
buffer/retirement layouts, audio voice map, particle wrappers, screen effects,
and preload completion. Exact module gates remain in place. The scene producer
requires its matching reviewed renderer because the GPU buffer member moved
from +0x60 to +0x70. CPU allocation pointers remain +0x18. Older camera, engine,
scene, renderer and audio profiles are retained; automatic preload and the editing
baseline target the new build.

The camera's new wrapper is exact-build only; it must not seed the old camera
signature fallback. Its profile generator verifies the reviewed camera functions,
RTTI and engine/tier0 identities before emission. The new unlocker includes a
DLL-local ICvar adapter for two removed virtual slots and tolerates the removed
optional string-token debug export. Its patch and provenance are bundled under
`third_party/cvar_unlocker/`; secure-mode and shutdown guards remain enforced.

The audio generator now emits current `soundsystem-2026-09-29.json` and retained
`soundsystem-2026-09-09.json` profiles. Resolution carries each profile's voice-map
offset and rejects ambiguous cross-profile matches. No game binary is needed to
regenerate the header from saved profiles.

These are offline-reviewed compatibility changes, not an in-game certification.
An owned replay startup, camera/F9 recovery, audio and layered export still need
runtime verification before release. No running game was inspected or modified.

## Historical review: Complete module scan and audio fallback (2026-09-28)

**Check game build** and Native launch now hash seven game modules in one scan:
`client`, `engine2`, `tier0`, `scenesystem`, `soundsystem`, `resourcesystem`, and
`rendersystemdx11`. The manifest lists the existing reviewed pins for all seven.
The details include observed hash, review date, newer-than-reviewed status and
the affected feature. Unknown or missing optional modules make the overall
report unsupported/incomplete but do not change the three-module camera launch
gate. Players capture, audio, preload and renderer diagnostics keep their own
runtime gates. The unlocker's `server.dll` is Dolly's bundled binary, checked
separately, not an installed game-module dependency.

Audio now has an exact-hash fast path and a fail-closed signature fallback.
`tools/generate_sound_profile.py` creates five unique masked instruction-prefix
signatures from the exact reviewed sound binary: four hooks plus the event-name
helper. RIP displacements and direct call/jump operands are wildcarded using
Capstone's operand offsets/sizes. Four decoded RIP-relative references, from
three independent functions, must agree on one aligned slot in writable,
non-executable `.data`. All five prologues are checked. Ambiguous/missing matches,
changed fixed bytes or disagreeing/out-of-range pointers leave audio disabled.
The native worker probes each loaded module base once, avoiding repeated scans
of an unsupported build. The offline Python scan still reports an unknown sound
hash as unreviewed; it does not claim that the native fallback has succeeded.

The saved `native/profiles/soundsystem-2026-09-09.json` preserves the signatures
after Steam replaces the binary. Regenerate its header with
`python tools/generate_sound_profile.py`; add `--game-dir <Deadlock>` only to
rebuild the profile from its exact reviewed binary. The normal client generator
also emits the sound header from this saved profile. It preserves optional
module pins instead of approving them during a client-only review. New sound
code/layouts require a fresh review and updated profile; prefix matches do not
prove every helper or data layout unchanged.

The generated client header and `dolly/preload.py::CLIENT_SHA256` now obtain
their hashes from saved reviewed inputs through the unified generator above.
Camera support accepts several older hashes; preload's code/layout review
accepts one. The manifest's `feature_pins` records that narrower requirement,
which the generator checks against the runtime contract. An older accepted
camera build therefore does not imply preload support. Existing tests also
cross-check the resource-system, native scene, sound and renderer pins. Review
each relevant subsystem before updating its inputs; do not copy the camera's
accepted hash list into preload.

Validation is offline: generated signatures are unique in the installed PE,
native resolution is checked against its full `.text` snapshot, and synthetic
tests cover moved functions/data, signed displacements, ambiguity, changed
prologues, truncation and invalid table targets. A bounded owned replay remains
necessary before claiming in-game capture behavior on a newly updated build.

## Historical reference: Compatibility scanner and generator — 0.5.3

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

No network request is made; the manifest is bundled. The current on-demand entry is **Settings → Troubleshooting & recovery →
Startup controls → Check game build**. The section above describes the
original three-module scanner; current scans include the optional modules.

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

### Historical profile generation procedure

The original client-only procedure has been superseded by the
[shared reviewed-contract workflow](#shared-reviewed-contracts). Profile
discovery does not approve new fingerprints. Review each affected subsystem,
regenerate the saved contracts, inspect and test the changes, then validate
the affected runtime behavior before publishing support.

## Historical reference: 0.3.13 compatibility

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

## Historical 0.3.13 installation procedure

1. Check out the matching tag or branch, or use a source ZIP from the same build
   if you have one. Either way the tree must contain the complete repository
   source, including native/src/dolly_effects.cpp and native/tests/effect_tests.cpp.
2. Upload its contents at the GitHub repository root, preserving folder paths.
   For browser uploads use batches: native first, dolly and tests next, then
   remaining folders/root files. Commit every batch before building.
3. Start a new Build Windows app run on the updated branch. Re-running an old
   failed run uses its old checkout and will not include the new commits.
4. Download the successful artifact and extract its Windows_x64 ZIP into a
   fresh directory. Keep Dolly.exe and the complete _internal folder together.
5. Check native startup, play a short path twice, animate DOF, and Stop / restore.

## Historical updater proposal (0.3.13)

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

Console's primary Play action uses the manual Startup controls rather than the
private-client automatic preload and replay-camera readers. Its configuration
admission is separate from camera layout admission: reviewed engine, Tier0,
filesystem and DX11 settings consumers, bundle integrity, current stock-content
provenance and the exact server/unlocker pair are still required. Native remains
blocked on unknown client layouts. Changed configuration content or consumers
and a changed server ABI remain independent update boundaries.

Dolly's temporary GameInfo sets the stock
`deadlock_early_development_warning_disabled` preference before the specific
early-development warning is constructed. The normal interactive intro retains
its existing verified phase2 advancement. Hero announcements and other prompts
remain available. If one blocks the hideout, the user dismisses it and
**Check startup again** reuses the same owned session and all normal checks.
Dolly does not dispatch input at an unverified screen or load a replay merely
because a timer elapsed. Normal-play warning preferences stay untouched.
