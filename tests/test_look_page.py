"""Look page (Figma [design reference]): the bento cards, actions, tables, Citadel DOF sliders and the pinned Shot Playback card.

PLAN-5 5.2 with USER-ADDITIONS UPDATE 7 (Shot Playback pinned like on Camera, the scroll fade) and
UPDATE 8 (the cards as a bento grid; Kara kept the bento over Replay's one-column look).
"""
from dataclasses import fields
import math
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.particles import PARTICLE_INTENSITY_MAX, PARTICLE_INTENSITY_MIN, PARTICLE_LABELS
from dolly.ui import art, fade, fonts, kit_controls
from dolly.ui.kit_controls import CanvasButton, KitSlider, KitStrip
from dolly.ui.kit_page import KitPage
from dolly.ui.look_page import (
    BENTO_GAP, CURVES, FIXED_INFO, LOOK_COLUMNS, RANGE_INFO, SUBTITLE, TITLE, LookActions, LookPage, LookState,
)
from dolly.ui.scale import px
from dolly.ui.stage_page import TREE_CELL_INSET

INTENSITY_UNIT = f"x ({PARTICLE_INTENSITY_MIN:g}–{PARTICLE_INTENSITY_MAX:g})"
# tkinter's event fields, in the order its bind scripts pass them.
_FIELDS = ("%#", "%b", "%f", "%h", "%k", "%s", "%t", "%w", "%x", "%y", "%A", "%E", "%K", "%N", "%W", "%T",
           "%X", "%Y", "%D")


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def fire(widget, sequence, keysym=""):
    """Run ``widget``'s own binding for a key ``sequence``.

    Tk delivers key events only to the window that has the keyboard, and the hidden
    test windows never get it; this runs exactly the bound script instead.
    """
    script = widget.bind(sequence)
    if not script:
        raise AssertionError(f"{widget} has no {sequence} binding")
    values = {"%W": str(widget), "%K": keysym or "??", "%A": "{}", "%T": "2"}
    for name in _FIELDS:
        script = script.replace(name, values.get(name, "0"))
    widget.tk.eval(script)


class LookPageCase(unittest.TestCase):
    """A standalone Look page on its own root (helpers only)."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        # Tk on Windows keeps one `tk scaling` for every interpreter in the process: pin
        # 100 % (a test asks for 150 % itself) and hand the old value back before closing.
        scaling = self.root.tk.call("tk", "scaling")
        self.addCleanup(lambda: self.root.tk.call("tk", "scaling", scaling))
        self.root.tk.call("tk", "scaling", 96 / 72)
        self.calls = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback raised an exception")

    def record(self, name):
        return lambda *args, **kwargs: self.calls.append(name)

    def tree(self, parent, columns, labels, widths, height):
        # Shaped like DollyApp._tree: a panel frame holding the table and its rail (PLAN-5 PR4).
        frame = tk.Frame(parent, background=ui_theme.TOKENS["panel"], borderwidth=0, highlightthickness=0)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        widget = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
        for column, label, width in zip(columns, labels, widths):
            widget.heading(column, text=label)
            widget.column(column, width=width, anchor="e")
        widget.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        bar.grid(row=0, column=1, sticky="ns")
        widget.configure(yscrollcommand=bar.set)
        return frame, widget

    def build(self, *, shown="948x591", playback=True, parent=None, scale=1.0):
        """The page with recorded callbacks; ``shown`` = window size in design px to map it at (C 930 by default).

        ``playback`` adopts a stand-in for the app's timeline_frame (103 design px tall, so
        the Shot Playback card is the frame's 178) into the page's host card.
        """
        if scale != 1.0:
            self.root.tk.call("tk", "scaling", 96 * scale / 72)
        gui_theme.initialize(self.root)
        if shown:
            self.root.deiconify()
            width, height = (int(value) for value in shown.split("x"))
            self.root.geometry(f"{round(width * scale)}x{round(height * scale)}")
        variables = {name: tk.StringVar(self.root, "") for name in (
            "particles_preset", "particles_intensity", "confetti_spawn_height", "track_name", "track_mode",
            "restore_value", "track_time", "track_value")}
        variables.update(confetti_enabled=tk.BooleanVar(self.root, False),
                         confetti_despawn_on_ground=tk.BooleanVar(self.root, True))
        variables["particles_preset"].set(PARTICLE_LABELS[next(iter(PARTICLE_LABELS))])
        variables["particles_intensity"].set("1")
        variables["confetti_spawn_height"].set("250")
        variables["track_mode"].set("linear")
        self.state = LookState(**variables, particle_labels=tuple(PARTICLE_LABELS.values()),
                               intensity_unit=INTENSITY_UNIT)
        commands = LookActions(**{f.name: self.record(f.name) for f in fields(LookActions) if f.name != "tree"},
                               tree=self.tree)
        view = LookPage(parent or self.root, self.state, commands)
        view.stand_in = None
        if playback:
            view.stand_in = self.stand_in(view.look_page)
            view.playback_host.adopt(view.stand_in.frame)
        if shown:
            self.root.update()
        else:
            self.root.update_idletasks()
        view.look_page.layout_now()
        self.root.update_idletasks()
        return view

    def stand_in(self, page):
        """A Shot Playback stand-in shaped like playback_card's timeline_frame (a no-paint frame, one child)."""
        s = page.scale
        frame = tk.Frame(self.root, background="", borderwidth=0, highlightthickness=0, takefocus=0)
        body = tk.Frame(frame, background=ui_theme.TOKENS["panel"], height=px(s, 103))
        body.pack_propagate(False)
        body.pack(fill="both", expand=True)
        entry = ttk.Entry(body, style="Kit.TEntry")
        entry.pack(fill="x")
        strip = KitStrip(body)
        strip.pack(fill="x", pady=(px(s, 10), 0))
        play = strip.button("Play shot", self.record("play"))
        return SimpleNamespace(frame=frame, body=body, entry=entry, strip=strip, play=play)

    # geometry in design px, relative to a card's plate
    def d(self, page, value):
        return value / page.scale

    def card_rect(self, view, card):
        page = view.look_page
        x0, y0, x1, y1 = card.rect
        return tuple(round(self.d(page, v), 1) for v in (x0, y0, x1 - x0, y1 - y0))

    def in_card(self, view, card, target):
        """``target``'s (x, y, w, h) in design px from ``card``'s top-left."""
        page = view.look_page
        x0, y0, x1, y1 = page.rect_of(target)
        return tuple(self.d(page, v) for v in (x0 - card.rect[0], y0 - card.rect[1], x1 - x0, y1 - y0))

    def assertNear(self, actual, expected, tolerance=1, msg=None):
        self.assertLessEqual(abs(actual - expected), tolerance, msg or (actual, expected))

    def in_view(self, page, rect):
        view = page.canvas.canvasy(0)
        return view <= rect[1] and rect[3] <= view + page.canvas.winfo_height()


class LookPageTests(LookPageCase):
    def test_titles_copy_and_page_variables(self):
        self.assertEqual(TITLE, "Look")
        self.assertEqual(SUBTITLE, "Animate camera variables over the shot, set fixed values and add particles.")
        # Today's Range DOF help line, verbatim; the Fixed Values copy is the design's (look.md H9).
        self.assertEqual(RANGE_INFO, "Range DOF: near blurry · near crisp · far crisp · far blurry. "
                                     "Enter four numbers separated by spaces.")
        self.assertEqual(FIXED_INFO, "Fixed values… opens a separate dialog on Look. These values are applied "
                                     "for the shot; a track with the same name overrides the fixed value while "
                                     "the shot runs.")
        self.assertEqual(CURVES, ("step", "linear", "smooth"))
        self.assertEqual((LOOK_COLUMNS.main, LOOK_COLUMNS.side, LOOK_COLUMNS.gap, LOOK_COLUMNS.main_min,
                          LOOK_COLUMNS.side_min), (613, 297, 20, 520, 297))
        view = self.build(shown=None, playback=False)
        page = view.look_page
        self.assertIsInstance(page, KitPage)
        self.assertEqual([card.title for card in page.cards],
                         ["Animated Tracks", "Track Editor", "Particles", "Citadel Depth of Field", "Fixed Values"])
        self.assertEqual([card.column for card in page.cards], ["main", "main", "side", "side", "side"])
        self.assertEqual((view.tracks_card, view.editor_card, view.particles_card, view.dof_card, view.fixed_card),
                         tuple(page.cards))
        self.assertEqual(view.playback_host.title, "Shot Playback")
        # Pinned at the bottom like on Camera, with its art; the cards above scroll with the fade (UPDATE 7).
        self.assertEqual(view.playback_host.where, "dock")
        host = view.playback_host     # every card title is 15 px, Shot Playback's too (UPDATE 25)
        self.assertEqual(str(host.page.dock.itemcget(host._dock["title"], "font")), "DollyCardTitle")
        for card in page.cards:
            self.assertEqual(str(card.host.itemcget(card.title_item, "font")), "DollyCardTitle", card.title)
        self.assertTrue(page.pinned)
        self.assertIsNotNone(page.dock)
        self.assertIsInstance(page.fade, fade.ScrollFade)
        self.assertIs(view.playback_host.art, art.playback_art)
        self.assertEqual(view.playback_host.edge, "card_edge_pinned")
        self.assertIs(page.all_cards()[-1], view.playback_host.card)
        self.assertFalse(view.playback_host.card.visible)   # docked: never a card of the scroll area
        self.assertEqual((BENTO_GAP, page.CARD_GAP), (20, 20))
        with self.assertRaises(TypeError):            # one layout only: the bento (UPDATE 8 DECIDED)
            LookPage(self.root, self.state, view_commands(self), layout="stack")
        # The Citadel DOF variables are the page's now, with today's defaults (N12).
        self.assertIs(view.citadel_enabled.get(), False)
        self.assertEqual(view.citadel_sensor.get(), 1.0)
        self.assertEqual(view.citadel_sensor_text.get(), "Sensor size · 1.00")
        self.assertEqual(view.citadel_focus_internal.get(), 0.0)
        self.assertEqual(view.citadel_focus_text.get(), "Focus distance · 200 in")
        self.assertEqual(tuple(view.track_mode_combo.cget("values")), CURVES)
        self.assertEqual(str(view.track_mode_combo.cget("state")), "readonly")
        self.assertEqual(tuple(view.particles_combo.cget("values")), tuple(PARTICLE_LABELS.values()))
        self.assertEqual(str(view.particles_combo.cget("state")), "readonly")
        # Every ttk widget on a card carries a kit style (the theme's restyler finds no card frame here).
        for widget in (view.track_name_entry, view.restore_entry, view.track_time_entry, view.track_value_entry,
                       view.intensity_entry, view.spawn_height_entry):
            self.assertEqual(str(widget.cget("style")), "Kit.TEntry")
        for widget in (view.track_mode_combo, view.particles_combo):
            self.assertEqual(str(widget.cget("style")), "Kit.TCombobox")
        for widget in (view.confetti_checkbox, view.despawn_checkbox, view.citadel_dof_checkbox):
            self.assertEqual(str(widget.cget("style")), "Kit.TCheckbutton")
        self.assertIsInstance(view.citadel_sensor_scale, KitSlider)
        self.assertIsInstance(view.citadel_focus_scale, KitSlider)

    def check_cards(self, view, tolerance):
        page = view.look_page
        self.assertTrue(page.two_columns)
        # The bento (UPDATE 8): Animated Tracks beside Particles, Track Editor beside Citadel DOF over
        # Fixed Values; 20 px gutters. Track Editor's buttons sit on its title line (293 = 168 + 20 + 105)
        # and Animated Tracks' table takes the 5 px it lacks beside Particles (208 -> 213).
        expected = {view.tracks_card: (0, 0, 613, 213), view.editor_card: (0, 233, 613, 293),
                    view.particles_card: (633, 0, 297, 213), view.dof_card: (633, 233, 297, 168),
                    view.fixed_card: (633, 421, 297, 105)}
        for card, rect in expected.items():
            with self.subTest(card=card.title, scale=page.scale):
                for actual, design in zip(self.card_rect(view, card), rect):
                    self.assertNear(actual, design, tolerance)
        # Flush edges, exactly: neighbours share their top and bottom lines at any scale.
        tracks, editor, particles, dof, fixed = (view.tracks_card, view.editor_card, view.particles_card,
                                                view.dof_card, view.fixed_card)
        self.assertEqual((tracks.rect[1], tracks.rect[3]), (particles.rect[1], particles.rect[3]))
        self.assertEqual((editor.rect[1], editor.rect[3]), (dof.rect[1], fixed.rect[3]))
        gap = px(page.scale, BENTO_GAP)
        self.assertEqual((editor.rect[1] - tracks.rect[3], fixed.rect[1] - dof.rect[3],
                          particles.rect[0] - tracks.rect[2]), (gap, gap, gap))
        # The columns scroll above the docked Shot Playback (930 x 178).
        self.assertNear(self.d(page, page.content_height), 526, tolerance)
        self.assertNear(self.d(page, page.content_width), 930, tolerance)
        dock = view.playback_host._dock["rect"]
        if view.stand_in is not None:
            self.assertEqual(tuple(round(self.d(page, v)) for v in (dock[2] - dock[0], dock[3] - dock[1])),
                             (930, 178))

    def test_cards_follow_the_frame_at_the_design_width(self):
        self.check_cards(self.build(), 1)

    def test_cards_follow_the_frame_at_150_percent(self):
        view = self.build(scale=1.5)
        self.assertEqual(view.look_page.scale, 1.5)
        self.check_cards(view, 2)     # PLAN-5 6.1: ±2 design px at 150 %

    def test_rows_and_fixed_cells_follow_the_frame(self):
        view = self.build()
        page = view.look_page
        right = {613: 592, 297: 276}         # content right edge (design px from the plate's left)

        def check(card, target, x=None, y=None, w=None, h=None, right_edge=False):
            rx, ry, rw, rh = self.in_card(view, card, target)
            for name, actual, design in (("x", rx, x), ("y", ry, y), ("w", rw, w), ("h", rh, h)):
                if design is not None:
                    self.assertNear(actual, design, msg=(card.title, target, name, actual, design))
            if right_edge:
                width = round(self.d(page, card.rect[2] - card.rect[0]))
                self.assertNear(rx + rw, right[width], msg=(card.title, target, "right"))
        tracks, editor, particles, dof, fixed = (view.tracks_card, view.editor_card, view.particles_card,
                                                view.dof_card, view.fixed_card)
        check(tracks, view.range_dof_button, x=21, y=54, h=30)
        self.assertNear(self.in_card(view, tracks, view.citadel_dof_button)[0],
                        21 + self.d(page, view.range_dof_button.winfo_width()) + 10)
        check(tracks, view.remove_track_button, y=54, right_edge=True)
        check(tracks, view.track_table, x=21 - self.d(page, TREE_CELL_INSET), y=99, h=103, right_edge=True)
        # Track Editor's buttons on its title line (centred on the 18 px line at 21), right-aligned.
        check(editor, view.create_track_button, y=15, h=30)
        check(editor, view.update_track_button, y=15, h=30, right_edge=True)
        self.assertNear(self.in_card(view, editor, view.update_track_button)[0],
                        self.in_card(view, editor, view.create_track_button)[0]
                        + self.d(page, view.create_track_button.winfo_width()) + 10)
        check(editor, view.track_name_entry, y=54, h=30, right_edge=True)
        check(editor, view.track_mode_combo, y=94, w=120, h=30)
        check(editor, view.restore_entry, y=94, right_edge=True)
        check(editor, view.value_table, y=139, h=98, right_edge=True)
        check(editor, view.track_time_entry, y=242, w=80, h=30)
        check(editor, view.track_value_entry, y=242)
        check(editor, view.delete_value_button, y=242, right_edge=True)
        # Value + its info icon 5 px after the text; the field 10 px after the icon.
        icon = self.in_card(view, editor, view.range_info)
        self.assertNear(icon[2], 13)
        self.assertNear(self.in_card(view, editor, view.track_value_entry)[0], icon[0] + 13 + 10)
        self.assertNear(icon[1] + 6.5, 242 + 15, 1.5)
        check(particles, view.confetti_checkbox, x=21)
        self.assertNear(self.in_card(view, particles, view.confetti_checkbox)[1]
                        + self.in_card(view, particles, view.confetti_checkbox)[3] / 2, 69)
        check(particles, view.particles_combo, y=54, h=30, right_edge=True)
        check(particles, view.intensity_entry, x=86, y=94, h=30)    # fixed 55 px label column + 10
        check(particles, view.spawn_height_entry, x=86, y=134, h=30)
        rx, ry, rw, rh = self.in_card(view, particles, view.despawn_checkbox)
        self.assertNear(ry + rh / 2, 183)                            # an 18 px row at 174
        rx, ry, rw, rh = self.in_card(view, dof, view.citadel_dof_checkbox)
        self.assertNear(ry + rh / 2, 63)
        check(dof, view.citadel_sensor_scale, y=91, h=12, right_edge=True)
        check(dof, view.citadel_focus_scale, y=126, h=12, right_edge=True)
        check(fixed, view.fixed_values_button, x=21, y=54, h=30)
        # The title's info icon 6 px after the title (the card canvas starts a radius into the plate).
        info = self.in_card(view, fixed, fixed.info)
        title_right = fixed.host.bbox(fixed.title_item)[2]
        self.assertNear(info[0], self.d(page, px(page.scale, page.CARD_RADIUS) + title_right) + 6)

    def test_numeric_fields_fit_their_widest_value(self):
        view = self.build(shown=None, playback=False)
        page = view.look_page
        font = (ui_theme.FONT_VALUE, ui_theme.FONT_SIZES["body"])   # values: Inter Medium (UPDATE 23)
        for entry, widest, design in ((view.intensity_entry, "0.25", 32), (view.spawn_height_entry, "1500", 49)):
            measured = int(self.root.tk.call("font", "measure", font, widest))
            rect = page.rect_of(entry)
            self.assertEqual(rect[2] - rect[0], max(px(page.scale, design), measured + px(page.scale, 26)))

    def test_one_column_below_the_main_minimum_and_two_down_to_it(self):
        view = self.build(shown="855x591")           # C 837: the main column shrinks to its 520 minimum
        page = view.look_page
        self.assertTrue(page.two_columns)
        self.assertNear(self.d(page, page.main_width), 520)
        self.assertNear(self.d(page, view.particles_card.rect[0]), 540)
        self.root.geometry("854x591")                 # C 836: one column, main then side cards
        self.root.update()
        page.layout_now()
        self.assertFalse(page.two_columns)
        order = [view.tracks_card, view.editor_card, view.particles_card, view.dof_card, view.fixed_card]
        tops = [card.rect[1] for card in order]
        self.assertEqual(tops, sorted(tops))
        for card, after in zip(order, order[1:]):   # each at its own height, 20 apart
            self.assertEqual(after.rect[1] - card.rect[3], px(page.scale, BENTO_GAP))
        self.assertNear(self.d(page, view.tracks_card.rect[3] - view.tracks_card.rect[1]), 208)
        for card in order:
            self.assertEqual(card.rect[0], 0)
            self.assertNear(self.d(page, card.rect[2] - card.rect[0]), 613)
        self.assertIsNone(view.playback_host.card.rect)      # still docked, not a card of the column
        self.root.geometry("780x591")                 # the 1000 x 700 window's C 762 (PLAN-5 3.0.2)
        self.root.update()
        page.layout_now()
        self.assertFalse(page.two_columns)
        self.assertNear(self.d(page, page.content_width), 613)

    def test_every_action_is_reachable_from_a_control(self):
        view = self.build()
        found = {button.cget("text"): button for button in kit_controls.buttons(view.look_page)}
        self.assertEqual(list(found), ["+ Range DOF", "+ Citadel DOF", "Remove track", "Create track",
                                       "Update settings", "Add / Replace", "Delete", "Fixed values…"])
        for button in found.values():
            self.assertIsInstance(button, CanvasButton)
        self.assertEqual([found[t].kind for t in ("Remove track", "Delete")], ["quiet", "quiet"])
        self.assertEqual({found[t].kind for t in found if t not in ("Remove track", "Delete")}, {"secondary"})
        expected = {"+ Range DOF": "range_dof_preset", "+ Citadel DOF": "citadel_dof_preset",
                    "Remove track": "remove_track", "Create track": "create_track",
                    "Update settings": "update_track", "Add / Replace": "put_value", "Delete": "delete_value",
                    "Fixed values…": "show_fixed_values"}
        for text, action in expected.items():
            self.calls.clear()
            found[text].invoke()
            self.assertEqual(self.calls, [action], text)
        steps = (
            (lambda: view.track_tree.event_generate("<<TreeviewSelect>>"), ["select_track"]),
            (lambda: view.value_tree.event_generate("<<TreeviewSelect>>"), ["select_value"]),
            (lambda: fire(view.value_tree, "<Delete>", "Delete"), ["delete_value"]),
            (view.confetti_checkbox.invoke, ["particles_changed"]),
            (view.despawn_checkbox.invoke, ["particles_changed"]),
            (lambda: view.particles_combo.event_generate("<<ComboboxSelected>>"), ["particles_changed"]),
            (lambda: fire(view.intensity_entry, "<Return>", "Return"), ["particles_changed"]),
            (lambda: view.intensity_entry.event_generate("<FocusOut>"), ["particles_changed"]),
            (lambda: fire(view.spawn_height_entry, "<Return>", "Return"), ["particles_changed"]),
            (lambda: view.spawn_height_entry.event_generate("<FocusOut>"), ["particles_changed"]),
            (view.citadel_dof_checkbox.invoke, ["toggle_citadel_dof"]),
        )
        for index, (step, calls) in enumerate(steps):
            self.calls.clear()
            step()
            self.root.update()
            self.assertEqual(self.calls, calls, index)
        self.assertIs(view.citadel_enabled.get(), True)
        # The sliders report while they move and commit on release, as the ttk scales did.
        for slider, moved, committed in ((view.citadel_sensor_scale, "sensor_changed", "sensor_commit"),
                                         (view.citadel_focus_scale, "focus_changed", "focus_commit")):
            self.calls.clear()
            width = slider.winfo_width()
            slider.event_generate("<ButtonPress-1>", x=width // 2, y=6)
            slider.event_generate("<B1-Motion>", x=width // 3, y=6, state=0x100)
            slider.event_generate("<ButtonRelease-1>", x=width // 3, y=6, state=0x100)
            self.root.update()
            self.assertEqual(self.calls, [moved, moved, committed])

    def test_tables_are_left_aligned_and_their_rails_hide_while_the_rows_fit(self):
        view = self.build()
        page = view.look_page
        for table, tree, columns in ((view.track_table, view.track_tree, ("name",)),
                                     (view.value_table, view.value_tree, ("time", "value"))):
            self.assertEqual(str(tree.cget("style")), "Kit.Treeview")
            self.assertEqual(int(tree.cget("height")), 2)
            # The card keeps the table's two rows under its heading (26 + 2 x 36); in the bento Animated
            # Tracks' table also takes the 5 px its card lacks beside Particles.
            rect = page.rect_of(table)
            extra = view.track_block.extra if table is view.track_table else 0
            self.assertEqual(rect[3] - rect[1], tree.winfo_reqheight() + extra)
            self.assertEqual(rect[3] - rect[1], px(page.scale, 26) + 2 * px(page.scale, 36) + extra)
            self.assertEqual(extra, px(page.scale, 213) - px(page.scale, 208) if extra else 0)
            for column in columns:
                self.assertEqual(str(tree.column(column, "anchor")), "w")
                self.assertEqual(str(tree.heading(column, "anchor")), "w")
        self.assertEqual([tree for tree in (view.track_tree, view.value_tree)
                          if str(tree.column(tree.cget("columns")[-1], "stretch")) not in ("1", "True", "true")], [])
        self.assertIn(str(view.value_tree.column("time", "stretch")), ("0", "False", "false"))
        self.assertEqual(view.value_tree.column("time", "width"), px(view.look_page.scale, 120))
        self.assertEqual([view.value_tree.heading(c, "text") for c in ("time", "value")], ["Shot seconds", "Value"])
        self.assertEqual(view.track_tree.heading("name", "text"), "Variable")
        bar = next(c for c in view.value_table.winfo_children() if isinstance(c, ttk.Scrollbar))
        self.assertEqual(str(bar.cget("style")), "Card.Vertical.TScrollbar")
        for row in (("0", "200"), ("5", "600")):
            view.value_tree.insert("", "end", values=row)
        self.root.update()
        self.assertEqual(bar.winfo_manager(), "")       # two rows fit
        for row in range(4):
            view.value_tree.insert("", "end", values=(str(10 + row), "1"))
        self.root.update()
        self.assertEqual(bar.winfo_manager(), "grid")

    def test_info_bubbles_carry_the_help_texts(self):
        view = self.build()
        page = view.look_page
        self.assertEqual(view.range_info.text, RANGE_INFO)
        self.assertEqual(view.fixed_card.info.text, FIXED_INFO)
        windows = page.window_counts()["page"]
        for icon in (view.range_info, view.fixed_card.info):
            icon.set_focus(True)               # keyboard focus: the bubble at once, the icon stays dim
            self.root.update()
            bubbles = [i for i in page.canvas.find_withtag("bubble")]
            self.assertEqual(len(bubbles), 1)
            tip = page.nametowidget(page.canvas.itemcget(bubbles[0], "window"))
            texts = [tip.itemcget(i, "text") for i in tip.find_all() if tip.type(i) == "text"]
            self.assertEqual(texts, [icon.text])
            self.assertEqual(page.window_counts()["page"], windows + 1)
            icon.set_focus(False)
            self.root.update()
            self.assertEqual(page.canvas.find_withtag("bubble"), ())

    def test_dof_sliders_map_like_the_frame(self):
        view = self.build()
        page = view.look_page
        s = page.scale
        sensor, focus = view.citadel_sensor_scale, view.citadel_focus_scale
        # 600 in on the app's log mapping (DollyApp._focus_to_slider).
        view.citadel_focus_internal.set(math.log(601.0) / math.log(10001.0))
        self.root.update()
        self.assertEqual(sensor.get(), 1.0)
        for slider, fraction in ((sensor, (1.0 - .5) / 2.5), (focus, math.log(601.0) / math.log(10001.0))):
            x0, _y, w, _h = self.in_card(view, view.dof_card, slider)
            thumb = x0 + self.d(page, slider.thumb_x())
            self.assertNear(thumb, x0 + 6 + fraction * (w - 12), 0.7)
            # The fill runs from the track's left end to the thumb centre.
            fill = slider.coords(slider._fill[0])
            self.assertNear(self.d(page, fill[2]), self.d(page, slider.thumb_x()), 0.7)
            if math.isclose(w, 110, abs_tol=1):  # the caption column kept the design's 135 px
                self.assertNear(thumb, 191.6 if slider is sensor else 240.1, 1)
        # Both captions sit in one column wide enough for their widest texts (look.md F7).
        font = fonts.font(page, "body")
        widest = max(font.measure("Sensor size · 3.00"), font.measure("Focus distance · 10000 in"))
        x0 = self.in_card(view, view.dof_card, sensor)[0]
        self.assertGreaterEqual((x0 - 21 - 10) * s + 1, widest)
        self.assertNear(x0, 21 + max(135, math.ceil(widest / s)) + 10)
        self.assertEqual(self.in_card(view, view.dof_card, focus)[0], x0)
        # The captions follow their variables; a written value moves the thumb but runs no command.
        self.calls.clear()
        view.citadel_sensor.set(2.0)
        view.citadel_sensor_text.set("Sensor size · 2.00")
        self.root.update()
        self.assertEqual(sensor.get(), 2.0)
        self.assertEqual(self.calls, [])
        captions = [view.dof_card.host.itemcget(i, "text") for i in view.dof_card.host.find_all()
                    if view.dof_card.host.type(i) == "text"]
        self.assertIn("Sensor size · 2.00", captions)
        self.assertIn("Focus distance · 200 in", captions)

    def test_the_variable_name_is_elided_at_rest(self):
        view = self.build()
        self.state.track_name.set("r_citadel_depthoffield_focus_distance_" * 4)
        self.root.update()
        cover = view.track_name_cover
        self.assertEqual(cover.winfo_manager(), "place")
        self.assertTrue(cover.cget("text").endswith("…"))
        view.track_name_entry.event_generate("<FocusIn>")
        self.root.update()
        self.assertEqual(cover.winfo_manager(), "")
        self.assertEqual(self.state.track_name.get(), "r_citadel_depthoffield_focus_distance_" * 4)

    def test_shot_playback_is_pinned_in_the_dock_while_the_cards_scroll(self):
        view = self.build()
        page, host, stand_in = view.look_page, view.playback_host, view.stand_in
        frame = stand_in.frame
        s = page.scale
        self.assertIs(frame._kit_host, host)
        self.assertEqual(host.mode, "dock")
        self.assertEqual(frame.winfo_manager(), "place")
        self.assertEqual(str(frame.place_info()["in"]), str(page.dock))
        self.assertFalse(host.card.visible)
        # The dock: 15 above the card, the card, and the page's end gap to the footer links (UPDATE 9).
        self.assertEqual(int(page.dock.cget("height")), px(s, 15) + host.card_height() + page.end_gap())
        self.assertEqual(page.end_gap(), 29)
        self.assertEqual(host.card_height(), px(s, 178))
        # At 1200 x 810 the 526 px of cards overflow the viewport above the dock and scroll; the dock stays.
        self.assertEqual(page.canvas.winfo_height(), px(s, 591) - int(page.dock.cget("height")))
        self.assertNear(self.d(page, page.content_height - page.canvas.winfo_height()), 526 - 369)
        self.assertEqual(float(page.canvas.cget("scrollregion").split()[3]), page.content_height)  # no end margin
        self.assertTrue(page.scrollbar.winfo_ismapped())
        self.assertFalse(page._foot_packed)          # the dock brings the gap
        before = frame.winfo_rooty()
        page.scroll_to(page.content_height)
        self.root.update()
        self.assertGreater(page.canvas.canvasy(0), 0)
        self.assertEqual(frame.winfo_rooty(), before)
        # The wheel over the docked card, a slider or a field scrolls the page; tables keep theirs.
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            for widget in (stand_in.entry, view.citadel_sensor_scale, view.track_name_entry):
                page.scroll_to(0)
                self.assertEqual(page._wheel(SimpleNamespace(widget=widget, delta=-120)), "break", widget)
                self.assertGreater(page.canvas.canvasy(0), 0, widget)
            page.scroll_to(0)
            for widget in (view.track_tree, view.particles_combo):
                self.assertIsNone(page._wheel(SimpleNamespace(widget=widget, delta=-120)))
            self.assertEqual(page.canvas.canvasy(0), 0)
        self.assertEqual(getattr(frame, "scroll_page_wheel", None), page.scroll_page_wheel)
        # Reveal of something in the dock scrolls nothing (it is always in view); release hands it back.
        page.scroll_to(0)
        page.reveal(stand_in.play)
        self.assertEqual(page.canvas.canvasy(0), 0)
        host.release(frame)
        page.layout_now()
        self.root.update()
        self.assertEqual(frame.winfo_manager(), "")
        self.assertFalse(page._dock_packed)
        self.assertTrue(page._foot_packed)           # without the dock, the foot keeps the gap
        self.assertEqual(int(page.foot.cget("height")), page.end_gap())

    def test_a_short_window_unpins_shot_playback_into_the_scroll_area(self):
        view = self.build(shown="948x330")
        page, host = view.look_page, view.playback_host
        self.assertEqual(host.mode, "flow")           # the viewport would drop below 160 px (N10)
        self.assertTrue(host.card.visible)
        self.assertEqual(str(view.stand_in.frame.place_info()["in"]), str(host.card.host))
        self.assertTrue(page._foot_packed)
        self.root.geometry("948x591")
        self.root.update()
        page.layout_now()
        self.assertEqual(host.mode, "dock")

    def test_tab_order_follows_the_cards_and_ends_in_shot_playback(self):
        view = self.build(shown="948x591")
        page = view.look_page
        start = view.range_dof_button.group.proxy
        order, widget = [], start
        for _ in range(60):
            order.append(widget)
            widget = widget.tk_focusNext()
            if widget is None or widget is start:
                break
        stand_in = view.stand_in
        reading = [view.range_dof_button.group.proxy, view.track_tree, view.create_track_button.group.proxy,
                   view.track_name_entry, view.track_mode_combo, view.restore_entry, view.value_tree,
                   view.track_time_entry, view.range_info.group.proxy, view.track_value_entry,
                   view.put_value_button.group.proxy, view.confetti_checkbox, view.particles_combo,
                   view.intensity_entry, view.spawn_height_entry, view.despawn_checkbox, view.citadel_dof_checkbox,
                   view.citadel_sensor_scale, view.citadel_focus_scale, view.fixed_card.info.group.proxy,
                   view.fixed_values_button.group.proxy, stand_in.entry, stand_in.play.group.proxy]
        self.assertEqual([w for w in order if w in reading], reading)
        # No card canvas, table frame or label is a stop; the page's own scrollers neither.
        for widget in walk(page):
            if widget in reading or isinstance(widget, ttk.Treeview):
                continue
            if getattr(widget, "_kit_group", None) is None and isinstance(widget, (tk.Canvas, tk.Frame, ttk.Label)):
                self.assertIn(str(widget.cget("takefocus")), ("0", ""), widget)
        # Reaching an off-screen stop scrolls it into view first; the docked card needs no scroll.
        page.scroll_to(0)
        script = str(view.fixed_values_button.group.proxy.cget("takefocus"))
        self.assertEqual(str(self.root.tk.call(script, str(view.fixed_values_button.group.proxy))), "1")
        self.assertTrue(self.in_view(page, page.rect_of(view.fixed_values_button)))
        top = page.canvas.canvasy(0)
        script = str(stand_in.entry.cget("takefocus"))
        self.assertEqual(str(self.root.tk.call(script, str(stand_in.entry))), "1")
        self.assertEqual(page.canvas.canvasy(0), top)
        self.assertTrue(stand_in.entry.winfo_ismapped())

    def test_hosting_and_window_budgets(self):
        view = self.build()
        page = view.look_page
        counts = page.window_counts()
        if page.sheet is not None:
            self.assertEqual(counts["page"], 1)                  # the sheet (PR2)
            self.assertEqual(counts["moving"], 1)                # Shot Playback is docked: it never moves
        else:   # without window regions: one window per card, never one per widget
            self.assertEqual(counts["page"], len(page.cards))
        self.assertEqual(counts["inputs"], 15)                   # PR3: Look <= 20
        self.assertLessEqual(counts["inputs"], 20)
        for widget, element in page._widgets.items():
            self.assertIs(widget.master, element.card.host)
            self.assertEqual(widget.winfo_manager(), "place")
        # Labels, units, captions and buttons are card-canvas items (PR1); containers are tk frames (PR4).
        for card in page.cards:
            self.assertEqual([i for i in card.host.find_all() if card.host.type(i) == "window"], [])
            for widget in walk(card.host):
                self.assertNotIsInstance(widget, (ttk.Frame, tk.Label))
                if isinstance(widget, ttk.Label):
                    self.assertIsInstance(widget.master, ttk.Entry)   # the at-rest cover of Variable name
        texts = {view.editor_card.host.itemcget(i, "text") for i in view.editor_card.host.find_all()
                 if view.editor_card.host.type(i) == "text"}
        self.assertTrue({"Track Editor", "Variable name", "Curve", "Restore · optional", "Time", "Value"} <= texts)
        texts = {view.particles_card.host.itemcget(i, "text") for i in view.particles_card.host.find_all()
                 if view.particles_card.host.type(i) == "text"}
        self.assertTrue({"Particles", "Intensity", INTENSITY_UNIT, "Height", "units (100–1500)"} <= texts)
        units = [i for i in view.particles_card.host.find_all() if view.particles_card.host.type(i) == "text"
                 and view.particles_card.host.itemcget(i, "text") in (INTENSITY_UNIT, "units (100–1500)")]
        self.assertEqual({view.particles_card.host.itemcget(i, "fill") for i in units}, {ui_theme.TOKENS["dim"]})

    def test_the_bento_regrows_after_a_resize_and_shrinks_back_in_one_column(self):
        view = self.build()
        page = view.look_page
        self.assertGreater(view.track_block.extra, 0)
        self.root.geometry("854x591")                 # one column: every card at its own height
        self.root.update()
        page.layout_now()
        self.assertFalse(page.two_columns)
        self.assertEqual((view.track_block.extra, view.value_block.extra, view.fixed_card.extra), (0, 0, 0))
        self.root.geometry("948x591")
        self.root.update()
        page.layout_now()
        self.assertTrue(page.two_columns)
        self.assertEqual(view.tracks_card.rect[3], view.particles_card.rect[3])
        self.assertEqual(view.editor_card.rect[3], view.fixed_card.rect[3])


def view_commands(case):
    return LookActions(**{f.name: case.record(f.name) for f in fields(LookActions) if f.name != "tree"},
                       tree=case.tree)


class LookAppTests(unittest.TestCase):
    """The page in the real app (wired by the integration package, PLAN-5 4.4)."""

    def setUp(self):
        from dolly import gui_layout
        if not hasattr(gui_layout, "build_look"):
            self.skipTest("Look is wired into the app by the integration package (PLAN-5 4.4 step 7)")
        from dolly.gui import DollyApp
        from dolly.settings import AppSettings
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.scaling = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", 96 / 72)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DOLLY_UI_MOTION": "0"}).start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patch.object(DollyApp, "_submit").start()
        patch.object(DollyApp, "_poll").start()
        patch.object(DollyApp, "_load_app_settings", return_value=AppSettings(
            auto_updates=False, export_folder=str(Path(directory.name) / "exports"))).start()
        patch("dolly.graphics_profiles.load_library", return_value={"format": 1, "selected": "", "profiles": []}).start()
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()

    def test_look_is_built_on_the_kit_and_hosts_shot_playback(self):
        app = self.app
        page = app.look_page
        self.assertIsInstance(page, KitPage)

        def on_page(widget):
            while widget is not None:
                if widget is page:
                    return True
                widget = widget.master
            return False
        for name in ("track_tree", "value_tree", "citadel_dof_checkbox", "citadel_sensor_scale",
                     "citadel_focus_scale"):
            self.assertTrue(on_page(getattr(app, name)), name)
        self.assertIsInstance(app.citadel_sensor_scale, KitSlider)
        # The app's Citadel helpers drive the page's variables.
        app._refresh_citadel_dof()
        self.assertTrue(app.citadel_sensor_text.get().startswith("Sensor size · "))
        self.assertEqual(app.citadel_sensor_scale.get(), float(app.citadel_sensor.get()))
        self.root.deiconify()
        self.root.geometry("1200x885")   # a 1200 x 830 page area under the title bar, above the link bar
        app._select_page(app.cvar_tab)
        self.root.update()
        frame = app.timeline_frame
        self.assertEqual(frame.winfo_manager(), "place")
        host = frame._kit_host
        self.assertIs(host.page, page)
        # Pinned in Look's dock like on Camera, with the card's art (UPDATE 7).
        self.assertEqual(host.where, "dock")
        self.assertEqual(str(frame.place_info()["in"]), str(page.dock))
        self.assertIs(page.all_cards()[-1], host.card)
        self.assertIsNotNone(app.playback_view._art)
        counts = page.window_counts()
        self.assertLessEqual(counts["inputs"], 20)
        if page.sheet is not None:
            self.assertEqual(counts["page"], 1)
            self.assertEqual(counts["moving"], 1)
        self.assertTrue(app.slider.winfo_ismapped())
        # Camera and Look hand the one frame back and forth; each shows it in its own dock.
        app._select_page(app.camera_tab)
        self.root.update()
        self.assertEqual(str(frame.place_info()["in"]), str(app.camera_page.dock))
        app._select_page(app.cvar_tab)
        self.root.update()
        self.assertEqual(str(frame.place_info()["in"]), str(page.dock))


if __name__ == "__main__":
    unittest.main()
