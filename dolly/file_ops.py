"""Atomic file replacement and ordinary-path guards for local configuration IO.

Callers own file bounds, transaction policy, ownership and recovery decisions.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Callable, TypeVar

_T = TypeVar("_T")

# OneDrive and antivirus scanners can hold a just-written file for a moment;
# Windows then refuses access with a sharing or access error (WinError 32 or
# 5). Retry that case for about a second; every other error fails at once.
LOCK_RETRY_DELAYS = (0.1, 0.2, 0.3, 0.4)


def _transient_lock(error: OSError) -> bool:
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) in (5, 32)


def retry_locked(action: Callable[[], _T], delays: tuple[float, ...] = LOCK_RETRY_DELAYS) -> _T:
    """Run one file operation, repeating it while a brief lock refuses it."""
    for delay in (*delays, None):
        try:
            return action()
        except OSError as error:
            if delay is None or not _transient_lock(error):
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def replace_retrying(source: str | os.PathLike[str], destination: str | os.PathLike[str],
                     delays: tuple[float, ...] = LOCK_RETRY_DELAYS) -> None:
    """os.replace that tolerates a briefly held destination, within a bound."""
    retry_locked(lambda: os.replace(source, destination), delays)


def publish_new(temporary: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
    """Publish a completed temporary under a name that must not exist yet.

    Raises FileExistsError rather than replacing anything. On POSIX the
    temporary keeps its own link, so the caller still removes it afterwards.
    """
    if os.name == "nt":
        os.rename(temporary, destination)  # Windows refuses an existing name.
    else:
        os.link(temporary, destination)  # Same-filesystem no-overwrite publication.


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace one file atomically after flushing its new contents to disk.

    Steam validation and modding guides can leave gameinfo.gi read-only, and
    Windows refuses to replace a read-only destination. Clear exactly that
    attribute for the swap. Prepare the recorded mode on the completed temporary
    first, so replacement publishes the new bytes and mode together.
    """
    descriptor, name = tempfile.mkstemp(prefix=".dolly-write-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    writable = stat.S_IWRITE | stat.S_IREAD
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        try:
            replace_retrying(temp, path)
        except PermissionError:
            if os.name != "nt" or not path.exists():
                raise
            original_mode = stat.S_IMODE(path.stat().st_mode)
            if original_mode & stat.S_IWRITE:
                raise  # Not read-only: a lock that outlasted the retries.
            os.chmod(path, writable)
            try:
                replace_retrying(temp, path)
            except BaseException:
                try:
                    os.chmod(path, original_mode)
                except OSError:
                    pass  # Preserve the replacement error if restoration is denied too.
                raise
    finally:
        try:
            if temp.exists():
                os.chmod(temp, writable)
                temp.unlink()
        except OSError:
            pass  # A locked leftover must not hide the real write result.


def _plain_path(path: Path) -> bool:
    """Do not traverse symbolic links, Windows junctions or other reparse points.

    A reparse point that resolves to itself redirects nothing and stays plain.
    Wine reports every Unix mount point that way, including the root behind
    the Z: drive, so Proton paths would otherwise all look linked.
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return False
    if not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return True
    return os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.abspath(path))


def _plain_ancestors(path: Path) -> bool:
    return all(_plain_path(parent) for parent in (path, *path.parents))
