"""Published-release discovery and verified, bounded update staging."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "cravvnn/deadlock-dolly"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST = "UPDATE_MANIFEST.json"
MAX_DOWNLOAD = 1024 * 1024 * 1024
MAX_UNPACKED = 4 * MAX_DOWNLOAD


def version_key(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)(?:[.-]?(\d+))?)?", value)
    if not match:
        raise ValueError("Unsupported release version")
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch), {"alpha": 0, "beta": 1, "rc": 2, None: 3}[stage], int(number or 0))


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or "\\" in name or ":" in name
            or any(p in (".", "..") or p.endswith((".", " ")) or any(ord(c) < 32 for c in p)
                   or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p) for p in path.parts)):
        raise ValueError("Unsafe update path")
    return name


def owned_name(name):
    safe_name(name)
    return name in ("Dolly.exe", "DollyUpdater.exe", "BUILD_INFO.json", "LICENSE.txt", "Start_Here.txt",
                    "Video_and_ReShade.md", "LAYER_EXPORT.md", "Updating.md", "Supported_Camera_Cvars.md", "Supported_Camera_Cvars.json") or name.startswith("_internal/")


def read_manifest(root):
    path = Path(root) / MANIFEST
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Update manifest is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") not in (1, 2) or not isinstance(data.get("files"), dict):
        raise ValueError("Unsupported update manifest")
    version_key(data["version"])
    folded = set()
    for name, digest in data["files"].items():
        if not owned_name(name) or name.lower() in folded or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid update manifest file")
        folded.add(name.lower())
    entry = "_internal/DollyApp.exe" if data["schema"] == 2 else "DollyUpdater.exe"
    if not {"Dolly.exe", entry, "_internal/native/bin/win64/DollyNative.dll",
            "_internal/third_party/ffmpeg/bin/ffmpeg.exe"} <= data["files"].keys():
        raise ValueError("Update is missing required application files")
    return data


def write_manifest(root, version):
    root = Path(root)
    data = {"schema": 2 if (root / "_internal/DollyApp.exe").is_file() else 1, "version": version, "files": {p.relative_to(root).as_posix(): digest_file(p)
            for p in sorted(root.rglob("*")) if p.is_file() and owned_name(p.relative_to(root).as_posix())}}
    (root / MANIFEST).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    read_manifest(root)


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or url.hostname not in ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"):
            raise ValueError("Unexpected update download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Deadlock-Dolly-Updater", "Accept": "application/vnd.github+json" if url == LATEST_URL else "application/octet-stream"})
    return urllib.request.build_opener(ReleaseRedirect).open(request, timeout=20)


def select_release(data, current):
    if data.get("draft") is not False or data.get("prerelease") is not False:
        return None
    tag = data.get("tag_name", "")
    if version_key(tag) <= version_key(current):
        return None
    version = tag.removeprefix("v")
    name = f"Deadlock_Dolly_{version}_Windows_x64.zip"
    assets = [a for a in data.get("assets", []) if a.get("name") == name and a.get("state") == "uploaded"]
    if len(assets) != 1:
        raise ValueError("Latest release has no unique Windows x64 package")
    asset = assets[0]
    url = asset.get("browser_download_url", "")
    expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    if url != expected or type(asset.get("size")) is not int or not 0 < asset["size"] <= MAX_DOWNLOAD:
        raise ValueError("Invalid Windows release asset")
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Latest release is missing its GitHub SHA-256 digest; update deferred")
    return {"version": version, "url": url, "sha256": digest[7:], "size": asset["size"]}


def check_latest(current):
    with open_url(LATEST_URL) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("Release metadata is too large")
    return select_release(json.loads(payload), current)


def extract_verified(archive, destination, release):
    if digest_file(archive) != release["sha256"]:
        raise ValueError("Update download checksum mismatch")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        names = set()
        total = 0
        for entry in z.infolist():
            name = entry.filename.rstrip("/")
            safe_name(name)
            parts = PurePosixPath(name).parts
            if parts[0] != "DeadlockDolly" or len(parts) < 2:
                raise ValueError("Unexpected archive layout")
            relative = "/".join(parts[1:])
            if relative.lower() in names or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise ValueError("Duplicate, linked or encrypted archive entry")
            names.add(relative.lower())
            total += entry.file_size
            if total > MAX_UNPACKED or len(names) > 20000:
                raise ValueError("Update expands beyond allowed size")
            target = destination / relative
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(entry) as source, target.open("xb") as out:
                    shutil.copyfileobj(source, out, 1024 * 1024)
    manifest = read_manifest(destination)
    if manifest["version"] != release["version"]:
        raise ValueError("Release version disagrees with package")
    for name, digest in manifest["files"].items():
        if digest_file(destination / name) != digest:
            raise ValueError("Update package file checksum mismatch: " + name)
    return manifest


def download_update(release, parent, progress=lambda text: None):
    # Staging beside the install keeps replacement on one volume. This directory
    # is application-created; a failed download never changes installed files.
    work = Path(tempfile.mkdtemp(prefix=".dolly-update-", dir=parent))
    archive = work / "download.zip"
    downloaded = 0
    with open_url(release["url"]) as response, archive.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            downloaded += len(chunk)
            if downloaded > release["size"]:
                raise ValueError("Update download exceeds advertised size")
            output.write(chunk)
            progress(f"Downloading Dolly {release['version']}: {downloaded * 100 // release['size']}%")
    if downloaded != release["size"]:
        raise ValueError("Incomplete update download")
    extract_verified(archive, work / "payload", release)
    archive.unlink()
    return work
