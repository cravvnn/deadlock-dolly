"""Bounded Windows shared-memory transport for the native render camera.

Only the editor writes control bytes; only its launched game writes status.
Sequence counters publish coherent snapshots. The heartbeat is independent of
command snapshots, so a long wait cannot look like an abandoned editor.
"""
from __future__ import annotations

import ctypes
from collections import deque
from copy import deepcopy
import hashlib
import json
import math
import mmap
import os
import re
import struct
import threading
import time
import uuid

from .native_effects import compile_shot, EFFECTS
from .runtime import resource_root

ABI = 3
CONTROL_BYTES = 2 * 1024 * 1024
MAPPING_BYTES = CONTROL_BYTES + 4096
PAYLOAD_OFFSET = 1024
MAX_PAYLOAD_BYTES = CONTROL_BYTES - PAYLOAD_OFFSET
CONTROL = struct.Struct("<8s6IQ2I2d512s")
STATUS = struct.Struct("<8s6IQ3diI14d2dQ256s512s2d2IQd")
CONTROL_MAGIC = b"DLYCAM01"
STATUS_MAGIC = b"DLYSTAT1"
STATES = ("starting", "probe", "armed", "playing", "completed", "stopped", "fault", "unsupported")


class NativeBridgeError(RuntimeError):
    """Native camera is unavailable or did not acknowledge a command."""


def _load_atomic_library():
    """Load the verified Dolly helper, without invoking its game factory.

    Windows x64 implements the Interlocked APIs as compiler intrinsics; they
    cannot be assumed to exist as kernel32 exports for ctypes. Dolly exports
    small wrappers compiled with those intrinsics and their memory barriers.
    Loading this DLL initializes no game hooks: only CreateInterface can start
    the separate game-side loader, and the editor never calls that export.
    """
    native = resource_root() / "native"
    dll = native / "bin" / "win64" / "DollyNative.dll"
    try:
        metadata = json.loads((native / "build_info.json").read_text(encoding="utf-8"))
        data = dll.read_bytes()
    except (OSError, ValueError) as exc:
        raise NativeBridgeError("The native camera helper is missing or invalid. Extract the complete Dolly package or rebuild it.") from exc
    if (not isinstance(metadata, dict) or type(metadata.get("abi")) is not int
            or metadata["abi"] != ABI
            or metadata.get("sha256") != hashlib.sha256(data).hexdigest()):
        raise NativeBridgeError("The native camera helper failed its ABI or SHA-256 check. Extract the complete matching Dolly package.")
    offset = struct.unpack_from("<I", data, 60)[0] if len(data) >= 64 else len(data)
    if (data[:2] != b"MZ" or offset < 64 or offset + 26 > len(data)
            or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664
            or not struct.unpack_from("<H", data, offset + 22)[0] & 0x2000
            or struct.unpack_from("<H", data, offset + 24)[0] != 0x20B):
        raise NativeBridgeError("The native camera helper must be a Windows x64 DLL.")
    try:
        # An absolute path and restricted dependency search avoid selecting
        # an unrelated DLL from the editor's working directory or PATH.
        library = ctypes.WinDLL(str(dll.resolve()), use_last_error=True, winmode=0x1100)
        protocol = library.DollyNativeProtocolVersion
        protocol.argtypes = []
        protocol.restype = ctypes.c_uint32
        if protocol() != ABI:
            raise NativeBridgeError("The loaded native camera helper has a different protocol. Restart Dolly with the complete updated package.")
        return library
    except (OSError, AttributeError) as exc:
        raise NativeBridgeError("The native camera helper could not load. Rebuild Dolly or extract the complete updated package.") from exc


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'} and finite")
    return result


def _pid(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 0xFFFFFFFF:
        raise ValueError("Native camera requires a valid process ID")
    return value


class NativeBridge:
    @classmethod
    def create(cls, *, mapping_factory=None, clock=time.monotonic,
               sleep=time.sleep, start_heartbeat=True, editor_pid=None, token=None):
        """Create a private session mapping before starting its development game.

        The injectable factory/clock are for portable transport tests; the
        production mapping is always a Windows pagefile-backed named mapping.
        """
        editor_pid = _pid(os.getpid() if editor_pid is None else editor_pid)
        token = uuid.uuid4().hex if token is None else token
        if not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{32}", token) is None:
            raise ValueError("Native camera session token must be 32 lowercase hex digits")
        name = "Local\\DeadlockDollyNative_" + token
        if mapping_factory is None:
            if os.name != "nt":
                raise NativeBridgeError("Native camera sessions require 64-bit Windows")
            mapping = mmap.mmap(-1, MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
        else:
            mapping = mapping_factory(name, MAPPING_BYTES)
        try:
            return cls(mapping, token, editor_pid, clock, sleep, start_heartbeat,
                       mapping_factory=mapping_factory)
        except Exception:
            close = getattr(mapping, "close", None)
            if close is not None:
                close()
            raise

    def __init__(self, mapping, token, editor_pid, clock, sleep, start_heartbeat,
                 *, mapping_factory=None):
        if len(mapping) != MAPPING_BYTES:
            raise NativeBridgeError("Native camera mapping has an unexpected size")
        self._mapping = mapping
        self._mapping_factory = mapping_factory
        self._viewer_mapping = None
        self._viewer_sequence = 0
        self._viewer_enabled = False
        self._viewer_error = ""
        self.token = token
        self.editor_pid = editor_pid
        self.game_pid = 0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._heartbeat_stop = threading.Event()
        self._thread = None
        self._closed = False
        self._command = 0
        self._sequence = 0
        self._heartbeat = 0
        self._flags = 0
        self._start = 0.0
        self._speed = 1.0
        self._demo = b""
        self._prepared = False
        self._manual = False
        self._editor_values = {}
        self._editor_sequence = 0
        self._editor_owner_sequence = 0
        self._editor_ack = 0
        self._graphics_samples = deque(maxlen=120)
        self._graphics_sample_at = -math.inf
        self._graphics_error = ""
        self._mode = 0
        self._atomic32 = self._atomic64 = None
        self._read32 = None
        self._atomic_library = None
        if os.name == "nt" and isinstance(mapping, mmap.mmap):
            self._atomic_library = _load_atomic_library()
            try:
                self._atomic32 = self._atomic_library.DollyAtomicExchange32
                self._atomic64 = self._atomic_library.DollyAtomicExchange64
                self._read32 = self._atomic_library.DollyAtomicCompareExchange32
            except AttributeError as exc:
                raise NativeBridgeError("This native camera helper is from an older build. Rebuild Dolly and replace the complete package, including _internal.") from exc
            self._atomic32.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32]
            self._atomic32.restype = ctypes.c_int32
            self._atomic64.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.c_int64]
            self._atomic64.restype = ctypes.c_int64
            self._read32.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int32]
            self._read32.restype = ctypes.c_int32
        mapping[:] = b"\0" * MAPPING_BYTES
        self._publish(0, b"", increment=False)
        self._beat()
        if start_heartbeat:
            self._thread = threading.Thread(target=self._heartbeat_loop,
                                            name="dolly-native-heartbeat", daemon=True)
            self._thread.start()

    def _store(self, offset, value, bits=32):
        atomic = self._atomic32 if bits == 32 else self._atomic64
        if atomic is None:
            struct.pack_into("<I" if bits == 32 else "<Q", self._mapping, offset, value)
        else:
            typ = ctypes.c_int32 if bits == 32 else ctypes.c_int64
            slot = typ.from_buffer(self._mapping, offset)
            atomic(ctypes.byref(slot), typ(value))

    def _check_open(self):
        if self._closed:
            raise NativeBridgeError("Native camera session is closed")

    def _load_sequence(self, offset=CONTROL_BYTES + 8):
        if self._read32 is None:
            return struct.unpack_from("<I", self._mapping, offset)[0]
        slot = ctypes.c_int32.from_buffer(self._mapping, offset)
        return self._read32(ctypes.byref(slot), 0, 0) & 0xFFFFFFFF

    def _beat(self):
        with self._lock:
            if self._closed:
                return
            self._heartbeat = (self._heartbeat + 1) & 0xFFFFFFFFFFFFFFFF
            self._store(32, self._heartbeat, 64)

    def _heartbeat_loop(self):
        while not self._heartbeat_stop.wait(0.1):
            self._beat()

    def _publish(self, mode, payload=b"", *, increment=True):
        with self._lock:
            self._check_open()
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("Native camera path exceeds the shared-memory capacity")
            if increment and self._command == 0xFFFFFFFF:
                raise NativeBridgeError("Native camera command counter exhausted; relaunch the session")
            command = self._command + int(increment)
            odd = (self._sequence + 1) & 0xFFFFFFFF
            even = (odd + 1) & 0xFFFFFFFF
            header = CONTROL.pack(CONTROL_MAGIC, ABI, odd, command, mode,
                                  self.editor_pid, self.game_pid, 0, self._flags,
                                  len(payload), self._start, self._speed, self._demo)
            self._store(12, odd)
            # Neither the header copy nor the sequence publication touches the
            # aligned heartbeat at 32:40; it belongs to the heartbeat writer.
            self._mapping[:12] = header[:12]
            self._mapping[16:32] = header[16:32]
            self._mapping[40:CONTROL.size] = header[40:]
            if payload:
                self._mapping[PAYLOAD_OFFSET:PAYLOAD_OFFSET + len(payload)] = payload
            self._store(12, even)
            self._sequence = even
            self._command = command
            self._mode = mode
            return command

    def bind_game(self, pid):
        pid = _pid(pid)
        with self._operations, self._lock:
            self._check_open()
            if self.game_pid:
                if self.game_pid != pid:
                    raise NativeBridgeError("Native camera cannot attach to another game process")
                return
            self.game_pid = pid
            self._publish(self._mode, increment=False)

    def status(self):
        """Return one coherent, validated native status snapshot."""
        with self._lock:
            self._check_open()
            self._sample_graphics()
            for _ in range(8):
                first = self._load_sequence()
                if first & 1:
                    self._sleep(0.0005)
                    continue
                data = bytes(self._mapping[CONTROL_BYTES:CONTROL_BYTES + STATUS.size])
                second = self._load_sequence()
                if first == second and not second & 1 and struct.unpack_from("<I", data, 8)[0] == first:
                    break
                self._sleep(0.0005)
            else:
                raise NativeBridgeError("Native camera status is busy; retry the operation")
            if data == b"\0" * STATUS.size:
                return {"state": "starting", "state_code": 0, "abi": ABI,
                        "game_pid": self.game_pid, "ack_command": 0, "complete": False,
                        "frame_count": 0, "phase": 0.0, "message": "Waiting for the native camera plugin"}
            values = STATUS.unpack(data)
            magic, sequence, abi, state, pid, ack, error = values[:7]
            if magic != STATUS_MAGIC or abi != ABI:
                raise NativeBridgeError("Native camera protocol does not match this editor build")
            if not self.game_pid or pid != self.game_pid:
                raise NativeBridgeError("Native camera status belongs to a different game process")
            if state >= len(STATES) or ack > self._command:
                raise NativeBridgeError("Native camera returned an invalid state or command acknowledgment")
            frame_count, real_time, engine_time, phase, tick, paused = values[7:13]
            original, applied = list(values[13:20]), list(values[20:27])
            original_fov, applied_fov, hook_calls, message, demo, maximum_ms, interval_ms = values[27:34]
            effect_count, effect_error, effect_frames, effect_phase = values[34:]
            if effect_count > len(EFFECTS) or effect_error > 3:
                raise NativeBridgeError("Native effect status is invalid")
            numbers = [effect_phase, real_time, engine_time, phase, *original, *applied,
                       original_fov, applied_fov, maximum_ms, interval_ms]
            if not all(math.isfinite(value) for value in numbers) or paused not in (0, 1):
                raise NativeBridgeError("Native camera returned non-finite view or timing data")
            return {"state": STATES[state], "state_code": state, "abi": abi,
                    "game_pid": pid, "ack_command": ack, "error": error,
                    "complete": state == 4, "frame_count": frame_count,
                    "real_time": real_time, "engine_time": engine_time,
                    "phase": phase, "tick": tick, "paused": bool(paused),
                    "original_pose": original, "applied_pose": applied,
                    "original_fov": original_fov, "applied_fov": applied_fov,
                    "hook_calls": hook_calls,
                    "effect_count": effect_count, "effect_error": effect_error,
                    "effect_frames": effect_frames, "effect_phase": effect_phase,
                    "message": message.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
                    "demo_name": demo.split(b"\0", 1)[0].decode("utf-8", errors="replace"),
                    "max_frame_interval_ms": maximum_ms, "frame_interval_ms": interval_ms}

    def _wait(self, command, states, timeout):
        deadline = self._clock() + timeout
        while True:
            snapshot = self.status()
            acknowledged = snapshot["ack_command"] == command
            # A previous command's fault remains visible until the callback
            # processes this command. In particular, Release and a fresh Hold
            # must be allowed to recover instead of failing on that old error.
            # Unsupported is a session-wide startup/build failure, independent
            # of command acknowledgments, and cannot be recovered by a shot.
            if snapshot["state"] == "unsupported" or (acknowledged and snapshot["state"] == "fault"):
                raise NativeBridgeError(snapshot.get("message") or "Native camera refused this game build or view")
            if acknowledged and snapshot["state"] in states:
                return snapshot
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise NativeBridgeError("Native camera did not acknowledge the command in time: " + snapshot.get("message", snapshot["state"]))
            self._sleep(min(0.01, remaining))

    def prepare(self, project, start, speed, frozen, demo_name, timeout=5):
        start = _number(start, "Start time")
        speed = _number(speed, "Playback speed", positive=True)
        timeout = _number(timeout, "Native acknowledgment timeout")
        if not isinstance(frozen, bool):
            raise ValueError("Frozen preview must be a boolean")
        if not isinstance(demo_name, str) or not demo_name or any(c in demo_name for c in "\0\r\n"):
            raise ValueError("Native camera requires the current replay name")
        demo = demo_name.encode("utf-8")
        if len(demo) >= 512:
            raise ValueError("Replay name is too long for the native camera protocol")
        payload = compile_shot(project)
        if start > project.duration:
            raise ValueError("Native start time exceeds the shot duration")
        with self._operations:
            self._check_open()
            if not self.game_pid:
                raise NativeBridgeError("Native camera requires its launched game process")
            self._prepared = False
            self._manual = False
            self._start, self._speed, self._demo = start, speed, demo
            self._flags = (1 if frozen else 0) | 2
            command = self._publish(1, payload)
            try:
                result = self._wait(command, {"armed"}, timeout)
            except Exception:
                self._publish(0)
                raise
            self._prepared = True
            return result

    def play(self, timeout=3):
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            if not self._prepared:
                raise NativeBridgeError("Prepare a native camera path before playing")
            if self._editor_values.get("enabled"):
                # Authored footage starts with the panel hidden. F8 can reopen
                # it; manual integration is inactive while the path plays.
                self.configure_editor(owner="flight")
            command = self._publish(2)
            try:
                return self._wait(command, {"playing", "completed"}, timeout)
            except Exception:
                self._prepared = False
                self._publish(0)
                if self._editor_values.get("enabled"):
                    self.configure_editor(owner="panel")
                raise

    def release(self, timeout=3):
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            self._prepared = False
            self._manual = False
            command = self._publish(0)
            if not self.game_pid:
                return self.status()
            return self._wait(command, {"stopped", "probe"}, timeout)

    def hold(self, timeout=3):
        """Freeze the native callback's latest phase without a read/write race."""
        timeout = _number(timeout, "Native acknowledgment timeout")
        with self._operations:
            self._check_open()
            if not self._prepared and not self._manual:
                raise NativeBridgeError("Prepare a native camera path before holding")
            command = self._publish(3)
            return self._wait(command, {"armed"}, timeout)

    def _wait_editor_input(self, timeout, cancelled=None):
        """Wait for the actual game window's input/renderer, not foreground focus."""
        deadline = self._clock() + timeout
        editor = {}
        while True:
            if cancelled is not None and cancelled():
                raise NativeBridgeError("Opening the paused camera was cancelled.")
            native = self.status()
            if native["state"] == "unsupported":
                raise NativeBridgeError(native.get("message") or "Native camera does not support this game build")
            try:
                editor = self.editor_status()
            except NativeBridgeError as exc:
                if "being updated" not in str(exc):
                    raise
            else:
                if all(editor.get(key) for key in ("enabled", "ready", "overlay_available", "input_available", "paused")):
                    return editor
            remaining = deadline - self._clock()
            if remaining <= 0:
                missing = [label for key, label in (("overlay_available", "DX11 panel"),
                           ("input_available", "game input"), ("ready", "replay view"),
                           ("paused", "paused replay")) if not editor.get(key)]
                raise NativeBridgeError("Timed out waiting for " + ", ".join(missing or ["editor initialization"]) +
                    ". Let the replay finish loading, then retry Paused camera. Export diagnostics if it persists.")
            self._sleep(min(0.02, remaining))

    def start_flight(self, demo_name, pose=None, timeout=8, *, cancelled=None):
        """Enter native manual flight, optionally seeded from a displayed view."""
        timeout = _number(timeout, "Native acknowledgment timeout")
        if cancelled is not None and not callable(cancelled):
            raise ValueError("Native flight cancellation must be callable")
        if not isinstance(demo_name, str) or not demo_name or any(c in demo_name for c in "\0\r\n"):
            raise ValueError("Native flight requires the current replay name")
        demo = demo_name.encode("utf-8")
        if len(demo) >= 512:
            raise ValueError("Replay name is too long")
        payload = b""
        if pose is not None:
            try:
                numbers = tuple(float(x) for x in pose)
            except (TypeError, ValueError) as exc:
                raise ValueError("Manual camera requires seven finite values") from exc
            if len(numbers) != 7 or not all(math.isfinite(x) and abs(x) <= 1e8 for x in numbers) or not .25 <= numbers[6] <= 8:
                raise ValueError("Manual camera pose is invalid")
            payload = struct.pack("<7d", *numbers)
        with self._operations:
            self._check_open()
            if not self.game_pid:
                raise NativeBridgeError("Native flight requires its launched game process")
            self._prepared = False
            self._manual = False
            self._demo, self._flags, self._start, self._speed = demo, 3, 0.0, 1.0
            self.configure_editor(enabled=True, owner="panel")
            try:
                self._wait_editor_input(timeout, cancelled)
                if cancelled is not None and cancelled():
                    raise NativeBridgeError("Opening the paused camera was cancelled.")
                self.configure_editor(owner="flight")
                command = self._publish(4, payload)
                result = self._wait(command, {"armed"}, timeout)
            except Exception:
                self._publish(0)
                self.configure_editor(owner="panel")
                raise
            self._manual = True
            return result

    def configure_editor(self, **values):
        """Publish UI/input settings without replacing the native camera path."""
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            changed = dict(self._editor_values)
            changed.update(values)
            owner_sequence = self._editor_owner_sequence + int("owner" in values)
            if owner_sequence > 0xffffffff:
                raise NativeBridgeError("Editor owner counter exhausted; relaunch Dolly")
            odd = (self._editor_sequence + 1) & 0xffffffff
            even = (odd + 1) & 0xffffffff
            data = wire.pack_config(odd, owner_sequence, self._editor_ack, changed)
            offset = wire.CONFIG_OFFSET
            self._store(offset + 8, odd)
            self._mapping[offset:offset + 8] = data[:8]
            self._mapping[offset + 12:offset + len(data)] = data[12:]
            self._store(offset + 8, even)
            self._editor_sequence, self._editor_owner_sequence = even, owner_sequence
            self._editor_values = changed

    def editor_status(self):
        from . import editor_wire as wire
        with self._lock:
            self._check_open()
            self._sample_graphics()
            for _ in range(4):
                first = self._load_sequence(wire.STATUS_OFFSET + 8)
                if first & 1:
                    continue
                data = bytes(self._mapping[wire.STATUS_OFFSET:wire.STATUS_OFFSET + wire.STATUS_BYTES])
                second = self._load_sequence(wire.STATUS_OFFSET + 8)
                if first == second and not second & 1 and struct.unpack_from("<I", data, 8)[0] == first:
                    try:
                        return wire.unpack_status(data, self._editor_ack)
                    except ValueError as exc:
                        raise NativeBridgeError(str(exc)) from exc
            raise NativeBridgeError("Native editor status is being updated")

    def acknowledge_editor_event(self, sequence):
        with self._lock:
            self._check_open()
            if type(sequence) is not int or sequence != self._editor_ack + 1:
                raise NativeBridgeError("Native editor actions must be acknowledged in order")
            self._editor_ack = sequence
            self.configure_editor()

    def _sample_graphics(self, *, force=False):
        """Read at most once a second; probe errors cannot stop camera input."""
        if self._closed:
            return
        now = self._clock()
        if not force and now - self._graphics_sample_at < 1:
            return
        self._graphics_sample_at = now
        from . import graphics_diagnostics as wire
        try:
            for _ in range(2):
                before = self._load_sequence(wire.OFFSET + 8)
                if before & 1:
                    continue
                data = bytes(self._mapping[wire.OFFSET:wire.OFFSET + wire.WIRE.size])
                after = self._load_sequence(wire.OFFSET + 8)
                if before != after or struct.unpack_from("<I", data, 8)[0] != before:
                    continue
                sample = wire.unpack(data)
                if sample is not None and (not self._graphics_samples or
                        sample["sample"] != self._graphics_samples[-1]["sample"]):
                    self._graphics_samples.append(sample)
                self._graphics_error = ""
                return
            self._graphics_error = "Graphics snapshot was being updated; retained previous samples."
        except (NativeBridgeError, OSError, ValueError, struct.error) as exc:
            self._graphics_error = str(exc)

    def graphics_diagnostics(self):
        with self._lock:
            self._sample_graphics(force=True)
            samples = deepcopy(list(self._graphics_samples))
            return {"cached_after_close": self._closed, "samples": samples,
                    "latest": samples[-1] if samples else None,
                    "read_error": self._graphics_error,
                    "note": "Read-only asynchronous observations; no renderer state or camera timing is changed."}

    def _viewer_store_sequence(self, value):
        from . import visualization_wire as wire
        if self._atomic32 is None:
            struct.pack_into("<I", self._viewer_mapping, wire.SEQUENCE_OFFSET, value)
        else:
            slot = ctypes.c_int32.from_buffer(self._viewer_mapping, wire.SEQUENCE_OFFSET)
            self._atomic32(ctypes.byref(slot), ctypes.c_int32(value))

    def _viewer_write(self, packet):
        """Commit through the helper's release barrier, leaving no torn packet."""
        from . import visualization_wire as wire
        sequence = struct.unpack_from("<I", packet, wire.SEQUENCE_OFFSET)[0]
        self._viewer_store_sequence(sequence - 1)
        self._viewer_mapping[:wire.SEQUENCE_OFFSET] = packet[:wire.SEQUENCE_OFFSET]
        self._viewer_mapping[wire.SEQUENCE_OFFSET + 4:len(packet)] = packet[wire.SEQUENCE_OFFSET + 4:]
        self._viewer_store_sequence(sequence)
        self._viewer_sequence = sequence
        self._viewer_enabled = bool(struct.unpack_from("<I", packet, 16)[0])

    def publish_visualization(self, project=None, *, enabled=True, selected_camera=0):
        """Publish optional in-game guides without changing camera transport.

        Call only after a shot, selection, or visibility change; compilation
        runs on the editor side and never streams samples during playback.
        Viewer failures remain diagnostic and cannot fail camera operations.
        """
        from . import visualization_wire as wire
        with self._lock:
            try:
                self._check_open()
                sequence = self._viewer_sequence + 2
                if sequence > 0xFFFFFFFE:
                    sequence = 2
                packet = wire.build_visualization(project, sequence=sequence,
                                                   enabled=enabled, selected_camera=selected_camera)
                if self._viewer_mapping is None:
                    # A disabled or empty shot needs no additional OS object.
                    if not struct.unpack_from("<I", packet, 16)[0]:
                        self._viewer_enabled = False
                        self._viewer_error = ""
                        return True
                    name = "Local\\DeadlockDollyNative_" + self.token + ".viewer"
                    if self._mapping_factory is None:
                        if os.name != "nt":
                            raise NativeBridgeError("Path viewer transport requires Windows")
                        mapping = mmap.mmap(-1, wire.MAPPING_BYTES, tagname=name, access=mmap.ACCESS_WRITE)
                    else:
                        mapping = self._mapping_factory(name, wire.MAPPING_BYTES)
                    if len(mapping) != wire.MAPPING_BYTES:
                        close = getattr(mapping, "close", None)
                        if close is not None:
                            close()
                        raise NativeBridgeError("Path viewer mapping has an unexpected size")
                    self._viewer_mapping = mapping
                    mapping[:] = b"\0" * wire.MAPPING_BYTES
                self._viewer_write(packet)
                self._viewer_error = ""
                return True
            except Exception as exc:
                self._viewer_error = str(exc)
                # Invalid edits must not leave an earlier path misleadingly
                # visible. A clearing packet is independent of shot parsing.
                if self._viewer_mapping is not None:
                    try:
                        sequence = self._viewer_sequence + 2
                        if sequence > 0xFFFFFFFE:
                            sequence = 2
                        self._viewer_write(wire.build_visualization(None, sequence=sequence, enabled=False))
                    except Exception:
                        self._viewer_enabled = False
                return False

    def _close_visualization(self):
        """Clear before unmapping: the game may still hold its read-only view."""
        if self._viewer_mapping is None:
            return
        from . import visualization_wire as wire
        mapping = self._viewer_mapping
        try:
            sequence = self._viewer_sequence + 2
            if sequence > 0xFFFFFFFE:
                sequence = 2
            self._viewer_store_sequence(sequence - 1)
            mapping[:8] = b"\0" * 8
            mapping[12:] = b"\0" * (wire.MAPPING_BYTES - 12)
            self._viewer_store_sequence(sequence)
            self._viewer_enabled = False
        except Exception as exc:
            self._viewer_error = "Path viewer close: " + str(exc)
        finally:
            self._viewer_mapping = None
            close = getattr(mapping, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    self._viewer_error = "Path viewer close: " + str(exc)

    def visualization_diagnostics(self):
        with self._lock:
            return {"mapping_open": self._viewer_mapping is not None,
                    "enabled": self._viewer_enabled, "revision": self._viewer_sequence,
                    "error": self._viewer_error}

    def diagnostics(self):
        try:
            result = self.status()
            try:
                result["editor"] = self.editor_status()
            except (NativeBridgeError, ValueError) as exc:
                result["editor"] = {"message": str(exc)}
        except (NativeBridgeError, OSError, ValueError) as exc:
            result = {"state": "unavailable", "message": str(exc)}
        result["graphics"] = self.graphics_diagnostics()
        result["visualization"] = self.visualization_diagnostics()
        return result

    def close(self):
        """Release once, stop heartbeats, and close the mapping even after faults."""
        with self._operations:
            if self._closed:
                return
            with self._lock:
                self._sample_graphics(force=True)
            try:
                self.release(timeout=0.25)
            except (NativeBridgeError, OSError, ValueError):
                pass
            finally:
                self._heartbeat_stop.set()
                if self._thread is not None and self._thread is not threading.current_thread():
                    self._thread.join(timeout=0.5)
                with self._lock:
                    self._close_visualization()
                    self._closed = True
                    close = getattr(self._mapping, "close", None)
                    if close is not None:
                        close()
