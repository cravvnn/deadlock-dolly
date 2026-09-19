"""Derive the attach-camera schema field table from saved console layouts.

Maintainer tool. The live game reports each class layout through
``schema_detailed_class_layout``; save those outputs (one file per class) and
run this tool to produce the field table the attach camera expects. It never
launches the game and never writes product data: the reviewed result is applied
to ``dolly/attach_camera.py`` by hand after Phase 0 verification.

Usage::

    python tools/derive_attach_fields.py --directory research/schema-dumps
    python tools/derive_attach_fields.py --layout CGameSceneNode=CGameSceneNode.txt --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dolly import attach_camera  # noqa: E402  (path setup precedes the import)


def collect_layouts(pairs: list[tuple[str, Path]], directory: Path | None) -> dict[str, str]:
    """Read named layouts and every ``<Class>.txt`` in a folder."""
    layouts: dict[str, str] = {}
    for name, path in pairs:
        layouts[name] = Path(path).read_text(encoding="utf-8", errors="replace")
    if directory is not None:
        for path in sorted(Path(directory).glob("*.txt")):
            layouts.setdefault(path.stem, path.read_text(encoding="utf-8", errors="replace"))
    return layouts


def format_report(report: list[dict]) -> list[str]:
    lines = []
    for entry in report:
        offset = "-" if entry["offset"] is None else hex(entry["offset"])
        marker = "required" if entry["required"] else "optional"
        lines.append("%-18s %-20s %-26s %-8s %-8s %s" % (
            entry["purpose"], entry["class"], entry["field"], offset, marker, entry["status"]))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", action="append", default=[], metavar="CLASS=PATH",
                        help="Console layout for one class, repeatable.")
    parser.add_argument("--directory", type=Path, default=None,
                        help="Folder of <Class>.txt layout captures.")
    parser.add_argument("--json", action="store_true", help="Emit the raw field report.")
    args = parser.parse_args(argv)
    pairs = []
    for entry in args.layout:
        name, separator, path = entry.partition("=")
        if not separator or not name or not path:
            parser.error("--layout expects CLASS=PATH")
        pairs.append((name, Path(path)))
    if not pairs and args.directory is None:
        parser.error("provide --layout CLASS=PATH at least once or --directory")
    report = attach_camera.field_report(collect_layouts(pairs, args.directory))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(format_report(report)))
    absent = [entry for entry in report if entry["required"] and entry["status"] != "ok"]
    if absent:
        print("Missing required fields: " + ", ".join(entry["field"] for entry in absent),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
