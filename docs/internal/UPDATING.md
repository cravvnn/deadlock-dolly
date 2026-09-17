# Published release updates

Dolly 0.5.6-alpha introduces the single-entry-point Windows updater. Earlier public
ZIPs and the unpublished 0.5.5 preview need one manual download of this package.
Future releases use the same startup updater. Source checkouts still use the
local build workflow.

## For users

Open `Dolly.exe`. Its startup window shows **Checking for updates...** before
opening the editor. **Open Dolly** skips a slow check. Only this executable is
shown at the top of the folder; its editor runtime stays under `_internal`.

Dolly checks GitHub's public Latest release. A newer Windows package downloads, is verified against GitHub's SHA-256
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
folder. A managed application file that was modified locally (for example a
hand-built DLL) is backed up in the update folder and replaced. An update still
refuses to overwrite an unowned file that conflicts with a new package. Keep
edited shader presets and external tools outside the bundled runtime to avoid
those conflicts.

## Interrupted updates and recovery

The updater verifies complete backups before changing installed files, journals
the transaction, and restores the previous files on replacement or startup-check
failure. Staging and backups are kept in a `.dolly-update-*` folder inside the
Dolly folder, never beside it. After a successful update the following launch
removes that folder automatically; it is kept only while a recovery is pending
or a transaction is unfinished. A verified download is reused on a retry instead
of downloading the same release again.

After an interrupted update, close Deadlock and open `Dolly.exe` again. This
self-contained launcher can recover even if the editor runtime in `_internal`
is incomplete. It starts a private copy of its embedded update worker, waits for
the old processes to close, restores journaled application files, then reopens
Dolly. No separate updater executable is installed beside Dolly.exe.

Recovery does not restore, replace or downgrade your preferences or shot files.
Keep the `.dolly-update-*` folder inside the Dolly folder and its verified
backups until recovery finishes. If the public Dolly.exe itself is damaged and
cannot start, the private `DollyUpdater.exe` in that recovery folder can be run
with `--plan plan.json --recover`; otherwise extract a fresh release into a
separate folder. Ordinary recovery needs only Dolly.exe.

## For the publisher

1. Use a new increasing version in `dolly/__init__.py` for every public build.
2. Build locally with `tools/build_windows.py`; do not build on GitHub Actions.
   The build includes Dolly.exe with embedded update/recovery, a file manifest, bundled
   FFmpeg, and matching native/Python components. Both GUI and updater smoke
   checks must pass.
3. Create a release from the intended commit on main, using a tag such as
   `v0.5.14-alpha`. Attach the generated
   `Deadlock_Dolly_0.5.14-alpha_Windows_x64.zip`, source ZIP and SHA256SUMS.txt.
4. Leave GitHub's pre-release box unchecked and mark the release Latest.
   "alpha" can remain in the title and version. GitHub must expose the uploaded
   Windows asset's SHA-256 digest before the updater accepts it.
5. Experimental branches should publish as pre-releases and should not be Latest.

Selection follows GitHub's Latest designation, not branch polling. The publisher
is responsible for designating a main-branch build as Latest. A push or source
ZIP alone does not trigger an update. The updater never downloads or builds source.
Do not replace an existing version's asset to distribute changes; publish a newer
version. If a newer build regresses, publish the fix with another increasing version.
