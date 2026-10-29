"""Compile and pack Dolly's Panorama overlay override.

The sources in ``native/ui_override`` present the shader-loading card and hide
the client-status mark and match/server debug label during recording. The pack ships as
``native/assets/ui/pak02_dir.vpk`` and the launcher copies it beside the
confetti pack into each native session's mounted ``cvar_unlocker`` folder.

A Source 2 SDK with a working Panorama-capable ``resourcecompiler.exe`` is
required; the community "Reduced CSDK 12" layout is the reviewed one (sources
under ``content/citadel_addons/<addon>``, outputs under
``game/citadel_addons/<addon>``, compiler in ``game/bin_cs2/win64``).

Re-run and re-pin after a Deadlock UI update: the same-path override replaces
the stock stylesheet, so the sources must be refreshed from the new build.
The rebuild, re-pin and in-game checklist for the current card are in
``native/ui_override/panorama/README.md``.

Usage:
    python tools/build_ui_override.py --sdk "D:\\deadlock 3d\\Reduced_CSDK_12"
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native" / "ui_override"
DESTINATION = ROOT / "native" / "assets" / "ui" / "pak02_dir.vpk"
ADDON = "dolly_capture"

SIGNATURE = 0x55AA1234
VERSION = 2
INLINE_ARCHIVE = 0x7FFF
ENTRY_TERMINATOR = 0xFFFF
# The capture-clean rules, the card, and the redesigned card's own parts (the
# Familiar icon's boil keyframes and the chalk doodle), so a pack compiled from
# the older card sources is refused.
EXPECTED_RULES = ("ClientServerDebugStats", "GameLogoIcon", "DollyLoadingProbeV2",
                  "DollyFamiliarBoil", "DollyChalk")
EXPECTED_SOURCES = {
    "panorama/styles/citadel_client_status.css": ".vcss_c",
    "panorama/styles/citadel_hud_and_db_overlay.css": ".vcss_c",
    "panorama/styles/citadel_prewarm_assets_status.css": ".vcss_c",
    "panorama/layout/citadel_prewarm_assets_status.xml": ".vxml_c",
    "panorama/layout/hud_hideout.xml": ".vxml_c",
}
EXPECTED_RESOURCES = frozenset(str(Path(name).with_suffix(extension)).replace("\\", "/")
                               for name, extension in EXPECTED_SOURCES.items())


def locate_compiler(sdk: Path) -> Path | None:
    for candidate in (sdk / "game" / "bin_cs2" / "win64" / "resourcecompiler.exe",
                      sdk / "game" / "bin" / "win64" / "resourcecompiler.exe"):
        if candidate.is_file():
            return candidate
    return None


def compile_sources(sdk: Path, compiler: Path) -> tuple[Path, list[Path]]:
    game_dir = sdk / "game" / "citadel"
    if not (game_dir / "gameinfo.gi").is_file():
        raise SystemExit(f"gameinfo.gi not found in {game_dir}; pass a full Source 2 SDK root.")
    content = sdk / "content" / "citadel_addons" / ADDON
    output = sdk / "game" / "citadel_addons" / ADDON
    sources = sorted(path for path in SOURCE.rglob("*") if path.is_file() and path.suffix in (".css", ".xml"))
    if {path.relative_to(SOURCE).as_posix() for path in sources} != set(EXPECTED_SOURCES):
        raise SystemExit("UI override sources must contain the complete reviewed three stylesheets and two layouts.")
    compiled: list[Path] = []
    for source in sources:
        relative = source.relative_to(SOURCE)
        target = content / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        result = subprocess.run([str(compiler), "-game", str(game_dir),
                                 "-i", str(target), "-nop4", "-f"],
                                cwd=str(compiler.parent))
        if result.returncode != 0:
            raise SystemExit(f"resourcecompiler failed for {relative} (rc={result.returncode})")
        produced = (output / relative).with_suffix(EXPECTED_SOURCES[relative.as_posix()])
        if not produced.is_file():
            raise SystemExit(f"resourcecompiler produced no output for {relative}")
        compiled.append(produced)
    return output, compiled


def cstring(value: str) -> bytes:
    return value.encode("utf-8") + b"\0"


def pack(output_root: Path, compiled: list[Path], destination: Path) -> None:
    entries: dict[str, bytes] = {}
    combined = b"".join(path.read_bytes() for path in compiled)
    for rule in EXPECTED_RULES:
        if rule.encode("ascii") not in combined:
            raise SystemExit(f"compiled pack does not carry the {rule} rule")
    for path in compiled:
        entries[path.relative_to(output_root).as_posix()] = path.read_bytes()
    if len(compiled) != len(entries) or set(entries) != EXPECTED_RESOURCES:
        raise SystemExit("UI override pack must contain exactly the five reviewed layout/stylesheet resources.")
    grouped: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
    for virtual_path, payload in entries.items():
        path = Path(virtual_path)
        extension = path.name.split(".", 1)[1]
        name = path.name.split(".", 1)[0]
        grouped.setdefault((extension, path.parent.as_posix()), []).append((name, payload))
    tree = bytearray()
    data = bytearray()
    for extension in sorted({key[0] for key in grouped}):
        tree += cstring(extension)
        for (ext, directory), members in sorted(grouped.items()):
            if ext != extension:
                continue
            tree += cstring(directory)
            for name, payload in sorted(members):
                tree += cstring(name)
                tree += struct.pack("<IHHIIH", binascii.crc32(payload) & 0xFFFFFFFF, 0,
                                    INLINE_ARCHIVE, len(data), len(payload), ENTRY_TERMINATOR)
                data += payload
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack("<IIIIIII", SIGNATURE, VERSION, len(tree), len(data), 0, 0, 0) + tree + data)
    print(f"{destination}: {len(entries)} resources, {destination.stat().st_size} bytes")
    for virtual_path, payload in sorted(entries.items()):
        print(f"  {virtual_path} sha256 {hashlib.sha256(payload).hexdigest()}")
    print("sha256 " + hashlib.sha256(destination.read_bytes()).hexdigest())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path, required=True, help="Source 2 SDK root")
    parser.add_argument("--compiler", type=Path, help="explicit resourcecompiler.exe")
    args = parser.parse_args()
    compiler = args.compiler or locate_compiler(args.sdk)
    if not compiler or not compiler.is_file():
        raise SystemExit("resourcecompiler.exe not found; pass --sdk or --compiler")
    output_root, compiled = compile_sources(args.sdk, compiler)
    for path in compiled:
        print(f"compiled {path.name} ({path.stat().st_size} bytes)")
    pack(output_root, compiled, DESTINATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
