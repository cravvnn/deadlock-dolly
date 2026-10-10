"""Add reviewed support for a new Deadlock build by driving the update pipeline.

This generalizes the per-update ``analysis/update-*`` scripts (which were cloned
and hand-corrected every time) into one command. It clones the previous run's
scripts into a new run directory, shifts the build numbers/dates/paths/hashes,
auto-derives the values that used to be hand-edited (from the previous run's
``resolved-*.json`` and ``resolve-server-*.json``), then optionally runs the
stages in order.

Offline only. It reads installed DLLs and the previous run's JSON; it launches
nothing. Review the generated directory before committing any generated profile.

Stages: the client pipeline (audit -> materialize) runs first; the unlocker
stages are generated after ``resolve_server`` produces the new server tuple.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ANALYSIS = ROOT / "analysis"

CLIENT_SCRIPTS = ("collect_anchors.py", "audit_full_{b}.py", "build_map_{b}.py",
                  "resolve_data_{b}.py", "resolve_settings_{b}.py", "resolve_missing_{b}.py",
                  "merge_resolved_{b}.py", "translate_contract_{b}.py", "materialize_{b}.py",
                  "resolve_server_{b}.py")
UNLOCKER_SCRIPTS = ("prepare_unlocker_{b}.py", "finalize_unlocker_{b}.py", "promote_unlocker_{b}.py")


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def run_dir_identity(directory: Path) -> tuple[str, str]:
    match = re.fullmatch(r"update-(\d{8})-(\d+)", directory.name)
    if not match:
        raise SystemExit(f"Not an update run directory: {directory.name}")
    date = datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    return match.group(2), date


def latest_run() -> Path:
    runs = [p for p in ANALYSIS.glob("update-*") if p.is_dir() and re.fullmatch(r"update-\d{8}-\d+", p.name)]
    if not runs:
        raise SystemExit("No previous analysis/update-* run found; pass --prev")
    return max(runs, key=lambda p: p.stat().st_mtime)


def read_names(build: str, date: str) -> list[str]:
    compact = date.replace("-", "")
    return [
        f"update-{compact}-{build}", f"client-{build}-original.dll", f"server-{build}-original.dll",
        f"deadlock-{date}-{build}", f"resolved-missing-{build}",
        f"resolved-data-{build}", f"resolved-{build}", f"client-map-{build}", f"settings-resolve-{build}",
        f"resolve-server-{build}", f"unlocker-build-{build}", f"build_compat-{build}",
        f"unlocker-pe-audit-{build}", f"g_{build}_server", f"BRANCH_{build}", f"SERVER_{build}_SHA",
        f"dolly-{build}-source-", f"dolly-build-{build}",
    ]


def shift(text: str, pairs: list[tuple[str, str]]) -> str:
    markers = []
    for index, (old, new) in enumerate(pairs):
        marker = f"\x01{index}\x02"
        if old and old in text:
            text = text.replace(old, marker)
            markers.append((marker, new))
    for marker, new in markers:
        text = text.replace(marker, new)
    return text


def first_match(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text)
    if not match:
        raise SystemExit(f"Could not find {label} in the previous scripts")
    return match.group(1)


def read_steam_inf(game_dir: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    path = game_dir / "citadel" / "steam.inf"
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip()
    return info


def parse_version_date(value: str) -> str:
    for fmt in ("%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise SystemExit(f"Could not parse VersionDate {value!r}")


def resolve_game_dir(selected: Path) -> Path:
    selected = Path(selected)
    if (selected / "citadel" / "steam.inf").is_file():
        return selected
    if selected.name.lower() == "game":
        return selected
    return selected / "game"


def build_pairs(prev_build: str, prev_date: str, prior_build: str, prior_date: str,
                new_build: str, new_date: str, hashes: dict[str, str]) -> list[tuple[str, str]]:
    pairs = list(zip(read_names(prev_build, prev_date), read_names(new_build, new_date)))
    pairs += list(zip(read_names(prior_build, prior_date), read_names(prev_build, prev_date)))
    pairs += [
        (hashes["prev_client"], hashes["new_client"]),
        (hashes["prev_server"], hashes["new_server"]),
        (hashes["prior_client"], hashes["prev_client"]),
        (hashes["prior_server"], hashes["prev_server"]),
    ]
    return pairs


def _normalize_patched(text: str, new_build: str) -> str:
    return re.sub(r"server-\d+-patched\.dll", f"server-{new_build}-patched.dll", text)


def _client_autofix(template: str, text: str, game_dir: Path, resolved: dict, server: dict) -> str:
    if template.startswith("materialize"):
        text = re.sub(r'GAME = Path\([^)]*\)', 'GAME = Path(%r)' % str(game_dir).replace("\\", "/"), text)
    elif template.startswith("resolve_missing"):
        text = re.sub(r"OLD_RULES = 0x[0-9A-Fa-f]+",
                      "OLD_RULES = " + hex(int(resolved["ReplayCamera.RULES_GLOBAL"], 16)), text)
        text = re.sub(r"OLD_CS8 = 0x[0-9A-Fa-f]+",
                      "OLD_CS8 = " + hex(int(resolved["ReplayCamera.CODE_SPANS[8]"], 16)), text)
        if "OLD_CS10" in text and "ReplayCamera.CODE_SPANS[10]" in resolved:
            text = re.sub(r"OLD_CS10 = 0x[0-9A-Fa-f]+",
                          "OLD_CS10 = " + hex(int(resolved["ReplayCamera.CODE_SPANS[10]"], 16)), text)
    elif template.startswith("resolve_server"):
        slots = server.get("slots") or []
        if len(slots) == 3:
            text = re.sub(r'OLD_METHODS = \{[^}]*\}',
                          'OLD_METHODS = {"connect": %s, "disconnect": %s, "interval": %s}'
                          % (slots[0], slots[1], slots[2]), text)
        if server.get("table"):
            text = re.sub(r"OLD_TABLE = 0x[0-9A-Fa-f]+", "OLD_TABLE = " + server["table"], text)
    return text


def _prepare_autofix(text: str, source_text: str, prev_dir: Path, prev_build: str,
                     new_build: str, new_server: dict, prev_server: dict) -> str:
    match = re.search(r"PATCHES = \((.*?)\)", source_text, re.S)
    if match:
        entries = [line.strip() for line in match.group(1).strip().splitlines() if line.strip()]
        entries.append(f'    ROOT / "analysis/{prev_dir.name}/unlocker-build-{prev_build}.patch",')
        text = re.sub(r"PATCHES = \(.*?\)", "PATCHES = (\n" + "\n".join(entries) + "\n)", text, count=1, flags=re.S)
    for build, data in ((new_build, new_server), (prev_build, prev_server)):
        slots = data.get("slots") or []
        if data.get("table") and len(slots) == 3:
            branch = ('BRANCH_%s = ("        return reinterpret_cast<std::uintptr_t>(table)==base+%s && "\n'
                      '               "table[0]==base+%s && table[1]==base+%s && table[13]==base+%s;")'
                      % (build, data["table"], slots[0], slots[1], slots[2]))
            text = re.sub(rf'BRANCH_{build} = \(".*?"\)', branch, text, count=1, flags=re.S)
    return _normalize_patched(text, new_build)


def _finalize_autofix(text: str, new_build: str, prev_server: str) -> str:
    text = text.replace("    SERVER_SHA,\n", '    SERVER_SHA,\n    "%s",\n' % prev_server, 1)
    return _normalize_patched(text, new_build)


def generate_client(prev_dir: Path, new_dir: Path, game_dir: Path, pairs: list[tuple[str, str]],
                    prev_build: str, new_build: str, resolved: dict, server: dict) -> list[str]:
    written = []
    for template in CLIENT_SCRIPTS:
        source = prev_dir / template.format(b=prev_build)
        if not source.is_file():
            continue
        text = shift(source.read_text(encoding="utf-8"), pairs)
        text = _client_autofix(template, text, game_dir, resolved, server)
        (new_dir / template.format(b=new_build)).write_text(text, encoding="utf-8", newline="\n")
        written.append(template.format(b=new_build))
    return written


def generate_unlocker(prev_dir: Path, new_dir: Path, pairs: list[tuple[str, str]],
                      prev_build: str, new_build: str, new_server: dict, prev_server: dict,
                      prev_server_hash: str) -> list[str]:
    written = []
    for template in UNLOCKER_SCRIPTS:
        source = prev_dir / template.format(b=prev_build)
        if not source.is_file():
            continue
        source_text = source.read_text(encoding="utf-8")
        text = shift(source_text, pairs)
        if template.startswith("prepare_unlocker"):
            text = _prepare_autofix(text, source_text, prev_dir, prev_build, new_build, new_server, prev_server)
        elif template.startswith("finalize_unlocker"):
            text = _finalize_autofix(text, new_build, prev_server_hash)
        else:
            text = _normalize_patched(text, new_build)
        (new_dir / template.format(b=new_build)).write_text(text, encoding="utf-8", newline="\n")
        written.append(template.format(b=new_build))
    return written


def run_script(script: Path) -> None:
    print(f"--- {script.name} ---", flush=True)
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{script.name} failed with {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prev", type=Path, default=None, help="previous analysis/update-* run (default: latest)")
    parser.add_argument("--game-dir", type=Path, default=None, help="Deadlock install root or game dir")
    parser.add_argument("--label", default=None, help="new run directory name (default: update-<date>-<build>)")
    parser.add_argument("--run", action="store_true", help="also execute the generated stages")
    args = parser.parse_args(argv)

    prev_dir = (args.prev or latest_run()).resolve()
    prev_build, prev_date = run_dir_identity(prev_dir)

    selected = args.game_dir
    if selected is None:
        from dolly.settings import load_settings
        selected = Path(load_settings().game_path)
    game_dir = resolve_game_dir(selected)

    steam = read_steam_inf(game_dir)
    new_build = steam.get("ClientVersion", "")
    if not new_build.isdigit():
        raise SystemExit(f"Could not read ClientVersion from {game_dir / 'citadel' / 'steam.inf'}")
    new_date = parse_version_date(steam.get("VersionDate", ""))

    prev_materialize = (prev_dir / f"materialize_{prev_build}.py").read_text(encoding="utf-8")
    prior_profile = first_match(r'SOURCE_PROFILE = ROOT / "native/profiles/(deadlock-\d{4}-\d{2}-\d{2}-\d+)-complete.json"',
                                prev_materialize, "SOURCE_PROFILE")
    prior_build = prior_profile.rsplit("-", 1)[1]
    prior_date = prior_profile.split("deadlock-", 1)[1].rsplit("-", 1)[0]

    prev_audit = (prev_dir / f"audit_full_{prev_build}.py").read_text(encoding="utf-8")
    prev_client = sha256(prev_dir / f"client-{prev_build}-original.dll")
    prev_server = sha256(prev_dir / f"server-{prev_build}-original.dll")
    prior_client = first_match(r'assert old\.sha256 == "([0-9a-f]{64})"', prev_audit, "prior client hash")
    prev_server_script = (prev_dir / f"resolve_server_{prev_build}.py").read_text(encoding="utf-8")
    prior_server = first_match(r'OLD_SHA = "([0-9a-f]{64})"', prev_server_script, "prior server hash")
    new_client = sha256(game_dir / "citadel" / "bin" / "win64" / "client.dll")
    new_server = sha256(game_dir / "citadel" / "bin" / "win64" / "server.dll")

    resolved = json.loads((prev_dir / f"resolved-{prev_build}.json").read_text(encoding="utf-8"))["resolved"]
    prev_server_json = prev_dir / f"resolve-server-{prev_build}.json"
    server = json.loads(prev_server_json.read_text(encoding="utf-8")) if prev_server_json.is_file() else {}

    hashes = {"prior_client": prior_client, "prev_client": prev_client,
              "prior_server": prior_server, "prev_server": prev_server,
              "new_client": new_client, "new_server": new_server}
    pairs = build_pairs(prev_build, prev_date, prior_build, prior_date, new_build, new_date, hashes)

    label = args.label or f"update-{new_date.replace('-', '')}-{new_build}"
    new_dir = ANALYSIS / label
    if new_dir.exists():
        raise SystemExit(f"Run directory already exists: {new_dir}")
    new_dir.mkdir(parents=True)
    for name, source in ((f"client-{new_build}-original.dll", game_dir / "citadel" / "bin" / "win64" / "client.dll"),
                         (f"server-{new_build}-original.dll", game_dir / "citadel" / "bin" / "win64" / "server.dll")):
        if not source.is_file():
            raise SystemExit(f"Installed module missing: {source}")
        shutil.copy2(source, new_dir / name)

    print(f"previous: {prev_dir.name}  ({prev_build})   prior: {prior_build}   new: {new_build} ({new_date})")
    print(f"client:   {prev_client[:12]} -> {new_client[:12]}")
    print(f"server:   {prev_server[:12]} -> {new_server[:12]}")

    written = generate_client(prev_dir, new_dir, game_dir, pairs, prev_build, new_build, resolved, server)
    print(f"wrote {len(written)} client scripts to {new_dir.name}")

    if not args.run:
        print("\nClient scripts written. Run with --run to execute them and finish the unlocker stages.")
        return 0

    for template in CLIENT_SCRIPTS:
        run_script(new_dir / template.format(b=new_build))

    new_server_json = new_dir / f"resolve-server-{new_build}.json"
    if not new_server_json.is_file():
        raise SystemExit("resolve_server did not produce the new server tuple; inspect its output")
    new_server_data = json.loads(new_server_json.read_text(encoding="utf-8"))
    written = generate_unlocker(prev_dir, new_dir, pairs, prev_build, new_build, new_server_data, server, prev_server)
    print(f"wrote {len(written)} unlocker scripts")
    print("NOTE: review prepare_unlocker_%s.py -- its `replacements` (the g_*_server static/guard/"
          "return/branch edits) encode the previous build and may need adjusting." % new_build)

    run_script(new_dir / f"prepare_unlocker_{new_build}.py")
    candidate_dir = new_dir / "unlocker-candidate"
    build_dir = new_dir / "unlocker-build"
    subprocess.run(["cmake", "-S", str(candidate_dir), "-B", str(build_dir),
                    "-G", "Visual Studio 17 2022", "-A", "x64"], cwd=ROOT, check=True)
    subprocess.run(["cmake", "--build", str(build_dir), "--config", "Release", "--parallel", "2"],
                   cwd=ROOT, check=True)
    run_script(new_dir / f"finalize_unlocker_{new_build}.py")
    run_script(new_dir / f"promote_unlocker_{new_build}.py")
    print("\nPipeline complete. Next: python tools/generate_compatibility.py --write, review, then build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
