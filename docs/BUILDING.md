# Windows builds and publishing

[Documentation](README.md) · [User guide](USER_GUIDE.md)

Build and test release packages locally. Publishing source, creating a version
tag or publishing a GitHub Release must not start a hosted build. The retained
`.github/workflows/windows.yml` is manual-only; use it only if you explicitly
choose to spend GitHub Actions minutes. Do not rerun an old hosted failure as part
of the normal release process.

## Update the source repository

Use the matching Git checkout (the release tag or `main`). Keep the
`dolly`, `native`, `tests`, `tools`, `packaging`, vendored dependencies and license
files together at the repository root. Review and commit the source changes,
then push the intended branch. Do not commit portable runtime folders, personal
shots, replays, logs or credentials.

Build locally using the commands below. The build creates both the native helper
and the desktop executables; package components must come from the same completed
build. Inspect `build/checks/` if a check fails. Keep verification enabled.
For routine development edits, use the [focused checks](internal/TESTING.md)
before requesting a release build; the full release gates remain required.
A successful build does not publish anything or prove in-game behavior; complete
the relevant [checks for the changed workflow](internal/TESTING.md#manual-integration-checks) before publishing.

Do not upload the game's `client.dll`, `engine2.dll`, `tier0.dll` or `server.dll`.
They are inspected from an installation and are not distributed with Dolly.
The official unlocker under `third_party` is a separate packaged component.

## Publish the tested package

Follow [UPDATING.md: For the publisher](internal/UPDATING.md#for-the-publisher) for version,
asset and update-channel requirements. Create the release from the tested commit
on main and attach the generated Windows ZIP from `dist/`; that is the artifact
users install and the only one the updater reads. Upload the complete Windows
ZIP, not just `Dolly.exe`. The Source ZIP and `SHA256SUMS.txt` stay in `dist/` as
local build records and are not release assets: source is public, so developers
use the tag or the repository itself.

For public updates, leave GitHub's pre-release box unchecked and designate the
release Latest; the version may still contain "alpha". Experimental builds
belong in pre-releases and must not be Latest. Publishing is a manual step and
does not run the Windows build workflow. Tags pointing to older commits can
still contain the former automatic trigger, so use the current tested source.

After publishing, download the Windows asset once and confirm it contains the
complete `DeadlockDolly` folder. Users extract the entire folder and open
`Dolly.exe`, with `_internal` beside it. GitHub's automatic source archives are
for developers.

## Build on your Windows PC

Install **Visual Studio 2022 Build Tools** with **Desktop development with C++**
(including the Windows SDK), **CMake 3.21 or newer**, and 64-bit Python 3.12 with
Tcl/Tk.
Your Python 3.12 installation can create the build environment. In a terminal
at the source folder, run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_windows.py
```

No environment activation is required. Build dependencies are isolated from
the app's source runtime. Run all commands from the repository root with that
same interpreter. `cmake` and `ctest` must be available on `PATH` (the CMake
installer can add them). The script stops if a regression, icon check, or actual
packaged-editor startup fails. Inspect `build/checks/` after a failure.

The Windows build commands run on a private desktop, so GUI and native overlay
checks do not open windows over your work. Their checks, console output and failure
status are retained. If desktop isolation cannot be verified, the build stops
instead of running visible UI checks. This affects build commands only; the editor
and its manually requested preview/self-test windows still open normally.


Successful outputs are under `dist/`:

- `Deadlock_Dolly_0.6.87-alpha_Windows_x64.zip` - the release asset
- `Deadlock_Dolly_0.6.87-alpha_Source.zip` - build record, not attached
- `SHA256SUMS.txt` - local checksum record, not attached

The portable ZIP contains a `DeadlockDolly` folder with `Dolly.exe`, `_internal`,
`Start_Here.txt`, `LICENSE.txt`, export guides, an example shot and `BUILD_INFO.json`.
The editor executable is `_internal/DollyApp.exe`; `Dolly.exe` is the public
launcher with embedded update recovery. Users extract the whole folder and
launch `Dolly.exe`; they do not run build commands or install Python.

Project credits are included at `_internal/CREDITS.md`, where existing Dolly
updaters can install and verify them alongside the runtime files.

## Refreshing the bundled FFmpeg pin

`tools/fetch_ffmpeg.py` pins the download URL, archive filename and SHA-256.
If a clean build fails because the dated upstream asset has disappeared, choose
an available **Windows x64 LGPL shared** asset from
[BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases), download it,
verify its SHA-256 against the release checksum, and update all three constants.
Keep the shared runtime DLLs and license notices; do not substitute a GPL build
or use the rolling `latest` URL with a stale checksum.

Prove the new pin using a fresh, empty cache directory so a local cached ZIP
cannot conceal a broken URL. For example (choose unused output directories):

```powershell
.\.venv\Scripts\python.exe tools\fetch_ffmpeg.py --cache build\ffmpeg-pin-check-cache --destination build\ffmpeg-pin-check
.\build\ffmpeg-pin-check\bin\ffmpeg.exe -version
```

Confirm the expected version/build configuration and notices, then verify the
packaged runtime during the normal release build. Preserve older verified cache
archives. Dated autobuilds can be pruned; a durable alternative is a publisher-
managed immutable mirror of the same verified archive. Changing the URL to that
mirror must retain the verified hash. Uploading a dependency asset is a separate
manual publishing action.

## Generating a compatibility profile after a Deadlock update

Follow the [game-update review workflow](internal/GAME_UPDATES.md) before changing
compatibility data. `native/profiles/runtime-contracts.json` records reviewed
runtime contracts; camera and audio profiles are also retained under
`native/profiles/`. A new module hash or a matching signature alone does not
review a changed layout or enable every optional feature.

After reviewing the saved inputs, regenerate and check the Python/native tables
from the source root using the same build environment:

```powershell
.\.venv\Scripts\python.exe tools\generate_compatibility.py --write
.\.venv\Scripts\python.exe tools\generate_compatibility.py --check
```

The generator owns `dolly/_runtime_generated.py`,
`native/include/dolly_runtime_generated.hpp` and the generated compatibility
headers; do not edit them directly. To verify saved evidence against an installed
game without launching it, run:

```powershell
.\.venv\Scripts\python.exe tools\generate_compatibility.py --check --verify-game-dir "B:\SteamLibrary\steamapps\common\Deadlock"
```

That check reads installed files; it does not establish live camera or export
behavior. The normal native build checks generated inputs before rebuilding the
DLL. Keep its matching metadata and runtime files together.

## What the build includes and checks

- CMake builds the x64 native helper with Visual Studio and runs its C++ tests.
  The build inspects the helper's architecture and required exports, records
  its hash, and packages it with its build profile in `_internal/native`.
  It never packages the game's client, engine, tier0 or server DLLs.
- PyInstaller creates a windowed x64 EXE with the existing nine-size ICO and
  version information. A PE inspection checks the GUI subsystem and verifies
  every embedded icon frame against the supplied asset.
- Python, Tcl/Tk and imported runtime modules live in `_internal`. The official
  game plugin is copied afterward and its pinned hash is checked, so it is not
  analyzed, rewritten or compressed as a Python dependency.
- The complete bundle is copied to a path with spaces and launched from a
  different working directory. Its self-test creates the real editor, loads
  the Windows ICO, reads the bundled unlocker, verifies data locations and
  closes. It does not start Deadlock or edit its game files.
- Logs and gameinfo recovery journals stay in `logs` beside the EXE. Settings
  stay in `%APPDATA%/DeadlockDolly/settings.json`, preserving action bindings and movement preferences.
  Assets are read from `_internal`; they are not used as a writable data folder.
- External game launch temporarily clears the bundled DLL search directory
  and removes bundled PATH entries from the child environment, then restores
  Dolly's directory. Source-mode game launch retains its existing behavior.
- Original component notices accompany the binary. Python's installed license
  and PyInstaller's COPYING file are copied from the actual build environment.

The result is an unsigned portable app with [desktop update notices](UPDATING.md):
the editor opens immediately, release checks run in the background, and every
download and installation requires an explicit **Update now** action.
No installer or certificate-based code signing is configured. A successful
bundle startup or native callback test does not establish Deadlock camera
compatibility. Complete the [relevant manual checks](internal/TESTING.md#manual-integration-checks)
on the supported DX11 game build before publishing. Cross-platform math tests do not validate
Windows injection or a Linux/Proton game session. Choose the camera driver in
Replay's **Launch setup**; Settings' **Troubleshooting & recovery** contains the
manual startup controls.

## Recovery and updating

Use **File → Recover game configuration** after closing Deadlock. This calls
the existing guarded journal recovery. If the editor itself cannot open, run:

```powershell
.\Dolly.exe --recover
```

Source users can run `Recover_Game_Config.bat` on Windows, or
`python -m dolly.launcher --recover` from the source root. Recover before replacing
old package files, and keep logs and saved shots. Do not delete an installation
with a pending recovery journal. The [user update guide](UPDATING.md) explains
**Update now**, explicit consent and recovery of an already approved update.

## Maintaining the repository

The [credits](../CREDITS.md) record contributors and upstream projects,
including [@paralin](https://github.com/paralin) for
Linux/Proton discovery and compatibility fixes. Keep that record current as
contributions are integrated.

Keep `SOURCE_FILES.txt` updated when adding a source, documentation, workflow
or asset file. Release exports use that list instead of copying the whole
working directory. The included export checks reject logs, recordings,
build folders and paths outside the repository.

The application version lives in `dolly/__init__.py`. The build generates
Windows version resources and output names from it. Update the release notes
and example filenames in this document when bumping the version.

Primary references:

- [PyInstaller: platform-specific builds](https://pyinstaller.org/en/stable/index.html)
- [Bundled resource and executable paths](https://pyinstaller.org/en/stable/runtime-information.html)
- [Windowed streams and launching external programs](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)
- [GitHub workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)

## Historical 0.3.9 Windows build correction

The 0.3.8 run compiled the native helper and passed both C++ tests, then failed
its Windows shared-memory test with `InterlockedExchange` not found. This update
uses Dolly-owned compiled atomic exports. Keep the test enabled and rebuild
locally. Python test failures print a bounded log tail and retain the complete
log in `build/checks/tests.log`.

## Video encoder build gate

The Windows workflow prepares Media Foundation on its Server 2022 runner.
CTest records a short synthetic H.264 MP4, reopens it to verify timestamps, and
checks finish, cancel and no-overwrite behavior. Missing media components or
encoder errors fail that gate; they do not get reported as a successful test.
The ReShade runtime DLL is installed separately. The crosire/prod80 shader
library and its notices are bundled under `third_party/reshade_shaders`.
