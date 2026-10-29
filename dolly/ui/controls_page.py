"""One searchable home for desktop and in-game control preferences (Figma [design reference], PLAN-5 3.4).

A kit page that scrolls as one, in one column like Replay's (USER-ADDITIONS UPDATE 13).
On the backdrop: the toolbar (the search field filling the column up to Reset all
shortcuts at its right end) and the feedback line, whose info bubble holds the F7 note.
Below them, top to bottom: the shortcut cards (Camera, Replay, Interface), Movement
(its Move rows, then the Look & Roll rows under them: one list), Camera Feel and
Camera Movement. Every binding row is its label, the keycap (a real ttk button that
opens the Change-shortcut dialog) and the quiet Clear / Reset canvas buttons.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping, Sequence
import math
import tkinter as tk
from tkinter import ttk

from .. import ui_theme
from . import boil
from .camera_feel import FEEL_INFO, CameraFeel
from .kit_controls import boil_while
from .kit_page import KitPage
from .scale import px

TITLE = "Controls"
SUBTITLE = "Click a shortcut and press its replacement. Changes are saved across launches."
FEEDBACK_INFO = ("F7 is reserved for the game console and releases camera input. "
                 "Mouse 4 and Mouse 5 are the usual side buttons.")
SEARCH_PLACEHOLDER = "Find an action or shortcut"
EMPTY_TEXT = "No matching controls."
UNBOUND = "Unbound"
KEYCAP = "Kit.Keycap.TButton"
KEYCAP_EMPTY = "Kit.KeycapEmpty.TButton"
# Keycaps are 88 design px, with Figma's widest default label (Backspace, Inter Medium 63 px, [design reference])
# 12.5 px in from each side; a card whose widest label needs more gives all its keycaps that label + 24.
KEYCAP_WIDTH = 88
KEYCAP_PAD = 24
QUIET_WIDTHS = {"Clear": 30, "Reset": 33}
MOVEMENT = "Movement"
LOOK_ACTIONS = ("look_", "roll_")   # the Movement card's Look & Roll rows, listed under the Move rows
# Between the search field and Reset all shortcuts, design px.
TOOLBAR_GAP = 10


@dataclass(frozen=True)
class ControlsActions:
    change_binding: Callable[[str], object]
    clear_binding: Callable[[str], object]
    reset_binding: Callable[[str], object]
    reset_all: Callable[[], object]
    save_movement: Callable[[], object]
    save_sensitivity: Callable[[], object]


class _EmptyState:
    """The "No matching controls." line on the backdrop, shown only while no shortcut matches.

    A band of its own, where the first shortcut card would start; the cards still shown
    (Camera Feel, Camera Movement) start 15 px below it.
    """

    def __init__(self, page):
        self.band = page.band(height=16, top=15)
        self.label = self.band.label(EMPTY_TEXT)
        self.band.set_visible(False)

    @property
    def visible(self) -> bool:
        return self.band.visible

    def set_visible(self, visible: bool) -> None:
        self.band.set_visible(visible)

    def winfo_manager(self) -> str:
        return "canvas" if self.band.visible else ""

    def cget(self, option):
        return self.label.cget(option)


class ControlsPage:
    def __init__(self, parent, records: Sequence[tuple[str, str, str]], commands: ControlsActions,
                 *, movement_speed, mouse_sensitivity, feedback):
        self.page = page = KitPage(parent)
        page.pack(fill="both", expand=True)
        self.records = tuple(records)
        self.commands = commands
        self.rows, self.binding_buttons = {}, {}
        self._bindings = {}
        self._filter_after = None
        self._filter_query = None
        self._filter_to_top = False
        self._listening = None
        self._stop_pulse = None
        self._keycaps = {}         # action -> (its card, its row cell): a card's keycaps share one width
        self._sections = []        # (the Move or the Look & Roll rows of Movement, their actions)
        page.bind("<Destroy>", self._destroyed, add="+")
        self.toolbar = toolbar = page.band()
        self.search = tk.StringVar(parent)
        self.search_field = toolbar.search(self.search, placeholder=SEARCH_PLACEHOLDER)
        self.search_entry = self.search_field.entry
        toolbar.push()
        self.reset_all_button = toolbar.button("Reset all shortcuts", commands.reset_all)
        # Comment #3 and UPDATE 13: the field fills the column up to Reset all, which ends on the column's edge.
        self.search_field.winfo_reqwidth = self._search_width
        feedback_line = page.band(height=16, top=10)
        self.feedback_text = feedback_line.text(textvariable=feedback, info=FEEDBACK_INFO)
        self.feedback_info = feedback_line.controls()[-1]
        self.empty_label = _EmptyState(page)
        # Cards top to bottom (= Tab order): the shortcut groups in the records' order, Movement
        # (comment #1: Look & Roll below the Move rows, one list), then Camera Feel and Camera Movement.
        names = list(dict.fromkeys(group for _action, _label, group in self.records))
        grouped = {name: [record for record in self.records if record[2] == name] for name in names}
        self.groups = dict.fromkeys(names)
        for name in names:
            if name != MOVEMENT:
                card = self.groups[name] = page.add_card(name)
                for record in grouped[name]:
                    self._binding_row(card, card, record)
        if MOVEMENT in self.groups:
            card = self.groups[MOVEMENT] = page.add_card(MOVEMENT)
            look = [record for record in grouped[MOVEMENT] if record[0].startswith(LOOK_ACTIONS)]
            move = [record for record in grouped[MOVEMENT] if record not in look]
            for records in (move, look):
                # A search hides either list on its own; the rows stay 10 apart across both.
                section = card.section()
                for record in records:
                    self._binding_row(section, card, record)
                self._sections.append((section, tuple(action for action, _label, _group in records)))
                section.set_visible(bool(records))
        feel = page.add_card("Camera Feel", info=FEEL_INFO)
        self.camera_feel = CameraFeel(page, mouse_sensitivity, commands.save_sensitivity, card=feel)
        motion = page.add_card("Camera Movement")
        row = motion.row()
        row.label("Move speed", width=105)
        row.gap(10)
        self.movement_speed_entry = row.widget(ttk.Entry(row.host, textvariable=movement_speed, style="Kit.TEntry"),
                                               width="fill")
        self.save_movement_button = motion.row().button("Save movement settings", commands.save_movement)
        self.search.trace_add("write", self._filter)
        page.watch_focus()
        # The search field sits on no card, so the page's focus script cannot find it to
        # scroll it into view (a Tab that wraps around to it): it reveals the toolbar instead.
        self.search_entry.configure(takefocus=page.register(self._search_takes_focus))

    def _search_width(self) -> int:
        page = self.page
        column = page.main_width or px(page.scale, 613)
        return max(px(page.scale, 160), column - px(page.scale, TOOLBAR_GAP) - self.reset_all_button.winfo_reqwidth())

    def _search_takes_focus(self, _path):
        try:
            if not self.page.winfo_viewable() or self.search_entry.instate(["disabled"]):
                return 0
        except tk.TclError:
            return 0
        self.page.reveal(self.toolbar)
        return 1

    def _binding_row(self, owner, card, record):
        """Label (wraps) · 10 · keycap · 10 · Clear · 14 · Reset (quiet canvas buttons, one focus group)."""
        action, label, _group = record
        commands = self.commands
        row = owner.row()
        row.label(label, wrap=True)
        row.gap(10)
        keycap = row.widget(ttk.Button(row.host, text=UNBOUND, style=KEYCAP_EMPTY,
                                       command=lambda name=action: commands.change_binding(name)),
                            width=KEYCAP_WIDTH)
        # Its row cell: the kit sets a cell's width only when the row is built, and a card's
        # keycaps follow its widest label (_fit_keycaps).
        self._keycaps[action] = (card, next(cell for cell in row.core.cells if cell.obj is keycap))
        row.gap(10)
        row.clear_button = row.button("Clear", lambda name=action: commands.clear_binding(name), kind="quiet")
        row.gap(14)
        row.reset_button = row.button("Reset", lambda name=action: commands.reset_binding(name), kind="quiet")
        for button in (row.clear_button, row.reset_button):
            # At least the frame's (Inter) widths, so the keycaps line up with it under any face.
            width = QUIET_WIDTHS[button.cget("text")]
            if button.winfo_reqwidth() < px(self.page.scale, width):
                button.configure(width=width)
        self.rows[action] = row
        self.binding_buttons[action] = keycap

    def _keycap_style(self, action) -> str:
        return KEYCAP if self._bindings.get(action) is not None or action == self._listening else KEYCAP_EMPTY

    def _fit_keycaps(self) -> None:
        """Give every keycap of a card that card's widest label + 26, at least 88 (controls.md h3)."""
        page = self.page
        font = ttk.Style(page).lookup(KEYCAP, "font") or (ui_theme.FONT_FAMILY, ui_theme.FONT_SIZES["body"])
        widest = {}
        for action, (card, _cell) in self._keycaps.items():
            text = str(self.binding_buttons[action].cget("text"))
            widest[card] = max(widest.get(card, 0), int(page.tk.call("font", "measure", font, text)))
        changed = set()
        for action, (card, cell) in self._keycaps.items():
            width = max(KEYCAP_WIDTH, math.ceil(widest[card] / page.scale) + KEYCAP_PAD)
            if cell.width != width:
                cell.width = width
                changed.add(card)
        for card in changed:
            card._changed()   # measure the card again at the next layout

    def refresh(self, bindings: Mapping[str, object]):
        self._bindings = dict(bindings)
        for action, button in self.binding_buttons.items():
            binding = bindings.get(action)
            button.configure(text=binding.label if binding else UNBOUND, style=self._keycap_style(action))
            self.rows[action].clear_button.configure(state="normal" if binding else "disabled")
        self._fit_keycaps()
        self._filter()

    def _filter(self, *_args):
        query = self.search.get().strip().casefold()
        if query != self._filter_query:
            self._filter_query = query
            self._filter_to_top = True
        shown = set()
        for action, label, group in self.records:
            binding = self._bindings.get(action)
            text = " ".join((label, group, binding.label if binding else UNBOUND)).casefold()
            matches = not query or query in text
            self.rows[action].set_visible(matches)
            if matches:
                shown.add(group)
        for group, card in self.groups.items():
            card.set_visible(group in shown)
        for section, actions in self._sections:
            section.set_visible(any(self.rows[action].visible for action in actions))
        self.empty_label.set_visible(not shown)
        if self._filter_after is None:
            self._filter_after = self.page.after_idle(self._refresh_filter_geometry)

    def _refresh_filter_geometry(self):
        self._filter_after = None
        try:
            if not self.page.winfo_exists():
                return
        except tk.TclError:
            return
        if self._filter_to_top:
            # A new search starts with its first result in view (the page has laid the
            # filter out by now: its layout was queued first). Updating existing shortcut
            # labels keeps the user's scroll position.
            self._filter_to_top = False
            self.page.scroll_to(0)

    def set_listening(self, action):
        """Show ``action``'s keycap Listening while its Change-shortcut dialog is open (None: none).

        Listening is the kit keycap's 1 px accent edge, on and off with the boil clock
        (the ttk ``alternate`` state), steady with motion off; nothing pulses at rest.
        """
        if action == self._listening:
            return
        previous, self._listening = self._listening, None
        if self._stop_pulse is not None:
            self._stop_pulse()
            self._stop_pulse = None
        if previous in self.binding_buttons:
            keycap = self.binding_buttons[previous]
            keycap.state(["!alternate"])
            keycap.configure(style=self._keycap_style(previous))
        keycap = self.binding_buttons.get(action)
        if keycap is None:
            return
        self._listening = action
        keycap.configure(style=KEYCAP)
        keycap.state(["alternate"])
        if boil.motion_enabled():
            self._stop_pulse = boil_while(keycap, lambda: keycap.state(
                ["!alternate"] if keycap.instate(["alternate"]) else ["alternate"]))

    def _destroyed(self, event):
        if event.widget is not self.page:
            return
        if self._filter_after is not None:
            self.page.after_cancel(self._filter_after)
            self._filter_after = None
        if self._stop_pulse is not None:
            self._stop_pulse()
            self._stop_pulse = None

    def reveal_action(self, action):
        """Clear the search, scroll ``action``'s keycap into view and give it the keyboard."""
        self.search.set("")
        self.page.update_idletasks()
        keycap = self.binding_buttons[action]
        self.page.reveal(keycap)
        keycap.focus_set()
