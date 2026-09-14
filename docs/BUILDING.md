# Windows builds and publishing

Build and test release packages locally. Publishing source, creating a version
tag or publishing a GitHub Release must not start a hosted build. The retained
`.github/workflows/windows.yml` is manual-only; use it only if you explicitly
choose to spend GitHub Actions minutes. Do not rerun an old hosted failure as part
of the normal release process.

## Update the source repository

Use the complete versioned source ZIP or the matching Git checkout. Keep the
`dolly`, `native`, `tests`, `tools`, `packaging`, vendored dependencies and license
files together at the repository root. Review and commit the source changes,
then push the intended branch. Do not commit portable runtime folders, personal
shots, replays, logs or credentials.

Build locally using the commands below. The build creates both the native helper
and the desktop executables; package components must come from the same completed
build. Inspect `build/checks/` if a check fails. Keep verification enabled.
A successful build does not publish anything or prove in-game behavior; complete
the relevant [in-game checks](STAGE1_TESTING.md) before publishing.

Do not upload the game's `client.dll`, `engine2.dll`, `tier0.dll` or `server.dll`.
They are inspected from an installation and are not distributed with Dolly.
The official unlocker under `third_party` is a separate packaged component.

## Publish the tested package

Follow [UPDATING.md: For the publisher](UPDATING.md#for-the-publisher) for version,
asset and update-channel requirements. Create the release from the tested commit
on main and upload the generated Windows ZIP, source ZIP and `SHA256SUMS.txt`
from `dist/`. Upload the complete Windows ZIP, not just `Dolly.exe`.

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
the app's source runtime. The script stops if a regression, icon check, or
actual packaged-editor startup fails. Inspect `build/checks/` after a failure.

Successful outputs are under `dist/`:

- `Deadlock_Dolly_<version>_Windows_x64.zip`
- `Deadlock_Dolly_<version>_Source.zip`
- `SHA256SUMS.txt`

The portable ZIP contains a `DeadlockDolly` folder with `Dolly.exe`, `_internal`,
`Start_Here.txt`, `LICENSE.txt`, an example shot and `BUILD_INFO.json`. Users
only need to extract the folder and launch `Dolly.exe`; they do not run the
build commands or install Python.

## Generating a compatibility profile after a Deadlock update

`native/profiles/manifest.json` and `native/src/dolly_compat_generated.hpp` are
generated, not hand-edited. When Valve ships a new `client.dll`, run the packer
from the source root on a machine with the updated game installed:

```powershell
py -3.12 -m pip install pefile capstone
py -3.12 tools\generate_profile.py --game-dir "B:\SteamLibrary\steamapps\common\Deadlock" ^
    --previous native\profiles\deadlock-2026-09-09-complete.json ^
    --label 2026-09-11 --update-manifest
```

It writes `native/profiles/deadlock-<label>-complete.json`, refreshes
`manifest.json` and emits `dolly_compat_generated.hpp` with every reviewed
profile plus AOB signatures for the installed build. Review the diff: confirm
the main view setup, caller, `CViewRender` vtable, `SetGlobals` pointer and
aspect-source pointer, and that no view field offset changed. Then rebuild
`DollyNative.dll` and ship a release that lists the new hashes. A signature match
at runtime can adopt a byte-identical build, but a layout change always requires
this reviewed profile.

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

The result is an unsigned portable app with the [startup updater](UPDATING.md).
No installer or certificate-based code signing is configured. A successful
bundle startup or native callback test does not establish Deadlock camera
compatibility. Complete [STAGE1_TESTING.md](STAGE1_TESTING.md) on the supported
DX11 game build before publishing. The new in-game panel, native input and
ReShade coexistence have not been tested in the Linux development workspace.
Console remains available through Troubleshooting for the older workflow.

## Recovery and updating

Use **File → Recover game configuration** after closing Deadlock. This calls
the existing guarded journal recovery. If the editor itself cannot open, run:

```powershell
.\Dolly.exe --recover
```

For an older source installation, its original `Recover_Game_Config.bat`
still works. Recover before replacing old package files, and keep logs and
saved shots. Do not delete an older installation with a pending recovery journal.

## Maintaining the repository

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

## 0.3.9 Windows build correction

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
ReShade runtime DLLs and shaders are not bundled or downloaded by the build.
