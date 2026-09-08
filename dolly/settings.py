"""Small, user-wide preferences file for the capture binding.

The capture-enabled switch intentionally never persists. Invalid preferences
raise on load so the UI can explain its fallback without changing the file.
An explicit save preserves invalid existing content in a sibling ``.invalid``
backup before replacing it atomically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import tempfile

from .bindings import CaptureBinding, DEFAULT_BINDING

SETTINGS_VERSION = 1
MAX_SETTINGS_BYTES = 64 * 1024


@dataclass(frozen=True)
class AppSettings:
    capture_binding: CaptureBinding = field(default_factory=lambda: DEFAULT_BINDING)

    def __post_init__(self) -> None:
        if not isinstance(self.capture_binding, CaptureBinding):
            raise ValueError("capture_binding must be a CaptureBinding.")
        # Validate here as well as at the JSON boundary before saving anything.
        CaptureBinding.from_dict(self.capture_binding.to_dict())


def settings_path() -> Path:
    """Return the per-user settings path, shared by extracted Dolly versions."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata and appdata.strip() else Path.home() / ".config"
    return base / "DeadlockDolly" / "settings.json"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate settings field: {key}.")
        result[key] = value
    return result


def _decode_settings(data: bytes) -> AppSettings:
    if len(data) > MAX_SETTINGS_BYTES:
        raise ValueError("Dolly settings exceed the 64 KiB size limit.")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Dolly settings are not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "capture_binding"}:
        raise ValueError("Dolly settings must contain version and capture_binding only.")
    if type(raw["version"]) is not int or raw["version"] != SETTINGS_VERSION:
        raise ValueError(f"Unsupported Dolly settings version: {raw['version']!r}.")
    return AppSettings(capture_binding=CaptureBinding.from_dict(raw["capture_binding"]))


def load_settings(path: str | os.PathLike[str] | None = None) -> AppSettings:
    """Load preferences; missing files use defaults, invalid files raise.

    This function never writes or renames anything. The UI should catch
    ``ValueError`` and ``OSError``, explain the problem, and use ``AppSettings()``.
    """
    target = Path(path) if path is not None else settings_path()
    try:
        with target.open("rb") as handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return AppSettings()
    return _decode_settings(data)


def _backup_invalid_settings(target: Path) -> None:
    """Preserve malformed content only when an explicit save will replace it."""
    try:
        with target.open("rb") as handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return
    try:
        _decode_settings(data)
    except ValueError:
        backup: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=target.name + ".",
                suffix=".invalid", delete=False,
            ) as destination:
                backup = Path(destination.name)
                with target.open("rb") as source:
                    shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            if backup is not None:
                backup.unlink(missing_ok=True)
            raise


def save_settings(
    settings: AppSettings, path: str | os.PathLike[str] | None = None,
) -> None:
    """Atomically save preferences, retaining a backup of invalid old content.

    Saving failures raise and leave the original file in place. Backups are
    named ``settings.json.<unique>.invalid`` in the same folder.
    """
    if not isinstance(settings, AppSettings):
        raise ValueError("settings must be an AppSettings instance.")
    payload = {
        "version": SETTINGS_VERSION,
        "capture_binding": settings.capture_binding.to_dict(),
    }
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _decode_settings(data)
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=target.name + ".",
            suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _backup_invalid_settings(target)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
