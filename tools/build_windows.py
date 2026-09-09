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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dolly import __version__
from dolly.launcher import UNLOCKER_SHA256
from release_files import source_zip, sha256
from build_native import build_native, copy_native_runtime, reject_game_binaries


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
    third_party = bundle / "_internal" / "third_party"
    shutil.copytree(ROOT / "third_party", third_party, dirs_exist_ok=True)
    dll = third_party / "cvar_unlocker" / "bin" / "win64" / "server.dll"
    if sha256(dll) != UNLOCKER_SHA256:
        raise RuntimeError("The official unlocker changed during packaging")
    copy_runtime_licenses(third_party / "notices")
    native_report = copy_native_runtime(ROOT, bundle / "_internal" / "native")
    shutil.copy2(ROOT / "native" / "vendor" / "minhook" / "LICENSE.txt",
                 third_party / "notices" / "MinHook-LICENSE.txt")
    shutil.copy2(ROOT / "LICENSE.txt", bundle / "LICENSE.txt")
    shutil.copy2(ROOT / "packaging" / "Portable_Start_Here.txt", bundle / "Start_Here.txt")
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
            "python": platform.python_version(), "pyinstaller": PyInstaller.__version__,
            "executable": pe_report, "bundle_gui_smoke_passed": True,
            "game_runtime_verified": False, "code_signed": False,
            "unlocker_sha256": UNLOCKER_SHA256,
            "native_bridge": {"abi": native_info["abi"], "sha256": native_info["sha256"],
                              "pe": native_report, "game_runtime_verified": False}}
    (bundle / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    windows_zip = dist / f"Deadlock_Dolly_{__version__}_Windows_x64.zip"
    with zipfile.ZipFile(windows_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                archive.write(file, "DeadlockDolly/" + file.relative_to(bundle).as_posix())
    with zipfile.ZipFile(windows_zip) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Windows archive integrity check failed")
    sources = source_zip(ROOT, dist / f"Deadlock_Dolly_{__version__}_Source.zip")
    (dist / "SHA256SUMS.txt").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in (windows_zip, sources)), encoding="utf-8")
    print(f"Ready: {windows_zip}\nSource: {sources}\nNothing was uploaded or published.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
