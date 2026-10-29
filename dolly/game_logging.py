"""DLSS-compatible Windows startup and support logs, without a stdout handle.

The engine classifies any valid stdout file/pipe as a console application and
disables NVIDIA DLSS. Keep stderr, an engine-owned console file and received
console text instead. Output written only to stdout before the engine logger
starts cannot be recovered by these channels.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import threading

ENGINE_LOG = "engine_console.log"
CONSOLE_LOG = "console_transcript.log"
SESSION_LOGS = (ENGINE_LOG, CONSOLE_LOG)
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
        try:
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            yield {"startupinfo": info, "close_fds": True}
        finally:
            for handle, inherited in zip(handles, original):
                os.set_handle_inheritable(handle, inherited)


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
