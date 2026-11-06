"""Run source-side models and mutants. Node is a development dependency only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"PlayersRestoreRetry.cfg", "PlayersRecovery.cfg", "CleanupResult-guarded.cfg", "BackupPublish.cfg", "BackupPublish-link.cfg", "AtomicMode.cfg", "AtomicMode-noFaults.cfg", "AtomicCopy.cfg", "UpdateRollback.cfg", "WorkspaceCleanup.cfg", "LauncherHandoff.cfg", "FollowSelection.cfg", "CleanupMarker.cfg", "FollowLens.cfg", "PlaybackIdentity.cfg"}


def check(root=ROOT):
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("TLA checks need Node 20 or newer. See docs/BUILDING.md.")
    tools = root / "tools" / "tla"
    if not (tools / "node_modules" / "tla-ts" / "package.json").is_file():
        raise RuntimeError("Install model dependencies: npm ci --prefix tools/tla --ignore-scripts")
    result = subprocess.run([node, str(tools / "check.mjs"), "-json", str(root / "dolly")],
                            cwd=root, text=True, capture_output=True)
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    checked = {Path(row["name"]).name for row in rows if row.get("kind") == "model"}
    if result.returncode or not REQUIRED.issubset(checked):
        raise RuntimeError("TLA model/mutant checks failed or required configs were not checked.\n"
                           + result.stdout + result.stderr)
    return rows


# Additional independent source contracts.
REQUIRED.update({'PngStage-collision.cfg', 'PresetPublish-link.cfg', 'CleanupWorker.cfg', 'PresetPublish.cfg', 'PngStage.cfg', 'PresetPublish-collision.cfg', 'PngStage-noFaults.cfg'})
REQUIRED.update({'PublishReplay-link.cfg', 'ScratchStatus.cfg', 'PublishReplay.cfg', 'ScratchStatus-collision.cfg', 'HelperWorkspace.cfg', 'ScratchStatus-concurrent.cfg', 'ScratchStatus-noFaults.cfg', 'ScratchStatus-concurrent-noFaults.cfg', 'HelperWorkspace-noFaults.cfg'})
REQUIRED.update({"SaveResult.cfg", "ManifestScan.cfg"})
REQUIRED.update({"MemoryWatch.cfg", "RestoreHandles.cfg"})
REQUIRED.update({"SocketSetup.cfg", "Admission.cfg", "ExportCleanup.cfg", "ExportCleanup-link.cfg"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit upstream results, including mutant traces")
    args = parser.parse_args()
    try:
        rows = check()
    except (OSError, ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    for row in rows:
        if args.json:
            print(json.dumps(row))
        elif row["kind"] == "model":
            print(f"PASS {row['name']}: {row['states']} states")
        else:
            print(f"KILLED {row['name']}: {row['rule']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
