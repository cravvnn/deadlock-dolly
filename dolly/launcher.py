"""Windows-only Deadlock development launcher with recoverable config mounting.

``game_path`` may be the Deadlock installation root, its ``game`` or ``citadel``
directory, or ``game/bin/win64/deadlock.exe`` (legacy ``citadel.exe`` is also
supported). Steam launch settings are not edited.
The current gameinfo is backed up, temporarily mounted, and restored byte-for-byte
after unlocker initialization or game exit; recovery also runs before next launch.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any
import uuid
from .runtime import application_root, resource_root, external_program_environment
from .native_bridge import NativeBridge, ABI as NATIVE_ABI

PACKAGE_ROOT = application_root(Path(__file__).resolve().parent.parent)
UNLOCKER_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "third_party" / "cvar_unlocker"
NATIVE_ROOT = resource_root(Path(__file__).resolve().parent.parent) / "native"
UNLOCKER_SHA256 = "e86f270b1dedc81fd54a230f0080eee568a4f2bd39e1f41080dcf71d833267ba"
NATIVE_GAME_SHA256 = {
    "bin/win64/tier0.dll": (
        "b4300eb0abfe73e1e877516ab6b8bdd1a1bdb4ffc47c7515a349d0623b852f69",
        "b3192eac3cb8c54ac3f9c7aaf7c725ddfcc2dc46d99ba13d16177b6ebf736ebc",
    ),
    "citadel/bin/win64/client.dll": (
        "c7d068857c617c9c41d2c501865a94d93c52f3081864623ae23146e495f3021b",
        "769bf1e74afd67ab0aa02fa94c0c7eb3c133991d32c43e099289210511551a2b",
    ),
    "bin/win64/engine2.dll": (
        "887201acec33837fdb18d73c04f8e0894971d26eebafe992a28a12fada118afb",
        "301d042c7443090241d7b83244747bf8a32916f61df60aea5d8a1799f432ef8d",
    ),
}
STEAM_APP_ID = "1422450"
GAME_EXECUTABLE_NAMES = ("deadlock.exe", "citadel.exe")


class LaunchError(RuntimeError):
    """Actionable launch failure; suitable for display in the GUI."""


@dataclass(frozen=True)
class GamePaths:
    root: Path
    game_dir: Path
    citadel_dir: Path
    executable: Path
    gameinfo: Path


@dataclass(frozen=True)
class _Token:
    value: str
    start: int
    end: int
    kind: str = "text"


@dataclass
class _Entry:
    key: _Token
    value: _Token | None = None
    children: list["_Entry"] | None = None
    opening: _Token | None = None
    closing: _Token | None = None
    end: int = 0


def _tokens(text: str) -> list[_Token]:
    """Tokenize Valve KeyValues, retaining offsets for localized edits."""
    out: list[_Token] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace() or text[pos] == "\ufeff":
            pos += 1
            continue
        if text.startswith("//", pos):
            end = text.find("\n", pos + 2)
            pos = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                raise LaunchError("gameinfo.gi contains an unterminated block comment.")
            pos = end + 2
            continue
        start = pos
        if text[pos] in "{}":
            out.append(_Token(text[pos], pos, pos + 1, "brace"))
            pos += 1
        elif text[pos] == '"':
            pos += 1
            value = []
            while pos < len(text) and text[pos] != '"':
                if text[pos] == "\\" and pos + 1 < len(text) and text[pos + 1] in '\\"':
                    pos += 1
                value.append(text[pos])
                pos += 1
            if pos == len(text):
                raise LaunchError("gameinfo.gi contains an unterminated quoted value.")
            pos += 1
            out.append(_Token("".join(value), start, pos))
        elif text[pos] == "[":
            end = text.find("]", pos + 1)
            if end < 0:
                raise LaunchError("gameinfo.gi contains an unterminated condition.")
            pos = end + 1
            out.append(_Token(text[start:pos], start, pos, "condition"))
        else:
            while pos < len(text) and not text[pos].isspace() and text[pos] not in '{}"':
                if text.startswith("//", pos) or text.startswith("/*", pos):
                    break
                pos += 1
            if pos == start:
                raise LaunchError("Unsupported gameinfo.gi syntax.")
            out.append(_Token(text[start:pos], start, pos))
    return out


def _parse(text: str) -> list[_Entry]:
    tokens = _tokens(text)
    index = 0

    def block(nested: bool = False) -> tuple[list[_Entry], _Token | None]:
        nonlocal index
        entries: list[_Entry] = []
        while index < len(tokens):
            key = tokens[index]
            if key.value == "}" and key.kind == "brace":
                if not nested:
                    raise LaunchError("gameinfo.gi has an unmatched closing brace.")
                index += 1
                return entries, key
            if key.kind != "text":
                raise LaunchError("Unsupported gameinfo.gi key syntax.")
            index += 1
            if index >= len(tokens):
                raise LaunchError("gameinfo.gi ends before a key's value.")
            value = tokens[index]
            index += 1
            if value.value == "{" and value.kind == "brace":
                children, closing = block(True)
                assert closing is not None
                entry = _Entry(key, children=children, opening=value, closing=closing, end=closing.end)
            elif value.kind == "text":
                entry = _Entry(key, value=value, end=value.end)
            else:
                raise LaunchError("Unsupported gameinfo.gi value syntax.")
            while index < len(tokens) and tokens[index].kind == "condition":
                entry.end = tokens[index].end
                index += 1
            entries.append(entry)
        if nested:
            raise LaunchError("gameinfo.gi has an unclosed block.")
        return entries, None

    return block()[0]


def _named(entries: list[_Entry], name: str) -> list[_Entry]:
    return [entry for entry in entries if entry.key.value.casefold() == name.casefold()]


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def make_gameinfo(original: str, overlay_name: str) -> str:
    """Temporarily mount the plugin in current FileSystem/SearchPaths only.

    The generated overlay name is a restricted, single relative directory.
    All existing relative paths retain their meaning because the file stays in
    its original directory. Other blocks stay exact.
    """
    if not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay_name):
        raise LaunchError("Invalid temporary plugin directory name.")
    roots = _named(_parse(original), "GameInfo")
    if len(roots) != 1 or roots[0].children is None:
        raise LaunchError("Expected one GameInfo block in the current gameinfo.gi.")
    filesystems = _named(roots[0].children, "FileSystem")
    if len(filesystems) != 1 or filesystems[0].children is None:
        raise LaunchError("Expected one FileSystem block in the current gameinfo.gi.")
    searches = _named(filesystems[0].children, "SearchPaths")
    if len(searches) != 1 or searches[0].children is None:
        raise LaunchError("Expected one FileSystem/SearchPaths block in gameinfo.gi.")
    search = searches[0]
    entries = search.children
    assert search.opening and search.closing
    if any(entry.children is not None for entry in entries):
        raise LaunchError("Nested SearchPaths entries are not supported; original file was not changed.")
    if not any(entry.key.value.casefold() == "game" for entry in entries):
        raise LaunchError("Current gameinfo.gi has no Game search path.")

    edits: list[tuple[int, int, str]] = []
    for entry in entries:
        assert entry.value is not None
        raw = entry.value.value.replace("\\", "/")
        if entry.key.value.casefold() == "game" and raw.strip("/").casefold() == "citadel/cvar_unlocker":
            # Avoid mounting a user's second unlocker in the development session.
            edits.append((entry.key.start, entry.end, "// Existing unlocker mount temporarily replaced by Dolly."))
            continue

    newline = "\r\n" if "\r\n" in original else "\n"
    first_game = next(entry for entry in entries if entry.key.value.casefold() == "game")
    line_start = original.rfind("\n", 0, first_game.key.start) + 1
    prefix = original[line_start:first_game.key.start]
    indent = prefix if not prefix.strip() else "\t\t\t"
    additions = ["// Dolly temporary development mount; original bytes are backed up in this session's logs."]
    kinds = {entry.key.value.casefold() for entry in entries}
    if "mod" not in kinds:
        additions.append('Mod\t"citadel"')
    if "write" not in kinds:
        additions.append('Write\t"citadel"')
    additions.append('Game\t' + _quote(overlay_name + "/cvar_unlocker"))
    insertion = (newline + indent).join(additions) + newline + indent
    edits.append((first_game.key.start, first_game.key.start, insertion))
    result = original
    # Replacement precedes insertion at identical start positions.
    for start, end, value in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        result = result[:start] + value + result[end:]
    _parse(result)
    return result


def _find_game_executable(directory: Path) -> Path | None:
    """Prefer the current executable name when a folder was selected."""
    # Windows filenames are case-insensitive. Retain their actual spelling when
    # inspecting an installation copied onto a case-sensitive filesystem too.
    if directory.is_dir():
        files = {entry.name.casefold(): entry for entry in directory.iterdir() if entry.is_file()}
        for name in GAME_EXECUTABLE_NAMES:
            if name in files:
                return files[name]
    return None


def validate_game(path: str | os.PathLike[str]) -> GamePaths:
    value = Path(path).expanduser().resolve()
    if any(c in str(value) for c in '\r\n\x00";+'):
        raise LaunchError("The game path contains console separators. Use a Steam library path without quotes, semicolons, or plus signs.")
    selected_executable = value if value.suffix.casefold() == ".exe" else None
    if selected_executable is not None:
        if selected_executable.name.casefold() not in GAME_EXECUTABLE_NAMES:
            raise LaunchError("Select Deadlock's deadlock.exe or legacy citadel.exe from game/bin/win64, or select the Deadlock installation folder.")
        if not selected_executable.is_file():
            raise LaunchError(f"The selected Deadlock executable is missing: {selected_executable}. Browse to the installed deadlock.exe or legacy citadel.exe in game/bin/win64.")
    candidates = [value, *list(value.parents)[:5]]
    missing: list[str] = []
    for root in candidates:
        game = root / "game"
        executable_dir = game / "bin" / "win64"
        if selected_executable is not None and selected_executable.parent != executable_dir:
            continue
        executable = selected_executable or _find_game_executable(executable_dir)
        gameinfo = game / "citadel" / "gameinfo.gi"
        if executable is not None and gameinfo.is_file():
            if not (game / "citadel" / "bin" / "win64" / "server.dll").is_file():
                raise LaunchError(f"Deadlock server.dll is missing: {game / 'citadel/bin/win64/server.dll'}. Verify the game's installed files in Steam.")
            return GamePaths(root, game, game / "citadel", executable, gameinfo)
        if executable is not None or executable_dir.is_dir() or (game / "citadel").is_dir():
            absent = []
            if executable is None:
                absent.append(f"game executable (deadlock.exe or legacy citadel.exe) in {executable_dir}")
            if not gameinfo.is_file():
                absent.append(f"gameinfo.gi at {gameinfo}")
            missing.append("Missing Deadlock " + " and ".join(absent) + ". Verify the game's installed files in Steam.")
    if missing:
        raise LaunchError(missing[0])
    raise LaunchError("Select the Deadlock installation folder containing game/bin/win64/deadlock.exe (or legacy citadel.exe) and game/citadel/gameinfo.gi, or browse directly to that executable.")


def _game_is_running(processes: set[str]) -> bool:
    return any(name.casefold() in GAME_EXECUTABLE_NAMES for name in processes)


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        import winreg
        for hive, key, value in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
    for name in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]) / "Steam")
    return list(dict.fromkeys(roots))


def discover_game() -> Path | None:
    """Return the installation root from Steam registry/library manifests."""
    roots = _steam_roots()
    libraries = list(roots)
    for root in roots:
        manifest = root / "steamapps" / "libraryfolders.vdf"
        try:
            entries = _parse(manifest.read_text(encoding="utf-8-sig"))
            sections = _named(entries, "libraryfolders")
            for section in sections:
                for entry in section.children or []:
                    if entry.children:
                        for folder in _named(entry.children, "path"):
                            if folder.value:
                                libraries.append(Path(folder.value.value))
                    elif entry.key.value.isdigit() and entry.value:
                        libraries.append(Path(entry.value.value))
        except (OSError, UnicodeError, LaunchError):
            pass
    for library in dict.fromkeys(libraries):
        install_name = "Deadlock"
        try:
            app = _parse((library / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf").read_text(encoding="utf-8-sig"))
            states = _named(app, "AppState")
            if states:
                names = _named(states[0].children or [], "installdir")
                if names and names[0].value:
                    candidate = names[0].value.value
                    if not any(c in candidate for c in "/\\:") and candidate not in (".", ".."):
                        install_name = candidate
        except (OSError, UnicodeError, LaunchError):
            pass
        try:
            return validate_game(library / "steamapps" / "common" / install_name).root
        except LaunchError:
            continue
    return None


def running_processes() -> set[str]:
    """Enumerate process names without localized tasklist parsing or shell use."""
    if os.name != "nt":
        raise LaunchError("Deadlock Dolly's game launcher requires 64-bit Windows Python.")
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise LaunchError("Could not inspect running processes; launch was refused.")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise LaunchError("Could not inspect running processes; launch was refused.")
        names = set()
        while True:
            names.add(entry.szExeFile.casefold())
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() not in (0, 18):  # ERROR_NO_MORE_FILES
                    raise LaunchError("Process enumeration was incomplete; launch was refused.")
                break
        return names
    finally:
        kernel.CloseHandle(snapshot)


def _check_runtime() -> None:
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise LaunchError("Launching Deadlock requires Windows and 64-bit Python 3.10 or newer.")


def _listener_table_rows(data: bytes, family: int) -> list[tuple[int, int]]:
    """Decode IP Helper listener rows as (localhost-capable port, owner PID)."""
    if len(data) < 4:
        raise LaunchError("Windows returned an incomplete TCP listener table.")
    count = struct.unpack_from("<I", data)[0]
    row_size = 24 if family == 2 else 56
    if count > (len(data) - 4) // row_size:
        raise LaunchError("Windows returned a truncated TCP listener table.")
    listeners = []
    for index in range(count):
        start = 4 + index * row_size
        if family == 2:
            address = data[start + 4:start + 8]
            port = struct.unpack_from("<I", data, start + 8)[0]
            pid = struct.unpack_from("<I", data, start + 20)[0]
            local = address in (b"\0\0\0\0", b"\x7f\0\0\x01")
        else:
            address = data[start:start + 16]
            port = struct.unpack_from("<I", data, start + 20)[0]
            pid = struct.unpack_from("<I", data, start + 52)[0]
            local = address == b"\0" * 16  # An IPv6 wildcard may accept IPv4 too.
        if local:
            listeners.append((socket.ntohs(port & 0xFFFF), pid))
    return listeners


def _tcp_listeners() -> list[tuple[int, int]]:
    _check_runtime()
    from ctypes import wintypes
    iphelper = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphelper.GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    listeners: list[tuple[int, int]] = []
    for family in (2, 23):  # Windows AF_INET and AF_INET6.
        size = wintypes.DWORD()
        result = get_table(None, ctypes.byref(size), False, family, 3, 0)
        if result in (50, 87) and family == 23:  # IPv6 not supported on this system.
            continue
        if result not in (0, 122):
            raise LaunchError(f"Could not verify the console listener's Windows process owner (error {result}).")
        for _attempt in range(3):
            data = ctypes.create_string_buffer(max(size.value, 4))
            result = get_table(data, ctypes.byref(size), False, family, 3, 0)
            if result != 122:
                break
        if result != 0:
            raise LaunchError(f"Could not verify the console listener's Windows process owner (error {result}).")
        listeners.extend(_listener_table_rows(data.raw[:size.value], family))
    return listeners


def _validate_demo(path: str | os.PathLike[str] | None) -> Path | None:
    if path is None or not str(path).strip():
        return None
    value = Path(path).expanduser().resolve()
    if value.suffix.casefold() != ".dem" or not value.is_file():
        raise LaunchError("Select an existing, extracted .dem replay file.")
    if any(c in str(value) for c in '\r\n\x00";+'):
        raise LaunchError("The replay path contains console separators. Move it to a folder without quotes, semicolons, or plus signs.")
    return value


def _validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise LaunchError("Console port must be an integer from 1024 through 65535.")
    return port


def build_command(paths: GamePaths, overlay_dir: Path, port: int, demo_path: Path | None = None, protocol: str = "netcon", launch_options: str = "") -> list[str]:
    """Start the development lobby; replay loading waits for unlocker readiness.

    The selected replay is still validated here, but neither it nor an unlocker
    command is executed on the command line. The controller sends cvar_unhide
    after connecting in the pre-lobby/hideout and only then loads the replay.
    """
    _validate_port(port)
    if overlay_dir.parent.resolve() != paths.game_dir.resolve() or not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay_dir.name):
        raise LaunchError("The temporary plugin directory must be inside this Deadlock installation.")
    if protocol not in ("vconsole", "netcon"):
        raise LaunchError("Console protocol must be vconsole or netcon.")
    args = [str(paths.executable), "-dev", "-insecure", "-console"]
    args.extend(["-vconsole", "-vconport", str(port)] if protocol == "vconsole" else ["-netconport", str(port)])
    # Preserve citadel game identity; alternate game names can reject real demos.
    args.extend(["-game", str(paths.citadel_dir.resolve())])
    _validate_demo(demo_path)
    from .replays import parse_launch_options
    args.extend(parse_launch_options(launch_options))
    return args


def _verified_unlocker() -> Path:
    dll = UNLOCKER_ROOT / "bin" / "win64" / "server.dll"
    try:
        data = dll.read_bytes()
    except OSError as exc:
        raise LaunchError("The bundled cvar unlocker is missing. Extract the complete Dolly ZIP again.") from exc
    if hashlib.sha256(data).hexdigest() != UNLOCKER_SHA256:
        raise LaunchError("The bundled cvar unlocker failed its pinned SHA-256 check. Extract a fresh copy of this Dolly release.")
    if data[:2] != b"MZ" or len(data) < 64:
        raise LaunchError("The bundled cvar unlocker is not a Windows DLL.")
    offset = struct.unpack_from("<I", data, 60)[0]
    if data[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664:
        raise LaunchError("The bundled cvar unlocker is not an x64 Windows DLL.")
    return dll


def _verified_native(paths: GamePaths) -> Path:
    """Fail closed on changed game binaries or a missing native release build."""
    unsupported = []
    for relative, expected in NATIVE_GAME_SHA256.items():
        installed = paths.game_dir / relative
        try:
            with installed.open("rb") as stream:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
        except OSError as exc:
            raise LaunchError(f"Native camera needs the supported installed game file: {installed}") from exc
        accepted = (expected,) if isinstance(expected, str) else expected
        if digest not in accepted:
            unsupported.append(installed.name)
    if unsupported:
        raise LaunchError(f"Native camera does not support this {', '.join(unsupported)} build. Game files were not changed. Choose Console camera mode or use a native Dolly build for this Deadlock update.")
    dll = NATIVE_ROOT / "bin/win64/DollyNative.dll"
    try:
        metadata = json.loads((NATIVE_ROOT / "build_info.json").read_text(encoding="utf-8"))
        data = dll.read_bytes()
    except (OSError, ValueError) as exc:
        raise LaunchError("The native camera build is missing. Extract the complete Windows Dolly release, or choose Console camera mode.") from exc
    if not isinstance(metadata, dict) or type(metadata.get("abi")) is not int or metadata["abi"] != NATIVE_ABI or not isinstance(metadata.get("sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"]) is None:
        raise LaunchError("The native camera build manifest is invalid; extract a fresh Windows Dolly release.")
    if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise LaunchError("The native camera DLL failed its SHA-256 check; extract a fresh Windows Dolly release.")
    offset = struct.unpack_from("<I", data, 60)[0] if len(data) >= 64 else len(data)
    if data[:2] != b"MZ" or offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0" or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664:
        raise LaunchError("The native camera DLL is not an x64 Windows DLL.")
    return dll


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace one file atomically after flushing its new contents to disk."""
    descriptor, name = tempfile.mkstemp(prefix=".dolly-write-", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _save_record(session_dir: Path, record: dict[str, Any]) -> None:
    _atomic_write(session_dir / "session.json", (json.dumps(record, indent=2) + "\n").encode("utf-8"))


def _load_record(session_dir: Path) -> dict[str, Any]:
    try:
        from .session_cleanup import _plain_path, _plain_ancestors
        if not _plain_ancestors(session_dir) or not _plain_path(session_dir / "session.json"):
            raise ValueError("linked session directory or journal")
        record = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("owner") != "Deadlock Dolly" or Path(record["session_dir"]).resolve() != session_dir.resolve():
            raise ValueError("unrecognized session owner or directory")
        paths = validate_game(Path(record["original_gameinfo"]).parent)
        if paths.gameinfo.resolve() != Path(record["original_gameinfo"]).resolve():
            raise ValueError("unexpected gameinfo target")
        overlay = Path(record["overlay_dir"])
        if overlay.parent.resolve() != paths.game_dir.resolve() or not re.fullmatch(r"citadel_dolly_[a-z0-9_]+", overlay.name):
            raise ValueError("unexpected plugin mount directory")
        if record["backup_name"] != "original.gameinfo.gi":
            raise ValueError("unexpected backup name")
        if not all(re.fullmatch(r"[0-9a-f]{64}", record[key]) for key in ("original_sha256", "patched_sha256")):
            raise ValueError("invalid content hashes")
        return record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LaunchError(f"Could not read Dolly's recovery journal at {session_dir}: {exc}") from exc


def _restore_record(session_dir: Path) -> bool:
    record = _load_record(session_dir)
    if record.get("config_state") == "restored":
        return True
    target = Path(record["original_gameinfo"])
    backup = session_dir / record["backup_name"]
    try:
        from .session_cleanup import _plain_path
        if not _plain_path(backup) or not _plain_path(target):
            raise LaunchError("Linked game configuration or backup left untouched during recovery.")
        original = backup.read_bytes()
        if hashlib.sha256(original).hexdigest() != record["original_sha256"]:
            raise LaunchError(f"Dolly's original gameinfo backup failed its hash check. Nothing was overwritten. Backup: {backup}")
        current = target.read_bytes()
        digest = hashlib.sha256(current).hexdigest()
        if digest == record["original_sha256"]:
            record["config_state"] = "restored"
        elif digest == record["patched_sha256"]:
            _atomic_write(target, original, record.get("original_mode"))
            record["config_state"] = "restored"
        else:
            record["config_state"] = "conflict"
            _save_record(session_dir, record)
            raise LaunchError(f"Deadlock gameinfo.gi changed after Dolly mounted its plugin. Dolly left those newer bytes untouched. Original backup: {backup}\nCompare the current file with this backup before another Dolly launch; recovery remains pending.")
        record["restored_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_record(session_dir, record)
        return True
    except OSError as exc:
        raise LaunchError(f"Could not restore Deadlock gameinfo.gi: {exc}\nOriginal backup: {backup}. Exit Deadlock, then use File > Recover game configuration in Dolly.") from exc


def recover_pending(game_path: str | os.PathLike[str] | None = None) -> list[str]:
    """Restore our unfinished transactions only while no Deadlock process runs.

    Conflicting Steam/user changes are never overwritten. Their pending journal
    remains visible so another launch cannot silently bury the recovery issue.
    """
    _check_runtime()
    if _game_is_running(running_processes()):
        raise LaunchError("Exit Deadlock before recovering a previous Dolly game configuration.")
    from .session_cleanup import remove_overlay, recover_orphans
    selected = validate_game(game_path) if game_path is not None else None
    expected = selected.gameinfo if selected is not None else None
    installations = {selected.game_dir: selected} if selected is not None else {}
    recovered: list[str] = []
    for journal in sorted((PACKAGE_ROOT / "logs").glob("*/session.json")):
        try:
            preliminary = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LaunchError(f"Unreadable Dolly session journal: {journal}. Check this file before relaunching: {exc}") from exc
        if not isinstance(preliminary, dict) or preliminary.get("owner") != "Deadlock Dolly" or "patched_sha256" not in preliminary:
            continue
        if expected is not None and Path(preliminary.get("original_gameinfo", "")).resolve() != expected.resolve():
            continue
        pending = preliminary.get("config_state") != "restored"
        if not pending and not Path(preliminary.get("overlay_dir", "")).exists():
            continue
        if not pending and Path(preliminary.get("session_dir", "")).resolve() != journal.parent.resolve():
            # A portable folder may have been moved with its historical logs.
            # Do not rewrite that old journal's identity; discover its marked
            # mount independently and verify the current game search paths.
            paths = validate_game(Path(preliminary["original_gameinfo"]).parent)
            installations[paths.game_dir] = paths
            continue
        record = _load_record(journal.parent)
        paths = validate_game(Path(record["original_gameinfo"]).parent)
        installations[paths.game_dir] = paths
        _restore_record(journal.parent)
        overlay = Path(record["overlay_dir"])
        removed = remove_overlay(overlay, paths, journal.parent)
        if pending or removed:
            recovered.append(str(journal.parent))
    if selected is None:
        discovered = discover_game()
        if discovered is not None:
            paths = validate_game(discovered)
            installations[paths.game_dir] = paths
    for paths in installations.values():
        recovered.extend(recover_orphans(paths))
    return list(dict.fromkeys(recovered))


@dataclass
class Session:
    process: subprocess.Popen[Any]
    session_dir: Path
    overlay_dir: Path
    log_path: Path
    command: tuple[str, ...]
    port: int
    protocol: str = "netcon"
    native: NativeBridge | None = field(default=None, repr=False)
    _log_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _restore_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cleanup_handed_off: bool = field(default=False, repr=False)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def status(self) -> str:
        code = self.process.poll()
        return "running" if code is None else f"exited ({code})"

    def owns_console_port(self) -> bool:
        """True only if this live game's PID owns the chosen local TCP port."""
        if not self.running:
            return False
        owners = {pid for port, pid in _tcp_listeners() if port == self.port}
        return owners == {self.pid}

    def log(self, message: str) -> None:
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + str(message) + "\n")

    def read_log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def record_exit(self, code: int) -> None:
        """Keep the exit result in the same durable journal as launch recovery.

        Serialize with gameinfo restoration so an exit notification cannot
        overwrite a newer config_state with an earlier copy of the journal.
        """
        with self._restore_lock:
            record = _load_record(self.session_dir)
            record["exit_code"] = int(code)
            record["exited_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _save_record(self.session_dir, record)

    def restore_gameinfo(self) -> bool:
        """Restore original bytes after the caller confirms unlocker loaded.

        Safe to call during our game session once its plugin has initialized.
        On external modifications, raise with backup location and do not write.
        """
        with self._restore_lock:
            result = _restore_record(self.session_dir)
            self.log("Original gameinfo.gi restored; loaded plugin stays available in this development process.")
            return result

    def close(self) -> bool:
        """Restore config and remove our plugin directory after game exit.

        Never terminate or attach to a game. Returns False while the game runs;
        the UI should call restore_gameinfo explicitly before closing itself.
        """
        if self.running:
            return False
        if self.native is not None:
            self.native.close()
            self.record_native_diagnostics()
        self.restore_gameinfo()
        from .session_cleanup import remove_overlay
        try:
            record = _load_record(self.session_dir)
            paths = validate_game(Path(record["original_gameinfo"]).parent)
            if remove_overlay(self.overlay_dir, paths, self.session_dir):
                self.log("Removed the temporary plugin directory after game exit.")
        except (OSError, ValueError, LaunchError) as exc:
            self.log(f"Temporary plugin directory cleanup deferred: {exc}")
        return True

    def record_native_diagnostics(self) -> None:
        if self.native is None:
            return
        try:
            snapshot = self.native.diagnostics()
            if isinstance(snapshot, dict):
                _atomic_write(self.session_dir / "native_diagnostics.json",
                              (json.dumps(snapshot, indent=2) + "\n").encode("utf-8"))
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics must not prevent configuration recovery.

    def handoff_cleanup(self) -> bool:
        """Keep game-exit cleanup alive when the desktop editor is closing."""
        if not self.running:
            return self.close()
        if self._cleanup_handed_off:
            return True
        self.record_native_diagnostics()
        from .session_cleanup import start_waiter
        try:
            start_waiter(self)
            self._cleanup_handed_off = True
            self.log("Dolly is closing; a background cleanup helper will remove its temporary plugin files after this game process exits.")
            return True
        except (OSError, ValueError) as exc:
            self.log(f"Could not start background cleanup; the next Dolly launch will retry temporary-file removal: {exc}")
            return False


def launch(game_path: str | os.PathLike[str], demo_path: str | os.PathLike[str] | None = None, port: int = 29090, protocol: str = "netcon", native: bool = False, launch_options: str = "") -> Session:
    _check_runtime()
    from .replays import parse_launch_options
    parse_launch_options(launch_options)  # Validate before changing game files.
    paths = validate_game(game_path)
    port = _validate_port(port)
    if protocol not in ("vconsole", "netcon"):
        raise LaunchError("Console protocol must be vconsole or netcon.")
    demo = _validate_demo(demo_path)
    processes = running_processes()
    if _game_is_running(processes):
        raise LaunchError("Deadlock is already running. Exit it before using Dolly; Dolly only controls the development session it launches.")
    if not isinstance(native, bool):
        raise LaunchError("Native camera selection must be a boolean.")
    native_dll = _verified_native(paths) if native else None
    recover_pending(paths.root)
    if "steam.exe" not in processes:
        raise LaunchError("Open Steam and sign in before launching Deadlock through Dolly.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            # Exclusive bind on Windows prevents claiming another app's listener.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise LaunchError(f"Console port {port} is busy. Close the previous Dolly/game session or choose another port.") from exc
    dll = _verified_unlocker()
    process = None
    bridge = None
    try:
        original_data = paths.gameinfo.read_bytes()
        original = original_data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise LaunchError("Could not read the installed UTF-8 gameinfo.gi; no game files were changed.") from exc
    ident = time.strftime("%Y%m%d_%H%M%S", time.gmtime()) + "_" + uuid.uuid4().hex[:10]
    overlay = paths.game_dir / ("citadel_dolly_" + ident)
    patched_data = make_gameinfo(original, overlay.name).encode("utf-8")
    session_dir = PACKAGE_ROOT / "logs" / ident
    log_path = session_dir / "launch.log"
    try:
        if native:
            bridge = NativeBridge.create()
        session_dir.mkdir(parents=True, exist_ok=False)
        overlay.mkdir(exist_ok=False)
        marker = {"owner": "Deadlock Dolly", "session_dir": str(session_dir), "original_gameinfo": str(paths.gameinfo), "original_sha256": hashlib.sha256(original_data).hexdigest()}
        (overlay / ".dolly-session.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
        target = overlay / "cvar_unlocker" / "bin" / "win64"
        target.mkdir(parents=True)
        if native_dll is None:
            shutil.copyfile(dll, target / "server.dll")
        else:
            shutil.copyfile(native_dll, target / "server.dll")
            shutil.copyfile(dll, target / "dolly_cvar_unlocker.dll")
            (target / "dolly_native.cfg").write_text(
                f"DOLLY_NATIVE_1\n{bridge.token}\n{bridge.editor_pid}\n",
                encoding="ascii", newline="\n")
        command = build_command(paths, overlay, port, demo, protocol, launch_options)
        metadata = {**marker, "command": command, "selected_demo": str(demo) if demo is not None else None, "port": port, "protocol": protocol, "overlay_dir": str(overlay), "unlocker_version": "v0.5.2", "unlocker_sha256": UNLOCKER_SHA256, "validation": "Windows game startup and selected console protocol require a local probe.", "backup_name": "original.gameinfo.gi", "patched_sha256": hashlib.sha256(patched_data).hexdigest(), "original_mode": stat.S_IMODE(paths.gameinfo.stat().st_mode), "config_state": "prepared"}
        if native:
            metadata["native_camera"] = {"abi": NATIVE_ABI, "game_sha256": NATIVE_GAME_SHA256,
                                         "dll_sha256": hashlib.sha256(native_dll.read_bytes()).hexdigest()}
        # Durably save the original and journal before touching the installed file.
        _atomic_write(session_dir / "original.gameinfo.gi", original_data)
        _save_record(session_dir, metadata)
        log_path.write_text("Deadlock Dolly development launcher\n" + json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        # Check again immediately before process creation, after file preparation.
        if _game_is_running(running_processes()):
            raise LaunchError("Deadlock started while Dolly was preparing. Exit it and try again.")
        if hashlib.sha256(paths.gameinfo.read_bytes()).hexdigest() != metadata["original_sha256"]:
            raise LaunchError("Deadlock gameinfo.gi changed while Dolly was preparing. Launch was refused without overwriting the newer file.")
        _atomic_write(paths.gameinfo, patched_data, metadata["original_mode"])
        metadata["config_state"] = "mounted"
        _save_record(session_dir, metadata)
        with (session_dir / "game_stdout.log").open("ab") as output:
            with external_program_environment() as environment:
                options = {} if environment is None else {"env": environment}
                process = subprocess.Popen(command, cwd=str(paths.game_dir), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, shell=False, **options)
        session = Session(process, session_dir, overlay, log_path, tuple(command), port, protocol, native=bridge)
        if bridge is not None:
            bridge.bind_game(process.pid)
        try:
            session.log(f"Created development process PID {session.pid}; -dev and -insecure are mandatory.")
            session.log("Replay loading deferred until the controller confirms cvar_unhide in the pre-lobby/hideout.")
            metadata["pid"] = session.pid
            _save_record(session_dir, metadata)
        except OSError:
            # A disk failure after process creation must not delete a live mount.
            pass

        def watch() -> None:
            code = process.wait()
            try:
                session.record_exit(code)
            except (OSError, LaunchError) as exc:
                try:
                    session.log(f"Could not save process exit result ({code}): {exc}")
                except OSError:
                    pass
            try:
                session.log(f"Deadlock process exited with code {code}.")
            except OSError:
                pass
            try:
                session.close()
            except (OSError, LaunchError) as exc:
                try:
                    session.log(f"Recovery needs attention: {exc}")
                except OSError:
                    pass

        threading.Thread(target=watch, name="dolly-game-exit", daemon=True).start()
        return session
    except Exception as exc:
        if bridge is not None:
            bridge.close()
        if process is None:
            if (session_dir / "session.json").is_file():
                try:
                    _restore_record(session_dir)
                except LaunchError as recovery:
                    raise LaunchError(f"{exc}\nConfiguration recovery needs attention: {recovery}") from exc
            if overlay.is_dir() and (overlay / ".dolly-session.json").is_file():
                shutil.rmtree(overlay, ignore_errors=True)
        if isinstance(exc, LaunchError):
            raise
        raise LaunchError(f"Could not prepare or start the development game session: {exc}\nExtract Dolly into a writable folder and check {log_path}.") from exc


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Recover a Deadlock Dolly configuration after an interrupted launch.")
    parser.add_argument("--recover", action="store_true", help="Restore pending original gameinfo backups; Deadlock must be closed.")
    parser.add_argument("--game", default=None, help="Limit recovery to this Deadlock installation.")
    options = parser.parse_args()
    if not options.recover:
        parser.print_help()
        return 0
    try:
        restored = recover_pending(options.game)
        print(f"Recovered or cleaned {len(restored)} Dolly session(s)." if restored else "No pending Dolly recovery or temporary-folder cleanup is needed.")
        for directory in restored:
            print(directory)
        return 0
    except LaunchError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
