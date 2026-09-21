"""Point the private ReShade config at Dolly's bundled shader library.

Dolly ships shaders and a preset but never the ReShade runtime. This module
only edits the per-user config Dolly already hands to the user's own runtime
(``settings.reshade_config_path``), so the bundled library is discoverable
without touching a global ReShade installation. Every function is a no-op when
no library was packaged.
"""
from __future__ import annotations

from pathlib import Path
import ntpath
import os
import tempfile

PRESET_NAME = "Deadlock-Dolly.ini"
_SHADER_RELATIVE = Path("third_party") / "reshade_shaders"
_PRESET_RELATIVE = Path("assets") / "reshade" / PRESET_NAME
_GENERAL_SECTION = "[general]"


def _candidate_roots() -> list[Path]:
    from .runtime import resource_root

    try:
        root = resource_root()
    except (ImportError, OSError, ValueError):
        return []
    # Source builds keep the files at the repository root. A frozen onedir
    # bundle stores collected data under _internal, so consider both layouts.
    return [root, root / "_internal"]


def bundled_shader_paths() -> tuple[Path | None, Path | None]:
    """Return (Shaders, Textures) for the packaged library, or (None, None)."""
    for root in _candidate_roots():
        shaders = root / _SHADER_RELATIVE / "Shaders"
        if shaders.is_dir():
            textures = root / _SHADER_RELATIVE / "Textures"
            return shaders, (textures if textures.is_dir() else None)
    return None, None


def bundled_preset() -> Path | None:
    for root in _candidate_roots():
        preset = root / _PRESET_RELATIVE
        if preset.is_file():
            return preset
    return None


def _normalize_path(value: str) -> str:
    text = value.strip().strip('"').strip()
    return ntpath.normcase(ntpath.normpath(text))


def runtime_issue(selected, *, application_root: Path | None = None) -> str | None:
    """Explain why a selected ReShade runtime cannot be used yet, or None.

    Dolly never bundles, copies or deletes the user's ReShade runtime: it stays
    wherever the user put it. The two failures reported after an update are a
    path that no longer exists (moved, cleaned or replaced) and a DLL kept
    inside the Dolly folder, which a portable update can replace wholesale.
    """
    candidate = Path(str(selected)).expanduser()
    if not candidate.is_file():
        return (f"ReShade runtime missing: {candidate}. Choose the DLL again; Dolly never "
                "moves or deletes it.")
    if application_root is not None:
        try:
            inside = candidate.resolve().is_relative_to(Path(application_root).resolve())
        except (OSError, ValueError):
            inside = False
        if inside:
            return ("ReShade runtime is inside the Dolly folder, which an update replaces. "
                    "Move the DLL to its own folder (for example Documents\\Dolly-ReShade) "
                    "and select it again.")
    return None


def _bundled_family(value: str):
    """Name a bundled library folder (Shaders/Textures), or None."""
    text = value.strip().strip('"')
    parts = [part for part in ntpath.normpath(text).split("\\") if part and part != "."]
    if len(parts) >= 2 and parts[-2].lower() == "reshade_shaders":
        name = parts[-1].lower()
        if name in ("shaders", "textures"):
            return name
    return None


def merge_path_list(existing: str, additions: list[Path]) -> str:
    """Comma-join paths, dropping duplicates and stale bundled folders.

    Duplicate spellings already in the list are collapsed, and a library path
    from an older Dolly extraction is replaced by the current one instead of
    accumulating on every update. That keeps ReShade from listing each bundled
    effect several times.
    """
    tokens = existing.split(",") if existing else []
    additions_text = [str(path) for path in additions if str(path)]
    families = {_bundled_family(text) for text in additions_text}
    families.discard(None)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        text = token.strip()
        normalized = _normalize_path(text)
        if not normalized or normalized in seen:
            continue
        family = _bundled_family(text)
        if family is not None and family in families:
            continue  # Stale copy of Dolly's own library; the current path wins.
        seen.add(normalized)
        result.append(text)
    for text in additions_text:
        normalized = _normalize_path(text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return ",".join(result)


def _install_presets(config: Path, preset: Path | None) -> tuple[Path | None, list[Path]]:
    if preset is None:
        return None, []
    folder = config.parent / "presets"
    folder.mkdir(parents=True, exist_ok=True)
    sources = [preset]
    optional = preset.parent / "Deadlock-AO.ini"
    if optional != preset and optional.is_file():
        sources.append(optional)
    created = []
    for source in sources:
        target = folder / source.name
        data = source.read_bytes()
        try:
            with target.open("xb") as output:
                output.write(data)
        except FileExistsError:
            continue  # ReShade saves the user's edits here. Keep them on upgrades.
        created.append(target)
    return folder / preset.name, created


def _write_config(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_config(config_path) -> dict:
    """Merge bundled shader paths and preset into a ReShade config file.

    The existing file is preserved; only the ``[GENERAL]`` search paths are
    extended and ``PresetPath`` is filled in when it is still empty. Returns a
    summary and never raises when the bundled library is absent.
    """
    shaders, textures = bundled_shader_paths()
    preset = bundled_preset()
    summary = {"shaders": shaders, "textures": textures, "preset": preset, "changed": False}
    if shaders is None and textures is None and preset is None:
        return summary

    path = Path(config_path)
    try:
        original_bytes = path.read_bytes()
    except FileNotFoundError:
        original_bytes = b""
    original = original_bytes.decode("utf-8-sig")
    preset, installed = _install_presets(path, preset)
    summary["preset"] = preset
    summary["installed_presets"] = installed
    newline = "\r\n" if "\r\n" in original else ("\n" if original else "\r\n")
    lines = original.splitlines()

    header = next((i for i, line in enumerate(lines)
                   if line.strip().lower() == _GENERAL_SECTION), None)
    if header is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[GENERAL]")
        header = len(lines) - 1
    end = len(lines)
    for index in range(header + 1, len(lines)):
        if lines[index].lstrip().startswith("["):
            end = index
            break

    def find_field(name: str) -> tuple[int | None, str]:
        for index in range(header + 1, end):
            text = lines[index].strip()
            if "=" in text and text.split("=", 1)[0].strip().lower() == name.lower():
                return index, text.split("=", 1)[1]
        return None, ""

    desired: list[tuple[str, list[Path]]] = []
    if shaders is not None:
        desired.append(("EffectSearchPaths", [shaders]))
    if textures is not None:
        desired.append(("TextureSearchPaths", [textures]))
    for name, additions in desired:
        index, existing = find_field(name)
        value = merge_path_list(existing, additions)
        if index is None:
            lines.insert(end, f"{name}={value}")
            end += 1
        else:
            lines[index] = f"{name}={value}"

    if preset is not None:
        index, existing = find_field("PresetPath")
        if index is None:
            lines.insert(end, f"PresetPath={preset}")
        elif not existing.strip():
            lines[index] = f"PresetPath={preset}"

    result = newline.join(lines) + newline
    if result != original:
        bom = b"\xef\xbb\xbf" if original_bytes.startswith(b"\xef\xbb\xbf") else b""
        _write_config(path, bom + result.encode("utf-8"))
        summary["changed"] = True
    return summary
