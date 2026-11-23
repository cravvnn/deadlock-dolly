"""Stage the pinned LGPL FFmpeg runtime used by Dolly's external encoder.

The release build downloads this archive once, verifies its SHA-256 and copies
``ffmpeg.exe`` plus the shared ``av*`` DLLs and license notices into the
application's private ``third_party/ffmpeg`` folder. The binary is never
committed to source control and is not part of the source archive.

The pinned build is an LGPL (no GPL components) shared build. It provides the
hardware encoders Dolly exposes (NVENC, Quick Sync, AMF), the Media Foundation
wrapper and the lossless FFV1 muxer, but not libx264/libx265, whose GPL license
would change the distribution terms.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

from release_files import sha256

URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
       "autobuild-2026-11-14-12-37/"
       "ffmpeg-n8.1.3-35-gf1acb0a266-win64-lgpl-shared-8.1.zip")
SHA256 = "97927ac5391d5e3a11dc3bce679d54f7fde04cf54380a09970c32eea49ba20a2"
ARCHIVE_NAME = "ffmpeg-n8.1.3-35-gf1acb0a266-win64-lgpl-shared-8.1.zip"


def download(cache: Path) -> Path:
    """Return the verified archive, downloading it to ``cache`` if needed."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE_NAME
    try:
        if archive.is_file() and sha256(archive) == SHA256:
            return archive
    except OSError:
        pass
    print(f"Downloading FFmpeg: {ARCHIVE_NAME}", flush=True)
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "DeadlockDolly-build"})
        with urllib.request.urlopen(request, timeout=300) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if sha256(temporary) != SHA256:
            raise RuntimeError("The downloaded FFmpeg archive failed its SHA-256 check.")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


def _extract(package: zipfile.ZipFile, name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with package.open(name) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def stage(archive: Path, destination: Path) -> Path:
    """Extract ffmpeg.exe, its DLLs and notices into ``destination``."""
    destination = Path(destination)
    binary_dir = destination / "bin"
    notice_dir = destination / "notices"
    binary_dir.mkdir(parents=True, exist_ok=True)
    notice_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            normalized = name.replace("\\", "/")
            if normalized.endswith("/"):
                continue
            lower = normalized.lower()
            if "bin/" in normalized:
                tail = normalized.rsplit("bin/", 1)[1]
                if "/" not in tail and tail.lower().endswith((".exe", ".dll")):
                    if tail.lower() == "ffmpeg.exe" or tail.lower().endswith(".dll"):
                        _extract(package, name, binary_dir / tail)
            elif any(word in lower for word in ("license", "copying", "notice")):
                _extract(package, name, notice_dir / Path(normalized).name)
    if not (binary_dir / "ffmpeg.exe").is_file():
        raise RuntimeError("The FFmpeg archive did not contain ffmpeg.exe.")
    return destination


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Stage the pinned LGPL FFmpeg build")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--cache", default=str(root / "build" / "ffmpeg-cache"))
    args = parser.parse_args(argv)
    archive = download(Path(args.cache))
    stage(archive, Path(args.destination))
    print("FFmpeg staged in " + str(Path(args.destination)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"FFmpeg staging failed: {error}", file=sys.stderr)
        raise SystemExit(1)
