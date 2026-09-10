"""Optional video/ReShade transport; camera and editor payloads stay independent."""
from __future__ import annotations

import ntpath
import struct

ABI = 1
MAPPING_BYTES = 12288
STATUS_OFFSET = 8192
COMMAND = struct.Struct("<8s6I2048s2048s")
STATUS = struct.Struct("<8s10I3Q2I768s768s768s")
VIDEO_STATES = ("idle", "starting", "recording", "finalizing", "completed", "cancelled", "failed")
COMMANDS = {"start_video": 1, "stop_video": 2, "cancel_video": 3,
            "configure_reshade": 4, "disable_reshade": 5, "toggle_reshade": 6}


def _path(value: str, required: bool = False) -> bytes:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("Media path must be text without control characters")
    if required and not value:
        raise ValueError("Choose a media path")
    # The native consumer is always Windows; drive-relative paths are unsafe.
    if value and (not ntpath.isabs(value) or not ntpath.splitdrive(value)[0]):
        raise ValueError("Choose an absolute Windows media path")
    encoded = value.encode("utf-16-le")
    if len(encoded) >= 2048:
        raise ValueError("Media path is too long (maximum 1,023 UTF-16 characters)")
    return encoded + b"\0\0"


def pack_command(sequence, command, *, path="", config_path="", fps=60, bitrate=20000000):
    if type(sequence) is not int or not 0 < sequence <= 0xfffffffe or sequence & 1:
        raise ValueError("Invalid media command sequence")
    if command not in COMMANDS:
        raise ValueError("Unknown media command")
    if type(fps) is not int or fps not in (30, 60, 120):
        raise ValueError("Choose 30, 60 or 120 video FPS")
    if type(bitrate) is not int or not 1000000 <= bitrate <= 80000000:
        raise ValueError("Video bitrate must be between 1 and 80 Mbps")
    return COMMAND.pack(b"DLYMED01", sequence, ABI, COMMANDS[command], fps, bitrate, 0,
                        _path(path, command in ("start_video", "configure_reshade")),
                        _path(config_path, command == "configure_reshade"))


def _text(data):
    text = data.decode("utf-16-le", errors="replace")
    return text.split("\0", 1)[0]


def unpack_status(data):
    if len(data) != STATUS.size:
        raise ValueError("Wrong media status size")
    if not any(data):
        return {"available": False, "state": "idle", "ack": 0, "reshade": {"state": 0, "open": False}}
    fields = STATUS.unpack(data)
    (magic, seq, abi, ack, command_error, video_state, fps, width, height,
     reshade_state, reshade_open, written, dropped, duration, video_error,
     reshade_error, video_message, reshade_message, command_message) = fields
    if (magic != b"DLYMDS01" or abi != ABI or seq & 1 or video_state >= len(VIDEO_STATES)
            or reshade_open not in (0, 1) or fps not in (0, 30, 60, 120)
            or width > 16384 or height > 16384):
        raise ValueError("Media status does not match this Dolly build")
    return {"available": True, "ack": ack, "command_error": command_error,
            "command_message": _text(command_message), "state": VIDEO_STATES[video_state],
            "fps": fps, "width": width, "height": height, "frames_written": written,
            "frames_dropped": dropped, "duration_100ns": duration,
            "duration": duration / 10000000, "error_code": video_error,
            "error": _text(video_message),
            "reshade": {"state": reshade_state, "open": bool(reshade_open),
                        "error_code": reshade_error, "message": _text(reshade_message)}}
