"""The shared smooth-motion driver (USER-ADDITIONS UPDATE 4).

Stickers and the backdrop shard keep the posterized 500 ms boil (:mod:`.boil`). Everything that
moves smoothly instead runs here, on one :class:`Animator` per toplevel: a single ``after()`` loop
at about 60 fps that exists only while something animates. Motion is time-based: every frame
computes where the motion should be *now* (``time.perf_counter``), so a late frame skips ahead
and never slows the motion down. With motion off (:func:`.boil.motion_enabled` false, i.e.
``DOLLY_UI_MOTION=0`` or Windows animation effects off) or a widget that is not viewable (a
withdrawn test root, the hidden bundle self-test), every motion applies its end state at once
and nothing is scheduled.

:class:`WheelScroller` eases page wheel scrolling. A page gives it its own immediate scroll
(``scroll(units) -> units moved``) and feeds it wheel distances; the scrollbar, the keyboard,
focus reveal and every scroll from code stay immediate and call :meth:`WheelScroller.stop` first.

:meth:`Animator.run` moves one value over a duration (the first step is drawn at once, in the
event handler) and :class:`ColorRamp` builds the hover/press colour ramps of canvas controls on it.
Image faces never get built inside a frame: :func:`steps` names the :data:`STEPS` colours of a
ramp and :func:`prewarm` builds their images in idle slices once the widget has been mapped; a
frame shows the nearest step built so far. This module imports nothing from the rest of the UI
but :mod:`.boil`, so every control module can use it (:func:`mix` equals ``canvas_controls.mix``).
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from typing import Callable

from . import boil

FRAME_MS = 16           # ~60 fps
HOVER_IN_MS = 120       # colour ramps (PLAN-5 2.11 M3)
HOVER_OUT_MS = 180
PRESS_MS = 120
SLIDE_MS = 200          # the sidebar highlight
WHEEL_MS = 140          # one wheel notch
FADE_MS = 150           # edge-fade alpha ramps
STEPS = 8               # pre-warmed colours of an image face's ramp, both ends included


def ease_out(t: float) -> float:
    """Cubic ease-out, ``1 - (1 - t)**3``, for ``t`` clamped to [0, 1]."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    u = 1.0 - t
    return 1.0 - u * u * u


def smooth(widget) -> bool:
    """True when smooth motion may run for ``widget``: motion is on and the widget is viewable."""
    if not boil.motion_enabled():
        return False
    try:
        return bool(widget.winfo_viewable())
    except tk.TclError:
        return False


def _rgb(hex_color: str) -> tuple[int, int, int]:
    # ui_theme.rgb, repeated so this module stays a leaf (the same result and the same errors).
    value = hex_color.strip()
    if len(value) != 7 or value[0] != "#":
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"not a #rrggbb colour: {hex_color!r}") from exc


def mix(color: str, other: str, amount: float) -> str:
    """``amount`` of ``color`` over ``other`` (0..1), as ``#rrggbb`` (= ``canvas_controls.mix``)."""
    a, b = _rgb(color), _rgb(other)
    return "#%02x%02x%02x" % tuple(int(y + (x - y) * amount + .5) for x, y in zip(a, b))


def steps(rest: str, hover: str, n: int = STEPS) -> tuple[str, ...]:
    """``n`` colours from ``rest`` to ``hover``, both included: the faces an image ramp pre-warms."""
    if n < 2:
        raise ValueError(f"a ramp needs at least 2 steps, got {n}")
    return tuple(mix(hover, rest, i / (n - 1)) for i in range(n))


def prewarm(widget, builder: Callable[[str], object], colours) -> None:
    """Call ``builder(colour)`` for each colour, one per idle callback, once ``widget`` is mapped.

    For the image faces of a ramp (the images it shows between its ends, cached by the
    builder): they are built after the widget's first ``<Map>`` (at once if it is mapped
    already), one per idle slice, and never while an animation of the toplevel runs (the queue
    waits until its motions end). The caller builds both ends itself, before any ramp needs them.
    """
    colours = tuple(colours)
    if not colours:
        return
    try:
        animator = Animator.of(widget)
        mapped = widget.winfo_ismapped()
    except tk.TclError:
        return
    if mapped:
        animator._prewarm(widget, builder, colours)
        return
    pending = [True]

    def first_map(event) -> None:
        if pending[0] and str(event.widget) == str(widget):
            pending[0] = False
            animator._prewarm(widget, builder, colours)

    widget.bind("<Map>", first_map, add="+")


class _Run:
    """The step of :meth:`Animator.run`: one value eased from ``start`` to ``end``."""

    __slots__ = ("apply", "start", "end", "t0", "duration", "ease", "done")

    def __init__(self, apply, start, end, t0, duration, ease, done):
        self.apply, self.start, self.end = apply, start, end
        self.t0, self.duration, self.ease, self.done = t0, duration, ease, done

    def value(self, now: float) -> float:
        t = (now - self.t0) / self.duration
        if t >= 1.0:
            return self.end
        return self.start + (self.end - self.start) * self.ease(max(0.0, t))

    def __call__(self, now: float) -> bool:
        # Decimal frame durations are not exactly representable as binary floats.
        # Treat a sub-nanosecond rounding error at the endpoint as complete so
        # cleanup callbacks (including removal of layered window styles) run.
        if now - self.t0 + 1e-9 < self.duration:
            self.apply(self.value(now))
            return True
        self.apply(self.end)
        if self.done is not None:
            self.done()
        return False


class Animator:
    """One frame loop per toplevel; it runs only while a step is registered.

    ``start(key, step)`` registers ``step(now) -> bool`` (``now`` in seconds from the animator's
    clock); every frame calls each step once and drops the ones that return False. The first
    frame runs at the next idle moment, after the events already queued (a burst of wheel events
    becomes one frame), then one every :data:`FRAME_MS`. A step that raises is dropped and the
    error reported after the frame. ``tick()`` runs one frame by hand (tests).

    ``run(key, duration_ms, apply, ...)`` is the usual client: one value eased over a duration,
    its first step applied at once. ``finish()`` brings every motion (or one) to its end now. The
    animator also holds the toplevel's :func:`prewarm` queue, served only while nothing moves.
    """

    def __init__(self, root, *, clock: Callable[[], float] = time.perf_counter, frame_ms: int = FRAME_MS):
        self.root = root
        self.clock = clock
        self.frame_ms = frame_ms
        self.frames = 0
        self._steps: dict = {}
        self._job = None
        self._ticking = False
        self._dead = False
        self._warm: list = []         # (widget, builder, colour) still to build (prewarm)
        self._warm_job = None
        self._warm_command = None
        # One Tcl command for the loop's whole life (tkinter's after() makes a new one per call).
        self._command = root.register(self._run)
        root.bind("<Destroy>", self._destroyed, add="+")

    @classmethod
    def of(cls, widget) -> "Animator":
        """The animator of ``widget``'s toplevel, created on first use."""
        top = widget.winfo_toplevel()
        animator = getattr(top, "_dolly_animator", None)
        if animator is None:
            animator = top._dolly_animator = cls(top)
        return animator

    @property
    def running(self) -> bool:
        """True while a frame is scheduled."""
        return self._job is not None

    def active(self, key=None) -> bool:
        return bool(self._steps) if key is None else key in self._steps

    def start(self, key, step: Callable[[float], bool]) -> None:
        if self._dead:
            return
        self._steps[key] = step
        if self._job is None and not self._ticking:
            try:
                self._job = self.root.tk.call("after", "idle", self._command)
            except tk.TclError:
                self._job = None

    def stop(self, key) -> None:
        self._steps.pop(key, None)
        if not self._steps:
            self._cancel()
            self._settled()

    def run(self, key, duration_ms: float, apply: Callable[[float], object], *, start: float = 0.0,
            end: float = 1.0, ease: Callable[[float], float] = ease_out,
            done: Callable[[], object] | None = None, lead_frame: bool = True) -> None:
        """Ease a value from ``start`` to ``end`` over ``duration_ms``, calling ``apply(value)``.

        The first step is applied before this returns (pointer feedback starts on the event
        itself, PLAN-5 M1): the event counts as the motion's first frame, so that step is one
        frame along, then one per frame until ``end`` (time-based: a late frame skips ahead).
        ``done()`` runs once the end is applied. A run whose key is still moving retargets from
        its current value (``start`` is ignored then; the old run ends without its ``done``).
        With motion off or the toplevel not viewable (:func:`smooth`), a duration of one frame
        or less, or nothing to travel: ``apply(end)`` and ``done()`` now, nothing scheduled.
        """
        if self._dead:
            return
        now = self.clock()
        moving = self._steps.get(key)
        if isinstance(moving, _Run):
            start = moving.value(now)
        if moving is not None:
            self.stop(key)
        frame = self.frame_ms / 1000.0
        duration = duration_ms / 1000.0
        if duration <= frame or start == end or not smooth(self.root):
            apply(end)
            if done is not None:
                done()
            return
        step = _Run(apply, start, end, now - frame if lead_frame else now, duration, ease, done)
        apply(step.value(now))
        self._steps[key] = step
        if self._job is None and not self._ticking:
            try:   # the first step is drawn: the next one is a frame away
                self._job = self.root.tk.call("after", self.frame_ms, self._command)
            except tk.TclError:
                self._job = None

    def finish(self, key=None) -> None:
        """Every motion (or the one of ``key``) at its end now, then dropped.

        Each step runs once at ``clock() + 3600 s``, so it applies its end value (a run calls
        its ``done``; a :class:`WheelScroller` travels the rest of its distance). For tests, a
        page that hides, a control that is disabled or destroyed. Motions that a finished one
        starts (a ``done`` that chains) are finished too.
        """
        if self._dead:
            return
        error = None
        for _pass in range(16):
            if key is None:
                due = list(self._steps.items())
                self._steps.clear()
            else:
                step = self._steps.pop(key, None)
                due = [] if step is None else [(key, step)]
            if not due:
                break
            now = self.clock() + 3600.0
            for _key, step in due:
                try:
                    step(now)
                except tk.TclError:
                    pass   # its widget is gone
                except Exception:  # noqa: BLE001 - reported below, the other motions still end
                    error = error or sys.exc_info()
            if key is not None:
                break
        if not self._steps:
            self._cancel()
            self._settled()
        if error is not None:
            self.root.report_callback_exception(*error)

    def tick(self, now: float | None = None) -> None:
        """Run one frame of every registered step."""
        now = self.clock() if now is None else now
        self.frames += 1
        error = None
        self._ticking = True
        try:
            for key, step in list(self._steps.items()):
                if self._steps.get(key) is not step:
                    continue   # stopped or replaced by an earlier step of this frame
                try:
                    keep = step(now)
                except tk.TclError:
                    keep = False   # its widget is gone
                except Exception:  # noqa: BLE001 - reported below, the other motions go on
                    keep = False
                    error = error or sys.exc_info()
                if not keep and self._steps.get(key) is step:
                    del self._steps[key]
        finally:
            self._ticking = False
        if not self._steps:
            self._settled()
        if error is not None:
            self.root.report_callback_exception(*error)

    def _run(self) -> None:
        self._job = None
        if self._dead:
            return
        self.tick()
        if self._steps and self._job is None and not self._dead:
            try:
                self._job = self.root.tk.call("after", self.frame_ms, self._command)
            except tk.TclError:
                self._job = None

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.root.tk.call("after", "cancel", self._job)
            except tk.TclError:
                pass
            self._job = None

    def _destroyed(self, event) -> None:
        if event.widget is self.root:
            self._dead = True
            self._steps.clear()
            self._cancel()
            try:
                self.root.deletecommand(self._command)
            except tk.TclError:
                pass
            self._drop_warm()

    # -- pre-warm queue (prewarm) ---------------------------------------------------------------

    def _drop_warm(self) -> None:
        self._warm.clear()
        try:
            if self._warm_job is not None:
                self.root.tk.call("after", "cancel", self._warm_job)
            if self._warm_command is not None:
                self.root.deletecommand(self._warm_command)
        except tk.TclError:
            pass
        self._warm_job = self._warm_command = None

    def _prewarm(self, widget, builder, colours) -> None:
        if self._dead:
            return
        self._warm.extend((widget, builder, colour) for colour in colours)
        self._settled()

    def _settled(self) -> None:
        """Nothing moves: serve the pre-warm queue (one idle slice at a time)."""
        if not self._warm or self._warm_job is not None or self._steps or self._dead:
            return
        try:
            if self._warm_command is None:
                self._warm_command = self.root.register(self._warm_slice)
            self._warm_job = self.root.tk.call("after", "idle", self._warm_command)
        except tk.TclError:
            self._warm_job = None

    def _warm_slice(self) -> None:
        self._warm_job = None
        if self._dead or self._steps:
            return   # a motion runs: the queue waits until it ends (_settled)
        while self._warm:
            widget, builder, colour = self._warm.pop(0)
            try:
                if not widget.winfo_exists():
                    continue
                builder(colour)
            except tk.TclError:
                continue   # its widget went away meanwhile
            except Exception:  # noqa: BLE001 - one face fails to warm, the rest still do
                self.root.report_callback_exception(*sys.exc_info())
            break          # one image per idle callback
        self._settled()


class WheelScroller:
    """Eased wheel scrolling for one page (USER-ADDITIONS UPDATE 4 (c)).

    ``scroll(units)`` is the page's immediate scroll: it moves the view by whole units at once
    (and does whatever the page does after a scroll) and returns how many units it really moved,
    fewer at an edge. :meth:`add` takes wheel distances in px. With smooth motion each distance is
    travelled over :data:`WHEEL_MS`, eased out; a notch during the motion retargets from where the
    view is (same direction: the rest of the way plus the new distance; the other way: the new
    distance only). One scroll per frame, however many notches arrive. An edge ends the motion.

    Frame cost: each frame's scroll and the redraws it causes are timed (from the frame's start to
    the end of the idle work it queued). When that cost is high the frames thin out, so drawing
    takes at most about half the main thread (frame interval ``2 x cost``), and above
    :attr:`INSTANT_MS` the wheel scrolls at once, as with motion off; the cost is still measured
    there, so cheaper pages ease again. Fractions of a unit (precision wheels) carry over.
    """

    DURATION_MS = WHEEL_MS
    INSTANT_MS = 24.0       # fewer than ~3 frames per notch would be left at half the main thread
    _EMA = 0.35

    def __init__(self, widget, scroll: Callable[[int], int], *, unit_pixels: float = 1,
                 duration_ms: float | None = None, animator: Animator | None = None,
                 clock: Callable[[], float] | None = None, measure: bool = True):
        self.widget = widget
        self.scroll = scroll
        self.unit = float(unit_pixels)
        self.duration = (self.DURATION_MS if duration_ms is None else duration_ms) / 1000.0
        self._animator = animator
        self._clock = clock
        self.measure = measure
        self.frames = 0           # animation frames that scrolled, over the scroller's life
        self.cost_ms = 0.0        # measured cost of one scroll (moving average, wall ms)
        self._carry = 0.0         # px not yet scrolled (less than a unit), kept for the next input
        self._from = self._to = 0.0
        self._t0 = 0.0
        self._moved = 0           # units scrolled since the motion began
        self._last = None         # time of the last frame that scrolled
        self._active = False
        self._mark = None         # Tcl command of the frame-cost marker (registered on first use)
        self._mark_job = None
        self._mark_start = None
        self._mark_round = 0

    # -- public -------------------------------------------------------------------------------

    @property
    def animator(self) -> Animator:
        if self._animator is None:
            self._animator = Animator.of(self.widget)
        return self._animator

    @property
    def active(self) -> bool:
        """True while an eased motion is under way."""
        return self._active

    @property
    def remaining(self) -> float:
        """Px still to travel (0 when idle)."""
        if not self._active:
            return 0.0
        return self._to - self._moved * self.unit

    def interval_ms(self) -> float:
        """Time between two frames of the motion for the measured cost."""
        return max(float(self.animator.frame_ms), 2.0 * self.cost_ms)

    def eases(self) -> bool:
        """True when wheel input is eased now (smooth motion, and frames cheap enough)."""
        return self.cost_ms <= self.INSTANT_MS and smooth(self.widget)

    def add(self, pixels: float) -> None:
        """Scroll ``pixels`` (positive: down), eased when :meth:`eases`, else at once."""
        if not pixels:
            return
        if not self.eases():
            if self._active:
                self.stop()
            self._instant(pixels)
            return
        now = self._now()
        if self._active:
            here = self._position(now)
            ahead = self._to - here
            self._from = here
            self._to = (self._to if ahead * pixels > 0 else here) + pixels
            self._t0 = now
            return
        start = self._carry if self._carry * pixels > 0 else 0.0
        self._carry = 0.0
        self._from, self._to, self._t0 = 0.0, start + pixels, now
        self._moved = 0
        self._last = None
        self._active = True
        self.animator.start(self, self._step)

    def stop(self) -> None:
        """End the motion where the view is now; the rest of the distance is dropped."""
        self._carry = 0.0
        if self._active:
            self._active = False
            self.animator.stop(self)

    def finish(self) -> None:
        """Travel the rest of the distance at once and end the motion."""
        if self._active:
            self._advance(self._to)
            self._end()

    # -- motion -------------------------------------------------------------------------------

    def _now(self) -> float:
        return (self._clock or self.animator.clock)()

    def _position(self, now: float) -> float:
        span = self.duration
        t = 1.0 if span <= 0 else (now - self._t0) / span
        return self._from + (self._to - self._from) * ease_out(t)

    def _step(self, now: float) -> bool:
        if not self._active:
            return False
        if not self.widget.winfo_viewable():   # (motion on/off is read per notch, in add)
            self.finish()
            return False
        done = now - self._t0 >= self.duration
        if not done and self._last is not None and (now - self._last) * 1000.0 < self.interval_ms() - 1.0:
            return True   # thinned out: the page's frames are expensive
        start = time.perf_counter()
        moved = self._advance(self._to if done else self._position(now))
        if moved is None:
            self._end(edge=True)
            return False
        if moved:
            self._last = now
            if self.measure:
                self._time(start)
        if done:
            self._end()
            return False
        return True

    def _advance(self, position: float) -> int | None:
        """Scroll to ``position`` (px from the motion's start): units moved, None when an edge stopped it."""
        units = int(round(position / self.unit)) - self._moved
        if not units:
            return 0
        moved = self.scroll(units)
        self._moved += moved
        self.frames += 1
        return moved if moved == units else None

    def _end(self, *, edge: bool = False) -> None:
        rest = self._to - self._moved * self.unit
        self._carry = 0.0 if edge or abs(rest) >= self.unit else rest
        self._active = False
        self.animator.stop(self)

    def _instant(self, pixels: float) -> None:
        total = (self._carry if self._carry * pixels > 0 else 0.0) + pixels
        units = int(total / self.unit)
        self._carry = total - units * self.unit
        if not units:
            return
        start = time.perf_counter()
        moved = self.scroll(units)
        if moved != units:
            self._carry = 0.0
        if self.measure and moved:
            self._time(start)

    # -- frame cost -----------------------------------------------------------------------------

    def _time(self, start: float) -> None:
        # The scroll queued its redraws as idle work: a marker runs after them, and once more
        # after the idle work they queued in turn (a widget redraw the canvas redraw asked for).
        if self._mark_start is not None:
            return   # still timing an earlier scroll
        try:
            if self._mark is None:
                # On the toplevel (it outlives the page); the job is cancelled with the page.
                self._mark = self.widget.winfo_toplevel().register(self._marked)
                self.widget.bind("<Destroy>", self._destroyed, add="+")
            self._mark_start, self._mark_round = start, 0
            self._mark_job = self.widget.tk.call("after", "idle", self._mark)
        except tk.TclError:
            self._mark_start = self._mark_job = None

    def _marked(self) -> None:
        self._mark_job = None
        if self._mark_start is None:
            return
        if self._mark_round == 0:
            self._mark_round = 1
            try:
                self._mark_job = self.widget.tk.call("after", "idle", self._mark)
                return
            except tk.TclError:
                pass
        cost = (time.perf_counter() - self._mark_start) * 1000.0
        self._mark_start = None
        self.cost_ms = cost if not self.cost_ms else self.cost_ms + (cost - self.cost_ms) * self._EMA

    def _destroyed(self, event) -> None:
        if event.widget is self.widget:
            self.stop()
            if self._mark_job is not None:
                try:
                    self.widget.tk.call("after", "cancel", self._mark_job)
                except tk.TclError:
                    pass
                self._mark_job = self._mark_start = None


class ColorRamp:
    """The hover/press colour of one canvas control (or one Tk option), eased on the Animator.

    The ramp is a position on a line: 0 = ``rest``, 1 = ``hover`` and, when a pressed colour is
    given, 2 = ``pressed``; colours in between are mixed per frame, so plain fills and labels
    change smoothly. :meth:`enter` and :meth:`leave` move to hover and rest over
    :data:`HOVER_IN_MS` / :data:`HOVER_OUT_MS`, :meth:`press` and :meth:`release` to pressed and
    back to hover over :data:`PRESS_MS` (a release outside the control is followed by a
    :meth:`leave`). A move during a move retargets from where the colour is (a move to the end it
    already heads for keeps its timing, so a control may call these from every redraw); its first
    step is drawn in the calling event handler. With motion off or the widget not viewable every move
    lands at once. :meth:`jump` sets a colour at once (disabled faces, selection).

    ``apply(colour)`` draws a colour (item fills, label colours). An image face (corners, icons)
    also passes ``images``, a ``builder(colour) -> PhotoImage`` (the builder caches): the ends are
    built now, the :data:`STEPS` colours of each segment are pre-warmed (:func:`prewarm`) and
    ``apply`` takes :meth:`image`, the nearest one built so far, so no frame ever builds one. The
    ramp starts at rest and draws nothing until it moves.
    """

    def __init__(self, widget, apply: Callable[[str], object], rest: str, hover: str, *,
                 pressed: str | None = None, images: Callable[[str], object] | None = None):
        self.widget = widget
        self.apply = apply
        self.rest, self.hover, self.pressed = rest, hover, pressed
        self.value = 0.0              # where the ramp is: 0 rest, 1 hover, 2 pressed
        self.target = 0.0             # where it is heading (= value at rest)
        self.colour = rest            # the colour drawn last
        self._images = images
        self._built: dict = {}        # colour -> (rgb, image), the faces built so far
        self._generation = 0
        self._animator = None
        if images is not None:
            self._warm()

    @property
    def animator(self) -> Animator:
        if self._animator is None:
            self._animator = Animator.of(self.widget)
        return self._animator

    @property
    def active(self) -> bool:
        """True while the colour is moving."""
        return self._animator is not None and self._animator.active(self)

    def colour_at(self, value: float) -> str:
        if value <= 1.0:
            return mix(self.hover, self.rest, max(0.0, value))
        return mix(self.pressed or self.hover, self.hover, min(1.0, value - 1.0))

    def enter(self) -> None:
        self._move(1.0, HOVER_IN_MS)

    def leave(self) -> None:
        self._move(0.0, HOVER_OUT_MS)

    def press(self) -> None:
        self._move(1.0 if self.pressed is None else 2.0, PRESS_MS)

    def release(self) -> None:
        self._move(1.0, PRESS_MS)

    def jump(self, colour: str) -> None:
        """Draw ``colour`` at once and end any motion; a colour that is not an end counts as rest."""
        if self._animator is not None:
            self._animator.stop(self)
        self.value = 0.0
        for value, end in ((2.0, self.pressed), (1.0, self.hover), (0.0, self.rest)):
            if colour == end:
                self.value = value
        self.target = self.value
        self._paint(colour)

    def set_colours(self, rest: str, hover: str, pressed: str | None = None) -> None:
        """New ends (a state or kind change); a move under way heads for the new colours.

        Nothing is drawn now (:meth:`jump` does that); image faces are built for the new ends.
        """
        if (rest, hover, pressed) == (self.rest, self.hover, self.pressed):
            return
        self.rest, self.hover, self.pressed = rest, hover, pressed
        if self._images is not None:
            self._warm()

    def finish(self) -> None:
        """The current move at its end now (tests, a page that hides)."""
        if self._animator is not None:
            self._animator.finish(self)

    def image(self, colour: str | None = None):
        """The face built for the colour nearest to ``colour`` (default: the current one), or None."""
        if not self._built:
            return None
        colour = self.colour if colour is None else colour
        exact = self._built.get(colour)
        if exact is not None:
            return exact[1]
        r, g, b = _rgb(colour)
        best, nearest = None, None
        for (cr, cg, cb), image in self._built.values():
            distance = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
            if nearest is None or distance < nearest:
                best, nearest = image, distance
        return best

    def _move(self, target: float, duration_ms: float) -> None:
        animator = self.animator
        moving = animator.active(self)
        if (self.value == target and not moving) or (moving and self.target == target):
            return
        self.target = target
        if not smooth(self.widget):
            animator.stop(self)
            self._at(target)
            return
        animator.run(self, duration_ms, self._at, start=self.value, end=target)

    def _at(self, value: float) -> None:
        self.value = value
        self._paint(self.colour_at(value))

    def _paint(self, colour: str) -> None:
        self.colour = colour
        self.apply(colour)

    def _warm(self) -> None:
        self._generation += 1
        generation = self._generation
        self._built = {}
        ends = [colour for colour in (self.rest, self.hover, self.pressed) if colour is not None]
        for colour in ends:
            self._build(colour, generation)
        between = []
        for a, b in zip(ends, ends[1:]):
            for colour in steps(a, b):
                if colour not in self._built and colour not in between:
                    between.append(colour)
        prewarm(self.widget, lambda colour: self._build(colour, generation), between)

    def _build(self, colour: str, generation: int) -> None:
        if generation == self._generation and colour not in self._built:
            self._built[colour] = (_rgb(colour), self._images(colour))
