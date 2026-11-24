"""Take-size estimate and recording progress text, shared by the desktop.

The in-game Export drawer draws the same lines from
``native/include/dolly_capture_estimate.hpp``; both must produce the same text
for the same inputs (see the shared cases in the tests).

A camera-path take renders ``duration x fps / speed`` frames in fixed-step
export (speed applies only there) and ``duration x fps`` in real time. Frames
and video length are exact for a known shot length. How long a fixed-step take
takes depends on this PC, so the time is only shown once a finished take of the
same kind (depth on/off, EXR on/off) has measured the capture rate, and while
recording from the take's own rate.
"""
from __future__ import annotations

import math
import time

# A rate needs this much of a take before it is trusted.
MIN_RATE_SECONDS = 5.0
MIN_RATE_FRAMES = 30
# Below this the remaining time reads "almost done" instead of "about 5 s left".
ALMOST_DONE_SECONDS = 5.0


def take_frames(duration: float, fps: float, speed: float, fixed_step: bool) -> int | None:
    """Frames one take renders, or None when the shot length is unknown."""
    try:
        duration, fps, speed = float(duration), float(fps), float(speed)
    except (TypeError, ValueError):
        return None
    if not fixed_step:
        speed = 1.0
    if not (math.isfinite(duration) and math.isfinite(fps) and math.isfinite(speed)):
        return None
    if duration <= 0 or fps <= 0 or speed <= 0:
        return None
    return max(1, math.ceil(duration * fps / speed - 1e-6))


def video_seconds(duration: float, speed: float, fixed_step: bool) -> float:
    return float(duration) / (float(speed) if fixed_step else 1.0)


def format_duration(seconds: float) -> str:
    """'40 s', '3 min', '1 h 5 min'; rounded the way a person would say it."""
    # Half-up rounding, the same as the native copy (Python's round() is half-even).
    seconds = max(0.0, float(seconds))
    if seconds < 55:
        return f"{max(5, math.floor(seconds / 5.0 + .5) * 5)} s"
    minutes = max(1, math.floor(seconds / 60.0 + .5))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def estimate_line(frames: int, video: float, rate: float | None, per_pass: bool = False) -> str:
    """Before recording: '15,720 frames · 26.2 s of video · about 24 min to record'.

    A layered export records several passes of different cost, so it gives the
    per-pass size without a time.
    """
    if per_pass:
        return f"{int(frames):,} frames per pass · {float(video):.1f} s of video"
    text = f"{int(frames):,} frames · {float(video):.1f} s of video"
    if rate and rate > 0:
        text += f" · about {format_duration(frames / rate)} to record"
    return text


def progress_line(written: int, total: int | None, seconds_left: float | None) -> str:
    """While recording: '1,810 / 15,720 frames · about 21 min left'."""
    written = max(0, int(written))
    if total is None:
        return f"{written:,} frames"
    text = f"{written:,} / {max(int(total), written):,} frames"
    if seconds_left is None:
        return text + " · estimating time left"
    if seconds_left < ALMOST_DONE_SECONDS:
        return text + " · almost done"
    return text + f" · about {format_duration(seconds_left)} left"


def capture_rate(frames: int, elapsed: float) -> float | None:
    """Frames per wall-clock second once enough of the take has run."""
    if frames < MIN_RATE_FRAMES or not elapsed or elapsed < MIN_RATE_SECONDS:
        return None
    return frames / elapsed


def seconds_left(written: int, total: int, rate: float | None) -> float | None:
    if not rate or rate <= 0:
        return None
    return max(0, total - written) / rate


class RateMemory:
    """Capture rate of the last finished fixed-step take of each kind, this session."""

    def __init__(self):
        self._rates: dict[tuple[bool, bool], float] = {}

    @staticmethod
    def key(depth: bool, depth_exr: bool) -> tuple[bool, bool]:
        return bool(depth), bool(depth and depth_exr)

    def remember(self, depth: bool, depth_exr: bool, rate: float | None) -> None:
        if rate and math.isfinite(rate) and rate > 0:
            self._rates[self.key(depth, depth_exr)] = float(rate)

    def rate(self, depth: bool, depth_exr: bool) -> float | None:
        return self._rates.get(self.key(depth, depth_exr))


class TakeProgress:
    """Progress of the current take; teaches ``memory`` its rate when it ends well."""

    def __init__(self, memory: RateMemory):
        self.memory = memory
        self.total: int | None = None
        self.depth = self.depth_exr = self.fixed_step = False
        self.rate: float | None = None
        self.learn = False
        self._recording = False

    def begin(self, total: int | None, *, depth: bool, depth_exr: bool, fixed_step: bool) -> None:
        """A new export's main color take; only it teaches the rate."""
        self.total, self.depth, self.depth_exr = total, bool(depth), bool(depth_exr)
        self.fixed_step, self.rate, self._recording = bool(fixed_step), None, False
        self.learn = True

    def layer_pass(self) -> None:
        """A later layer pass: same frame count, a different cost per frame."""
        self.rate, self._recording, self.learn = None, False, False

    def update(self, status: dict, now: float | None = None) -> str | None:
        """The progress line while recording, otherwise None."""
        state = status.get("state")
        if state != "recording":
            if (self._recording and state in ("finalizing", "completed")
                    and self.fixed_step and self.learn):
                self.memory.remember(self.depth, self.depth_exr, self.rate)
            self._recording = False
            return None
        self._recording = True
        written = max(0, int(status.get("frames_written") or 0))
        elapsed = elapsed_since(int(status.get("video_first_qpc") or 0),
                                int(status.get("qpc_frequency") or 0), now)
        rate = capture_rate(written, elapsed or 0)
        if rate is not None:
            self.rate = rate
        left = seconds_left(written, self.total, self.rate) if self.total is not None else None
        return progress_line(written, self.total, left)


def elapsed_since(first_qpc: int, frequency: int, now: float | None = None) -> float | None:
    """Seconds since the recorder's first frame. ``time.perf_counter`` reads the
    same QueryPerformanceCounter on Windows, so both sides share one clock."""
    if not first_qpc or not frequency:
        return None
    now = time.perf_counter() if now is None else now
    elapsed = now - first_qpc / frequency
    return elapsed if elapsed > 0 else None
