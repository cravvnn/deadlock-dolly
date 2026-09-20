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
import json
import math
import re
import struct
import shutil
import subprocess
import time
import zlib
import uuid
import tempfile
import threading
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
MIN_FRAMES, MAX_FRAMES = 2, 2_000_000
# Keep small capture reports through deployment cleanup. Never include image
# bundles, raw process/GPU events, or arbitrary files from the game directory.
DIAGNOSTIC_NAMES = (STATUS_NAME, MARKER_NAME, "dolly_owner_stall.txt",
                    "dolly_owner_draws.txt", "dolly_owner_captures.txt",
                    "dolly_owner_producers.txt")
DIAGNOSTIC_LIMIT = 64 * 1024


def preserve_diagnostics(deployment: Path, session: Path) -> None:
    """Best-effort bounded reports; failure must never block game cleanup."""
    for name in DIAGNOSTIC_NAMES:
        try:
            with (Path(deployment) / name).open("rb") as source:
                data = source.read(DIAGNOSTIC_LIMIT)
            destination = Path(session) / name
            temporary = destination.with_name(name + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        except OSError:
            pass


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


def marker_text(owner_offset: int, frames: int, back_offset: int = SCENE_NODE_FIELD_OFFSET,
                *, fps: int = 60, request_id: str = "", speed: float = 1.0) -> str:
    """Marker for a players-only capture: no fixed handles, class-discovered."""
    for value in (owner_offset, back_offset):
        if not _MIN_OFFSET <= value <= _MAX_OFFSET:
            raise ValueError("A schema offset is outside the reviewed range.")
    if not MIN_FRAMES <= frames <= MAX_FRAMES:
        raise ValueError("Player layer capture needs 2..%d frames." % MAX_FRAMES)
    if fps not in (30, 60, 120, 300, 600):
        raise ValueError("Unsupported player layer FPS.")
    if not math.isfinite(speed) or not .05 <= speed <= 4:
        raise ValueError("Player layer export speed must be between 0.05 and 4.")
    if not request_id and (fps != 60 or speed != 1):
        raise ValueError("A custom capture clock requires a capture request ID.")
    if request_id and not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("Invalid capture request ID.")
    suffix = (" fps %d request %s" % (fps, request_id)) if request_id else ""
    if speed != 1:
        suffix += " speed %.9g" % speed
    return "%s 0 %d players %d %d%s\n" % (CAPTURE_MODE, frames, owner_offset, back_offset, suffix)


def reference_frame_count(color_path: Path, fps: int) -> int:
    """Require the completed color take's measured length before arming Players."""
    color_path = Path(color_path)
    sidecar = color_path.parent / "shot.json"
    try:
        if not color_path.is_file() or not 0 < sidecar.stat().st_size <= 128 * 1024:
            raise ValueError("missing color take or invalid metadata size")
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid metadata")
        frames = data.get("frames_written")
        if (data.get("format") != "deadlock-dolly-shot" or data.get("version") != 1
                or data.get("video_file") != color_path.name or data.get("fps") != fps
                or data.get("fixed_step") is not True or type(frames) is not int
                or not MIN_FRAMES <= frames <= MAX_FRAMES
                or data.get("first_frame") != 0 or data.get("last_frame") != frames - 1):
            raise ValueError("color take metadata does not match this export")
        return frames
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("The players layer needs the completed color take's frame metadata. "
                           "Record a new color take before exporting Players.") from exc


def check_capture_space(deployment: Path, width: int, height: int, frames: int) -> int:
    """Preflight the known raw bundle size on the game deployment's volume."""
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 16384 or not 1 <= height <= 16384 or
            type(frames) is not int or not MIN_FRAMES <= frames <= MAX_FRAMES):
        raise RuntimeError("The Players layer needs the completed color take's capture dimensions.")
    required = frames * (64 + width * height * 8)
    # Keep room for diagnostics and the filesystem; output MOV may be elsewhere.
    reserve = 1024 ** 3
    free = shutil.disk_usage(deployment).free
    if free < required + reserve:
        raise RuntimeError(
            "Players capture needs approximately %.1f GiB of temporary space on the game drive "
            "plus 1 GiB free; only %.1f GiB is available. Shorten the path, lower FPS or "
            "resolution, or free space before exporting. The MOV also needs output-drive space."
            % (required / 1024 ** 3, free / 1024 ** 3))
    return required


def begin_capture(deployment: Path, owner_offset: int, frames: int,
                  back_offset: int = SCENE_NODE_FIELD_OFFSET, *, fps: int = 60, speed: float = 1.0) -> Path:
    """Write the capture marker beside the deployed native DLL."""
    marker = Path(deployment) / MARKER_NAME
    temporary = marker.with_name(marker.name + ".tmp")
    request_id = uuid.uuid4().hex
    temporary.write_text(marker_text(owner_offset, frames, back_offset, fps=fps, request_id=request_id, speed=speed), encoding="ascii")
    stop = Path(deployment) / STOP_NAME
    try:
        stop.unlink()
    except FileNotFoundError:
        pass
    os.replace(temporary, marker)
    return marker


def request_stop(deployment: Path) -> Path:
    """Ask a running capture to finish now, at the frames it already sealed.

    The take can end before the frame cap (a replay returning to the hideout,
    a stopped recording), so the recorder drops this file and the capture
    drains its captured frames instead of waiting out its budget. Incomplete
    shot captures are reported as failed rather than encoded at a shorter duration.
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
    text = path.read_text(encoding="ascii", errors="replace").strip()
    marker = Path(deployment) / MARKER_NAME
    if marker.is_file():
        match = re.search(r" request ([0-9a-f]{32})", marker.read_text(encoding="ascii"))
        if match and "request " + match[1] not in text.splitlines():
            return ""  # Never accept another take's status/bundle.
    return text


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
    with Path(bundle).open("rb") as source:
        while True:
            header = source.read(64)
            if not header:
                return
            if len(header) != 64:
                raise RuntimeError("The player layer bundle is truncated.")
            magic, width, height, draws, _, _, _, bpp = struct.unpack("<8Q", header)
            if (magic != BUNDLE_MAGIC or not 0 < width <= 3840 or not 0 < height <= 2160
                    or not draws or bpp != 8):
                raise RuntimeError("The player layer bundle is not a reviewed capture.")
            size = width * height * 8
            pixels = source.read(size)
            if len(pixels) != size:
                raise RuntimeError("The player layer bundle is truncated.")
            yield width, height, pixels


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


def wait_until_armed(deployment: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = capture_status(deployment)
        if text.startswith("armed"):
            return
        if text.startswith(("failed", "rejected", "complete")):
            raise RuntimeError("Player capture could not start: " + text)
        time.sleep(.05)
    raise RuntimeError("Player capture did not acknowledge this take. Use a matching updated Dolly build.")


def encode_bundle(ffmpeg: Path, bundle: Path, output: Path, *, fps: int = 60) -> Path:
    """Stream one frame at a time to an atomic alpha MOV; no PNG/MP4 intermediates."""
    if fps not in (30, 60, 120, 300, 600):
        raise ValueError("Unsupported player layer FPS.")
    frames = iter(iter_frames(bundle))
    first = next(frames, None)
    if first is None:
        raise RuntimeError("The player layer captured no frames.")
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + "." + uuid.uuid4().hex + ".partial.mov")
    width, height, _ = first
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
               "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{width}x{height}",
               "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "prores_ks",
               "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0",
               "-threads", "2", str(temporary)]
    import itertools
    process = None; timer = None
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=errors)
            estimated_frames = max(1, Path(bundle).stat().st_size // (64 + width * height * 8))
            timer = threading.Timer(max(120, 30 * estimated_frames), process.kill)
            timer.daemon = True; timer.start()
            try:
                for w, h, pixels in itertools.chain((first,), frames):
                    if (w, h) != (width, height):
                        raise RuntimeError("Player layer dimensions changed during capture.")
                    preview = bytearray(w * h * 4)
                    for i, rgba in enumerate(struct.iter_unpack("<4e", pixels)):
                        alpha = rgba[3]
                        if alpha:
                            preview[i*4:i*4+4] = bytes([*(_preview_channel(x / alpha) for x in rgba[:3]),
                                                       round(alpha * 255)])
                    process.stdin.write(preview)
                process.stdin.close()
                code = process.wait(timeout=120)
            except (BrokenPipeError, OSError) as exc:
                process.kill(); process.wait()
                errors.seek(0)
                raise RuntimeError("Could not encode the players MOV: " + errors.read(1000).decode(errors="replace")) from exc
            if code or not temporary.is_file() or not temporary.stat().st_size:
                errors.seek(0)
                raise RuntimeError("Could not encode the players MOV: " + errors.read(1000).decode(errors="replace"))
        os.replace(temporary, output)
        return output
    finally:
        if timer: timer.cancel()
        if process and process.poll() is None:
            process.kill(); process.wait()
        if process and process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        frames.close()
        temporary.unlink(missing_ok=True)


def cleanup_capture(deployment: Path, layer_dir: Path) -> list[str]:
    """Delete only known completed-take intermediates, never folders recursively."""
    failures = []
    for parent, names in ((Path(deployment), (BUNDLE_NAME, META_NAME, "dolly_owner_events.bin")),
                          (Path(layer_dir), ("players.mp4", "players.mp4.shot.json")),
                          (Path(layer_dir)/"capture", ())):
        if not parent.exists(): continue
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            failures.append(str(parent)); continue
        candidates = [parent / n for n in names]
        if parent.name == "capture":
            candidates = [p for p in parent.iterdir() if re.fullmatch(r"native-player-layer-\d+\.png", p.name)]
        for path in candidates:
            try:
                if path.is_symlink() or path.resolve().parent != parent.resolve():
                    raise OSError("Unexpected capture path")
                path.unlink(missing_ok=True)
            except OSError:
                failures.append(str(path))
        if parent.name == "capture":
            try: parent.rmdir()
            except OSError: pass
    return failures
