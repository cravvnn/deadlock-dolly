# Published release updates

Dolly 0.5.5-alpha is the first updater-enabled Windows build. Earlier ZIPs need
one manual download. Source checkouts still use the local build workflow.

## For users

Packaged Dolly checks GitHub's public Latest release when it opens. A newer
Windows package downloads in the background, is verified against GitHub's SHA-256
digest and its file manifest, then installs when the desktop has no unsaved shot,
operation, recording, open dialog or active Deadlock session. Dolly closes,
checks the replacement application and reopens at the same path. Shortcuts keep
working. There is no update while a game session is active.

Settings / Updates has a manual check and an automatic-update switch. Turning
automatic updates off leaves a downloaded update available through Restart to
update. Offline, incomplete, missing-asset and rate-limited checks leave the
installed application usable. Experimental pre-releases are ignored.

Preferences and ReShade configuration stay in `%APPDATA%/DeadlockDolly`.
Custom FFmpeg paths are saved there too. The bundled FFmpeg is found relative
to the running application, so updating or moving Dolly does not retain an old
bundled path. Export / FFmpeg runtime offers Save path and Use bundled FFmpeg.
An explicitly selected external FFmpeg or ReShade installation is not replaced.

Only files listed in the application manifest are updated. Shots, replays,
exports, logs and unknown custom files are preserved, even inside the install
folder. An update refuses to overwrite modified managed files or an unowned file
that conflicts with a new package. Keep edited shader presets and external tools
outside the bundled runtime to avoid those conflicts.

## Interrupted updates and recovery

The updater verifies complete backups before changing installed files, journals
the transaction, and restores the previous files on replacement or startup-check
failure. Staging and backups are kept in a `.dolly-update-*` folder beside Dolly.
Those folders contain update diagnostics and may be removed after confirming the
new build works; do not remove them while an update or recovery is pending.

After an interrupted update, opening Dolly attempts recovery. If Dolly itself
cannot start (for example, power failed during replacement), close Deadlock and
double-click `DollyUpdater.exe` beside it. This self-contained helper restores
the journaled application files and reopens Dolly. It does not restore, replace,
or downgrade the user's preferences or shot files. Recovery also needs the
`.dolly-update-*` folder and its verified backups to remain available.

## For the publisher

1. Use a new increasing version in `dolly/__init__.py` for every public build.
2. Build locally with `tools/build_windows.py`; do not build on GitHub Actions.
   The build includes DollyUpdater.exe, an application file manifest, bundled
   FFmpeg, and matching native/Python components. Both GUI and updater smoke
   checks must pass.
3. Create a release from the intended commit on main, using a tag such as
   `v0.5.5-alpha`. Attach the generated
   `Deadlock_Dolly_0.5.5-alpha_Windows_x64.zip`, source ZIP and SHA256SUMS.txt.
4. Leave GitHub's pre-release box unchecked and mark the release Latest.
   "alpha" can remain in the title and version. GitHub must expose the uploaded
   Windows asset's SHA-256 digest before the updater accepts it.
5. Experimental branches should publish as pre-releases and should not be Latest.

Selection follows GitHub's Latest designation, not branch polling. The publisher
is responsible for designating a main-branch build as Latest. A push or source
ZIP alone does not trigger an update. The updater never downloads or builds source.
Do not replace an existing version's asset to distribute changes; publish a newer
version. If a newer build regresses, publish the fix with another increasing version.
