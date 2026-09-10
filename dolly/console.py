"""Local Source 2 console transport for the replay controller.

VConsole packet framing is independently implemented from the protocol described
by theokyr/CS2RemoteConsole (libvconsole/src/vconsole.cpp) and
uilton-oliveira/VConsoleLib.python.  No engine addresses or game-memory access.
This module does not establish replay authorization: the caller must do that
before it sends camera commands. ``send`` / ``request`` are internal transports,
never endpoints accepting arbitrary text from project files or a web UI.
"""
from __future__ import annotations

from collections import deque
import codecs
import ipaddress
import math
import re
import secrets
import socket
import struct
import threading
import time
from typing import Literal


VCONSOLE_VERSION = 0x00D40000
MAX_PACKET_SIZE = 65535
_HEADER = struct.Struct("!4sIHH")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ConsoleError(RuntimeError):
    """Connection, protocol, or command-response error."""


class ConsoleTimeout(ConsoleError, TimeoutError):
    """A response was not completed within the requested deadline."""


def error_text(output: str) -> str | None:
    """Return a recognizable console rejection, or None (not proof of success)."""
    patterns = (
        r"\bunknown (?:command|convar|cvar)\b", r"\bno such (?:command|convar|cvar)\b",
        r"\bno (?:cvar|convar) or command named\b",
        r"\b(?:command|convar|cvar).+\bnot found\b", r"\bis cheat protected\b",
        r"\bcheat command.+\bignored\b", r"\b(?:cannot|can't) (?:change|set|use)\b",
        r"\bnot currently playing back a demo\b", r"^\s*(?:\[[^\]]*\]\s*)*error\s*[:\-]",
        r"\bnot allowed\b", r"\brequires?\s+sv_cheats\b",
    )
    for line in _ANSI.sub("", output).splitlines():
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            return line.strip()
    return None


def parse_demo_info(output: str) -> dict:
    """Parse replay identity and metadata, without inventing a playback tick.

    The ``Demo contents for`` / protobuf metadata format was observed in a
    Deadlock 2026-09-07 runtime report. Its playback_ticks and playback_time are
    TOTAL duration, not the current playback position. ``tick`` remains None.
    A metadata-derived tick_rate is descriptive, never a live clock.

    The legacy status and pause formats appear in GameTracking-Deadlock,
    game/bin/win64/engine2_strings.txt at 70a264134418d4546776e3a993245fdb12138200:
      Playing back demo: '%s' at tick %u
      Error - Not currently playing back a demo.
      Demo paused at engine time %g, demo tick %d

    A pause notification alone is not a fresh demo_info response. No pause or
    speed state is inferred from a stationary tick.
    """
    clean = _ANSI.sub("", output)
    if re.search(r"Error\s*-\s*Not currently playing back a demo\.", clean, re.I):
        return {"playing": False, "tick": None, "name": None, "paused": None}
    matches = list(re.finditer(r"Playing back demo:\s*'([^\r\n]+)'\s+at tick\s+(\d+)\b", clean))
    if not matches:
        headers = list(re.finditer(r"^\s*Demo contents for\s+([^\r\n]+):\s*$", clean, re.M))
        if not headers:
            raise ValueError("Unrecognized demo_info output; capture diagnostics to update compatibility.")
        header = headers[-1]
        metadata = clean[header.end():]
        if not re.search(r"^\s*DemoFileHeader:", metadata, re.M):
            raise ValueError("Incomplete demo_info response: missing DemoFileHeader.")
        info = re.search(r"^\s*DemoFileInfo:\s*(.*)", metadata, re.M | re.S)
        if not info:
            raise ValueError("Incomplete demo_info response: missing DemoFileInfo.")
        fields = info[1]
        tick_field = re.search(r"(?:^|\n)\s*playback_ticks:\s*([^\s]+)", fields)
        time_field = re.search(r"(?:^|\n)\s*playback_time:\s*([^\s]+)", fields)
        total_ticks = None
        playback_time = None
        if tick_field:
            if not re.fullmatch(r"\d+", tick_field[1]):
                raise ValueError("Invalid total playback_ticks in demo_info metadata.")
            total_ticks = int(tick_field[1])
        if time_field:
            try:
                playback_time = float(time_field[1])
            except ValueError as exc:
                raise ValueError("Invalid total playback_time in demo_info metadata.") from exc
            if not math.isfinite(playback_time) or playback_time < 0:
                raise ValueError("Invalid total playback_time in demo_info metadata.")
        tick_rate = (total_ticks / playback_time
                     if total_ticks is not None and total_ticks > 0 and playback_time
                     else None)
        return {"playing": True, "tick": None, "name": header[1].strip(),
                "paused": None, "total_ticks": total_ticks,
                "playback_time": playback_time, "tick_rate": tick_rate}
    match = matches[-1]
    paused = None
    pause_matches = list(re.finditer(r"Demo paused at engine time\s+[-+0-9.eE]+,\s*demo tick\s+(\d+)\b", clean))
    if pause_matches and pause_matches[-1].start() > match.start() and int(pause_matches[-1][1]) == int(match[2]):
        paused = True
    return {"playing": True, "tick": int(match[2]), "name": match[1], "paused": paused}


def parse_demo_tick(output: str) -> dict:
    """Read only the live status line returned by a bare ``demo_goto`` query.

    The current Deadlock engine string is:
      Currently playing %d of %d ticks. Minutes:%.2f File:%s
    The older Source status line lacks File. In that case callers must obtain
    fresh replay identity separately. Minutes and the second tick count describe
    total length and cannot replace the first, current tick count.
    """
    clean = _ANSI.sub("", output)
    if re.search(r"Error\s*-\s*Not currently playing back a demo\.", clean, re.I):
        return {"playing": False, "tick": None, "total_ticks": None,
                "name": None, "paused": None}
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    pattern = (r"^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*Currently playing[ \t]+(\d+)[ \t]+of[ \t]+(\d+)"
               r"[ \t]+ticks\.[ \t]+Minutes:[ \t]*(" + number + r")"
               r"(?:[ \t]+File:[ \t]*([^\r\n]+))?[ \t]*\r?$")
    matches = list(re.finditer(pattern, clean, re.M))
    if not matches:
        raise ValueError("Unrecognized live replay status from demo_goto; export diagnostics to update compatibility.")
    match = matches[-1]
    current, total, minutes = int(match[1]), int(match[2]), float(match[3])
    if current > total or not math.isfinite(minutes) or minutes < 0:
        raise ValueError("Invalid live replay tick range in demo_goto response.")
    name = match[4].strip() if match[4] else None
    if match[4] is not None and not name:
        raise ValueError("Missing replay filename in demo_goto response.")
    if name and name[0] in "\"'" and name[-1] == name[0]:
        name = name[1:-1]
    return {"playing": True, "tick": current, "total_ticks": total,
            "name": name or None, "paused": None}


def encode_command(command: str, version: int = VCONSOLE_VERSION) -> bytes:
    """Encode a single trusted console command or semicolon command batch."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Command must be a nonempty string.")
    if any(char in command for char in ("\x00", "\r", "\n")):
        raise ValueError("Console command cannot contain NUL or line breaks.")
    payload = command.encode("utf-8") + b"\x00"
    length = _HEADER.size + len(payload)
    if length > MAX_PACKET_SIZE:
        raise ValueError("Console command exceeds the VConsole packet limit.")
    return _HEADER.pack(b"CMND", version, length, 0) + payload


class VConsoleDecoder:
    """Bounded streaming decoder; accepts split and coalesced TCP packets."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self._buffer.extend(data)
        result = []
        while len(self._buffer) >= _HEADER.size:
            kind, _version, length, _handle = _HEADER.unpack_from(self._buffer)
            if not all(65 <= byte <= 90 or 48 <= byte <= 57 for byte in kind):
                raise ConsoleError("Invalid VConsole packet type. Check console protocol/launch port.")
            if not _HEADER.size <= length <= MAX_PACKET_SIZE:
                raise ConsoleError("Invalid VConsole packet length.")
            if len(self._buffer) < length:
                break
            payload = bytes(self._buffer[_HEADER.size:length])
            del self._buffer[:length]
            if kind == b"PRNT":
                if len(payload) < 28:
                    raise ConsoleError("Truncated VConsole PRNT metadata.")
                # channel int32 + 20 unknown bytes + RGBA uint32 = 28 bytes.
                result.append(payload[28:].rstrip(b"\x00").decode("utf-8", errors="replace"))
            # AINF, CHAN, ADON, CVAR, CFGV, etc. are framed and ignored.
        return result


class ConsoleClient:
    """Thread-safe console connection restricted to numeric loopback addresses.

    A background reader drains game logs even between requests. Request output
    is delimited by random begin/end echoes; TCP fragmentation and echoed command
    text do not end a request early. Requests execute serially. An echo marks
    console processing, not completion of asynchronous work or proof that a
    renderer visibly applied a setting. Empty output is not proof of failure or
    success. ``recent_output`` retains received text outside request boundaries
    for diagnostics, including logs that arrive after an end marker. A caller
    that knows a command's completion messages may supply ``completion_patterns``
    to keep collecting that request until those messages actually arrive.
    """

    def __init__(self, protocol: Literal["vconsole", "netcon"] = "vconsole", *,
                 max_response_bytes: int = 1024 * 1024, version: int = VCONSOLE_VERSION) -> None:
        if protocol not in {"vconsole", "netcon"}:
            raise ValueError("Protocol must be vconsole or netcon.")
        if max_response_bytes < 1024:
            raise ValueError("Response limit must be at least 1024 bytes.")
        self.protocol = protocol
        self.version = version
        self.max_response_bytes = max_response_bytes
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition()
        self._events: deque[tuple[int, str]] = deque()
        self._event_bytes = 0
        self._sequence = 0
        self._failure: str | None = None

    @property
    def is_connected(self) -> bool:
        with self._condition:
            return self._socket is not None and self._failure is None

    def recent_output(self, limit_bytes: int = 262144) -> str:
        """Return a bounded tail of this connection's received console text.

        This is diagnostic evidence only, not an acknowledgement for a new
        request: the tail may contain old responses or unrelated engine logs.
        Reading it neither consumes events nor changes a pending request.
        """
        if isinstance(limit_bytes, bool) or not isinstance(limit_bytes, int) or limit_bytes <= 0:
            raise ValueError("Console history limit must be a positive integer.")
        with self._condition:
            chunks = [chunk for _, chunk in self._events]
        # The event queue is already bounded by max_response_bytes * 2. If a
        # slice starts inside a multibyte character, discard that partial prefix.
        return "".join(chunks).encode("utf-8")[-limit_bytes:].decode("utf-8", errors="ignore")

    def connect(self, host: str = "127.0.0.1", port: int = 29090, timeout: float = 3.0) -> None:
        if host == "localhost":
            host = "127.0.0.1"
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError as exc:
            raise ValueError("Only numeric loopback console addresses are supported.") from exc
        if not is_loopback:
            raise ValueError("The dolly console must be on this computer's loopback address.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid console port.")
        _validate_timeout(timeout)
        with self._lock:
            self.close()
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(0.25)
            except OSError as exc:
                raise ConsoleError(f"Cannot connect to local {self.protocol} on {host}:{port}: {exc}") from exc
            with self._condition:
                self._socket = sock
                self._failure = None
                self._events.clear()
                self._event_bytes = 0
                self._sequence = 0
            self._reader = threading.Thread(target=self._read_loop, args=(sock,), daemon=True, name="dolly-console")
            self._reader.start()

    def close(self) -> None:
        # May be called during a pending request to cancel it immediately.
        with self._condition:
            sock = self._socket
            self._socket = None
            self._failure = "Console connection closed."
            self._condition.notify_all()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=0.5)
        self._reader = None

    def _read_loop(self, sock: socket.socket) -> None:
        decoder = VConsoleDecoder()
        text_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                with self._condition:
                    if self._socket is not sock:
                        return
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConsoleError("Game closed the console connection.")
                chunks = decoder.feed(data) if self.protocol == "vconsole" else [text_decoder.decode(data)]
                with self._condition:
                    if self._socket is not sock:
                        return
                    for chunk in chunks:
                        if not chunk:
                            continue
                        self._sequence += 1
                        self._events.append((self._sequence, chunk))
                        self._event_bytes += len(chunk.encode("utf-8"))
                    while self._event_bytes > self.max_response_bytes * 2 and self._events:
                        _, old = self._events.popleft()
                        self._event_bytes -= len(old.encode("utf-8"))
                    self._condition.notify_all()
        except (OSError, ConsoleError) as exc:
            with self._condition:
                if self._socket is sock:
                    self._failure = str(exc)
                    self._condition.notify_all()

    def _send_locked(self, command: str) -> None:
        self._send_commands_locked((command,))

    def _send_commands_locked(self, commands: tuple[str, ...]) -> None:
        # Validate every command before writing any bytes. Keep the individual
        # protocol frames intact, but submit a request's begin/command/end in
        # one write so separate sends do not introduce extra polling delays.
        encoded = [encode_command(command, self.version) for command in commands]
        data = (b"".join(encoded) if self.protocol == "vconsole" else
                b"".join(command.encode("utf-8") + b"\n" for command in commands))
        with self._condition:
            sock, failure = self._socket, self._failure
        if sock is None or failure:
            raise ConsoleError(failure or "Console is not connected.")
        try:
            sock.sendall(data)
        except OSError as exc:
            with self._condition:
                self._failure = f"Console send failed: {exc}"
                self._condition.notify_all()
            raise ConsoleError(self._failure) from exc

    def send(self, command: str) -> None:
        """Send a trusted batch without waiting. Caller owns replay guards."""
        with self._lock:
            self._send_locked(command)

    def request(self, command: str, timeout: float = 2.0, *,
                completion_patterns: tuple[str, ...] | None = None) -> str:
        """Send a trusted command and collect its fresh, bounded response.

        Normally the end echo finishes collection. With ``completion_patterns``,
        all trusted regexes must also match response text, or a recognizable
        rejection must arrive. This permits an initialization command's delayed
        log messages without delaying ordinary camera-frame requests. The same
        timeout covers echo acknowledgement and semantic completion together;
        the serial request lock is held throughout. Historical output is never
        used to satisfy these patterns.
        """
        _validate_timeout(timeout)
        encode_command(command, self.version)
        patterns = _compile_completion_patterns(completion_patterns)
        with self._lock:
            begin = "DOLLY_BEGIN_" + secrets.token_hex(12)
            end = "DOLLY_END_" + secrets.token_hex(12)
            # Match a printed marker, never `echo MARKER` in a command echo.
            begin_re = _marker_re(begin)
            end_re = _marker_re(end)
            with self._condition:
                cursor = self._sequence
            deadline = time.monotonic() + timeout
            self._send_commands_locked(("echo " + begin, command, "echo " + end))
            output = ""
            began = False
            ended = False
            while True:
                with self._condition:
                    if self._events and cursor < self._events[0][0] - 1:
                        raise ConsoleError("Console log overflowed while waiting for a response.")
                    new = [(seq, text) for seq, text in self._events if seq > cursor]
                    if new:
                        cursor = new[-1][0]
                    failure = self._failure
                for _, chunk in new:
                    output += chunk
                if not began:
                    found = begin_re.search(output)
                    if found:
                        output = output[found.end():]
                        began = True
                if began and not ended:
                    found = end_re.search(output)
                    if found:
                        response = output[:found.start()]
                        if patterns:
                            # A worker may print its completion after the echo.
                            # Keep that fresh suffix while discarding delimiters.
                            response += output[found.end():]
                        # A line console can print the entered `echo END` before
                        # it prints END itself. Keep delimiters out of results.
                        output = "".join(line for line in response.splitlines(keepends=True)
                                         if not (end in line and "echo" in line))
                        ended = True
                if len(output.encode("utf-8")) > self.max_response_bytes:
                    raise ConsoleError("Console response exceeds the configured size limit.")
                if ended and (not patterns or error_text(output)
                              or all(pattern.search(output) for pattern in patterns)):
                    return output.strip("\r\n")
                if failure:
                    raise ConsoleError(failure)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if ended and patterns:
                        raise ConsoleTimeout(
                            f"{self.protocol} acknowledged {command.split()[0]!r}, but the expected "
                            f"completion messages did not arrive within {timeout:g}s. "
                            "Export diagnostics to inspect the received console text.")
                    raise ConsoleTimeout(f"No complete {self.protocol} response to {command.split()[0]!r} within {timeout:g}s.")
                with self._condition:
                    if self._sequence == cursor and not self._failure:
                        self._condition.wait(timeout=remaining)

    def supports(self, name: str, timeout: float = 2.0) -> bool:
        """Conservative name availability check. Does not prove runtime effect."""
        if not _NAME.fullmatch(name):
            raise ValueError("Expected one plain console variable/command name.")
        output = self.request("help " + name, timeout)
        if error_text(output):
            return False
        lines = [line for line in output.splitlines() if not re.search(r"\bhelp\s+" + re.escape(name) + r"\b", line)]
        return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", "\n".join(lines)))


def _marker_re(marker: str) -> re.Pattern:
    return re.compile(r"(?m)^[ \t]*(?:\[[^\]\r\n]*\][ \t]*)*" + re.escape(marker) + r"[ \t\r]*\n")


def _compile_completion_patterns(values: tuple[str, ...] | None) -> tuple[re.Pattern, ...]:
    if values is None:
        return ()
    if not isinstance(values, tuple) or not 1 <= len(values) <= 8:
        raise ValueError("Completion patterns must be a tuple containing 1 to 8 trusted regexes.")
    if any(not isinstance(value, str) or not value or len(value) > 512 for value in values):
        raise ValueError("Each completion pattern must contain 1 to 512 characters.")
    try:
        return tuple(re.compile(value, re.MULTILINE) for value in values)
    except re.error as exc:
        raise ValueError(f"Invalid completion pattern: {exc}") from exc


def _validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and greater than zero.")
