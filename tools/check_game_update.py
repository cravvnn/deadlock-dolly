"""Report what a Deadlock update changed, before running the compatibility pipeline.

Reads the installed build id from ``game/citadel/steam.inf`` and hashes the
reviewed modules against ``native/profiles/manifest.json`` (plus the unlocker's
``server.dll`` against ``assets/editing/profile.json``). Prints the new build id
and exactly which modules changed, so the update pipeline can be scoped in
seconds instead of guessed.

Offline only: reads files, launches nothing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from dolly import compatibility  # noqa: E402
from dolly import launcher  # noqa: E402

STEAM_INF = "citadel/steam.inf"
CLIENT = "citadel/bin/win64/client.dll"
SERVER = "citadel/bin/win64/server.dll"


def read_steam_inf(game_dir: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    path = game_dir / STEAM_INF
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip()
    return info


def resolve_game_dir(selected: Path) -> tuple[Path, Path]:
    """Return (install root, game dir) from an install root, exe, or game dir."""
    selected = Path(selected)
    try:
        paths = launcher.validate_game(selected)
    except launcher.LaunchError:
        if selected.name.lower() == "game":
            return selected.parent, selected
        if selected.name.lower() == "win64":
            return selected.parents[2], selected.parents[1]
        return selected, selected / "game"
    return paths.root, paths.game_dir


def editing_server_pin() -> str | None:
    try:
        profile = json.loads((ROOT / "assets" / "editing" / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pin = profile.get("unlocker_server_sha256")
    return pin if isinstance(pin, str) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir", type=Path, default=None,
                        help="Deadlock install root, game dir, or deadlock.exe (default: Dolly settings)")
    args = parser.parse_args(argv)

    selected = args.game_dir
    if selected is None:
        from dolly.settings import load_settings
        selected = Path(load_settings().game_path)
    root, game_dir = resolve_game_dir(selected)

    if not game_dir.is_dir():
        print(f"error: no game directory at {game_dir}", file=sys.stderr)
        return 2

    steam = read_steam_inf(game_dir)
    client_version = steam.get("ClientVersion", "?")
    server_version = steam.get("ServerVersion", "?")
    version_date = steam.get("VersionDate", "?")
    version_time = steam.get("VersionTime", "")

    report = compatibility.scan_game_modules(game_dir)
    changed = [m for m in report.modules if m.installed and not m.matched]
    missing = [m for m in report.modules if not m.installed]

    server_path = game_dir / SERVER
    server_hash = compatibility.hash_file(server_path) if server_path.is_file() else None
    server_pin = editing_server_pin()
    server_state = ("MISSING" if server_hash is None else
                    "KNOWN" if server_hash == server_pin else "CHANGED")

    # The confetti/particle table is a separate compatibility surface: it is not
    # in the module manifest, so a stale table would silently disable particles.
    particle_state = "n/a"
    particle_detail = ""
    client_path = game_dir / CLIENT
    if client_path.is_file():
        import confetti_offsets  # noqa: E402
        try:
            row = confetti_offsets.scan(client_path)
            if confetti_offsets.newest_row_matches(confetti_offsets.parse_rows(
                    confetti_offsets.source_path().read_text(encoding="utf-8")), row):
                particle_state = "ok"
            else:
                particle_state = "STALE"
                particle_detail = f"  <- derive create 0x{row.create:x}"
        except ValueError as error:
            particle_state = "ERROR"
            particle_detail = f"  <- {error}"

    print("Deadlock update check")
    print(f"  install:       {root}")
    print(f"  game dir:      {game_dir}")
    print(f"  ClientVersion: {client_version}   ServerVersion: {server_version}   "
          f"VersionDate: {version_date} {version_time}".rstrip())
    print()
    print("Reviewed modules (vs native/profiles/manifest.json):")
    for module in report.modules:
        state = "MISSING" if not module.installed else ("ok" if module.matched else "CHANGED")
        digest = (module.observed_sha256 or "n/a")[:16]
        extra = "" if module.matched or not module.installed else "  <- not in the accepted list"
        print(f"  {state:8s} {module.name:22s} {digest}  reviewed={module.reviewed_utc}{extra}")
    print()
    print("Unlocker target:")
    print(f"  {server_state:8s} server.dll             "
          f"{(server_hash or 'n/a')[:16]}  pin={(server_pin or 'n/a')[:16]}")
    print()
    print("Confetti/particle table (native/src/dolly_confetti.cpp kBuilds):")
    print(f"  {particle_state:8s} client.dll             {particle_detail}")
    print()

    if missing:
        print(f"SUMMARY: {len(missing)} reviewed module(s) missing: "
              + ", ".join(m.name for m in missing))
        return 2
    if changed or server_state == "CHANGED" or particle_state in ("STALE", "ERROR"):
        names = ", ".join(m.name for m in changed)
        detail = names or ""
        if server_state == "CHANGED":
            detail = (detail + ", " if detail else "") + "server.dll"
        if particle_state == "STALE":
            detail = (detail + ", " if detail else "") + "confetti particle table"
        if particle_state == "ERROR":
            detail = (detail + ", " if detail else "") + "confetti table (scan failed)"
        print(f"SUMMARY: update detected — {detail} changed. Run the compatibility pipeline "
              "(tools/update_game.py).")
        return 1
    print(f"SUMMARY: build {client_version} matches a reviewed Dolly profile; no update needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
