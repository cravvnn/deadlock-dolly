"""Publish reviewed build definitions for already-released Dolly versions.

This is the maintainer step that replaces a native rebuild after a Deadlock
update. It reads a reviewed ``native/profiles/*-complete.json`` (produced by
``tools/generate_profile.py``) and appends the client camera record, the engine
identity and the tier0 identity to ``definitions.json`` at the repository root.
Installed Dolly copies fetch that file from the raw GitHub endpoint.

Nothing here reads or launches the game, and nothing is uploaded. Review the
diff, commit, and push to the default branch: that publish is the distribution.

Usage::

    python tools/publish_definitions.py --profile native/profiles/deadlock-2026-10-22-6819-complete.json \
        --engine-layout 6728 --tier0-layout modern_6801 --editing --stock

    python tools/publish_definitions.py --check
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dolly import definitions  # noqa: E402  (path set above)

DEFAULT_DOCUMENT = ROOT / definitions.DEFINITIONS_PATH
EDITING_PROFILE = ROOT / "assets/editing/profile.json"
STOCK_PROVENANCE = ROOT / "assets/editing/stock-provenance.json"

# Reviewed profile fields that make up one named native replay layout. These are
# pure RVAs, so deriving a layout from a reviewed profile is mechanical.
ENGINE_LAYOUT_FROM_PROFILE = {
    "demo_global": ("addresses", "demo_player_pointer"),
    "demo_table": ("addresses", "demo_player_vtable"),
    "engine_table": ("addresses", "engine_client_vtable"),
    "playing": ("functions", "demo_is_playing", "rva"),
    "active": ("functions", "engine_is_playing_demo", "rva"),
    "paused": ("functions", "demo_is_paused", "rva"),
    "seeking": ("functions", "demo_is_seeking", "rva"),
    "tick": ("functions", "demo_get_tick", "rva"),
    "filename": ("functions", "engine_get_demo_filename", "rva"),
}
TIER0_LAYOUT_FROM_PROFILE = {
    "table": ("vtable_rva",),
    "setter": ("set_value", "rva"),
    "find": ("find_cvar", "rva"),
    "data": ("get_data", "rva"),
    "data_slot": ("get_data", "slot"),
}


class PublishError(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"{path} is not a JSON object")
    return data


def _value(block: dict, path: tuple[str, ...], field: str):
    current = block
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise PublishError(f"The profile has no {field} value")
        current = current[key]
    return current


def _number(value, field: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.lower().startswith("0x"):
        try:
            return int(value, 16)
        except ValueError as exc:
            raise PublishError(f"The profile has an invalid {field} value") from exc
    raise PublishError(f"The profile has an invalid {field} value")


def _label_name(label: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:24].strip("_")
    return name or "published"


def derived_engine_layout(profile: dict) -> dict[str, int]:
    block = _object(profile.get("engine"), "engine block")
    return {field: _number(_value(block, path, field), field)
            for field, path in ENGINE_LAYOUT_FROM_PROFILE.items()}


def derived_tier0_layout(profile: dict) -> dict[str, int]:
    block = _object(profile.get("tier0"), "tier0 block")
    layout = {field: _number(_value(block, path, field), field)
              for field, path in TIER0_LAYOUT_FROM_PROFILE.items()}
    if layout["data_slot"] not in (41, 43):
        raise PublishError("The profile uses an unreviewed ConVar data slot")
    # Slot 41 is the September-and-later extended layout. A legacy slot-43
    # build keeps vector ConVar support off; the bridge verifies slots anyway.
    layout["extended"] = layout["data_slot"] == 41
    return layout


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise PublishError(f"The profile has no {field}")
    return value


def _layout_row(values: dict) -> dict:
    row = {}
    for key, value in values.items():
        if key == "extended":
            row[key] = bool(value)
        elif key == "data_slot":
            row[key] = value
        else:
            row[key] = hex(value)
    return row


def _same_layout(stored: dict, values: dict) -> bool:
    if not isinstance(stored, dict):
        return False
    for key, value in values.items():
        raw = stored.get(key)
        try:
            if key == "extended":
                if bool(raw) != bool(value):
                    return False
            elif key == "data_slot":
                if raw != value:
                    return False
            elif _number(raw, key) != value:
                return False
        except PublishError:
            return False
    return True


def ensure_layout(document: dict, section: str, values: dict, label: str) -> tuple[str, bool]:
    """Reuse an identical published layout or add one named after the build."""
    layouts = document.setdefault(section, {}).setdefault("layouts", {})
    for name, candidate in layouts.items():
        if _same_layout(candidate, values):
            return name, False
    base = _label_name(label)
    name, suffix = base, 2
    while name in layouts:
        name = f"{base[:22]}_{suffix}"
        suffix += 1
    layouts[name] = _layout_row(values)
    return name, True


def client_entry(profile: dict) -> dict:
    """One published camera record, in the same shape generate_profile emits."""
    from generate_profile import reviewed_camera_layout, reviewed_render_fraction
    client = profile.get("client")
    if not isinstance(client, dict):
        raise PublishError("The profile has no client block")
    try:
        entry = {
            "sha256": client["client_sha256"],
            "image_size": client["size_of_image"],
            "main_view_setup": client["main_view_setup_rva"],
            "main_view_caller": client["view_setup_abi"]["caller_return_rva"],
            "primary_vtable": client["primary_vtable_rva"],
            "globals": client["globals"]["pointer_rva"],
            "engine_client": client["aspect_scaling"]["aspect_source_interface_pointer_rva"],
            "render_fraction": hex(reviewed_render_fraction(client)),
            "camera_layout_revision": reviewed_camera_layout(client),
            "view_setup_delegate": client.get("view_setup_delegate_rva", "0x0"),
            "setup_prologue": client["main_view_setup_prologue_32"].replace(" ", ""),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise PublishError(f"The client block is missing {exc}") from exc
    signature = client.get("camera_signature") or {}
    if signature.get("client_sha256") == client.get("client_sha256"):
        entry["signature"] = signature["bytes"]
        entry["signature_mask"] = signature["mask"]
    return entry


def module_entry(document: dict, relative: str, digest: str) -> None:
    modules = document.setdefault("modules", {})
    entry = modules.setdefault(relative, {"accepted": []})
    accepted = entry.setdefault("accepted", [])
    if digest not in accepted:
        accepted.append(digest)


def append_profile(document: dict, *, profile: dict, engine_layout: str | None = None,
                   tier0_layout: str | None = None, label: str = "") -> list[str]:
    changes = []
    client = client_entry(profile)
    client_block = document.setdefault("client", {"profiles": []})
    profiles = client_block.setdefault("profiles", [])
    if any(row.get("sha256") == client["sha256"] for row in profiles):
        changes.append("client profile already published")
    else:
        profiles.append(client)
        changes.append(f"client profile {client['sha256'][:12]}")

    engine = profile.get("engine")
    if not isinstance(engine, dict):
        raise PublishError("The profile has no engine block")
    if engine_layout is None:
        engine_layout, created = ensure_layout(
            document, "engine", derived_engine_layout(profile), label)
        if created:
            changes.append(f"new engine layout {engine_layout}")
    elif engine_layout not in document.get("engine", {}).get("layouts", {}):
        raise PublishError(f"Unknown engine layout {engine_layout}")
    engine_rows = document.setdefault("engine", {}).setdefault("profiles", [])
    if any(row.get("sha256") == engine["sha256"] for row in engine_rows):
        changes.append("engine profile already published")
    else:
        engine_rows.append({"sha256": engine["sha256"],
                            "image_size": engine["image_size"],
                            "layout": engine_layout})
        changes.append(f"engine {engine_layout}")

    tier0 = profile.get("tier0")
    if not isinstance(tier0, dict):
        raise PublishError("The profile has no tier0 block")
    if tier0_layout is None:
        tier0_layout, created = ensure_layout(
            document, "tier0", derived_tier0_layout(profile), label)
        if created:
            changes.append(f"new tier0 layout {tier0_layout}")
    elif tier0_layout not in document.get("tier0", {}).get("layouts", {}):
        raise PublishError(f"Unknown tier0 layout {tier0_layout}")
    tier0_rows = document.setdefault("tier0", {}).setdefault("profiles", [])
    if any(row.get("sha256") == tier0["sha256"] for row in tier0_rows):
        changes.append("tier0 profile already published")
    else:
        tier0_rows.append({"sha256": tier0["sha256"],
                           "image_size": tier0["size_of_image"],
                           "layout": tier0_layout})
        changes.append(f"tier0 {tier0_layout}")

    module_entry(document, "citadel/bin/win64/client.dll", client["sha256"])
    module_entry(document, "bin/win64/engine2.dll", engine["sha256"])
    module_entry(document, "bin/win64/tier0.dll", tier0["sha256"])
    return changes


def append_editing(document: dict) -> list[str]:
    changes = []
    profile = read_json(EDITING_PROFILE)
    editing = document.setdefault("editing", {})
    modules = editing.setdefault("configuration_modules", {})
    for relative, digest in profile.get("configuration_modules", {}).items():
        current = modules.get(relative)
        if current == digest:
            continue
        if isinstance(current, list) and digest in current:
            continue
        if isinstance(current, list):
            current.append(digest)
        elif isinstance(current, str):
            modules[relative] = [current, digest]
        else:
            modules[relative] = [digest]
        changes.append(f"editing {relative}")
    # The cvar unlocker is a patched copy of the game's exact server.dll, so its
    # server pin is deliberately not publishable here. A server.dll change needs
    # a rebuilt unlocker and therefore a Dolly release.
    return changes


def append_stock(document: dict) -> list[str]:
    inventory = read_json(STOCK_PROVENANCE).get("files")
    if not isinstance(inventory, dict) or not inventory:
        raise PublishError("The stock provenance inventory is missing")
    rows = document.setdefault("editing", {}).setdefault("stock_provenance_files", {})
    changes = []
    for name, row in inventory.items():
        digest = row.get("sha1")
        size = row.get("bytes")
        if not isinstance(name, str) or not isinstance(digest, str) or type(size) is not int:
            raise PublishError("The stock provenance inventory has an invalid row")
        existing = rows.get(name)
        if existing == row:
            continue
        if isinstance(existing, list):
            if row not in existing:
                existing.append(row)
                changes.append(f"stock {name}")
        elif isinstance(existing, dict):
            rows[name] = [existing, row]
            changes.append(f"stock {name}")
        else:
            rows[name] = [row]
            changes.append(f"stock {name}")
    return changes


def write_document(path: Path, document: dict) -> None:
    document["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Validate exactly what installed copies will validate before writing.
    definitions.parse(document)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def profile_probe(profile: dict) -> dict:
    """A throwaway definitions document that exercises one profile exactly."""
    client = client_entry(profile)
    engine = _object(profile.get("engine"), "engine block")
    tier0 = _object(profile.get("tier0"), "tier0 block")
    engine_layout = derived_engine_layout(profile)
    tier0_layout = derived_tier0_layout(profile)
    return {
        "format": definitions.FORMAT,
        "native_abi": 4,
        "client": {"profiles": [client]},
        "engine": {
            "layouts": {"probe": _layout_row(engine_layout)},
            "profiles": [{"sha256": engine["sha256"],
                          "image_size": engine["image_size"],
                          "layout": "probe"}],
        },
        "tier0": {
            "layouts": {"probe": _layout_row(tier0_layout)},
            "profiles": [{"sha256": tier0["sha256"],
                          "image_size": tier0["size_of_image"],
                          "layout": "probe"}],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--profile", type=Path,
                        help="reviewed native/profiles/*-complete.json")
    parser.add_argument("--engine-layout", default=None,
                        help="published engine layout name (default: derive from the profile)")
    parser.add_argument("--tier0-layout", default=None,
                        help="published tier0 layout name (default: derive from the profile)")
    parser.add_argument("--editing", action="store_true",
                        help="also publish assets/editing/profile.json pins")
    parser.add_argument("--stock", action="store_true",
                        help="also publish assets/editing/stock-provenance.json")
    parser.add_argument("--check", action="store_true",
                        help="validate the checked-in document and exit")
    parser.add_argument("--check-profile", action="store_true",
                        help="validate one reviewed profile and print its derived layouts")
    options = parser.parse_args(argv)

    if options.check_profile:
        if options.profile is None:
            parser.error("--profile is required with --check-profile")
        try:
            profile = read_json(options.profile)
            definitions.parse(profile_probe(profile))
        except (PublishError, definitions.DefinitionsError) as exc:
            print(f"Invalid profile: {exc}")
            return 1
        print(f"Profile valid: {options.profile}")
        print("  client:", profile["client"]["client_sha256"])
        print("  engine layout:", {k: hex(v) for k, v in derived_engine_layout(profile).items()})
        print("  tier0 layout:", {k: (hex(v) if isinstance(v, int)
                                      and k not in ("data_slot", "extended") else v)
                                  for k, v in derived_tier0_layout(profile).items()})
        return 0

    if options.check:
        if not options.document.is_file():
            print(f"Missing {options.document}")
            return 1
        try:
            document = definitions.load(options.document)
        except definitions.DefinitionsError as exc:
            print(f"Invalid: {exc}")
            return 1
        print(f"Definitions valid: {document.describe()}"
              + (f" updated {document.updated}" if document.updated else ""))
        return 0

    if options.profile is None:
        parser.error("--profile is required unless --check is used")
    if not options.document.is_file():
        parser.error(f"{options.document} does not exist")

    try:
        document = read_json(options.document)
        profile = read_json(options.profile)
        label = options.profile.stem.removeprefix("deadlock-").removesuffix("-complete")
        changes = append_profile(document, profile=profile,
                                 engine_layout=options.engine_layout,
                                 tier0_layout=options.tier0_layout,
                                 label=label or options.profile.stem)
        if options.editing:
            changes += append_editing(document)
        if options.stock:
            changes += append_stock(document)
        write_document(options.document, document)
    except (PublishError, definitions.DefinitionsError) as exc:
        print(f"Refused: {exc}")
        return 1
    for change in changes:
        print("  " + change)
    print(f"Updated {options.document}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
