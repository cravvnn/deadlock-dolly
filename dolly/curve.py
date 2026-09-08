"""Editable aspect-ratio graph using the same sampler as shot playback.

Graph edits stay local until release. Dragging changes framing only; camera
timestamps remain untouched. This keeps a framing adjustment from accidentally
retiming the replay or changing a camera path.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import math
import tkinter as tk
from tkinter import ttk

from .path import ASPECT_MIN, ASPECT_MAX, Project


BACKGROUND = "#10151c"
GRID = "#28323f"
TEXT = "#e8edf3"
MUTED = "#8f9eae"
ACCENT = "#64d6c3"


def clamp_aspect(value: float) -> float:
    """Constrain an authored aspect to Dolly's supported framing range."""
    if not math.isfinite(value):
        raise ValueError("Aspect ratio must be finite")
    return max(ASPECT_MIN, min(ASPECT_MAX, value))


@dataclass(frozen=True)
class PlotBounds:
    left: float
    top: float
    right: float
    bottom: float
    duration: float
    low: float
    high: float

    def point(self, time: float, value: float) -> tuple[float, float]:
        return (self.left + time / self.duration * (self.right - self.left),
                self.bottom - (value - self.low) / (self.high - self.low)
                * (self.bottom - self.top))

    def aspect_at(self, y: float) -> float:
        value = self.low + (self.bottom - y) / (self.bottom - self.top) * (self.high - self.low)
        return clamp_aspect(value)


def plot_bounds(width: float, height: float, project: Project) -> PlotBounds:
    """Keep the complete framing range reachable with an ordinary in-canvas drag.

    A fixed scale avoids an initially flat shot having a tiny editable range,
    and lets the pointer keep the same meaning before and after every edit.
    """
    duration = project.duration if project.duration > 0 else 1.0
    return PlotBounds(48.0, 19.0, max(70.0, width - 18.0), max(46.0, height - 28.0),
                      duration, ASPECT_MIN, ASPECT_MAX)


def sample_curve(project: Project, duration: float, samples: int = 160) -> list[tuple[float, float]]:
    """Sample the real evaluator, preserving step jumps and authored key times."""
    if not project.keyframes:
        return [(0.0, project.standard_aspect), (duration, project.standard_aspect)]
    samples = max(2, min(256, samples))
    times = {duration * index / (samples - 1) for index in range(samples)}
    for key in project.keyframes:
        times.add(float(key.time))
        if project.lens_interpolation == "step" and key.time > 0:
            times.add(math.nextafter(float(key.time), -math.inf))
    return [(time, float(project.evaluate(time)["aspect_ratio"])) for time in sorted(times)]


def hit_key(points: list[tuple[float, float]], x: float, y: float, radius: float = 12.0) -> int | None:
    """Find the nearest key within a useful pointer target, including overlapped keys."""
    if not points:
        return None
    distances = [(px - x) ** 2 + (py - y) ** 2 for px, py in points]
    index = min(range(len(points)), key=distances.__getitem__)
    return index if distances[index] <= radius * radius else None


class AspectCurve(ttk.Frame):
    """Compact graph. Callbacks receive selection or one committed framing edit.

    ``on_change(index, time, aspect)`` owns validation and updating the project;
    ``on_select(index)`` can synchronize the camera list and inspector. This
    widget never mutates the caller's Project. Arrow keys edit the selected key
    by 0.01, or by 0.1 with Shift. Escape abandons an unfinished drag.
    """

    def __init__(self, parent, on_change, on_select, *, height: int = 145, **kwargs):
        super().__init__(parent, **kwargs)
        self.on_change = on_change
        self.on_select = on_select
        self.project = Project()
        self.selected_index = None
        self.current_time = 0.0
        self._enabled = True
        self._drag_index = None
        self._drag_value = None
        self._drag_bounds = None
        self._preview = None
        self._bounds = None
        self._points = []
        self.canvas = tk.Canvas(self, background=BACKGROUND, highlightthickness=0,
                                borderwidth=0, height=height, width=300, takefocus=True)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.hint = ttk.Label(self, text="Drag a key to change framing · ↑ / ↓ to fine tune",
                              foreground=MUTED, font=("Segoe UI", 9))
        self.hint.grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<KeyPress-Up>", self._key)
        self.canvas.bind("<KeyPress-Down>", self._key)
        self.canvas.bind("<KeyPress-Escape>", self._cancel)

    def set_project(self, project: Project, selected_index=None, current_time: float = 0.0):
        if project is not self.project or (self._drag_index is not None
                                          and self._drag_index >= len(project.keyframes)):
            self._clear_drag()
        self.project = project
        self.selected_index = (selected_index if isinstance(selected_index, int)
                               and 0 <= selected_index < len(project.keyframes) else None)
        self.current_time = max(0.0, float(current_time))
        self._redraw()

    def set_current_time(self, current_time: float):
        """Move just the playhead during playback without resampling the graph."""
        self.current_time = max(0.0, float(current_time))
        self._draw_playhead()

    def set_enabled(self, enabled: bool):
        """Prevent framing edits while the app is busy or a shot is playing."""
        self._enabled = bool(enabled)
        if not self._enabled and self._drag_index is not None:
            self._cancel()
        self.hint.configure(text=("Drag a key to change framing · ↑ / ↓ to fine tune"
                                  if self._enabled else "Pause playback to edit framing"))

    def _redraw(self, _event=None):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 90 or height < 55:
            return
        project = self._preview or self.project
        bounds = self._drag_bounds or plot_bounds(width, height, project)
        self._bounds = bounds
        for index in range(4):
            value = bounds.low + (bounds.high - bounds.low) * index / 3
            _, y = bounds.point(0, value)
            canvas.create_line(bounds.left, y, bounds.right, y, fill=GRID)
            canvas.create_text(bounds.left - 9, y, text=f"{value:.2f}", anchor="e",
                               fill=MUTED, font=("Segoe UI", 8))
        for index in range(5):
            timestamp = bounds.duration * index / 4
            x, _ = bounds.point(timestamp, bounds.low)
            canvas.create_line(x, bounds.top, x, bounds.bottom, fill=GRID)
            canvas.create_text(x, bounds.bottom + 14, text=f"{timestamp:g}s", fill=MUTED,
                               font=("Segoe UI", 8))
        canvas.create_text(bounds.left, 7, text="ASPECT RATIO", anchor="w", fill=MUTED,
                           font=("Segoe UI", 8))
        _, standard_y = bounds.point(0, project.standard_aspect)
        canvas.create_line(bounds.left, standard_y, bounds.right, standard_y,
                           fill=MUTED, dash=(3, 5))
        canvas.create_text(bounds.right, 7, text=f"Standard {project.standard_aspect:.3f}",
                           anchor="e", fill=MUTED, font=("Segoe UI", 8))
        samples = sample_curve(project, bounds.duration, max(40, int(width / 4)))
        coordinates = [coordinate for timestamp, aspect in samples
                       for coordinate in bounds.point(timestamp, aspect)]
        canvas.create_line(*coordinates, fill=ACCENT, width=2)
        self._points = [bounds.point(key.time, key.aspect_ratio) for key in project.keyframes]
        for index, (x, y) in enumerate(self._points):
            selected = index == self.selected_index
            radius = 6 if selected else 4
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                               fill=TEXT if selected else ACCENT, outline=BACKGROUND, width=2)
        self._draw_playhead()

    def _draw_playhead(self):
        self.canvas.delete("playhead")
        if self._bounds is None:
            return
        bounds = self._bounds
        x, _ = bounds.point(min(bounds.duration, self.current_time), bounds.low)
        self.canvas.create_line(x, bounds.top, x, bounds.bottom, fill="#6d879b",
                                width=1, dash=(2, 3), tags="playhead")

    def _press(self, event):
        if not self._enabled:
            return
        self.canvas.focus_set()
        index = hit_key(self._points, event.x, event.y)
        if index is None:
            return
        self.selected_index = index
        self._drag_index = index
        self._drag_value = None
        self._drag_bounds = self._bounds
        self.on_select(index)
        self._redraw()

    def _motion(self, event):
        if self._drag_index is None or self._drag_bounds is None:
            return
        self._drag_value = round(self._drag_bounds.aspect_at(event.y), 4)
        self._preview = copy.copy(self.project)
        self._preview.keyframes = list(self.project.keyframes)
        self._preview.keyframes[self._drag_index] = replace(
            self.project.keyframes[self._drag_index], aspect_ratio=self._drag_value)
        self._redraw()

    def _release(self, _event):
        index, value = self._drag_index, self._drag_value
        self._clear_drag()
        try:
            if index is not None and value is not None and index < len(self.project.keyframes):
                key = self.project.keyframes[index]
                if abs(value - key.aspect_ratio) > 1e-6:
                    self.on_change(index, key.time, value)
        finally:
            self._redraw()

    def _clear_drag(self):
        self._drag_index = None
        self._drag_value = None
        self._drag_bounds = None
        self._preview = None

    def _cancel(self, _event=None):
        self._clear_drag()
        self._redraw()
        return "break"

    def _key(self, event):
        if (not self._enabled or self._drag_index is not None or self.selected_index is None
                or not 0 <= self.selected_index < len(self.project.keyframes)):
            return "break"
        key = self.project.keyframes[self.selected_index]
        increment = 0.1 if event.state & 0x0001 else 0.01
        if event.keysym == "Down":
            increment = -increment
        value = clamp_aspect(round(key.aspect_ratio + increment, 4))
        if value != key.aspect_ratio:
            self.on_change(self.selected_index, key.time, value)
        self._redraw()
        return "break"
