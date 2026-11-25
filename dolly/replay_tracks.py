"""Hero tracks read straight from a replay file, in the background.

DollyReplayTracks (native/replay_tracks, built on s2replay) reads every hero's position from
the .dem and writes them beside the replay's track store as ``<store>.replay``. The game
adopts those cells into the store, so tracks are ready before a shot has been played. The
replay itself is only read. Live recording while the replay plays still fills in whatever
the reader could not (an unreadable replay, a game update the reader does not know yet).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .weather import hero_track_store

HELPER = Path("native/bin/win64/DollyReplayTracks.exe")
# A full match reads in about half a minute; a very long one gets several.
TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class ReplayTracksState:
    state: str = "idle"  # idle, reading, ready, failed
    message: str = ""


def replay_cells_file(replay) -> Path:
    """Where the reader's cells for ``replay`` are kept (beside its track store)."""
    store = hero_track_store(str(replay))
    return store.with_name(store.name + ".replay")


def _identity(replay: Path, helper: Path) -> dict:
    info = replay.stat()
    digest = hashlib.sha256(helper.read_bytes()).hexdigest() if helper.is_file() else ""
    return {"replay": str(replay), "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "helper": digest}


class ReplayTracksReader:
    """Starts the reader once per replay (and again only when the replay or the reader
    changes); one replay is read at a time."""

    def __init__(self, helper_path=None, run=subprocess.run):
        self._lock = threading.Lock()
        self._states: dict[str, ReplayTracksState] = {}
        self._running: str | None = None
        self._helper_path = helper_path
        self._run = run

    def _helper(self) -> Path:
        if self._helper_path is not None:
            return Path(self._helper_path)
        from .runtime import resource_root
        return resource_root() / HELPER

    def state(self, replay) -> ReplayTracksState:
        with self._lock:
            return self._states.get(str(replay), ReplayTracksState())

    def request(self, replay) -> ReplayTracksState:
        """Read ``replay`` in the background unless its cells are already current."""
        key = str(replay or "")
        if not key:
            return ReplayTracksState()
        with self._lock:
            known = self._states.get(key)
            if known is not None and known.state in ("reading", "ready", "failed"):
                return known
            if self._running is not None:
                return ReplayTracksState()  # one at a time; asked again on the next publish
            try:
                helper = self._helper()
                identity = _identity(Path(key), helper)
            except OSError as error:
                state = ReplayTracksState("failed", str(error)[:160])
                self._states[key] = state
                return state
            out = replay_cells_file(key)
            if _current(out, identity):
                state = ReplayTracksState("ready")
                self._states[key] = state
                return state
            if not helper.is_file():
                state = ReplayTracksState("failed", "The replay track reader is missing.")
                self._states[key] = state
                return state
            state = ReplayTracksState("reading")
            self._states[key] = state
            self._running = key
        threading.Thread(target=self._read, args=(key, helper, out, identity),
                         daemon=True).start()
        return state

    def _read(self, key, helper, out, identity):
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if os.name == "nt":
                flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
            process = self._run([str(helper), key, str(out)], capture_output=True,
                                timeout=TIMEOUT_SECONDS, creationflags=flags)
            if process.returncode:
                detail = process.stderr.decode("utf-8", "replace").strip()[:160]
                raise ValueError(detail or "The replay's hero positions could not be read.")
            _meta(out).write_text(json.dumps(identity), encoding="utf-8")
            state = ReplayTracksState("ready")
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            state = ReplayTracksState("failed", str(error)[:160])
        with self._lock:
            self._states[key] = state
            self._running = None


def _meta(out: Path) -> Path:
    return out.with_name(out.name + ".json")


def _current(out: Path, identity: dict) -> bool:
    try:
        return out.is_file() and json.loads(_meta(out).read_text(encoding="utf-8")) == identity
    except (OSError, ValueError):
        return False


READER = ReplayTracksReader()
