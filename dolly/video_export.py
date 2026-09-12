"""Normal-scene MP4 recording through the session's native DirectX 11 bridge.

Recording follows real time at the current game resolution. This module does
not change replay timing or claim fixed-step/offline rendering. UI mutations
run on Dolly's operation worker; status reads only inspect bridge telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import threading
import time


ACTIVE_STATES = frozenset(("starting", "recording", "finalizing"))
TERMINAL_STATES = frozenset(("idle", "completed", "cancelled", "failed"))
BITRATE_PRESETS = {"10 Mbps": 10_000_000, "20 Mbps": 20_000_000, "40 Mbps": 40_000_000}

# key -> (label, native encoder, native codec, requires ffmpeg). The numeric
# encoder/codec values match dolly::video::Encoder/Codec in dolly_video.hpp.
# "auto" prefers hardware FFmpeg (NVENC) when an ffmpeg path is available and
# otherwise uses the built-in Media Foundation encoder.
CODEC_CHOICES = (
    ("auto", "Auto (hardware when available)", 0, 0, False),
    ("h264_nvenc", "NVIDIA H.264 (NVENC)", 1, 1, True),
    ("hevc_nvenc", "NVIDIA HEVC (NVENC)", 1, 2, True),
    ("h264_qsv", "Intel H.264 (Quick Sync)", 1, 6, True),
    ("hevc_qsv", "Intel HEVC (Quick Sync)", 1, 7, True),
    ("h264_amf", "AMD H.264 (AMF)", 1, 8, True),
    ("hevc_amf", "AMD HEVC (AMF)", 1, 9, True),
    ("h264_mf", "H.264 via ffmpeg Media Foundation", 1, 3, True),
    ("libx264", "Software H.264 (x264, GPL build)", 1, 4, True),
    ("libx265", "Software HEVC (x265, GPL build)", 1, 5, True),
    ("lossless", "Lossless FFV1 (.mkv)", 1, 10, True),
    ("builtin", "Built-in Windows Media Foundation", 0, 0, False),
)
CODEC_BY_KEY = {key: (label, enc, codec, needs) for key, label, enc, codec, needs in CODEC_CHOICES}
CODEC_LABEL_TO_KEY = {label: key for key, label, *_ in CODEC_CHOICES}
DEFAULT_CODEC_KEY = "auto"
LOSSLESS_CODEC_ID = 10
QUALITY_RANGE = (0, 51)
PRESET_RANGE = (0, 7)


def resolve_ffmpeg(configured: "Path | None") -> "Path | None":
    """Return a usable ffmpeg.exe path or None; never raises."""
    if not configured:
        return None
    try:
        candidate = Path(configured).expanduser()
    except (TypeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def bundled_ffmpeg_path() -> "Path | None":
    """Locate the pinned FFmpeg shipped with a packaged Dolly, if present."""
    try:
        from .runtime import resource_root
        root = resource_root()
    except (ImportError, OSError, ValueError):
        return None
    for candidate in (root / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe",
                      root / "_internal" / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe"):
        if candidate.is_file():
            return candidate
    return None


def resolve_backend(options: "VideoOptions") -> tuple[int, int]:
    """Choose the native (encoder, codec) pair for validated options."""
    _label, encoder, codec_id, needs_ffmpeg = CODEC_BY_KEY[options.codec]
    if options.codec == "auto":
        return (1, 1) if (needs_ffmpeg or options.ffmpeg_path) else (0, 0)
    if needs_ffmpeg and not options.ffmpeg_path:
        return 0, 0
    return encoder, codec_id


def default_video_path(shot_name: str, directory: Path | None = None, *, now: datetime | None = None) -> Path:
    """Choose an unused filename without creating directories or files."""
    if directory is None:
        videos = Path.home() / "Videos"
        directory = videos if videos.is_dir() else Path.home()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(shot_name)).strip(" .")[:80] or "Dolly"
    # Prefixing with Dolly avoids Windows device basenames such as CON.mp4.
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"Dolly_{name}_{stamp}"
    candidate = Path(directory) / (stem + ".mp4")
    sequence = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = Path(directory) / f"{stem}_{sequence}.mp4"
        sequence += 1
    return candidate


@dataclass(frozen=True)
class VideoOptions:
    path: Path
    fps: int = 60
    bitrate: int = 20_000_000
    codec: str = DEFAULT_CODEC_KEY
    quality: int = 0
    preset: int = 0
    ffmpeg_path: "Path | None" = None
    fixed_step: bool = False
    # Replay slow-motion for fixed-step export (same range as playback speed).
    speed: float = 1.0

    def validated(self) -> VideoOptions:
        if type(self.fps) is not int or self.fps not in (30, 60, 120):
            raise ValueError("Choose a video frame rate of 30, 60 or 120 FPS.")
        if type(self.bitrate) is not int or self.bitrate not in BITRATE_PRESETS.values():
            raise ValueError("Choose a video bitrate of 10, 20, or 40 Mbps.")
        if self.codec not in CODEC_BY_KEY:
            raise ValueError("Choose a supported video codec.")
        _label, _encoder, codec_id, needs_ffmpeg = CODEC_BY_KEY[self.codec]
        if type(self.quality) is not int or not QUALITY_RANGE[0] <= self.quality <= QUALITY_RANGE[1]:
            raise ValueError("Choose a video quality between 0 and 51.")
        if type(self.preset) is not int or not PRESET_RANGE[0] <= self.preset <= PRESET_RANGE[1]:
            raise ValueError("Choose a supported encoder preset.")
        raw = str(self.path)
        if not raw.strip() or any(ord(char) < 32 for char in raw):
            raise ValueError("Choose an output file without control characters.")
        path = Path(self.path).expanduser().absolute()
        suffix = ".mkv" if codec_id == LOSSLESS_CODEC_ID else ".mp4"
        if path.suffix.lower() != suffix:
            raise ValueError(f"The output filename must end in {suffix}.")
        if not path.parent.is_dir():
            raise ValueError("Choose an existing output folder.")
        if path.exists() or path.is_symlink():
            raise ValueError("That output file already exists. Choose a new filename.")
        if type(self.fixed_step) is not bool:
            raise ValueError("Fixed-step export must be on or off.")
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)):
            raise ValueError("Export speed must be a number between 0.05 and 4.")
        speed = float(self.speed)
        if not 0.05 <= speed <= 4:
            raise ValueError("Export speed must be between 0.05 and 4.")
        ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        if needs_ffmpeg and not ffmpeg:
            raise ValueError("Select an ffmpeg.exe for the chosen encoder.")
        # The native writer also creates the file exclusively. This early
        # check gives a useful error; it is not the overwrite safety boundary.
        return VideoOptions(path, self.fps, self.bitrate, self.codec, self.quality, self.preset,
                            ffmpeg, self.fixed_step, speed)


def recording_ready(status: dict) -> bool:
    return bool(status.get("connected") and status.get("game_running")
                and status.get("camera_backend") == "native"
                and status.get("startup_stage") in ("editing_ready", "replay_ready"))


def format_video_status(status: dict) -> str:
    state = str(status.get("state", "idle"))
    if state == "failed":
        return "Recording failed: " + str(status.get("error") or "Native recording stopped unexpectedly.")
    if state == "idle":
        return "Ready to record."
    if state == "starting":
        return str(status.get("message") or "Starting recorder…")
    if state == "finalizing":
        return "Finishing MP4…"
    if state == "cancelled":
        return "Recording discarded."
    if state not in ("recording", "completed"):
        return "Video recorder unavailable."
    duration = max(0.0, float(status.get("duration", 0)))
    frames = max(0, int(status.get("frames_written", 0)))
    dropped = max(0, int(status.get("frames_dropped", status.get("dropped", 0))))
    width, height = int(status.get("width", 0)), int(status.get("height", 0))
    details = [f"{duration:.1f} s", f"{frames:,} frames"]
    if width > 0 and height > 0:
        details.append(f"{width} × {height}")
    if dropped:
        details.append(f"{dropped:,} missed frames")
    return ("Recording · " if state == "recording" else "Saved · ") + " · ".join(details)


class VideoExport:
    """Small lifecycle adapter; the native encoder owns the output file."""

    def __init__(self, controller):
        self.controller = controller
        self._bridge = None
        self._lock = threading.RLock()
        self._last = {"state": "idle"}
        self._start_pending = False
        self._start_ack = None
        self.output_path: Path | None = None

    def status(self) -> dict:
        with self._lock:
            bridge = self._bridge
            last = dict(self._last)
            pending, start_ack = self._start_pending, self._start_ack
        if bridge is None:
            return last
        try:
            current = dict(bridge.video_status())
            if current.get("state") not in ACTIVE_STATES | TERMINAL_STATES:
                raise RuntimeError("The native recorder returned an unsupported state.")
            if pending:
                if current.get("ack") == start_ack and current.get("state") not in ACTIVE_STATES:
                    # A timed-out start may still execute later. Old idle or
                    # completed telemetry cannot authorize a second start.
                    current = last
                elif current.get("command_error"):
                    current = {**current, "state": "failed", "error": str(current.get("command_message") or "The native recorder rejected the request.")}
                    with self._lock:
                        self._start_pending = False
                else:
                    with self._lock:
                        self._start_pending = False
            elif (current.get("command_error") and last.get("state") == "failed"
                  and current.get("ack") == last.get("ack")):
                current = last
            if (current.get("state") in ACTIVE_STATES
                    and self.controller.status().get("game_running") is False):
                with self._lock:
                    self._start_pending = False
                raise RuntimeError("Deadlock closed before MP4 finalization was confirmed. The recording may be incomplete.")
        except (RuntimeError, ValueError, OSError) as exc:
            with self._lock:
                uncertain = self._start_pending
            if uncertain:
                current = {**last, "state": "starting", "error": str(exc),
                           "message": "Recorder status unavailable. Finish or discard before recording again."}
            elif last.get("state") in ACTIVE_STATES:
                current = {**last, "state": "failed", "error": str(exc)}
            else:
                return last
        with self._lock:
            self._last = current
        return dict(current)

    def _set_export_timing(self, fps, speed):
        setter = getattr(self.controller, "set_export_timing", None)
        if callable(setter):
            setter(fps, speed)

    def _clear_export_timing(self):
        clear = getattr(self.controller, "clear_export_timing", None)
        if callable(clear):
            try:
                clear()
            except (RuntimeError, ValueError, OSError):
                pass

    def start(self, options: VideoOptions) -> dict:
        options = options.validated()
        if self.status().get("state") in ACTIVE_STATES:
            raise RuntimeError("Finish the current recording before starting another.")
        if not recording_ready(self.controller.status()):
            raise RuntimeError("Launch a DirectX 11 replay through Dolly and wait for the native editor before recording.")
        bridge = self.controller._native_bridge()
        if bridge is None:
            raise RuntimeError("The native recorder is not connected.")
        if options.fixed_step:
            self._set_export_timing(options.fps, options.speed)
        initial_ack = bridge.video_status().get("ack")
        with self._lock:
            self._bridge = bridge
            self._last = {"state": "starting"}
            self.output_path = options.path
            self._start_pending = True
            self._start_ack = initial_ack
        encoder, codec_id = resolve_backend(options)
        try:
            bridge.start_video(str(options.path), fps=options.fps, bitrate=options.bitrate,
                               encoder=encoder, codec=codec_id, quality=options.quality,
                               preset=options.preset,
                               ffmpeg_path=str(options.ffmpeg_path) if encoder == 1 and options.ffmpeg_path else "",
                               fixed_step=options.fixed_step)
        except Exception as exc:
            self._clear_export_timing()
            with self._lock:
                self._last = {"state": "starting", "error": str(exc),
                              "message": "Start not confirmed. Finish or discard before recording again."}
            raise
        with self._lock:
            self._start_pending = False
        return self.status()

    def stop(self, *, cancel: bool = False, timeout: float = 30.0) -> dict:
        """Request shutdown and wait on the operation worker, never on Tk."""
        try:
            with self._lock:
                bridge = self._bridge
            if bridge is None or self.status().get("state") not in ACTIVE_STATES:
                return self.status()
            bridge.stop_video(cancel=cancel)
            with self._lock:
                self._start_pending = False
            deadline = time.monotonic() + timeout
            while True:
                current = self.status()
                if current.get("state") not in ACTIVE_STATES:
                    if current.get("state") == "failed":
                        raise RuntimeError(str(current.get("error") or "MP4 finalization failed."))
                    return current
                if time.monotonic() >= deadline:
                    raise RuntimeError("The video recorder is still finishing. Wait for its status before starting another recording or closing the game.")
                time.sleep(0.05)
        finally:
            self._clear_export_timing()
