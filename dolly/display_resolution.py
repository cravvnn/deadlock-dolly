"""Restore the user's saved window resolution after a sized Dolly session.

The environment edition launches Deadlock with an explicit square window size so
panorama faces are square. The game persists that window size into the active
user's ``video.txt`` (``setting.defaultres``/``setting.defaultresheight``), so a
later ordinary launch comes up at the square size. This module snapshots those
two keys before the game starts and restores them after it exits.

It edits only the two resolution keys, never the rest of the file, and never
overwrites a newer value the user changed in the game during the session. It
mirrors the recoverable, journal-based style of ``graphics_profiles``.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex

from .launch_errors import LaunchError
from .keyvalues import _parse, _tokens
from .file_ops import _atomic_write, _plain_ancestors
from .settings import _unique_object
from . import graphics_profiles

MAX_BYTES = 128 * 1024
JOURNAL = "display-resolution.json"
RESOLUTION_KEYS = ("setting.defaultres", "setting.defaultresheight")


def window_size(launch_options: str) -> tuple[int, int] | None:
    """Return the explicit ``-w``/``-h`` window size, or None when unspecified."""
    if not isinstance(launch_options, str) or not launch_options.strip():
        return None
    try:
        tokens = shlex.split(launch_options, posix=True)
    except ValueError as exc:
        raise LaunchError("Launch options contain an unmatched quote.") from exc
    width = height = None
    index = 0
    while index < len(tokens):
        flag = tokens[index].casefold()
        index += 1
        if flag in ("-w", "-width", "-h", "-height"):
            if index >= len(tokens) or not tokens[index].isdigit():
                raise LaunchError(f"Launch option {flag} needs an integer size.")
            value = int(tokens[index])
            index += 1
            if flag in ("-w", "-width"):
                width = value
            else:
                height = value
    if width is None and height is None:
        return None
    if width is None or height is None:
        raise LaunchError("Both -w and -h are required to set a window size.")
    return width, height


def _read(path: Path) -> bytes:
    if not _plain_ancestors(path) or not path.is_file():
        raise LaunchError(f"Display configuration must be an ordinary local file: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise LaunchError(f"Display configuration exceeds {MAX_BYTES // 1024} KiB: {path}")
    return data


def _document(data: bytes):
    text = data.decode("utf-8-sig")
    tokens = _tokens(text)
    if sum(t.kind == "brace" for t in tokens) != 2 or any(t.kind == "condition" for t in tokens):
        raise ValueError("expected a single flat video.cfg block")
    roots = _parse(text)
    if len(roots) != 1 or roots[0].key.value.casefold() != "video.cfg" or roots[0].children is None:
        raise ValueError("expected a video.cfg block")
    entries = {}
    for entry in roots[0].children:
        key = entry.key.value.casefold()
        if entry.value is None or key in entries:
            raise ValueError(f"duplicate or nested video setting: {key}")
        entries[key] = entry
    return text, entries


def _resolution(data: bytes) -> dict[str, str]:
    _, entries = _document(data)
    missing = [key for key in RESOLUTION_KEYS if key not in entries]
    if missing:
        raise LaunchError("video.txt lacks resolution settings: " + ", ".join(missing))
    return {key: entries[key].value.value for key in RESOLUTION_KEYS}


def _with_resolution(data: bytes, values: dict[str, str]) -> bytes:
    text, entries = _document(data)
    edits = []
    for key, value in values.items():
        token = entries[key].value
        if value != token.value:
            edits.append((token.start, token.end, '"' + value + '"'))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return (b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8")


def _save_journal(session: Path, record: dict) -> None:
    _atomic_write(session / JOURNAL, (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def _target():
    account, root = graphics_profiles.active_account(), graphics_profiles.steam_root()
    return account, root, graphics_profiles.video_path(account, root)


def prepare(session: Path, launch_options: str) -> bool:
    """Snapshot the saved resolution when this session forces a window size."""
    size = window_size(launch_options)
    if size is None:
        return False
    account, root, target = _target()
    saved = _resolution(_read(target))
    applied = {"setting.defaultres": str(size[0]), "setting.defaultresheight": str(size[1])}
    _save_journal(session, {"format": 1, "state": "prepared", "target": str(target),
                            "steam_root": str(root), "account": account,
                            "resolution": saved, "applied": applied})
    return True


def pending(session: Path) -> bool:
    path = session / JOURNAL
    if not path.exists():
        return False
    try:
        record = json.loads(_read(path), object_pairs_hook=_unique_object)
        if not isinstance(record, dict) or record.get("format") != 1:
            raise ValueError("unrecognized display-resolution journal")
        return record.get("state") != "restored"
    except (OSError, ValueError) as exc:
        raise LaunchError(f"Cannot read display-resolution record: {path}: {exc}") from exc


def restore(session: Path) -> None:
    """Put the saved resolution back; caller must establish the game is closed."""
    if not pending(session):
        return
    record = json.loads(_read(session / JOURNAL), object_pairs_hook=_unique_object)
    root = graphics_profiles.steam_root()
    target = graphics_profiles.video_path(record["account"], root)
    if Path(record["steam_root"]) != root or Path(record["target"]) != target:
        raise LaunchError("Display-resolution target does not match this Steam installation.")
    saved, applied = record["resolution"], record["applied"]
    if not (isinstance(saved, dict) and isinstance(applied, dict)
            and set(saved) == set(RESOLUTION_KEYS) == set(applied)):
        raise LaunchError("Display-resolution record is malformed.")
    current = _resolution(_read(target))
    if current == saved:
        # The game never persisted the square size, or the user already fixed it.
        record["state"] = "restored"
        _save_journal(session, record)
        return
    if current != applied:
        record["state"] = "conflict"
        _save_journal(session, record)
        raise LaunchError(
            "The saved display resolution changed during the Dolly session; the newer "
            "settings were left untouched. Recover from: " + str(session / JOURNAL))
    updated = _with_resolution(_read(target), saved)
    if _resolution(updated) != saved:
        raise LaunchError("Display-resolution rewrite did not read back correctly.")
    _atomic_write(target, updated)
    if _resolution(_read(target)) != saved:
        raise LaunchError("Saved display resolution did not read back correctly.")
    record["state"] = "restored"
    _save_journal(session, record)