"""Report the latest published Dolly release and compare it with this checkout.

Read-only: asks the GitHub CLI for the newest release, prints the published
asset names/sizes/digests, and compares the latest release against
``dolly.__version__`` and the matching local ``dist`` artifact. It never
uploads, tags or publishes anything.

Run this before saying a version is unpublished:

    python tools/release_status.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dolly import __version__  # noqa: E402
from dolly.updater import version_key  # noqa: E402
from release_files import sha256  # noqa: E402


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _gh_json(args: list[str]):
    result = _run(["gh", *args])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "gh failed").strip())
    return json.loads(result.stdout)


def _slug() -> str:
    result = _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    result = _run(["git", "remote", "get-url", "origin"])
    match = re.search(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$",
                      result.stdout.strip())
    if not match:
        raise RuntimeError("Could not determine the GitHub repository slug")
    return match.group("slug")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=__version__,
                        help="local version to compare (default: dolly.__version__)")
    args = parser.parse_args(argv)
    try:
        slug = _slug()
        # Drafts are not published; never report one as the latest release.
        releases = _gh_json(["release", "list", "--limit", "1", "--exclude-drafts",
                             "--json", "tagName,name,createdAt,isDraft,isPrerelease"])
        if not releases:
            print(f"No published releases found for {slug}.")
            print(f"Local version: {args.version} (unpublished).")
            return 0
        latest = releases[0]
        tag = latest["tagName"]
        detail = _gh_json(["api", f"repos/{slug}/releases/tags/{tag}"])
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Could not check published releases: {exc}", file=sys.stderr)
        print("Install and authenticate the GitHub CLI, then rerun this tool.", file=sys.stderr)
        return 1

    print(f"Repository:       {slug}")
    print(f"Latest published: {latest['name']} ({tag})")
    print(f"Published at:     {latest['createdAt']}"
          + ("  [draft]" if latest.get("isDraft") else "")
          + ("  [prerelease]" if latest.get("isPrerelease") else ""))
    assets = detail.get("assets", [])
    if not assets:
        print("Published assets: (none)")
    else:
        print("Published assets:")
        for asset in assets:
            digest = asset.get("digest") or "(no digest)"
            print(f"  {asset['name']}  {asset['size']} bytes  {digest}")
    print(f"Local version:    {args.version}")
    local = ROOT / "dist" / f"Deadlock_Dolly_{args.version}_Windows_x64.zip"
    local_digest = sha256(local) if local.is_file() else None
    if local_digest:
        print(f"Local artifact:   {local.name}  {local.stat().st_size} bytes  sha256:{local_digest}")
    else:
        print(f"Local artifact:   not found ({local})")

    published_version = tag.removeprefix("v")
    published_digest = None
    for asset in assets:
        if asset["name"] == f"Deadlock_Dolly_{published_version}_Windows_x64.zip":
            published_digest = (asset.get("digest") or "").removeprefix("sha256:")
    try:
        order = (version_key(args.version) > version_key(published_version)) - (
            version_key(args.version) < version_key(published_version))
    except ValueError:
        order = None
    if order == 0:
        if local_digest and published_digest and local_digest == published_digest:
            print("STATUS: MATCH - the local build is the published artifact.")
        elif local_digest and published_digest:
            print("STATUS: VERSION MATCH, ARTIFACT DIFFERS - the published asset is not this local build.")
        else:
            print("STATUS: VERSION MATCH - no comparable local artifact.")
    elif order is None:
        print(f"STATUS: UNCOMPARABLE - cannot order {args.version} against {tag}.")
    elif order < 0:
        print(f"STATUS: LOCAL BEHIND - {args.version} is older than "
              f"the latest published release {tag}.")
    else:
        print(f"STATUS: LOCAL AHEAD - {args.version} is unpublished; "
              f"the latest published release is {tag}.")

    print("Nothing was uploaded or published by this check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
