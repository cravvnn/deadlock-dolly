# Deadlock updates and Dolly

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
