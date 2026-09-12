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


def merge_path_list(existing: str, additions: list[Path]) -> str:
    """Comma-join paths, preserving existing text and dropping duplicates."""
    tokens = existing.split(",") if existing else []
    normalize = lambda value: ntpath.normcase(ntpath.normpath(value.strip()))
    present = {normalize(token) for token in tokens if token.strip()}
    for path in additions:
        text = str(path)
        if text and normalize(text) not in present:
            tokens.append(text)
            present.add(normalize(text))
    return ",".join(tokens)


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
