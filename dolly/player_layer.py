"""Players-only layer capture.

The recorder's isolated layer takes separate scene classes with
``sc_setclassflags``: keeping ``SkinnedObject`` keeps every skinned unit,
including NPCs, and hiding everything else drops player-owned parts that live
in other classes (weapons, attachments, carried objects). The native player
capture instead selects the exact draws owned by player pawns -- classified
through the scene graph (scene object -> scene node -> owner pawn) -- and
records them with real coverage alpha, so the layer contains real players plus
their equipment and nothing else. The world stays in the scene, so scenery
occlusion is preserved.

This module owns the file protocol with the native capture (marker, status,
bundle and per-frame metadata) and the diagnostic preview/encode path that
matches the verified research proofs.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import time
import zlib
from pathlib import Path

CAPTURE_MODE = "color-sequence-v3"
OWNER_LAYOUT_CLASS = "CGameSceneNode"
OWNER_FIELD_NAME = "m_pOwner"
PLAYER_CLASS_NAME = "CitadelPlayerPawn"
ENTITY_LAYOUT_CLASS = "C_BaseEntity"
ENTITY_SCENE_NODE_FIELD = "m_pGameSceneNode"
SCENE_NODE_FIELD_OFFSET = 816
MARKER_NAME = "dolly_owner_start.txt"
STATUS_NAME = "dolly_owner_status.txt"
STOP_NAME = "dolly_owner_stop.txt"
BUNDLE_NAME = "dolly_owner_color_frame.bin"
META_NAME = "dolly_owner_frame_meta.bin"
BUNDLE_MAGIC = 0x314641524c4f4344
MIN_FRAMES, MAX_FRAMES = 2, 256
# The owner offset is read from the game's schema each run, so it may be small
# or move after a game update; only an implausible value is refused.
_MIN_OFFSET, _MAX_OFFSET = 8, 0x4000

_OWNER_FIELD = re.compile(r"^\s*(\+?)\s*(\d+)\s+(\d+)\s+(\S+)\s+(m_\S+)\s+(.+?)\s*$", re.M)


def _field_offset(layout_text: str, field: str) -> int:
    for plus, outer, offset, _kind, name, _type in _OWNER_FIELD.findall(layout_text):
        if not plus and int(outer) == 0 and name == field:
            value = int(offset)
            if _MIN_OFFSET <= value <= _MAX_OFFSET:
                return value
            raise RuntimeError("%s is outside the reviewed offset range." % field)
    raise RuntimeError("The game did not report %s; the player layer is unavailable." % field)


def parse_owner_offset(layout_text: str) -> int:
    """Return CGameSceneNode.m_pOwner's byte offset from schema console text.

    The offset is read from the game itself (``schema_detailed_class_layout
    CGameSceneNode``) so the capture never hardcodes a schema layout.
    """
    return _field_offset(layout_text, OWNER_FIELD_NAME)


def parse_scene_node_offset(layout_text: str) -> int:
    """Return C_BaseEntity.m_pGameSceneNode's byte offset (back-link check)."""
    return _field_offset(layout_text, ENTITY_SCENE_NODE_FIELD)


def marker_text(owner_offset: int, frames: int, back_offset: int = SCENE_NODE_FIELD_OFFSET) -> str:
    """Marker for a players-only capture: no fixed handles, class-discovered."""
    for value in (owner_offset, back_offset):
        if not _MIN_OFFSET <= value <= _MAX_OFFSET:
            raise ValueError("A schema offset is outside the reviewed range.")
    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise ValueError("Player layer capture needs 2..%d frames." % MAX_FRAMES)
    return "%s 0 %d players %d %d\n" % (CAPTURE_MODE, frames, owner_offset, back_offset)


def begin_capture(deployment: Path, owner_offset: int, frames: int,
                  back_offset: int = SCENE_NODE_FIELD_OFFSET) -> Path:
    """Write the capture marker beside the deployed native DLL."""
    marker = Path(deployment) / MARKER_NAME
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(marker_text(owner_offset, frames, back_offset), encoding="ascii")
    os.replace(temporary, marker)
    stop = Path(deployment) / STOP_NAME
    try:
        stop.unlink()
    except FileNotFoundError:
        pass
    return marker


def request_stop(deployment: Path) -> Path:
    """Ask a running capture to finish now, at the frames it already sealed.

    The take can end before the frame cap (a replay returning to the hideout,
    a stopped recording), so the recorder drops this file and the capture
    completes with its captured frames instead of waiting out its budget.
    """
    stop = Path(deployment) / STOP_NAME
    temporary = stop.with_name(stop.name + ".tmp")
    temporary.write_text("stop\n", encoding="ascii")
    os.replace(temporary, stop)
    return stop


def capture_status(deployment: Path) -> str:
    path = Path(deployment) / STATUS_NAME
    if not path.is_file():
        return ""
    return path.read_text(encoding="ascii", errors="replace").strip()


def wait_for_capture(deployment: Path, *, timeout: float = 240.0) -> str:
    """Bounded wait for the native capture to finish; never blocks forever."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = capture_status(deployment)
        if status.startswith(("complete", "failed", "rejected")):
            return status
        time.sleep(0.2)
    return capture_status(deployment)


def iter_frames(bundle: Path):
    """Yield (width, height, premultiplied RGBA half-float bytes) per frame."""
    raw = Path(bundle).read_bytes()
    offset = 0
    while offset + 64 <= len(raw):
        magic, width, height, draws = struct.unpack_from("<4Q", raw, offset)
        if magic != BUNDLE_MAGIC or not 0 < width <= 3840 or not 0 < height <= 2160:
            raise RuntimeError("The player layer bundle is not a reviewed capture.")
        size = 64 + width * height * 8
        if offset + size > len(raw):
            raise RuntimeError("The player layer bundle is truncated.")
        yield width, height, raw[offset + 64:offset + size]
        offset += size


def _preview_channel(value: float) -> int:
    value = max(0.0, value)
    value = value / (1 + value)
    value = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    return round(max(0.0, min(1.0, value)) * 255)


def write_previews(bundle: Path, output: Path) -> list[Path]:
    """Diagnostic sRGB previews with coverage alpha, matching the proofs.

    The native pixels are premultiplied half-float HDR; the preview path
    unpremultiplies, tone maps (Reinhard) and writes straight-alpha PNGs for
    the encoder. A product tonemapping decision is tracked separately.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, (width, height, pixels) in enumerate(iter_frames(bundle)):
        preview = bytearray(width * height * 4)
        for i, rgba in enumerate(struct.iter_unpack("<4e", pixels)):
            alpha = rgba[3]
            if alpha:
                preview[i * 4:i * 4 + 4] = bytes(
                    [_preview_channel(rgba[0] / alpha), _preview_channel(rgba[1] / alpha),
                     _preview_channel(rgba[2] / alpha), round(alpha * 255)])
        name = output / ("native-player-layer-%03d.png" % index)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        png = (b"\x89PNG\r\n\x1a\n" +
               chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(b"".join(
                   b"\0" + bytes(preview[y * width * 4:(y + 1) * width * 4]) for y in range(height)))) +
               chunk(b"IEND", b""))
        name.write_bytes(png)
        paths.append(name)
    return paths


def encode_layer(ffmpeg: Path, previews: list[Path], output: Path, *, fps: int = 60) -> Path:
    """Encode preview frames to the alpha-capable mezzanine layer video."""
    if not previews:
        raise RuntimeError("The player layer captured no frames.")
    if not Path(ffmpeg).is_file():
        raise RuntimeError("The player layer needs an FFmpeg executable for its alpha video.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite on purpose: a re-recorded take must replace its layer master
    # instead of silently keeping an older file under the same name.
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
               "-framerate", str(fps), "-start_number", "0",
               "-i", str(previews[0].with_name(previews[0].name.replace("000", "%03d"))),
               "-frames:v", str(len(previews)), "-an", "-c:v", "prores_ks", "-profile:v", "4444",
               "-pix_fmt", "yuva444p10le", "-vendor", "apl0", "-threads", "2", str(output)]
    result = subprocess.run(command, capture_output=True, timeout=max(120, 30 * len(previews)))
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError("Could not encode the player layer video: " +
                           result.stderr.decode(errors="replace")[:400])
    return output
