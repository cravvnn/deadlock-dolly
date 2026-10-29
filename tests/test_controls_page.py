"""Actual Controls page widgets and the application's shared recorder route."""
from dataclasses import fields
import json
import math
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from dolly import gui_theme
from dolly.editor_actions import ACTION_LABELS, ACTION_ORDER, BINDABLE_ORDER, EditorBinding
from dolly.settings import AppSettings
from dolly.ui import boil, controls_page, kit_controls
from dolly.ui.camera_feel import FEEL_INFO
from dolly.ui.controls_page import ControlsActions, ControlsPage
from dolly.ui.kit_card import KitRow
from dolly.ui.kit_page import KitPage
from dolly.ui.scale import px, ui_scale

# The application's groups (gui._build_keybinds) with the Object Picker flag off.
GROUPS = {
    "Camera": ("capture", "replace", "play_path", "stop", "previous_view", "next_view", "flight", "level_view"),
    "Replay": ("play_pause", "seek_back", "seek_forward"),
    "Interface": ("panel", "game_ui", "framing_grid", "reshade"),
    "Movement": ACTION_ORDER[12:],
}
FULL_RECORDS = tuple((action, "Toggle ReShade menu" if action == "reshade" else ACTION_LABELS[action], group)
                     for group, actions in GROUPS.items() for action in actions)
DESIGN_FEEDBACK = "Choose a shortcut below. Clear removes it; Reset restores that action's default."


def walk(parent):
    for child in parent.winfo_children():
        yield child
        yield from walk(child)


class ControlsCase(unittest.TestCase):
    records = (("capture", "Capture camera", "Camera"), ("replace", "Replace selected camera", "Camera"),
               ("panel", "Toggle Dolly panel", "Interface"), ("reshade", "Toggle ReShade menu", "Interface"),
               ("move_forward", "Move forward", "Movement"))

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.calls = []
        self.saved_sensitivity_values = []

    def build(self, feedback="Fixture controls"):
        def record(name, args):
            self.calls.append((name, args))
            if name == "save_sensitivity":
                self.saved_sensitivity_values.append(self.sensitivity.get())
        commands = ControlsActions(**{field.name: lambda *args, name=field.name:
                                     record(name, args) for field in fields(ControlsActions)})
        self.sensitivity = tk.StringVar(self.root, "0.12")
        self.movement_speed = tk.StringVar(self.root, "320")
        self.feedback = tk.StringVar(self.root, feedback)
        self.view = ControlsPage(self.root, self.records, commands,
            movement_speed=self.movement_speed, mouse_sensitivity=self.sensitivity, feedback=self.feedback)
        self.settings = AppSettings()
        self.view.refresh({**self.settings.action_bindings, "reshade": self.settings.reshade_binding})
        return self.view

    def tearDown(self):
        self.assertEqual(getattr(self, "errors", []), [])

    def visible(self, mapping):
        return [key for key, widget in mapping.items() if widget.winfo_manager()]

    def widgets(self):
        return tuple(walk(self.root))

    def button(self, text):
        return next(button for button in kit_controls.buttons(self.view.page) if button.cget("text") == text)

    def show(self, width, height):
        """Show the root at ``width`` x ``height`` design px (cloaked by the test harness)."""
        s = ui_scale(self.root)
        self.root.deiconify()
        self.root.geometry(f"{px(s, width)}x{px(s, height)}+0+0")
        self.root.update()
        self.view.page.layout_now()
        self.root.update_idletasks()
        return s

    def design(self, rect):
        s = self.view.page.scale
        return tuple(round(value / s) for value in rect)


class ControlsPageTests(ControlsCase):
    def setUp(self):
        super().setUp()
        self.build()

    def test_shortcut_clear_reset_and_reset_all_route_exact_action(self):
        for action, _label, _group in self.records:
            self.view.binding_buttons[action].invoke()
            self.view.rows[action].clear_button.invoke()
            self.view.rows[action].reset_button.invoke()
        self.view.reset_all_button.invoke()
        expected = [(name, (action,)) for action, _label, _group in self.records
                    for name in ("change_binding", "clear_binding", "reset_binding")]
        self.assertEqual(self.calls, expected + [("reset_all", ())])
        self.assertFalse(any(isinstance(widget, ttk.Combobox) for widget in self.widgets()))

    def test_search_matches_action_shortcut_and_group_hides_empty_cards(self):
        for query, rows, groups in ((" CAPTURE ", ["capture"], ["Camera"]),
                                   ("f8", ["panel"], ["Interface"]),
                                   ("movement", ["move_forward"], ["Movement"]),
                                   ("no such shortcut", [], [])):
            self.view.search.set(query)
            self.assertEqual(self.visible(self.view.rows), rows)
            self.assertEqual(self.visible(self.view.groups), groups)
            self.assertEqual(bool(self.view.empty_label.winfo_manager()), not rows)
        self.view.search.set("")
        self.assertEqual(self.visible(self.view.rows), [row[0] for row in self.records])
        self.assertEqual(self.visible(self.view.groups), ["Camera", "Interface", "Movement"])
        # The cards stack in the groups' order in one column (UPDATE 13), Movement after the others.
        self.view.page.layout_now()
        tops = [card.rect[1] for card in self.view.groups.values()]
        self.assertEqual(tops, sorted(tops))
        self.assertEqual([card.column for card in self.view.groups.values()], [None, None, None])

    def test_refresh_updates_live_search_and_unbound_clear_state(self):
        self.view.search.set("mouse5")
        self.assertEqual(self.visible(self.view.rows), [])
        bindings = {**self.settings.action_bindings, "reshade": None, "capture": EditorBinding("Mouse5")}
        self.view.refresh(bindings)
        self.assertEqual(self.visible(self.view.rows), ["capture"])
        self.assertEqual(self.view.binding_buttons["capture"].cget("text"), "Mouse5")
        self.assertEqual(str(self.view.rows["reshade"].clear_button.cget("state")), "disabled")
        self.view.rows["reshade"].clear_button.invoke()
        self.assertEqual(self.calls, [])
        self.view.search.set("Unbound")
        self.assertEqual(self.visible(self.view.rows), ["reshade"])
        # Unbound: the keycap's Empty face (same text) and a Clear the keyboard skips.
        self.assertEqual(str(self.view.binding_buttons["reshade"].cget("style")), "Kit.KeycapEmpty.TButton")
        self.assertEqual(str(self.view.binding_buttons["capture"].cget("style")), "Kit.Keycap.TButton")
        self.view.page.layout_now()   # a row shown again draws (and takes focus) from its next layout
        group = self.view.rows["reshade"].clear_button.group
        self.assertIs(group.first(), self.view.rows["reshade"].reset_button)

    def test_reveal_clears_filter_and_reaches_last_action_in_scroll_region(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        self.view.search.set("Capture")
        self.view.reveal_action("move_forward")
        self.root.update()
        self.assertEqual(self.view.search.get(), "")
        button = self.view.binding_buttons["move_forward"]
        canvas = self.view.page.canvas
        self.assertGreaterEqual(button.winfo_rooty(), canvas.winfo_rooty())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), canvas.winfo_rooty() + canvas.winfo_height())

    def test_camera_feel_and_movement_settings_are_preserved_on_controls_page(self):
        self.button("Very slow").invoke()
        self.assertAlmostEqual(float(self.sensitivity.get()), .02)
        self.button("Save movement settings").invoke()
        self.assertEqual(self.calls, [("save_sensitivity", ()), ("save_movement", ())])
        self.assertIs(self.button("Save movement settings"), self.view.save_movement_button)
        self.assertIs(self.button("Save sensitivity"), self.view.camera_feel.save_button)
        self.assertEqual([button.cget("text") for button in self.view.camera_feel.preset_buttons],
                         ["Very slow", "Slow", "Normal", "Fast", "Very fast"])

    def test_exact_sensitivity_entry_and_save_return_preserve_authored_precision(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        entry = self.view.camera_feel.sensitivity_entry
        self.assertEqual(str(entry.cget("textvariable")), str(self.sensitivity))
        save = self.button("Save sensitivity")
        for value in ("0.123456789", "0.001", "10"):
            entry.delete(0, "end")
            entry.insert(0, value)
            self.root.update_idletasks()
            self.assertEqual(self.sensitivity.get(), value)
            save.invoke()
            self.assertEqual(self.saved_sensitivity_values[-1], value)
        self.view.page.reveal(entry)
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.saved_sensitivity_values, ["0.123456789", "0.001", "10", "10"])

    def test_keyboard_traversal_reveals_offscreen_actions_and_exact_preferences(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        self.view.page.canvas.yview_moveto(0)
        self.view.search_entry.focus_force()
        self.root.update()
        # Reading order, top to bottom in the one column (UPDATE 13): the Movement card, then Camera Feel.
        targets = (self.view.binding_buttons["move_forward"], self.view.camera_feel.sensitivity_entry)
        visited = set()
        route = []
        chain = []
        current = self.view.search_entry
        for _ in range(len(self.widgets())+1):
            chain.append(current)
            current = current.tk_focusNext()
            if current is self.view.search_entry:
                break
            self.assertNotIn(current, chain, "Tab traversal must form one finite cycle.")
        self.assertIs(current, self.view.search_entry)
        self.assertFalse([str(widget) for widget in chain if isinstance(widget, (ttk.Frame, ttk.Label))],
                         "Focus reveal must not turn structural cards or labels into Tab stops.")
        self.assertTrue(set(targets).issubset(chain))
        # A focus group takes one Tab per canvas control it holds.
        steps = len(chain) + sum(len(widget._kit_group.controls) for widget in chain
                                 if getattr(widget, "_kit_group", None) is not None) + 1
        def visible(widget):
            canvas = self.view.page.canvas
            self.assertGreaterEqual(widget.winfo_rooty(), canvas.winfo_rooty())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), canvas.winfo_rooty()+canvas.winfo_height())
        for _ in range(steps):
            focused = self.root.focus_get()
            route.append((str(focused), focused.winfo_class() if focused else None,
                          self.view.page.canvas.yview()))
            if focused in targets:
                visible(focused)
                visited.add(focused)
                if focused is targets[-1]:
                    break
            self.assertIsNotNone(focused)
            focused.event_generate("<KeyPress>", keysym="Tab")
            self.root.update()
        self.assertEqual(visited, set(targets), route)
        self.assertGreater(self.view.page.canvas.yview()[0], 0)
        for _ in range(steps):
            focused = self.root.focus_get()
            if focused is self.view.search_entry:
                break
            focused.event_generate("<KeyPress>", keysym="Tab", state=0x1)
            self.root.update()
        self.assertIs(self.root.focus_get(), self.view.search_entry)
        visible(self.view.search_entry)
        self.assertEqual(self.calls, [])

    def test_tab_wrapping_around_to_the_search_field_scrolls_it_into_view(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        page, entry = self.view.page, self.view.search_entry
        last = self.view.save_movement_button     # the last card's last control
        page.reveal(last)
        self.root.update()
        self.assertGreater(page.canvas.canvasy(0), 0)
        if page.sheet is not None:   # (the per-card fallback puts the band stand-ins first)
            self.assertIs(last.group.proxy.tk_focusNext(), entry)    # evaluates its takefocus script
        else:
            self.assertEqual(self.root.tk.call(str(entry.cget("takefocus")), str(entry)), 1)
        self.root.update()
        self.assertEqual(page.canvas.canvasy(0), 0)
        self.assertEqual(self.root.tk.call(str(entry.cget("takefocus")), str(entry)), 1)

    def test_destroying_controls_cancels_pending_filter_geometry_refresh(self):
        self.root.update_idletasks()
        self.view.search.set("capture")
        token = self.view._filter_after
        self.assertIsNotNone(token)
        self.view.search.set("panel")
        self.assertEqual(self.view._filter_after, token)
        self.assertIn(token, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.view.page.destroy()
        self.assertNotIn(token, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.root.update()

    def test_page_constants_bubbles_and_exports(self):
        view = self.view
        self.assertEqual(controls_page.TITLE, "Controls")
        self.assertEqual(controls_page.SUBTITLE,
                         "Click a shortcut and press its replacement. Changes are saved across launches.")
        self.assertEqual(view.feedback_info.cget("text"), controls_page.FEEDBACK_INFO)
        self.assertEqual(controls_page.FEEDBACK_INFO, "F7 is reserved for the game console and releases camera "
                         "input. Mouse 4 and Mouse 5 are the usual side buttons.")
        self.assertEqual(controls_page.FEEL_INFO, FEEL_INFO)
        self.assertEqual(view.camera_feel.card.info.cget("text"), FEEL_INFO)
        self.assertEqual(view.feedback_text.cget("text"), "Fixture controls")
        self.assertIsInstance(view.page, KitPage)
        self.assertEqual(view.search_field.placeholder.cget("text"), "Find an action or shortcut")
        self.assertEqual(view.search.get(), "")
        self.assertEqual(str(view.movement_speed_entry.cget("style")), "Kit.TEntry")
        self.assertEqual(view.movement_speed_entry.get(), "320")
        self.assertTrue(all(isinstance(row, KitRow) for row in view.rows.values()))
        self.assertTrue(all(row.winfo_manager() == "canvas" for row in view.rows.values()))
        # Card buttons are canvas-drawn (N21); keycaps, fields and the slider stay real widgets.
        texts = [button.cget("text") for button in kit_controls.buttons(view.page)]
        self.assertEqual(texts[0], "Reset all shortcuts")
        self.assertEqual(texts.count("Clear"), len(self.records))
        self.assertEqual(texts.count("Reset"), len(self.records))
        self.assertEqual({widget for widget in self.widgets() if isinstance(widget, ttk.Button)},
                         set(view.binding_buttons.values()))

    def test_rows_put_clear_and_reset_in_one_focus_group_right_after_the_keycap(self):
        for action, row in self.view.rows.items():
            keycap, clear, reset = self.view.binding_buttons[action], row.clear_button, row.reset_button
            self.assertIs(keycap.master, row.host)
            self.assertIs(clear.group, reset.group)
            self.assertEqual(clear.group.controls, [clear, reset])
            self.assertEqual(clear.kind, "quiet")
            siblings = row.host.winfo_children()
            self.assertEqual(siblings.index(clear.group.proxy), siblings.index(keycap) + 1, action)
            self.assertIsInstance(clear.group.proxy, tk.Frame)

    def test_listening_keycap_pulses_on_the_boil_clock_only_while_its_dialog_is_open(self):
        self.root.deiconify()
        self.root.update()
        keycap = self.view.binding_buttons["panel"]
        clock = boil.BoilClock.of(keycap)
        self.assertEqual(clock.members, ())
        with patch("dolly.ui.boil.motion_enabled", return_value=True):
            self.view.set_listening("panel")
            self.assertTrue(keycap.instate(["alternate"]))
            self.assertEqual(len(clock.members), 1)
            faces = []
            for _ in range(3):
                clock.tick()
                faces.append(keycap.instate(["alternate"]))
            self.assertEqual(faces, [False, True, False])
            # Another dialog: the first keycap rests, the new one listens (an unbound one shows its label in text).
            self.view.refresh({**self.settings.action_bindings, "reshade": None})
            self.view.set_listening("reshade")
            self.assertFalse(keycap.instate(["alternate"]))
            reshade = self.view.binding_buttons["reshade"]
            self.assertTrue(reshade.instate(["alternate"]))
            self.assertEqual(str(reshade.cget("style")), "Kit.Keycap.TButton")
            self.assertEqual(len(clock.members), 1)
            self.view.set_listening(None)
            self.assertFalse(reshade.instate(["alternate"]))
            self.assertEqual(str(reshade.cget("style")), "Kit.KeycapEmpty.TButton")
            self.assertEqual(clock.members, ())
            self.assertFalse(clock.running)
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            self.view.set_listening("capture")
            self.assertTrue(self.view.binding_buttons["capture"].instate(["alternate"]))
            self.assertEqual(clock.members, ())   # motion off: a steady edge
            self.view.set_listening(None)
            self.assertFalse(self.view.binding_buttons["capture"].instate(["alternate"]))


class ControlsDesignTests(ControlsCase):
    """The full page against the frame ([design reference]) and its narrower layouts."""

    records = FULL_RECORDS
    scale = None
    tol = 1          # design px: ±1 at 100 %, ±2 at 150 % (PLAN-5 6.1)

    def assertNearDesign(self, actual_px, design):
        got = self.design(actual_px)
        self.assertTrue(all(abs(a - b) <= self.tol for a, b in zip(got, design)), (got, design))

    def setUp(self):
        super().setUp()
        if self.scale is not None:
            scaling = self.root.tk.call("tk", "scaling")
            self.addCleanup(lambda: self.root.tk.call("tk", "scaling", scaling))
            self.root.tk.call("tk", "scaling", self.scale * 96 / 72)
        gui_theme.initialize(self.root)
        self.build(DESIGN_FEEDBACK)

    def test_cards_toolbar_and_feedback_in_one_column_like_replay(self):
        view = self.view
        page = view.page
        s = page.scale
        self.show(948, 810)
        # UPDATE 13: one column of Replay's width (613, x 0, cards 15 apart), never a side column.
        self.assertFalse(page.two_columns)
        self.assertEqual(self.design((0, 0, page.content_width, 0))[2], 613)
        expected = {"Camera": (0, 71, 613, 456), "Replay": (0, 471, 613, 656), "Interface": (0, 671, 613, 896),
                    "Movement": (0, 911, 613, 1536), "Camera Feel": (0, 1551, 613, 1793),
                    "Camera Movement": (0, 1808, 613, 1953)}
        self.assertEqual([card.title for card in page.cards], list(expected))
        for card in page.cards:
            for actual, wanted in zip(self.design(card.rect), expected[card.title]):
                self.assertLessEqual(abs(actual - wanted), self.tol, (card.title, card.rect))
        rects = [card.rect for card in page.cards]
        self.assertEqual({(r[0], r[2]) for r in rects}, {(0, page.content_width)})
        self.assertEqual({b[1] - a[3] for a, b in zip(rects, rects[1:])}, {px(s, 15)})
        self.assertIs(page.cards[4], view.camera_feel.card)
        # Comment #3: the toolbar is as wide as the column below it: the search fills it up to
        # Reset all shortcuts, 10 px apart, and Reset all ends on the column's right edge.
        search, reset = view.search_field.rect, page.rect_of(view.reset_all_button)
        self.assertEqual(search[0], view.groups["Camera"].rect[0])
        self.assertEqual(reset[2], view.groups["Camera"].rect[2])
        self.assertEqual(reset[0] - search[2], px(s, 10))
        self.assertNearDesign((search[1], search[3], reset[1], reset[3]), (0, 30, 0, 30))
        # The feedback line sits 10 below the toolbar.
        line = page.rect_of(page.bands[1])
        self.assertNearDesign((line[1], line[3]), (40, 56))
        # Comment #1: Movement is one list, the Look & Roll rows below the Move rows (never side by side).
        card = view.groups["Movement"]
        move, look = view._sections[0][0], view._sections[1][0]
        self.assertFalse([e for e in card.elements if hasattr(e, "divider")], "no split in the Movement card")
        self.assertNearDesign((move.page_rect()[0], look.page_rect()[0]), (21, 21))
        self.assertEqual(look.page_rect()[1], move.page_rect()[3] + px(s, 10))
        self.assertEqual(move.page_rect()[2], look.page_rect()[2])

    def test_binding_rows_match_the_frame(self):
        view = self.view
        page = view.page
        self.show(948, 810)
        s = page.scale
        for action, label, design in (("capture", 21, (417, 505, 515, 559, 592)),
                                      ("look_left", 21, (417, 505, 515, 559, 592))):
            row = view.rows[action]
            ox = view.groups["Camera" if action == "capture" else "Movement"].rect[0]
            keycap = page.rect_of(view.binding_buttons[action])
            clear, reset = page.rect_of(row.clear_button), page.rect_of(row.reset_button)
            got = [round((value - ox) / s) for value in (keycap[0], keycap[2], clear[0], reset[0], reset[2])]
            # Exact: the row's gaps (10 · keycap 88 · 10 · Clear · 14 · Reset) and its right end. The x of
            # the keycap and Clear follow the quiet labels' widths (Inter, as in the frame).
            self.assertEqual(keycap[2] - keycap[0], px(s, 88))
            self.assertEqual(clear[0] - keycap[2], px(s, 10))
            self.assertEqual(reset[0] - clear[2], px(s, 14))
            self.assertLessEqual(abs(got[4] - design[4]), self.tol, (action, got, design))
            for actual, wanted in zip(got, design):
                self.assertLessEqual(abs(actual - wanted), self.tol, (action, got, design))
            self.assertEqual(round((row.page_rect()[0] - ox) / s), label)
            self.assertLessEqual(abs((keycap[3] - keycap[1]) / s - 30), self.tol)
        # Rows are 30 tall and 10 apart.
        first, second = view.rows["capture"].page_rect(), view.rows["replace"].page_rect()
        self.assertEqual(round((second[1] - first[1]) / page.scale), 40)

    def test_keycaps_take_their_card_widest_label(self):
        view = self.view
        page = view.page
        self.show(948, 810)
        widths = {action: page.rect_of(keycap)[2] - page.rect_of(keycap)[0]
                  for action, keycap in view.binding_buttons.items()}
        self.assertEqual(set(widths.values()), {px(page.scale, 88)})
        long = EditorBinding("MiddleMouse", ctrl=True, alt=True, shift=True)
        view.refresh({**self.settings.action_bindings, "reshade": self.settings.reshade_binding, "panel": long})
        page.layout_now()
        self.root.update_idletasks()
        font = ttk.Style(self.root).lookup("Kit.Keycap.TButton", "font")
        measured = int(self.root.tk.call("font", "measure", font, long.label))
        # 12 px a side (UPDATE 23: Figma's Backspace, Inter Medium 63 px, sits in an 88 px keycap).
        wanted = max(88, math.ceil(measured / page.scale) + 24)
        self.assertGreater(wanted, 88)
        interface = list(GROUPS["Interface"])
        for action, keycap in view.binding_buttons.items():
            width = page.rect_of(keycap)[2] - page.rect_of(keycap)[0]
            if action in interface:
                self.assertEqual(width, px(page.scale, wanted), (action, width, measured))
                self.assertGreaterEqual(width, measured + px(page.scale, 24) - 1)
            else:
                self.assertEqual(width, px(page.scale, 88), action)
        # The Interface rows stay aligned: every keycap of the card starts at the same x.
        self.assertEqual(len({page.rect_of(view.binding_buttons[a])[0] for a in interface}), 1)

    def test_one_column_and_one_movement_list_at_every_width(self):
        view = self.view
        page = view.page
        card = view.groups["Movement"]
        move, look = view._sections[0][0], view._sections[1][0]
        for width in (1400, 948, 780, 700, 560):
            self.show(width, 700)
            self.assertFalse(page.two_columns, width)
            column = min(px(page.scale, 613), page.winfo_width() - px(page.scale, page.GUTTER))
            self.assertEqual(page.content_width, column, width)
            rects = [c.rect for c in page.cards]
            self.assertEqual({(r[0], r[2]) for r in rects}, {(0, column)}, width)
            self.assertEqual(sorted(rects, key=lambda r: r[1]), rects, width)
            self.assertGreater(card.rect[1], view.groups["Interface"].rect[3])
            self.assertGreater(view.camera_feel.card.rect[1], card.rect[3])
            self.assertEqual(look.x0, move.x0)
            self.assertEqual(look.y, move.y + move.height + px(page.scale, 10))   # one list: rows 10 apart
            self.assertEqual(page.rect_of(view.reset_all_button)[2], column)
            self.assertEqual(view.search_field.rect[0], 0)

    def test_search_hides_sub_columns_and_shows_the_empty_state_at_the_column_top(self):
        view = self.view
        page = view.page
        card = view.groups["Movement"]
        move, look = view._sections[0][0], view._sections[1][0]
        self.show(948, 810)
        look_x = look.page_rect()[0]
        view.search.set("look")
        self.root.update()
        self.assertFalse(move.visible)
        self.assertTrue(look.visible)
        self.assertEqual(look.page_rect()[0], look_x)          # it keeps its x, and moves up to the first row
        self.assertNearDesign((look.page_rect()[1] - card.rect[1],), (21 + 18 + 15,))
        self.assertEqual(self.visible(view.groups), ["Movement"])
        self.assertEqual(page.canvas.canvasy(0), 0)
        view.search.set("no such shortcut")
        self.root.update()
        self.assertEqual(self.visible(view.groups), [])
        self.assertTrue(view.empty_label.winfo_manager())
        self.assertEqual(view.empty_label.cget("text"), "No matching controls.")
        self.assertTrue(view.camera_feel.card.visible)
        empty = page.rect_of(view.empty_label.band)
        self.assertNearDesign(empty[:2], (0, 71))          # where the first shortcut card starts
        self.assertNearDesign(view.camera_feel.card.rect[1:2], (71 + 16 + 15,))
        view.search.set("")
        self.root.update()
        self.assertFalse(view.empty_label.winfo_manager())
        self.assertNearDesign(view.camera_feel.card.rect[1:2], (1551,))

    def test_window_counts_stay_within_the_page_budgets(self):
        view = self.view
        page = view.page
        self.show(948, 810)
        counts = page.window_counts()
        if page.sheet is not None:
            self.assertEqual(counts["page"], 1)       # the sheet
            self.assertLessEqual(counts["moving"], 1)
        else:   # without window regions: one window per card (and the search field's middle), never per widget
            self.assertLessEqual(counts["moving"], len(page.cards) + 1)
        # 29 keycaps, the slider and two fields (PR3: <= 38); the search entry rides on the sheet.
        self.assertEqual(counts["inputs"], len(FULL_RECORDS) + 3)
        self.assertLessEqual(counts["inputs"], 38)
        page.scroll_to(page.content_height)
        self.root.update()
        self.assertEqual(page.window_counts(), counts)
        # Nothing in a card is a ttk frame or label, nor a canvas window item.
        for card in page.cards:
            self.assertEqual([i for i in card.host.find_all() if card.host.type(i) == "window"], [])
            for widget in walk(card.host):
                self.assertNotIsInstance(widget, (ttk.Frame, ttk.Label, tk.Label))


class ControlsDesignHighDpiTests(ControlsDesignTests):
    scale = 1.5
    tol = 2


class ControlsAppFixture(unittest.TestCase):
    def setUp(self):
        from dolly.gui import DollyApp
        self.DollyApp = DollyApp
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        # Tk on Windows keeps one `tk scaling` for every interpreter in the process:
        # hand the original back when this window closes, or a 150 % fixture would
        # leave every later test's window at 150 %.
        self.scaling = self.root.tk.call("tk", "scaling")
        if hasattr(self, "fixture_scale"):
            self.root.tk.call("tk", "scaling", self.fixture_scale*96/72)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.submit = patch.object(DollyApp, "_submit").start()
        patch.object(DollyApp, "_poll").start()
        patch.object(DollyApp, "_load_app_settings", return_value=AppSettings(
            auto_updates=False, full_editor=getattr(self, "fixture_full_editor", False))).start()
        patch("dolly.graphics_profiles.load_library", return_value={"format": 1, "selected": "", "profiles": []}).start()
        patch("dolly.ui.binding_capture.SideMouseCapture.start", return_value=False).start()
        self.addCleanup(patch.stopall)
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        if self.app.binding_dialog is not None and self.app.binding_dialog.winfo_exists():
            self.app.binding_dialog.event_generate("<Escape>")
            self.app.binding_dialog.destroy() if self.app.binding_dialog is not None else None
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()

    def tearDown(self):
        self.assertEqual(getattr(self, "errors", []), [])

    def open_recorder(self, action="panel"):
        self.root.deiconify()
        self.app._record_binding(action)
        self.root.update()
        dialog = self.app.binding_dialog
        dialog.focus_force()  # Own isolated Tk fixture only; never a user's app.
        self.root.update()
        return dialog

    def button(self, text):
        return next(widget for widget in walk(self.app.binding_dialog)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == text)


class ControlsRecorderIntegrationTests(ControlsAppFixture):
    def test_change_shortcut_dialog_keeps_its_frame_inside_the_work_area(self):
        requested = []
        geometry = tk.Wm.wm_geometry

        def record(window, new=None):
            if new is not None and window is not self.root:
                requested.append(new)
            return geometry(window, new)
        with patch("dolly.gui.chrome.work_area", return_value=(0, 0, 800, 600)), \
                patch("dolly.gui.chrome.frame_size", return_value=(16, 39)), \
                patch.object(tk.Wm, "wm_geometry", record), patch.object(tk.Wm, "geometry", record):
            self.open_recorder()
        size, x, y = requested[0].split("+")
        width, height = (int(value) for value in size.split("x"))
        self.assertEqual((width, height), self.app._px((480, 358)))
        self.assertTrue(0 <= int(x) and int(x) + width + 16 <= 800, requested[0])
        self.assertTrue(0 <= int(y) and int(y) + height + 39 <= 600, requested[0])

    def test_all_existing_actions_present_once_in_central_controls_page(self):
        expected = set(BINDABLE_ORDER) - {"object_picker"} | {"reshade"}
        actual = [record[0] for record in self.app.controls_view.records]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))
        self.app._show_keybinds("reshade")
        self.assertEqual(self.app.notebook.select(), str(self.app.controls_tab))
        self.assertTrue(self.app.full_editor.get())

    def test_filter_after_reaching_all_controls_keeps_the_search_result_visible(self):
        self._check_filter_after_reaching_all_controls()

    def _check_filter_after_reaching_all_controls(self):
        with patch.object(self.root, "winfo_screenwidth", return_value=1280), \
             patch.object(self.root, "winfo_screenheight", return_value=720):
            self.root.minsize(*self.app._main_dimensions(1000, 700))
        self.root.deiconify()
        self.root.geometry("1280x720+0+0")
        self.root.update()
        self.app._show_keybinds("capture")
        self.root.update()
        controls = self.app.controls_view
        page = controls.page
        canvas = page.canvas
        page.scroll_to(0)
        self.root.update()
        for action in controls.binding_buttons:
            page.reveal(controls.rows[action])
            self.root.update_idletasks()
            target = controls.binding_buttons[action]
            for _ in range(4):
                top = target.winfo_rooty()-canvas.winfo_rooty()
                if 0 <= top and top+target.winfo_height() <= canvas.winfo_height():
                    break
                canvas.yview_scroll(-1 if top < 0 else 1, "units")
                self.root.update_idletasks()
        page.reveal(controls.camera_feel.sensitivity_entry)
        self.root.update()
        previous_top = canvas.canvasy(0)
        self.app._refresh_bindings()
        self.root.update()
        self.assertEqual(canvas.canvasy(0), previous_top, "Refreshing shortcuts must preserve the user's scroll position.")
        def geometry():
            def widget(value):
                return {"manager": value.winfo_manager(), "mapped": bool(value.winfo_ismapped()),
                        "x": value.winfo_rootx(), "y": value.winfo_rooty(),
                        "width": value.winfo_width(), "height": value.winfo_height(),
                        "requested_height": value.winfo_reqheight()}
            return {"canvas": widget(canvas), "scrollregion": str(canvas.cget("scrollregion")),
                    "bbox": canvas.bbox("all"), "yview": canvas.yview(), "top": canvas.canvasy(0),
                    "content_height": page.content_height, "two_columns": page.two_columns,
                    "interface": page.rect_of(controls.groups["Interface"]),
                    "movement_visible": controls.groups["Movement"].visible,
                    "reshade": widget(controls.binding_buttons["reshade"]),
                    "reshade_rect": page.rect_of(controls.binding_buttons["reshade"])}
        before = geometry()
        self.assertGreater(canvas.yview()[0], 0)
        controls.search.set("reshade")
        for _ in range(5):
            self.root.update_idletasks()
            self.root.update()
        after = geometry()
        proof = json.dumps({"before": before, "after": after}, sort_keys=True)
        print("FILTER_GEOMETRY " + proof)
        self.assertFalse(controls.groups["Movement"].visible, proof)
        self.assertFalse(controls.groups["Movement"].winfo_ismapped(), proof)
        target = after["reshade"]
        self.assertTrue(target["mapped"], proof)
        self.assertGreaterEqual(target["y"], after["canvas"]["y"], proof)
        self.assertLessEqual(target["y"]+target["height"], after["canvas"]["y"]+after["canvas"]["height"], proof)
        self.assertEqual(canvas.canvasy(0), 0, "A new normalized query must start at the first result.")
        canvas.yview_moveto(1)
        self.root.update()
        unchanged_top = canvas.canvasy(0)
        controls.search.set(" ReShade ")
        self.root.update()
        self.assertEqual(canvas.canvasy(0), unchanged_top, "Equivalent normalized queries must preserve the viewport.")

    def test_real_keypress_review_requires_save_and_blocks_app_shortcuts(self):
        dialog = self.open_recorder()
        fallthrough = Mock()
        dialog.bind("<Control-s>", fallthrough)
        self.app._persist_preferences = Mock(return_value=True)
        dialog.event_generate("<KeyPress>", keysym="F24", state=0x8)  # Windows NumLock is not Alt.
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("F24"))
        self.app._persist_preferences.assert_not_called()
        dialog.event_generate("<KeyPress>", keysym="s", state=0x4)
        self.root.update()
        fallthrough.assert_not_called()
        self.button("Save shortcut").invoke()
        result = self.app._persist_preferences.call_args.args[0]
        self.assertEqual(result.action_bindings["panel"], EditorBinding("F24"))
        self.assertIsNone(self.app.binding_dialog)

    def test_f7_conflict_cancel_and_modifier_release_do_not_mutate_preferences(self):
        dialog = self.open_recorder("move_down")
        self.app._persist_preferences = Mock()
        for symbol in ("F7", "F8"):
            dialog.event_generate("<KeyPress>", keysym=symbol)
            self.root.update()
            self.assertFalse(self.app._binding_capture.ready)
            self.assertTrue(self.app._binding_capture.error)
        dialog.event_generate("<KeyPress>", keysym="Control_L")
        dialog.event_generate("<KeyPress>", keysym="semicolon", state=0x4)
        dialog.event_generate("<KeyRelease>", keysym="Control_L", state=0x4)
        self.root.update()
        self.assertFalse(self.app._binding_capture.ready)
        dialog.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.app.binding_dialog)
        self.app._persist_preferences.assert_not_called()

    def test_stale_review_cannot_save_then_record_again_uses_current_bindings(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.app.app_settings = self.app.app_settings.with_reshade_binding(None)
        self.button("Save shortcut").invoke()
        self.app._persist_preferences.assert_not_called()
        self.assertEqual(str(self.button("Save shortcut").cget("state")), "disabled")
        self.button("Record again").invoke()
        dialog.event_generate("<KeyPress>", keysym="F11")
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.button("Save shortcut").invoke()
        settings = self.app._persist_preferences.call_args.args[0]
        self.assertIsNone(settings.reshade_binding)
        self.assertEqual(settings.action_bindings["panel"].key, "F11")

    def test_explicit_modified_escape_and_middle_mouse_share_record_review_save(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        escape = self.button("Use Escape as the shortcut")
        escape.event_generate("<ButtonPress-1>", state=0x4, x=10, y=10)
        escape.event_generate("<ButtonRelease-1>", state=0x4, x=10, y=10)
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape", ctrl=True))
        self.button("Save shortcut").invoke()
        self.assertEqual(self.app._persist_preferences.call_args.args[0].action_bindings["panel"], EditorBinding("Escape", ctrl=True))
        dialog = self.open_recorder()
        dialog.event_generate("<ButtonPress-2>", state=0x20000 | 0x1)
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("MiddleMouse", alt=True, shift=True))
        self.button("Cancel").invoke()

    def test_dialog_key_handler_refuses_win_modifier_held_before_opening(self):
        dialog = self.open_recorder()
        with patch("dolly.ui.binding_capture.SideMouseCapture.keyboard_blocked", return_value=True):
            dialog.event_generate("<KeyPress>", keysym="F24")
            self.root.update()
        self.assertFalse(self.app._binding_capture.ready)
        self.assertIn("Windows", self.app._binding_capture.error)
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)

    def test_review_escape_keyboard_choice_drops_modifiers_from_previous_attempt(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        escape = self.button("Use Escape as the shortcut")
        escape.event_generate("<ButtonPress-1>", state=0x4, x=10, y=10)
        escape.event_generate("<ButtonRelease-1>", state=0x4, x=10, y=10)
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape", ctrl=True))
        self.button("Record again").invoke()
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("F24"))
        for _ in range(8):
            focused = dialog.focus_get()
            if focused is escape:
                break
            (focused or dialog).event_generate("<KeyPress>", keysym="Tab")
            self.root.update()
        self.assertIs(dialog.focus_get(), escape, "The Escape option must be reachable from review by Tab.")
        escape.event_generate("<KeyPress>", keysym="space")
        escape.event_generate("<KeyRelease>", keysym="space")
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape"))
        self.app._persist_preferences.assert_not_called()
        self.button("Save shortcut").invoke()
        self.assertEqual(self.app._persist_preferences.call_args.args[0].action_bindings["panel"], EditorBinding("Escape"))

    def test_busy_and_playback_block_recording_before_modal_or_listener(self):
        for attribute in ("busy", "playing"):
            setattr(self.app, attribute, True)
            self.app._record_binding("capture")
            self.assertIsNone(self.app.binding_dialog)
            setattr(self.app, attribute, False)

    def test_capture_save_failures_keep_settings_and_invalidate_queued_old_presses(self):
        from dolly.ui.binding_capture import binding_settings
        original = self.app.app_settings
        proposed = binding_settings(original, "capture", EditorBinding("Mouse5"))
        for phase in ("stop", "save"):
            with self.subTest(phase=phase):
                helper = Mock(running=True, is_game_focused=Mock(return_value=True))
                helper.stop.side_effect = OSError("listener failed") if phase == "stop" else lambda: setattr(helper, "running", False)
                self.app.capture_hotkey = helper
                self.app.hotkey_enabled.set(True)
                old_generation = self.app.capture_generation
                self.app.capture_here = Mock()
                self.submit.reset_mock()
                with patch("dolly.gui.save_settings", side_effect=OSError("disk unavailable")) as save:
                    self.app._persist_preferences(proposed, "Fixture saved")
                    self.assertFalse(self.app.hotkey_enabled.get())
                    self.assertEqual(self.app.capture_generation, old_generation + 1)
                    worker = self.submit.call_args.args[1]
                    with self.assertRaises(OSError):
                        worker()
                    if phase == "stop":
                        save.assert_not_called()
                    else:
                        save.assert_called_once()
                self.assertIs(self.app.app_settings, original)
                self.assertEqual(self.app.capture_binding, original.capture_binding)
                self.app._handle_capture_hotkey(old_generation)
                self.app.capture_here.assert_not_called()

    def test_clearing_capture_disables_listener_and_keeps_unbound_action(self):
        from dolly.ui.binding_capture import binding_settings
        helper = Mock(running=True)
        self.app.capture_hotkey = helper
        self.app.hotkey_enabled.set(True)
        proposed = binding_settings(self.app.app_settings, "capture", None)
        self.submit.reset_mock()
        self.app._persist_preferences(proposed, "Fixture unbound")
        worker, complete = self.submit.call_args.args[1:3]
        with patch("dolly.gui.save_settings") as save, patch("dolly.gui.editor_session.configure"):
            worker()
            complete(None)
        helper.stop.assert_called_once()
        save.assert_called_once()
        self.assertIsNone(self.app.capture_hotkey)
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertIsNone(self.app.app_settings.action_bindings["capture"])

    def test_unrelated_bind_change_keeps_running_capture_listener(self):
        from dolly.ui.binding_capture import binding_settings
        helper = Mock(running=True)
        self.app.capture_hotkey = helper
        self.app.hotkey_enabled.set(True)
        generation = self.app.capture_generation
        proposed = binding_settings(self.app.app_settings, "panel", EditorBinding("F24"))
        self.submit.reset_mock()
        self.app._persist_preferences(proposed, "Fixture changed")
        worker, complete = self.submit.call_args.args[1:3]
        with patch("dolly.gui.save_settings"), patch("dolly.gui.editor_session.configure"):
            worker()
            complete(None)
        helper.stop.assert_not_called()
        self.assertIs(self.app.capture_hotkey, helper)
        self.assertTrue(self.app.hotkey_enabled.get())
        self.assertEqual(self.app.capture_generation, generation)
        self.assertEqual(self.app.app_settings.action_bindings["panel"].key, "F24")

    def test_reset_conflict_is_visible_in_status_without_popup_or_displacement(self):
        bindings = dict(self.app.app_settings.action_bindings)
        bindings["move_fast"] = EditorBinding("F24")
        bindings["move_up"] = EditorBinding("Shift")
        self.app.app_settings = self.app.app_settings.with_action_bindings(bindings)
        before = self.app.app_settings
        self.app._refresh_bindings()
        self.app._persist_preferences = Mock()
        self.app._error = Mock()
        self.app.controls_view.rows["move_fast"].reset_button.invoke()
        self.assertIs(self.app.app_settings, before)
        self.app._persist_preferences.assert_not_called()
        self.app._error.assert_not_called()
        self.assertEqual(self.app.status_text.get(), self.app.binding_feedback.get())
        self.assertIn("Move up", self.app.status_text.get())
        self.assertIn("Move faster", self.app.status_text.get())
        self.assertEqual(self.app.app_settings.action_bindings["move_up"], EditorBinding("Shift"))
        self.assertEqual(self.app.app_settings.action_bindings["move_fast"], EditorBinding("F24"))

    def test_listening_face_follows_the_change_shortcut_dialog(self):
        # Wired by the integration package (PLAN-5 4.4 step 6); before that the dialog opens alone.
        # Motion off: the steady Listening edge (the boil pulse could be off when this looks).
        patch("dolly.ui.boil.motion_enabled", return_value=False).start()
        controls = self.app.controls_view
        self.open_recorder("panel")
        listening = controls._listening
        keycap = controls.binding_buttons["panel"]
        if listening is not None:
            self.assertEqual(listening, "panel")
            self.assertTrue(keycap.instate(["alternate"]))
        self.app.binding_dialog.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.app.binding_dialog)
        self.assertIsNone(controls._listening)
        self.assertFalse(keycap.instate(["alternate"]))


class ControlsHighDpiFilterTests(ControlsAppFixture):
    fixture_scale = 1.5
    fixture_full_editor = True

    def test_720p_150_percent_filter_after_all_rows_and_exact_sensitivity(self):
        self.assertAlmostEqual(self.app.ui_scale, 1.5, delta=.02)
        ControlsRecorderIntegrationTests._check_filter_after_reaching_all_controls(self)


if __name__ == "__main__":
    unittest.main()
