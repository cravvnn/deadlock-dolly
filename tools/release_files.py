"""Explicit source export: runtime logs, demos and personal files never enter it."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import zipfile


def source_files(root: Path) -> list[Path]:
    root = Path(root).resolve()
    names = [line.strip() for line in (root / "SOURCE_FILES.txt").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if len(names) != len(set(names)):
        raise ValueError("SOURCE_FILES.txt contains duplicate paths")
    result = []
    for name in names:
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or any(part in {"logs", "dist", "build", "__pycache__", ".git", ".venv"} for part in path.parts)
                or path.suffix.lower() in {".log", ".zip", ".dem", ".mp4", ".pyc"}
                or path.name.lower() in {"client.dll", "engine2.dll", "tier0.dll"}
                or path.name.startswith(".env")):
            raise ValueError(f"Non-source path in SOURCE_FILES.txt: {name}")
        file = root / name
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root):
            raise ValueError(f"Missing or unsafe source file: {name}")
        result.append(file)
    return sorted(result)


_BUNDLE_EXCLUDED_PARTS = {"logs", "build", "dist", "__pycache__", ".git", ".venv"}
_BUNDLE_EXCLUDED_SUFFIXES = {".log", ".dem", ".mp4", ".pyc"}


def _excluded_bundle_path(relative: PurePosixPath) -> bool:
    parts = [part.casefold() for part in relative.parts]
    if any(part in _BUNDLE_EXCLUDED_PARTS for part in parts):
        return True
    if any(part.startswith(".dolly-update") for part in parts):
        return True
    if relative.suffix.casefold() in _BUNDLE_EXCLUDED_SUFFIXES:
        return True
    return parts[-1].startswith(".env")


def bundle_files(bundle: Path) -> list[Path]:
    """Files that may enter the Windows package, never runtime or private data.

    Dolly keeps its journals, recordings and staged updates beside the
    executable, so a folder that was ever run in place would otherwise leak
    session data into the release ZIP. The source export enforces the same rule.
    """
    bundle = Path(bundle).resolve()
    result = []
    for file in bundle.rglob("*"):
        if not file.is_file() or file.is_symlink():
            continue
        relative = file.relative_to(bundle)
        if _excluded_bundle_path(PurePosixPath(relative.as_posix())):
            continue
        if not file.resolve().is_relative_to(bundle):
            raise ValueError(f"Bundle file escapes the bundle: {relative.as_posix()}")
        result.append(file)
    return sorted(result)


def _partial(output: Path) -> Path:
    return output.with_name(output.name + ".partial")


def bundle_zip(bundle: Path, output: Path) -> Path:
    bundle = Path(bundle).resolve()
    files = bundle_files(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build beside the final name so a failure never leaves a partial release ZIP.
    partial = _partial(output)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for file in files:
                archive.write(file, "DeadlockDolly/" + file.relative_to(bundle).as_posix())
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise OSError("Windows archive integrity check failed")
            for name in archive.namelist():
                relative = PurePosixPath(name.removeprefix("DeadlockDolly/"))
                if _excluded_bundle_path(relative):
                    raise OSError(f"Excluded runtime data entered the Windows archive: {name}")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return output


def source_zip(root: Path, output: Path) -> Path:
    root = Path(root).resolve()
    files = source_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = _partial(output)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for file in files:
                archive.write(file, file.relative_to(root).as_posix())
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise OSError("Source archive integrity check failed")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return output


def release_set(bundle: Path, root: Path, dist: Path, version: str) -> tuple[Path, Path]:
    """Write both release ZIPs, then SHA256SUMS.txt last and atomically.

    The old checksum file goes first, so an interrupted build never leaves a
    SHA256SUMS.txt that names ZIPs it does not match.
    """
    source_files(root)
    dist = Path(dist)
    dist.mkdir(parents=True, exist_ok=True)
    sums = dist / "SHA256SUMS.txt"
    sums.unlink(missing_ok=True)
    windows_zip = bundle_zip(bundle, dist / f"Deadlock_Dolly_{version}_Windows_x64.zip")
    sources = source_zip(root, dist / f"Deadlock_Dolly_{version}_Source.zip")
    partial = _partial(sums)
    try:
        partial.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in (windows_zip, sources)),
                           encoding="utf-8")
        os.replace(partial, sums)
    finally:
        partial.unlink(missing_ok=True)
    return windows_zip, sources


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
