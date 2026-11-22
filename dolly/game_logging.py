"""DLSS-compatible Windows startup and support logs, without a stdout handle.

The engine classifies any valid stdout file/pipe as a console application and
disables NVIDIA DLSS. Keep stderr, an engine-owned console file and received
console text instead. Output written only to stdout before the engine logger
starts cannot be recovered by these channels.
"""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
import subprocess
import threading
import time

ENGINE_LOG = "engine_console.log"
CONSOLE_LOG = "console_transcript.log"
SESSION_LOGS = (ENGINE_LOG, CONSOLE_LOG)
# Sessions whose game logs are kept, and how many a diagnostics export reads.
RETAINED_SESSIONS = 8
_SESSION_NAME = re.compile(r"\d{8}_\d{6}_[0-9a-f]{10}", re.IGNORECASE)
TRANSCRIPT_LIMIT = 8 * 1024 * 1024
_HANDLE_LOCK = threading.Lock()


@contextmanager
def gui_stdio(stderr_file):
    import msvcrt
    # Only these two handles may be inherited, and their original flags must
    # survive failed creation too. Never change the parent's standard handles.
    with _HANDLE_LOCK, open(os.devnull, "rb") as input_file:
        handles = [msvcrt.get_osfhandle(input_file.fileno()), msvcrt.get_osfhandle(stderr_file.fileno())]
        original = [os.get_handle_inheritable(handle) for handle in handles]
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESTDHANDLES
        info.hStdInput, info.hStdOutput, info.hStdError = handles[0], 0, handles[1]
        info.lpAttributeList = {"handle_list": handles}
        with ExitStack() as restore:
            for handle, inherited in reversed(list(zip(handles, original))):
                restore.callback(os.set_handle_inheritable, handle, inherited)
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            yield {"startupinfo": info, "close_fds": True}


def spawn(command, stderr_file, **options):
    # Supplying any ordinary Popen stream argument makes CPython construct a
    # replacement stdout pipe, defeating the engine's GUI-application test.
    if any(name in options for name in ("stdin", "stdout", "stderr", "startupinfo", "close_fds")):
        raise ValueError("DLSS startup owns its standard handles.")
    with gui_stdio(stderr_file) as windows_options:
        return subprocess.Popen(command, **options, **windows_options)


def append_transcript(path, text, *, limit=TRANSCRIPT_LIMIT):
    """Preserve early received output within a disk cap; recent_output keeps tails.

    Called only by a connection's reader. Reconnects append without erasing the
    first attempt. Open/close per chunk keeps evidence after abrupt app exit.
    """
    marker = b"\n[Dolly console transcript limit reached; see recent console output.]\n"
    if limit < len(marker):
        raise ValueError("Transcript limit is too small.")
    path = Path(path)
    with path.open("ab") as stream:
        remaining = max(0, limit - stream.tell())
        if not remaining:
            return
        data = text.encode("utf-8")
        if len(data) <= max(0, remaining - len(marker)):
            stream.write(data)
        else:
            prefix = data[:max(0, remaining - len(marker))].decode("utf-8", errors="ignore").encode("utf-8")
            stream.write(prefix + marker[:remaining - len(prefix)])
            # Consume spare bytes after a split multibyte character so a later
            # chunk cannot append a second marker or partial character.
            stream.write(b"\n" * max(0, remaining - len(prefix) - len(marker)))


def newest_sessions(logs_root):
    """Session folders, newest first by the launch time in their names.

    A name later than tomorrow was made while the clock was set ahead. Those
    go last, or they would outrank every real session until that date arrives.
    """
    limit = time.strftime("%Y%m%d_%H%M%S", time.gmtime(time.time() + 86400))
    folders = [path.parent for path in Path(logs_root).glob("*/session.json")
               if _SESSION_NAME.fullmatch(path.parent.name)]
    return sorted(folders, key=lambda folder: (folder.name[:15] <= limit, folder.name), reverse=True)


def prune_session_logs(logs_root, *, keep=RETAINED_SESSIONS):
    """Delete game logs from all but the newest ``keep`` session folders.

    Only the engine log and console transcript are removed; journals and the
    other small session files stay for recovery. ``keep`` matches how many
    sessions a diagnostics export reads, so nothing it would include is lost.
    Best-effort: a file that cannot be removed is left for the next launch.
    """
    try:
        folders = newest_sessions(logs_root)
    except OSError:
        return
    for folder in folders[keep:]:
        for name in SESSION_LOGS:
            try:
                (folder / name).unlink(missing_ok=True)
            except OSError:
                continue


def fatal_error(session_dir, *, tail=256_000, limit=300):
    """The last reason the engine logged for stopping itself, or None.

    Deadlock ends some crashes on purpose and says why first, for example a
    damaged content file. Read only the end of this launch's logs.
    """
    for name in SESSION_LOGS:
        try:
            with (Path(session_dir) / name).open("rb") as stream:
                size = stream.seek(0, os.SEEK_END)
                stream.seek(max(0, size - tail))
                text = stream.read(tail).decode("utf-8", errors="replace")
        except OSError:
            continue
        found = text.rfind("FATAL ERROR:")
        if found < 0:
            continue
        reason = " ".join(text[found + len("FATAL ERROR:"):].splitlines()[0].split())
        if reason:
            return reason if len(reason) <= limit else reason[:limit].rstrip() + "..."
    return None


UNLOCKER_REPORT = "dolly_unlocker_load.txt"
# Windows errors that mean a security policy or product refused the file.
_BLOCKED_BY_POLICY = {
    5: "access was denied",
    225: "the file was flagged by antivirus",
    226: "the file was flagged by antivirus",
    1260: "a Windows policy blocked this file",
    4551: "an Application Control policy blocked this file",
}


def unlocker_load_failure(*directories):
    """Why the native DLL could not start the cvar unlocker, or None.

    The native DLL writes a three-line report beside itself when the unlocker
    does not load. It is copied into the session folder at game exit, so read
    whichever of the given folders still has it.
    """
    for directory in directories:
        if not directory:
            continue
        try:
            with (Path(directory) / UNLOCKER_REPORT).open("rb") as stream:
                lines = stream.read(256).decode("ascii", errors="replace").split("\n")
        except OSError:
            continue
        if len(lines) < 3 or lines[0] != "DOLLY_UNLOCKER_LOAD_1":
            continue
        stage, error = lines[1].partition(" "), lines[2].partition(" ")
        if (stage[0] != "stage" or error[0] != "error" or not error[2].isdecimal()
                or stage[2] not in ("flags", "path", "read", "hash", "load", "export")):
            continue
        return {"stage": stage[2], "error": int(error[2])}
    return None


def unlocker_failure_advice(failure):
    """What to tell the user when the game could not get the unlocker interface."""
    stage = failure.get("stage") if failure else None
    code = failure.get("error", 0) if failure else 0
    blocked = _BLOCKED_BY_POLICY.get(code)
    if stage in ("load", "read") and blocked:
        return (f"Windows refused to load Dolly's cvar unlocker (error {code}: {blocked}). "
                "Smart App Control and some antivirus products block unsigned DLLs like this one. "
                "Turn off Smart App Control in Windows Security > App & browser control, or allow "
                "Dolly's folder and the Deadlock folder in your security software, then launch again.")
    if stage in ("read", "hash"):
        return ("Dolly's cvar unlocker was removed or changed after Dolly copied it into the game "
                "folder. Antivirus quarantine usually does this. Allow Dolly's folder and the "
                "Deadlock folder in your security software, then launch again.")
    if stage == "load":
        return (f"Windows could not load Dolly's cvar unlocker (error {code}). "
                "Export diagnostics so the cause can be checked.")
    if stage is not None:
        return (f"Dolly's native camera could not start its cvar unlocker (step: {stage}, "
                f"error {code}). Export diagnostics so the cause can be checked.")
    return ("Deadlock could not start Dolly's cvar unlocker. Windows security software that "
            "blocks or removes unsigned DLLs (Smart App Control, antivirus) is the usual cause. "
            "Allow Dolly's folder and the Deadlock folder, then launch again; export diagnostics "
            "if it keeps happening.")


def diagnostic_log(path, *, limit=2_000_000):
    """Keep startup and the latest failure when an engine log grows large."""
    path = Path(path)
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(0)
        if size <= limit:
            data = stream.read(limit)
        else:
            marker = b"\n[Dolly omitted the middle of this log; startup and latest output follow.]\n"
            head = min(64_000, limit // 2)
            data = stream.read(head)
            stream.seek(-(limit - head - len(marker)), os.SEEK_END)
            data += marker + stream.read(limit - head - len(marker))
    return data.decode("utf-8-sig", errors="replace")
