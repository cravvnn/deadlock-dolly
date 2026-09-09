# Deadlock updates and Dolly

## 0.3.12 compatibility

This patch adds the supplied September 9 client.dll to the reviewed builds.
The previous September 7 client remains supported. Both use the same native
camera hook, view layout, clock access and DOF bridge. Camera movement and
interpolation code were not changed.

The native loader verifies complete file fingerprints. A familiar function
prologue alone is not enough to approve a changed game. In this update, both
camera functions were reconstructed from the saved previous disassembly and
compared against the new file; the only changes were six calls to relocated
helpers. The relocated framing helper was separately checked for equivalence.
The bundled new compatibility profile records this static review.

The engine2.dll and tier0.dll checks still require the previously reviewed
files. If startup now identifies either of those files, supply a fresh copy
from the current installation's game/bin/win64 directory. Do not replace game
DLLs with older copies to satisfy Dolly's checks.

## Install this update

1. Apply the 0.3.12 GitHub Update ZIP to the repository root and commit it.
2. Run Build Windows app and download its successful Windows artifact.
3. Extract the inner Windows_x64 ZIP into a fresh directory. Keep Dolly.exe
   and the entire _internal directory together.
4. Launch a development replay through Dolly. Check camera connection, play
   a short path twice, and check an animated focus change and Stop / restore.
5. Publish the matching Windows ZIP after the updated game test passes.

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
