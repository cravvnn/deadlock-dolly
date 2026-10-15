"""Build, inspect and smoke-test a portable Windows application. No publication."""
from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dolly import __version__
from dolly.launcher import UNLOCKER_SHA256
from release_files import bundle_zip, sha256, source_zip
from build_native import build_native, copy_native_runtime, reject_game_binaries
from fetch_ffmpeg import download as download_ffmpeg, stage as stage_ffmpeg


def write_version(path: Path) -> None:
    version = tuple(int(v) for v in __version__.split("-", 1)[0].split(".")) + (0,)
    if len(version) != 4:
        raise ValueError("Expected major.minor.patch version")
    path.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
      flags=0x2, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Deadlock Dolly'),
      StringStruct('FileVersion', {__version__!r}),
      StringStruct('InternalName', 'Dolly'),
      StringStruct('OriginalFilename', 'Dolly.exe'),
      StringStruct('ProductName', 'Deadlock Dolly'),
      StringStruct('ProductVersion', {__version__!r}),
      StringStruct('LegalCopyright', 'Copyright 2026 Deadlock Dolly contributors')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)\n''', encoding="utf-8")


def check_executable(executable: Path, icon: Path) -> dict:
    import pefile
    image = pefile.PE(str(executable), fast_load=True)
    try:
        if image.FILE_HEADER.Machine != 0x8664 or image.OPTIONAL_HEADER.Subsystem != 2:
            raise ValueError("Dolly.exe must be a windowed x64 PE executable")
        image.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        embedded = set()
        group_found = False
        for kind in image.DIRECTORY_ENTRY_RESOURCE.entries:
            group_found |= kind.id == 14
            if kind.id == 3:  # RT_ICON data, preserving the source DIB bytes.
                for item in kind.directory.entries:
                    for language in item.directory.entries:
                        data = language.data.struct
                        embedded.add(image.get_data(data.OffsetToData, data.Size))
        raw = icon.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", raw)
        if (reserved, kind) != (0, 1) or not count:
            raise ValueError("Invalid source ICO")
        for index in range(count):
            size, offset = struct.unpack_from("<II", raw, 6 + index * 16 + 8)
            if raw[offset:offset + size] not in embedded:
                raise ValueError(f"EXE is missing supplied icon frame {index + 1}")
        if not group_found:
            raise ValueError("EXE has no Windows icon group")
        return {"machine": "x64", "subsystem": "Windows GUI", "verified_icon_frames": count}
    finally:
        image.close()


def copy_runtime_licenses(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python LICENSE.txt is missing; use the standard Windows Python 3.12 installation")
    shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    candidates = [file for file in (distribution.files or []) if file.name == "COPYING.txt"]
    if not candidates:
        raise RuntimeError("PyInstaller's COPYING.txt was not found in its installed distribution")
    shutil.copy2(distribution.locate_file(candidates[0]), destination / "PyInstaller-COPYING.txt")


def run_regression_tests(root: Path, log_path: Path) -> None:
    """Keep the complete test log and expose failures in the Actions job output."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                           cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True,
                           env=environment)
    except subprocess.CalledProcessError:
        # Read after closing the writer, including when the child exits early.
        with log_path.open(encoding="utf-8", errors="replace") as log:
            tail = "".join(deque(log, maxlen=150))
        print("Source regression tests failed. Last 150 log lines:\n" + tail,
              file=sys.stderr, flush=True)
        print(f"Complete test log: {log_path}\n"
              "On GitHub Actions, download the Windows-build-diagnostics artifact for tests.log.",
              file=sys.stderr, flush=True)
        raise


def main() -> int:
    build_edition = os.environ.get('DOLLY_BUILD_EDITION', 'standard')
    if build_edition not in ('standard', 'environment'):
        raise ValueError('Unknown build edition')
    environment = build_edition == 'environment'
    if sys.platform != "win32" or struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Build Dolly.exe on Windows x64 or use the included GitHub Actions workflow")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use 64-bit Python 3.12 with Tcl/Tk for this release build")
    import PyInstaller
    import tkinter
    required = re.search(r"^pyinstaller==([^\s]+)", (ROOT / "requirements-build.txt").read_text(), re.M)[1]
    if PyInstaller.__version__ != required:
        raise RuntimeError("Install the pinned requirements-build.txt in the build environment")
    build = ROOT / "build"
    checks = build / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    native_info = build_native(ROOT)
    print("Checking source regressions...", flush=True)
    run_regression_tests(ROOT, checks / "tests.log")
    write_version(build / "windows-version.txt")
    print("Building the windowed executable and bundled runtime...", flush=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(build / "frozen"), "--workpath", str(build / "pyinstaller"),
                    str(ROOT / "packaging" / "dolly.spec")], cwd=ROOT, check=True)
    bundle = build / "frozen" / "DeadlockDolly"
    # Collect a flat editor runtime beneath the public self-contained launcher.
    if bundle.exists():
        if not bundle.resolve().is_relative_to(ROOT.resolve()) or bundle.is_symlink() or bundle.is_junction():
            raise RuntimeError("Unexpected bundle cleanup path")
        shutil.rmtree(bundle)
    shutil.copytree(build / "frozen/DollyAppRuntime", bundle / "_internal")
    print("Building Dolly.exe with embedded startup/update recovery...", flush=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                    "--name", "Dolly", "--paths", str(ROOT), "--distpath", str(build / "bootstrap"),
                    "--workpath", str(build / "bootstrap-work"), "--specpath", str(build / "bootstrap-spec"),
                    "--icon", str(ROOT / "assets/dolly.ico"), "--version-file", str(build / "windows-version.txt"),
                    "--add-data", str(ROOT / "assets/dolly.ico") + ";assets",
                    str(ROOT / "packaging" / ("environment_launcher.py" if environment else "bootstrap_entrypoint.py"))], cwd=ROOT, check=True)
    shutil.copy2(build / "bootstrap/Dolly.exe", bundle / "Dolly.exe")
    subprocess.run([str(bundle / "Dolly.exe"), "--updater-self-test", str(checks / "updater-smoke.json")], check=True, timeout=60)
    if not json.loads((checks / "updater-smoke.json").read_text()).get("passed"):
        raise RuntimeError("Embedded updater smoke test failed")
    third_party = bundle / "_internal" / "third_party"
    shutil.copytree(ROOT / "third_party", third_party, dirs_exist_ok=True)
    print("Staging bundled FFmpeg (LGPL)...", flush=True)
    ffmpeg_archive = download_ffmpeg(ROOT / "build" / "ffmpeg-cache")
    stage_ffmpeg(ffmpeg_archive, third_party / "ffmpeg")
    dll = third_party / "cvar_unlocker" / "bin" / "win64" / "server.dll"
    if sha256(dll) != UNLOCKER_SHA256:
        raise RuntimeError("The official unlocker changed during packaging")
    copy_runtime_licenses(third_party / "notices")
    native_report = copy_native_runtime(ROOT, bundle / "_internal" / "native")
    shutil.copy2(ROOT / "native" / "vendor" / "minhook" / "LICENSE.txt",
                 third_party / "notices" / "MinHook-LICENSE.txt")
    shutil.copy2(ROOT / "native" / "vendor" / "imgui" / "LICENSE.txt",
                 third_party / "notices" / "Dear-ImGui-LICENSE.txt")
    shutil.copy2(ROOT / "native" / "vendor" / "reshade" / "LICENSE.md",
                 third_party / "notices" / "ReShade-API-LICENSE.md")
    video_guide = (ROOT / "docs" / "VIDEO_AND_RESHADE.md").read_text(encoding="utf-8")
    # Maintainer notes stay in the repository; point their links at GitHub.
    video_guide = video_guide.replace(
        "](internal/", "](https://github.com/cravvnn/deadlock-dolly/blob/main/docs/internal/")
    (bundle / "Video_and_ReShade.md").write_text(video_guide, encoding="utf-8")
    release_notes = ROOT / "docs" / f"RELEASE_NOTES_{__version__}.md"
    if release_notes.is_file():
        shutil.copy2(release_notes, bundle / "Release_Notes.md")
    shutil.copy2(ROOT / "LICENSE.txt", bundle / "LICENSE.txt")
    shutil.copy2(ROOT / "packaging" / "Portable_Start_Here.txt", bundle / "Start_Here.txt")
    if environment:
        (bundle / 'Start_Here.txt').write_text(
            'Dolly Environment Capture 0.1.6 - experimental\n\n'
            'Capture panorama is available in Keybinds, unbound by default. F8 still opens Dolly.\n'
            'A matching Capture panorama button is in Export. Read Environment_Capture.md.\n'
            'The complete six-face HDR reference workflow passed a local 1024-square replay check.\n'
            'If capture reports that the game is rendering below the HDR size, turn off upscaling (FSR/DLSS)\n'
            'or set render scale to 100% in Deadlock, then capture again.\n'
            'Select a square render size, open a replay, pause and position the camera before capture.\n'
            'If Deadlock waits on its intro, focus the game and press Escape once.\n'
            'Rendered HDR references are not calibrated lighting or certified fully empty scenery.\n'
            'This is an experimental map-reference tool; inspect each result before relying on it.\n'
            'Settings are separate from regular Dolly. Standard Dolly auto-updates are disabled.\n', encoding='utf-8')
        shutil.copy2(ROOT / 'docs/ENVIRONMENT_CAPTURE.md', bundle / 'Environment_Capture.md')
        (bundle / 'Release_Notes.md').write_text(
            '# Environment Capture 0.1.6 (experimental)\n\n'
            '- Overlapping 100-degree captures avoid the dark image borders that caused thin black panorama joins. Exact projection and normalized blending preserve HDR values.\n'
            '- Camera restoration now preserves the original projection mode as well as pose and lens.\n'
            '- Panorama capture now forces 100% render scale, so it works with FSR/DLSS upscaling or a reduced render scale. '
            'Previously capture failed with "HDR source dimensions were not established". The previous scale is restored afterward.\n'
            '- If capture still reports the game is rendering below the HDR size, it now names the setting to change.\n'
            '- The user\'s saved window resolution is restored after a sized panorama session, so a later normal launch is no longer left at the square size.\n'
            '- Focused desktop Export page: Screenshot and Environment panorama only. Camera-path tabs and Full editor switch hidden.\n'
            '- Screenshots default to lossless color PNG; depth and character cutouts are optional. Shared capture folder.\n'
            '- Removed the development header. Choose screenshot resolution before opening a replay.\n'
            '- Panorama still requires a square render size; native widescreen panorama is not included in this test build.\n'
            '- Capture panorama button and user-assigned action in existing Keybinds; unbound by default. F8 is unchanged.\n'
            '- Six fixed-centre overlapping 100-degree HDR views, world-class filtering and automatic EXR panorama assembly.\n'
            '- Actual GPU render-size validation, exposure consistency checks, cancellation and camera/HUD restoration.\n\n'
            'The render-scale fix was proven on build 6774: with render scale at 66.67% the source discovery reproduced the '
            'reported failure exactly (viewport 682x682 against a 1024x1024 texture), and capture succeeded at 100% scale with '
            'the previous scale restored. The production source workflow and bundled native helper passed a local 1024-square '
            'replay check, producing a 4096x2048 EXR, with complete session/configuration cleanup. Portable startup is checked '
            'separately. Zipline ribbons are removed during panorama capture. Intended for already-empty scenes; combat '
            'cleanup is incomplete. The packaged keypress-to-output path and higher resolutions still require acceptance checks.\n\n'
            'Intro dismissal can require a manual Escape press. A known Steam networking assertion remained in the live log; '
            'the owned process exited normally. Outputs are rendered HDR references, not calibrated lighting or certified '
            'complete actor/attachment/shadow removal. Settings and updates remain separate from regular Dolly.\n', encoding='utf-8')
    for extension in ("md", "json"):
        shutil.copy2(ROOT / "docs" / f"SUPPORTED_CAMERA_CVARS.{extension}",
                     bundle / f"Supported_Camera_Cvars.{extension}")
    if environment:
        # Ship the environment edition as a slim folder: runtime, examples,
        # BUILD_INFO.json, Dolly.exe and LICENSE.txt only.
        for name in ("Video_and_ReShade.md", "Release_Notes.md", "Start_Here.txt",
                     "Environment_Capture.md", "Supported_Camera_Cvars.md",
                     "Supported_Camera_Cvars.json"):
            (bundle / name).unlink(missing_ok=True)
    shutil.copytree(ROOT / "examples", bundle / "examples", dirs_exist_ok=True)
    reject_game_binaries(bundle)
    executable = bundle / "Dolly.exe"
    pe_report = check_executable(executable, ROOT / "assets" / "dolly.ico")
    print("Checking the actual EXE, icon and editor launch...", flush=True)
    # Relocate the COMPLETE bundle to a path with spaces and run from another
    # working directory. This checks the same layout users extract and launch.
    with tempfile.TemporaryDirectory(prefix="Dolly portable smoke ") as temporary:
        temporary = Path(temporary)
        relocated = temporary / "App folder with spaces"
        shutil.copytree(bundle, relocated)
        report_path = checks / "windows-smoke.json"
        if report_path.exists():
            report_path.unlink()
        result = subprocess.run([str(relocated / "Dolly.exe"), "--self-test", str(report_path)],
                                cwd=temporary, timeout=60)
        if result.returncode != 0 or not report_path.is_file():
            logs = relocated / "logs"
            if logs.exists():
                shutil.copytree(logs, checks / "failed-startup", dirs_exist_ok=True)
            raise RuntimeError("The packaged editor did not pass startup; inspect build/checks")
        smoke = json.loads(report_path.read_text())
        if not smoke.get("passed") or not smoke.get("frozen") or smoke.get("game_launched"):
            raise RuntimeError("Invalid packaged GUI smoke-test report")
    info = {"version": __version__, "built_utc": datetime.now(timezone.utc).isoformat(),
            "edition": build_edition, "environment_capture_ready": False,
            "environment_reference_capture_available": environment,
            "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
            "executable": pe_report, "bundle_gui_smoke_passed": True,
            "game_runtime_verified": False, "code_signed": False,
            "unlocker_sha256": UNLOCKER_SHA256,
            "native_bridge": {"abi": native_info["abi"], "sha256": native_info["sha256"],
                              "pe": native_report, "game_runtime_verified": False}}
    (bundle / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    if environment:
        # The environment edition has no standard update channel, so it ships
        # without the updater manifest (a slim folder for the user).
        (bundle / "UPDATE_MANIFEST.json").unlink(missing_ok=True)
    else:
        from dolly.updater import write_manifest
        write_manifest(bundle, __version__)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    prefix = 'Dolly_Environment_Capture_0.1.8_Experimental' if environment else f'Deadlock_Dolly_{__version__}'
    windows_zip = dist / f"{prefix}_Windows_x64.zip"
    bundle_zip(bundle, windows_zip)
    sources = source_zip(ROOT, dist / f"{prefix}_Source.zip")
    (dist / "SHA256SUMS.txt").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in (windows_zip, sources)), encoding="utf-8")
    print(f"Ready: {windows_zip}\nSource: {sources}\nNothing was uploaded or published.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
