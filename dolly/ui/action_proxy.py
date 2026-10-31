"""Button-like actions that canvas-drawn controls render.

Dolly drives its primary actions through the Tk button API: the poll writes
``configure(state=...)`` after comparing ``cget("state")`` (``gui._set_widget_state``),
tests read ``cget("text")`` and call ``invoke()``. An :class:`ActionProxy` keeps
that API while the visible control moves onto a canvas (a sticker or a footer
link), so pages stay independent of the shell that draws them. A
:class:`ProgressProxy` does the same for the indeterminate startup progress bar.
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

_STATES = ("normal", "active", "disabled", "readonly")


class _Subscribers:
    def __init__(self):
        self._callbacks = []

    def add(self, callback):
        self._callbacks.append(callback)

        def remove():
            if callback in self._callbacks:
                self._callbacks.remove(callback)
        return remove

    def notify(self, source):
        for callback in list(self._callbacks):
            callback(source)


class ActionProxy:
    """A Tk-button-compatible action with no widget of its own.

    ``text`` is the accessible name (kept for tests and existing call sites);
    ``label`` is the short face a sticker draws and defaults to ``text``.
    ``"readonly"`` and ``"active"`` count as enabled, as on ttk widgets.
    """

    def __init__(self, text: str, command=None, *, label: str | None = None, state: str = "normal"):
        self._options = {"text": str(text), "label": label, "state": _state(state), "command": command}
        self._subscribers = _Subscribers()

    def cget(self, option):
        option = str(option).lstrip("-")
        if option not in self._options:
            raise tk.TclError(f'unknown option "-{option}"')
        if option == "label":
            label = self._options["label"]
            return self._options["text"] if label is None else label
        return self._options[option]

    __getitem__ = cget

    def configure(self, cnf=None, **options):
        """Change ``text``, ``label``, ``state`` or ``command``; subscribers hear about a change."""
        if cnf:
            options = {**cnf, **options}
        if not options:
            return {name: self.cget(name) for name in self._options}
        changes = {}
        for option, value in options.items():
            option = str(option).lstrip("-")
            if option not in self._options:
                raise tk.TclError(f'unknown option "-{option}"')
            if option == "state":
                value = _state(value)
            elif option == "text":
                value = str(value)
            if self._options[option] != value:
                changes[option] = value
        if changes:
            self._options.update(changes)
            self._subscribers.notify(self)
        return None

    config = configure

    def __setitem__(self, option, value):
        self.configure(**{option: value})

    def enabled(self) -> bool:
        return self._options["state"] != "disabled"

    def invoke(self):
        """Run the command when enabled and return its result, else ``""`` (like Tk)."""
        command = self._options["command"]
        if not self.enabled() or command is None:
            return ""
        return command()

    def subscribe(self, callback: Callable[["ActionProxy"], None]) -> Callable[[], None]:
        """Call ``callback(proxy)`` after every change; returns an unsubscribe function."""
        return self._subscribers.add(callback)

    def __repr__(self):
        return f"<ActionProxy {self._options['text']!r} state={self._options['state']}>"


class ProgressProxy:
    """The ``start(interval)``/``stop()`` API of an indeterminate ``ttk.Progressbar``.

    The interval is ignored: the drawn line steps on the shared boil clock.
    """

    def __init__(self):
        self._running = False
        self._subscribers = _Subscribers()

    @property
    def running(self) -> bool:
        return self._running

    def start(self, interval=None) -> None:
        if not self._running:
            self._running = True
            self._subscribers.notify(self)

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._subscribers.notify(self)

    def subscribe(self, callback: Callable[["ProgressProxy"], None]) -> Callable[[], None]:
        return self._subscribers.add(callback)


def _state(value):
    value = str(value)
    if value not in _STATES:
        raise tk.TclError(f'bad state "{value}": must be active, disabled, normal or readonly')
    return value
