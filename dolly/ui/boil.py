"""The shared "boil" clock: posterized motion for the redesigned shell.

Deadlock's hero tags snap to a new pose every ~500 ms, all at once, and hold it;
nothing tweens. One :class:`BoilClock` per Tk toplevel advances every animated
member (backdrop, stickers, busy dot, progress line) in a single callback, in
registration order, so a frame changes everywhere together. A member may move on
every n-th tick only (the stickers: every second one, about once a second); all
such members still move on the same ticks.

Unlike RenderLock's class-level clock, each toplevel owns its clock and cancels
it on ``<Destroy>``: Dolly's tests create and destroy many roots per process.
The clock never schedules while motion is off, while the root is unmapped
(withdrawn test roots stay silent) or while a hold is active, such as the
settle hold that keeps decoration still during and just after a resize.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py, agr/gui_theme.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from typing import Callable

INTERVAL_MS = 500
SETTLE_MS = 250


def motion_enabled() -> bool:
    """False when ``DOLLY_UI_MOTION=0`` or Windows "Show animations" is off."""
    if os.environ.get("DOLLY_UI_MOTION", "1") == "0":
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    enabled = ctypes.c_int(1)
    try:
        # SPI_GETCLIENTAREAANIMATION: the Settings > Accessibility switch.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


class BoilClock:
    """One posterized timer per toplevel; members need ``advance()`` and ``winfo_exists()``.

    ``frame`` counts ticks. It is incremented before members advance, so a
    member reading it during ``advance()`` sees the tick in progress (1 for the
    first tick). ``tick()`` can be called directly; it never starts the timer.
    A member with a ``boil_every`` of n advances only on frames n, 2n, 3n...
    (default 1: every tick).
    """

    def __init__(self, root):
        self.root = root
        self.frame = 0
        self._members = []
        self._holds = set()
        self._job = None
        self._settle_job = None
        self._settle_listeners = []
        self._size = None
        self._stopped = False
        try:
            mapped = bool(root.winfo_ismapped())
        except tk.TclError:
            mapped = False
        if mapped:
            self._size = (root.winfo_width(), root.winfo_height())
        else:
            self._holds.add("unmapped")
        # The toplevel is in every descendant's bindtags: each handler filters
        # on the root itself and stays cheap for everything else.
        root.bind("<Map>", self._mapped, add="+")
        root.bind("<Unmap>", self._unmapped, add="+")
        root.bind("<Configure>", self._configured, add="+")
        root.bind("<Destroy>", self._destroyed, add="+")

    @classmethod
    def of(cls, widget) -> "BoilClock":
        """The clock of ``widget``'s toplevel, created on first use."""
        top = widget.winfo_toplevel()
        clock = getattr(top, "_dolly_boil", None)
        if clock is None:
            clock = top._dolly_boil = cls(top)
        return clock

    @property
    def held(self) -> bool:
        return bool(self._holds)

    @property
    def holds(self) -> frozenset:
        return frozenset(self._holds)

    @property
    def running(self) -> bool:
        """True while the next tick is scheduled."""
        return self._job is not None

    @property
    def members(self) -> tuple:
        return tuple(self._members)

    def register(self, member) -> None:
        if member not in self._members:
            self._members.append(member)
        self._schedule()

    def unregister(self, member) -> None:
        try:
            self._members.remove(member)
        except ValueError:
            return
        if not self._members:
            self._cancel()

    def tick(self) -> None:
        """Advance every live member that is due; dead or destroyed members are dropped."""
        self.frame += 1
        dead = []
        for member in list(self._members):
            if member not in self._members:
                continue  # unregistered by an earlier member during this tick
            try:
                if member.winfo_exists():
                    if self.frame % getattr(member, "boil_every", 1) == 0:
                        member.advance()
                    continue
            except tk.TclError:
                pass
            dead.append(member)
        if dead:
            self._members = [member for member in self._members if member not in dead]

    def hold(self, reason: str) -> None:
        """Keep every member still until the same reason is released."""
        if reason in self._holds:
            return
        self._holds.add(reason)
        self._cancel()
        if reason == "settle":
            self._notify_settle(True)

    def release(self, reason: str) -> None:
        if reason not in self._holds:
            return
        self._holds.discard(reason)
        if reason == "settle":
            self._cancel_settle()
            self._notify_settle(False)
        self._schedule()

    def add_settle_listener(self, callback: Callable[[bool], None]) -> Callable[[], None]:
        """``callback(True)`` when a resize freezes decoration, ``callback(False)`` once settled."""
        self._settle_listeners.append(callback)

        def remove():
            if callback in self._settle_listeners:
                self._settle_listeners.remove(callback)
        return remove

    def stop(self) -> None:
        """Cancel every pending job; the clock stays inert afterwards."""
        self._stopped = True
        self._cancel()
        self._cancel_settle()
        self._members = []
        self._settle_listeners = []

    # Scheduling ----------------------------------------------------------

    def _schedule(self):
        if (self._job is not None or self._stopped or self._holds or not self._members
                or not motion_enabled()):
            return
        try:
            self._job = self.root.after(INTERVAL_MS, self._run)
        except tk.TclError:
            self._job = None

    def _run(self):
        self._job = None
        if self._stopped or self._holds:
            return
        self.tick()
        self._schedule()

    def _cancel(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _cancel_settle(self):
        if self._settle_job is not None:
            try:
                self.root.after_cancel(self._settle_job)
            except tk.TclError:
                pass
            self._settle_job = None

    def _notify_settle(self, frozen):
        for callback in list(self._settle_listeners):
            try:
                callback(frozen)
            except tk.TclError:
                pass  # A listener's canvas may already be gone.

    # Root events ---------------------------------------------------------

    def _mapped(self, event):
        if event.widget is not self.root or self._stopped:
            return
        # The first size seen after each map is the baseline, not a resize.
        self._size = (self.root.winfo_width(), self.root.winfo_height())
        self.release("unmapped")

    def _unmapped(self, event):
        if event.widget is self.root and not self._stopped:
            self.hold("unmapped")

    def _configured(self, event):
        if event.widget is not self.root or self._stopped or "unmapped" in self._holds:
            return
        size = (event.width, event.height)
        if self._size is None:
            self._size = size
            return
        if size == self._size:
            return  # A move, not a resize.
        self._size = size
        self.hold("settle")
        self._cancel_settle()
        try:
            self._settle_job = self.root.after(SETTLE_MS, self._settled)
        except tk.TclError:
            self._settle_job = None

    def _settled(self):
        self._settle_job = None
        self.release("settle")

    def _destroyed(self, event):
        if event.widget is self.root:
            self.stop()


class IdleUpdate:
    """Combine a burst of notifications into one ``after_idle`` callback.

    ``schedule`` ignores its arguments, so it can be passed straight to
    ``bind`` or ``trace_add``. The pending call is cancelled on ``<Destroy>``.

    Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
    """

    def __init__(self, widget, callback):
        self.widget = widget
        self.callback = callback
        self._job = None
        self._dead = False
        widget.bind("<Destroy>", self._destroyed, add="+")

    @property
    def pending(self) -> bool:
        return self._job is not None

    def schedule(self, *_args) -> None:
        if self._job is None and not self._dead:
            try:
                self._job = self.widget.after_idle(self._flush)
            except tk.TclError:
                self._job = None

    __call__ = schedule

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def flush(self) -> None:
        """Run a pending update now."""
        if self._job is not None:
            self.cancel()
            self._flush()

    def _flush(self):
        self._job = None
        if not self._dead:
            self.callback()

    def _destroyed(self, event):
        if event.widget is self.widget:
            self.cancel()
            self._dead = True
