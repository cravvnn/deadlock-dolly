"""Build and inspect Dolly's x64 native bridge; never package game binaries."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dolly import __version__
from release_files import sha256

BRIDGE_ABI = 3
DLL_RELATIVE = Path("bin/win64/DollyNative.dll")
REQUIRED_EXPORTS = {
    "CreateInterface", "DollyNativeProtocolVersion",
    "DollyAtomicExchange32", "DollyAtomicExchange64", "DollyAtomicCompareExchange32",
}


def verify_native_dll(path: Path) -> dict:
    """Inspect PE architecture/exports without loading a DLL into Python."""
    import pefile
    image = pefile.PE(str(path), fast_load=True)
    try:
        if (image.FILE_HEADER.Machine != 0x8664
                or not image.FILE_HEADER.Characteristics & 0x2000
                or image.OPTIONAL_HEADER.Magic != 0x20B):
            raise ValueError("DollyNative.dll must be a Windows x64 PE32+ DLL")
        image.parse_data_directories([
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        ])
        symbols = getattr(getattr(image, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])
        exports = {symbol.name.decode("ascii") for symbol in symbols if symbol.name}
        if not REQUIRED_EXPORTS.issubset(exports):
            raise ValueError("DollyNative.dll is missing required bridge exports")
        # The portable DLL is built with /MT: no separate Visual C++ runtime
        # installation or copying arbitrary compiler DLLs into the game folder.
        imports = sorted(entry.dll.decode("ascii")
                         for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", []))
        if any(name.lower().startswith(("vcruntime", "msvcp", "libstdc++", "libgcc"))
               for name in imports):
            raise ValueError("DollyNative.dll must use the static C/C++ runtime")
        return {"machine": "x64", "dll": True, "exports": sorted(exports),
                "import_libraries": imports}
    finally:
        image.close()


def runtime_files(root: Path) -> tuple[list[tuple[Path, Path]], dict]:
    """Return an explicit runtime allowlist, rejecting missing/stale metadata."""
    native = Path(root) / "native"
    dll = native / DLL_RELATIVE
    metadata = native / "build_info.json"
    for path in (dll, metadata):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe native runtime file: {path.name}")
    info = json.loads(metadata.read_text(encoding="utf-8"))
    if (not isinstance(info, dict) or type(info.get("abi")) is not int
            or info.get("abi") != BRIDGE_ABI):
        raise ValueError("Native build metadata has an unsupported bridge ABI")
    if info.get("sha256") != sha256(dll):
        raise ValueError("Native bridge does not match its build metadata hash")
    report = verify_native_dll(dll)
    files = [(dll, DLL_RELATIVE), (metadata, Path("build_info.json"))]
    for profile in sorted((native / "profiles").glob("*.json")):
        if not profile.is_file() or profile.is_symlink():
            raise ValueError("Unsafe native build profile")
        if not isinstance(json.loads(profile.read_text(encoding="utf-8")), dict):
            raise ValueError("Native build profile must be a JSON object")
        files.append((profile, Path("profiles") / profile.name))
    return files, report


def copy_native_runtime(root: Path, destination: Path) -> dict:
    """Copy only Dolly's DLL, metadata and build profiles into the frozen app."""
    files, report = runtime_files(root)
    destination = Path(destination)
    for source, relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(target) != sha256(source):
            raise RuntimeError("The native runtime changed during packaging")
    expected = {relative.as_posix() for _, relative in files}
    actual = {path.relative_to(destination).as_posix()
              for path in destination.rglob("*") if path.is_file()}
    if actual != expected:
        raise RuntimeError("Unexpected files in the packaged native runtime")
    return report


def reject_game_binaries(bundle: Path) -> None:
    if any(path.name.lower() in {"client.dll", "engine2.dll", "tier0.dll"}
           for path in Path(bundle).rglob("*") if path.is_file()):
        raise RuntimeError("Game client.dll/engine2.dll/tier0.dll must never enter a Dolly release")


def build_native(root: Path = ROOT) -> dict:
    if (sys.platform != "win32" or struct.calcsize("P") != 8
            or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise RuntimeError("Build DollyNative.dll on Windows x64 using Visual Studio 2022")
    root = Path(root)
    native = root / "native"
    build = root / "build" / "native-msvc"
    checks = root / "build" / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    metadata = native / "build_info.json"
    metadata.unlink(missing_ok=True)
    commands = [
        ["cmake", "-S", str(native), "-B", str(build), "-G", "Visual Studio 17 2022",
         "-A", "x64", "-DBUILD_TESTING=ON"],
        ["cmake", "--build", str(build), "--config", "Release", "--parallel"],
        ["ctest", "--test-dir", str(build), "-C", "Release", "--output-on-failure"],
    ]
    print("Building and testing the native x64 camera bridge...", flush=True)
    with (checks / "native-build.log").open("w", encoding="utf-8") as log:
        for command in commands:
            subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
    dll = native / DLL_RELATIVE
    report = verify_native_dll(dll)
    info = {"abi": BRIDGE_ABI, "version": __version__, "sha256": sha256(dll),
            "built_utc": datetime.now(timezone.utc).isoformat(),
            "compiler": "Visual Studio 17 2022", "configuration": "Release",
            "runtime": "static", "native_path_tests_passed": True,
            "native_effect_tests_passed": True, "native_visualization_tests_passed": True,
            "native_callback_tests_passed": True,
            "native_flight_tests_passed": True, "native_overlay_tests_passed": True,
            "game_runtime_verified": False, "pe": report}
    metadata.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


if __name__ == "__main__":
    try:
        build_native()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Native build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
