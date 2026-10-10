# Deadlock Dolly 0.6.36-alpha

Fresh versioned package for Deadlock build 6769, including current hero labels, Hyperline startup handling and hidden FFmpeg output steps.

This release gives the 6769 compatibility package a new version after 0.6.35 was already published. Feature code is unchanged from the verified 6769 candidate.

## Highlights

- **October 9 update support (6769).** *Before:* Dolly refused the updated game modules. *After:* reviewed compatibility profiles and a rebuilt unlocker allow replay startup and camera editing on 6769.
- **Current hero names and portraits.** *Before:* Baba, Solomon and Rat King were missing from Dolly's hero mappings. *After:* their names and portrait paths match the installed game assets.
- **Hyperline mounts survive editor startup.** *Before:* Dolly's temporary editing configuration could omit Hyperline content mounts. *After:* those mounts are carried into the editing session, with verified configuration replacement and recovery.
- **Hidden FFmpeg output steps.** *Before:* still assembly, Players/layer encoding, audio muxing and depth previews could open console windows or inherit the editor's DLL search path. *After:* these FFmpeg steps run hidden in a clean environment.

## Validation

The working-source build passed 1,922 Python tests (22 skipped) and all 26 enabled native tests. The hardware encoder smoke was excluded. Reviewed installed-module checks cover the client, engine, resource, scene, renderer, sound and unlocker target.

One bounded visible replay check on 6769 passed normal startup/preload, Game Follow, Player POV preparation/finish without recording, Native/Citadel DOF guards, Follow and health-panel restoration, replay disconnect and normal exit. Cleanup completed in 76 seconds; configuration hashes matched and the temporary deployment was removed. A Steam networking assertion during startup also appeared in the earlier baseline; it remains recorded in the validation evidence.

The distribution is rebuilt from an isolated source-manifest snapshot, with native/Python regression checks and relocated portable-editor/updater smoke checks. The rebuilt package binary is not separately live-certified; the live check used a working-source native build of the same production code.

## Limits

The historical replay does not contain the new heroes. Their names and portrait paths have offline installed-asset evidence only. This run did not record output or validate new-hero visuals, Players/Depth rendered output, audio capture, or DOF appearance. Existing Players appearance limits remain.

## Updating

Extract the Windows ZIP into a fresh folder and run Dolly.exe. Python and Tcl/Tk are bundled. Keep the previous installation until your normal workflow is checked. The Source ZIP contains the complete source manifest for this version.
