# Windows executable and GitHub setup

The source is ready for a portable Windows build. This workspace did not have
a native Windows builder, so **no compiled EXE is included in the source ZIP**.
The workflow below runs the real build and executable checks on Windows.

## Put the project on your own GitHub

1. Extract the source ZIP to a new development folder, separate from your
   working Dolly installation. Its README, `dolly`, `tests` and `packaging`
   directories belong at the repository root.
2. Create your own empty GitHub repository, for example `deadlock-dolly`.
   Commit the extracted files using Git or GitHub Desktop. Include the supplied
   `.github/workflows/windows.yml`, `.gitignore` and `.gitattributes` files.
   The unmodified unlocker DLL is intentionally included; keep it tracked.
3. Push the source to the repository's default branch. The source ZIP contains
   no game recordings, diagnostic logs, saved personal shots or credentials.
4. Open the repository's **Actions** tab, choose **Build Windows app**, then
   **Run workflow**. This also runs when you push a `v*` tag.
5. Open the successful run and download its **Deadlock-Dolly-Windows-x64**
   artifact. The artifact download contains the Windows release ZIP, source
   ZIP and `SHA256SUMS.txt`.
6. Extract the Windows ZIP and test `Dolly.exe` on your PC. Then create your
   own GitHub Release and attach the Windows ZIP and checksums for users.
   GitHub's automatically generated source archives are for developers.

The workflow has read-only repository permissions and never publishes a
GitHub Release or pushes source changes. You control the repository and
release visibility. Build logs and failure reports remain in the workflow run.

## Build on your Windows PC

Use 64-bit Python 3.12 with Tcl/Tk. Your Python 3.12 installation can be used
to create the build environment. In a terminal at the source folder, run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools\build_windows.py
```

No environment activation is required. Build dependencies are isolated from
the app's source runtime. The script stops if a regression, icon check, or
actual packaged-editor startup fails. Inspect `build/checks/` after a failure.

Successful outputs are under `dist/`:

- `Deadlock_Dolly_0.3.3-alpha_Windows_x64.zip`
- `Deadlock_Dolly_0.3.3-alpha_Source.zip`
- `SHA256SUMS.txt`

The portable ZIP contains a `DeadlockDolly` folder with `Dolly.exe`, `_internal`,
`Start_Here.txt`, `LICENSE.txt`, an example shot and `BUILD_INFO.json`. Users
only need to extract the folder and launch `Dolly.exe`; they do not run the
build commands or install Python.

## What the build includes and checks

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
  stay in `%APPDATA%/DeadlockDolly/settings.json`, preserving capture bindings.
  Assets are read from `_internal`; they are not used as a writable data folder.
- External game launch temporarily clears the bundled DLL search directory
  and removes bundled PATH entries from the child environment, then restores
  Dolly's directory. Source-mode game launch retains its existing behavior.
- Original component notices accompany the binary. Python's installed license
  and PyInstaller's COPYING file are copied from the actual build environment.

The result is an unsigned portable app. No installer, automatic updater or
certificate-based code signing is configured in this recipe. A successful
bundle startup check does not establish Deadlock camera compatibility.

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
