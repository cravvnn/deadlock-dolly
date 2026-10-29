"""Settings page (Figma [design reference]): one column like Replay's, actions, info texts and reveal (PLAN-5 5.2).

USER-ADDITIONS UPDATE 17.4 (Kara's comment #4): Updates, Troubleshooting & Recovery, Game & Files,
Replay Graphics Profiles and ReShade, top to bottom in one 613 px column.
"""
from dataclasses import fields
import re
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme
from dolly.graphics_profiles_ui import PROFILES_INFO
from dolly.ui import kit_controls
from dolly.ui.kit_page import KitPage
from dolly.ui.scale import px, ui_scale
from dolly.ui.settings_page import SUBTITLE, TITLE, UPDATES_INFO, SettingsActions, SettingsPage, SettingsState

EMPTY_LIBRARY = {"format": 1, "selected": "", "profiles": []}
LONG_PATH = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Deadlock\\game\\bin\\win64\\deadlock.exe"
# The cards top to bottom (UPDATE 17.4) and their rects in design px at the 613 px column: 15 apart; each
# card as tall as in the frame, except Updates, whose status line fits one line at 613 (two at 297: 180).
CARDS = (("Updates", "updates_card", (0, 0, 613, 164)),
         ("Troubleshooting & Recovery", "tools_card", (0, 179, 613, 392)),
         ("Game & Files", "game_card", (0, 407, 613, 592)),
         ("Replay Graphics Profiles", "profiles_card", (0, 607, 613, 823)),
         ("NVIDIA DLSS", "dlss_card", (0, 838, 613, 978)),
         ("ReShade", "reshade_card", (0, 993, 613, 1209)),
         ("Discord Rich Presence", "presence_card", (0, 1224, 613, 1348)),
         ("Replay Mod Overrides", "mods_card", (0, 1363, 613, 1503)))
# Every button of the page, in reading order, and the action it runs.
BUTTONS = (("Check for updates", "check_updates"), ("Startup controls...", "open_advanced_startup"),
           ("Recover configuration...", "recover_game_config"), ("Export diagnostics", "diagnostics"),
           ("Browse game...", "browse_game"), ("Browse folder...", "browse_replay_folder"),
           ("Save paths", "save_layout_paths"), ("Display launch options...", "open_launch_options"),
           ("Save current graphics...", "capture"), ("Import video.txt...", "import_file"),
           ("Preview changes...", "preview"), ("Rename...", "rename"), ("Delete...", "delete"),
           ("Browse runtime...", "browse_reshade"), ("Enable ReShade", "configure_reshade"),
           ("Disable for this session", "disable_reshade"), ("Forget runtime", "forget_reshade"),
           ("Browse FX library...", "browse_reshade_library"), ("Menu shortcut...", "show_reshade_keybinds"))
# What the app and the desktop self-test read from the view (gui_layout.build_settings).
EXPORTS = ("auto_updates", "graphics_profiles", "reshade_browse_button", "reshade_library_button", "reshade_card",
           "reshade_configure_button", "reshade_disable_button", "reshade_forget_button", "reshade_path_entry",
           "settings_page", "update_check_button", "update_status")


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def fire(widget, sequence):
    """Run ``widget``'s own ``sequence`` binding (a generated key event needs the focus a hidden test never takes)."""
    script = str(widget.tk.call("bind", widget._w, sequence))
    commands = re.findall(r"\[(\S+) %#", script)
    if not commands:
        raise AssertionError(f"{widget} has no {sequence} binding")
    for command in commands:
        widget.tk.call(command, "0", "1", "0", "0", "0", "0", "0", "0", "0", "0", "\r", "0", "Return", "36",
                       widget._w, "2", "0", "0", "0")


class SettingsCase(unittest.TestCase):
    """The Settings page on its own root, with recorded actions (helpers)."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.calls = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        library = patch("dolly.graphics_profiles.load_library", return_value=EMPTY_LIBRARY)
        library.start()
        self.addCleanup(library.stop)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback raised an exception")

    def actions(self):
        def record(name):
            return lambda *args, **kwargs: self.calls.append((name, args))
        return SettingsActions(**{f.name: record(f.name) for f in fields(SettingsActions)})

    def build(self, *, size=(948, 591), scaling=None, parent=None):
        """``size`` is the page's design px (948 = 930 px of content + the 18 px rail gutter); None = withdrawn."""
        if scaling is not None:
            # Tk keeps the scaling per display, for every later root of this process too: put it back.
            self.addCleanup(self.root.tk.call, "tk", "scaling", self.root.tk.call("tk", "scaling"))
            self.root.tk.call("tk", "scaling", 96 * scaling / 72)
        gui_theme.initialize(self.root)
        s = ui_scale(self.root)
        if size is not None:
            self.root.deiconify()
            self.root.geometry(f"{px(s, size[0])}x{px(s, size[1])}")
        var = lambda value="": tk.StringVar(self.root, value)  # noqa: E731
        self.state = SettingsState(game_path=var(LONG_PATH), replay_folder=var(), reshade_runtime_path=var(),
                                   reshade_status_text=var("Choose the ReShade runtime to enable its in-game menu."),
                                   show_log=tk.BooleanVar(self.root, False), auto_updates_initial=True)
        view = SettingsPage(parent or self.root, self.state, self.actions(), root=self.root,
                            on_error=lambda *args: self.calls.append(("on_error", args)))
        if size is not None:
            self.root.update()
        else:
            self.root.update_idletasks()
        view.settings_page.layout_now()
        self.root.update_idletasks()
        return view

    def design(self, page, rect):
        return tuple(value / page.scale for value in rect)

    def assertRect(self, page, rect, expected, tolerance=1):
        for actual, design in zip(self.design(page, rect), expected):
            self.assertLessEqual(abs(actual - design), tolerance, (self.design(page, rect), expected))

    def in_view(self, page, rect):
        view = page.canvas.canvasy(0)
        return view <= rect[1] and rect[3] <= view + page.canvas.winfo_height()

    def button(self, view, text):
        return next(b for b in kit_controls.buttons(view.settings_page) if b.cget("text") == text)

    def resize(self, view, width, height):
        page = view.settings_page
        self.root.geometry(f"{px(page.scale, width)}x{px(page.scale, height)}")
        self.root.update()
        page.layout_now()
        self.root.update_idletasks()

    def local(self, page, widget, card):
        """``widget``'s rect in design px from its card's top-left."""
        x0, y0, x1, y1 = page.rect_of(widget)
        return self.design(page, (x0 - card.rect[0], y0 - card.rect[1], x1 - card.rect[0], y1 - card.rect[1]))

    def assertLocal(self, page, widget, card, expected, tolerance=1):
        actual = self.local(page, widget, card)
        for value, design in zip(actual, expected):
            self.assertLessEqual(abs(value - design), tolerance, (widget, actual, expected))


class SettingsLayoutTests(SettingsCase):
    def test_titles_cards_and_exports(self):
        view = self.build()
        page = view.settings_page
        self.assertEqual(TITLE, "Settings")
        self.assertEqual(SUBTITLE, "Set the game and replay paths, replay graphics profiles, ReShade and update checks.")
        self.assertIsInstance(page, KitPage)
        # One column like Replay's (UPDATE 17.4): no column spec, no side column, Updates first.
        self.assertIsNone(page.spec)
        self.assertFalse(page.two_columns)
        with self.assertRaises(ValueError):
            page.columns()
        self.assertEqual([(card.title, card.column) for card in page.cards], [(title, None) for title, _, _ in CARDS])
        self.assertEqual([getattr(view, name) for _, name, _ in CARDS], page.cards)
        self.assertEqual(page.hosts, [])
        self.assertIsNone(page.dock)        # nothing pinned: the whole page scrolls as one
        self.assertIsNotNone(page.fade)     # and fades at its cut edges (UPDATE 9.2)
        for name in EXPORTS:
            self.assertTrue(hasattr(view, name), name)
        self.assertIs(view.mods_card, page.cards[-1])
        self.assertFalse(hasattr(view, "controls_disclosure"))
        self.assertFalse(hasattr(view.graphics_profiles, "app"))
        self.assertEqual(str(view.reshade_path_entry.cget("textvariable")), str(self.state.reshade_runtime_path))
        # Every button is a kit button with today's text, in reading order (N21).
        self.assertEqual([b.cget("text") for b in kit_controls.buttons(page)], [text for text, _ in BUTTONS])
        self.assertFalse([w for w in walk(page) if isinstance(w, ttk.Button)])
        # The section label and the help texts are gone from the cards; the notes are bubbles now.
        texts = [card.host.itemcget(item, "text") for card in page.cards for item in card.host.find_all()
                 if card.host.type(item) == "text"]
        self.assertNotIn("SETTINGS", texts)
        self.assertNotIn(UPDATES_INFO, texts)
        self.assertNotIn(PROFILES_INFO, texts)
        self.assertEqual(view.profiles_card.info.cget("text"), PROFILES_INFO)
        self.assertEqual(view.updates_info.cget("text"), UPDATES_INFO)
        self.assertEqual(UPDATES_INFO, "Nothing downloads or installs until you choose Update now.")
        self.assertIsNone(view.game_card.info)
        self.assertIsNone(view.updates_card.info)

    def check_column(self, scaling, tolerance):
        # Replay's column: every card at x 0, as wide as the column (613), exactly 15 apart, top to bottom.
        view = self.build(scaling=scaling)
        page = view.settings_page
        self.assertEqual(page.scale, scaling)
        self.assertFalse(page.two_columns)
        self.assertEqual(page.content_width, px(page.scale, 613))
        self.assertEqual(int(page.canvas.cget("width")), px(page.scale, 613) + px(page.scale, page.GUTTER))
        rects = [card.rect for card in page.cards]
        self.assertEqual({(r[0], r[2]) for r in rects}, {(0, page.content_width)})
        self.assertEqual(rects[0][1], 0)
        self.assertEqual({b[1] - a[3] for a, b in zip(rects, rects[1:])}, {px(page.scale, 15)})
        for title, name, (x0, y0, x1, y1) in CARDS:
            card = getattr(view, name)
            with self.subTest(card=title):
                # Heights are the frame's; tops add up the cards above (a px or two of rounding at 150 %).
                self.assertLessEqual(abs((card.rect[3] - card.rect[1]) / page.scale - (y1 - y0)), tolerance)
                self.assertRect(page, card.rect, (x0, y0, x1, y1), 2 * tolerance)
        self.assertEqual(page.content_height, rects[-1][3])
        self.assertLessEqual(abs(page.content_height / page.scale - 1503), 2 * tolerance)
        # The fields keep the kit's 30 px at this scale; checkboxes sit on the middle of their 18 px row.
        for widget in (view.game_path_entry, view.graphics_profiles.combo, view.reshade_path_entry):
            rect = self.design(page, page.rect_of(widget))
            self.assertLessEqual(abs(rect[3] - rect[1] - 30), tolerance, (widget, rect))
        for widget, card, row_top in ((view.auto_updates_checkbox, view.updates_card, 54),
                                      (view.show_log_checkbox, view.tools_card, 174)):
            rect = self.local(page, widget, card)
            self.assertLessEqual(abs((rect[1] + rect[3]) / 2 - (row_top + 9)), tolerance, (widget, rect))

    def test_cards_stack_in_one_column_like_replay(self):
        self.check_column(1.0, 1)

    def test_cards_stack_in_one_column_like_replay_at_150_percent(self):
        self.check_column(1.5, 2)

    def test_rows_follow_the_frame(self):
        view = self.build()
        page = view.settings_page
        game, profiles, shade = view.game_card, view.profiles_card, view.reshade_card
        updates, tools = view.updates_card, view.tools_card
        # Fixed 125 label column, the field fills to 457, the fixed 125 Browse button at 467 (card px).
        self.assertLocal(page, view.game_path_entry, game, (156, 54, 457, 84))
        self.assertLocal(page, self.button(view, "Browse game..."), game, (467, 54, 592, 84))
        self.assertLocal(page, view.replay_folder_entry, game, (156, 94, 457, 124))
        self.assertLocal(page, self.button(view, "Save paths"), game, (156, 134))
        self.assertLocal(page, view.graphics_profiles.combo, profiles, (156, 54, 592, 84))
        self.assertLessEqual(abs(self.local(page, self.button(view, "Delete..."), profiles)[1] - 134), 1)
        self.assertLessEqual(abs(self.local(page, view.graphics_profiles.status_text, profiles)[1] - 179), 1)
        self.assertLocal(page, view.reshade_path_entry, shade, (156, 54, 457, 84))
        self.assertLocal(page, view.reshade_browse_button, shade, (467, 54, 592, 84))
        enable = page.rect_of(view.reshade_configure_button)
        self.assertLocal(page, view.reshade_configure_button, shade, (156, 94))
        self.assertEqual(page.rect_of(view.reshade_disable_button)[:2], (enable[2] + px(page.scale, 10), enable[1]))
        self.assertLocal(page, view.reshade_library_button, shade, (156, 134))
        self.assertLessEqual(abs(self.local(page, view.reshade_status, shade)[1] - 179), 1)
        # Updates: the checkbox row (18), its info icon 5 px after the label, the hugging button, the status.
        check = self.local(page, view.auto_updates_checkbox, updates)
        self.assertLocal(page, view.auto_updates_checkbox, updates, (21, 54), 2)
        self.assertLessEqual(abs(self.local(page, view.updates_info, updates)[0] - (check[2] + 5)), 1)
        self.assertLocal(page, view.update_check_button, updates, (21, 82))
        self.assertLessEqual(abs(self.local(page, view.update_status_text, updates)[1] - 127), 1)
        # Troubleshooting: one hugging button per row (as the frame draws them), then Show log.
        for text, y in (("Startup controls...", 54), ("Recover configuration...", 94), ("Export diagnostics", 134)):
            self.assertLocal(page, self.button(view, text), tools, (21, y))
        self.assertLessEqual(abs(self.local(page, view.show_log_checkbox, tools)[1] - 174), 2)
        # A hugging button is its label (Figma's text box) + 13 px each side.
        save = self.button(view, "Save paths")
        self.assertLessEqual(abs(save.winfo_width() - (kit_controls.fonts.hug_width(page, "body", "Save paths")
                                                       + 2 * px(page.scale, 13))), 1)

    def test_one_column_at_every_width(self):
        view = self.build()
        page = view.settings_page
        for width in (1400, 948, 864 + 18, 700, 560):
            self.resize(view, width, 591)
            self.assertFalse(page.two_columns, width)
            column = min(px(page.scale, 613), page.winfo_width() - px(page.scale, page.GUTTER))
            self.assertEqual(page.content_width, column, width)
            rects = [card.rect for card in page.cards]
            self.assertEqual({(r[0], r[2]) for r in rects}, {(0, column)}, width)   # never a side column
            self.assertEqual(sorted(rects, key=lambda r: r[1]), rects, width)      # Updates first, ReShade last
            # Left-aligned: the backdrop right of the column is the static side canvas.
            self.assertEqual(int(page.canvas.cget("width")), column + px(page.scale, page.GUTTER), width)

    def test_status_texts_wrap_and_grow_their_cards(self):
        view = self.build()
        page = view.settings_page
        updates, shade = view.updates_card.rect, view.reshade_card.rect
        view.update_status.set("Update installation is available in the packaged Windows build. Dolly downloads "
                               "the release, checks it and asks before it installs; restart Dolly after it finishes "
                               "to use the new version.")
        self.state.reshade_status_text.set("ReShade runtime selected. Launch a replay through Dolly, then press "
                                           "Enable ReShade (a live session enables it automatically).")
        page.layout_now()
        self.assertGreater(view.updates_card.rect[3], updates[3])
        self.assertGreater(view.reshade_card.rect[3], shade[3])
        # Everything below a grown card moves down with it, 15 apart.
        rects = [card.rect for card in page.cards]
        self.assertEqual({b[1] - a[3] for a, b in zip(rects, rects[1:])}, {px(page.scale, 15)})
        self.assertEqual(page.content_height, view.mods_card.rect[3])
        self.assertEqual(view.update_status_text.cget("text"), view.update_status.get())

    def test_paths_show_elided_until_the_field_is_edited(self):
        view = self.build()
        entry, cover = view.game_path_entry, view.game_path_cover
        self.root.update()
        self.assertEqual(cover.winfo_manager(), "place")
        shown = cover.cget("text")
        self.assertTrue(shown.endswith("\u2026") and LONG_PATH.startswith(shown[:-1]), shown)
        self.assertLessEqual(cover.winfo_width(), entry.winfo_width())
        entry.event_generate("<FocusIn>")
        self.assertEqual(cover.winfo_manager(), "")
        entry.event_generate("<FocusOut>")
        self.assertEqual(cover.winfo_manager(), "place")
        self.state.game_path.set("C:\\deadlock.exe")
        self.root.update()
        self.assertEqual(cover.winfo_manager(), "")
        self.assertEqual(self.state.game_path.get(), "C:\\deadlock.exe")
        for entry, cover in ((view.replay_folder_entry, view.replay_folder_cover),
                             (view.reshade_path_entry, view.reshade_path_cover)):
            self.assertIs(cover.master, entry)
            self.assertEqual(str(entry.cget("style")), "Kit.TEntry")


class SettingsActionTests(SettingsCase):
    def test_every_settings_action_is_reachable_from_a_control(self):
        view = self.build()
        panel = view.graphics_profiles
        for name in ("capture", "import_file", "preview", "rename", "delete", "select"):
            setattr(panel, name, lambda name=name: self.calls.append((name, ())))
        self.calls.clear()
        for text, name in BUTTONS:
            button = self.button(view, text)
            if name == "disable_reshade":
                before = list(self.calls)
                self.assertEqual(button.invoke(), "")      # created disabled: nothing runs
                self.assertEqual(self.calls, before)
                button.configure(state="normal")
            button.invoke()
            self.assertEqual(self.calls[-1][0], name, text)
        self.state.reshade_runtime_path.set("C:/ReShade/ReShade64.dll")
        fire(view.reshade_path_entry, "<Return>")
        self.assertEqual(self.calls[-1], ("select_reshade_runtime", ("C:/ReShade/ReShade64.dll",)))
        view.auto_updates_checkbox.invoke()
        self.assertEqual(self.calls[-1][0], "save_update_preference")
        self.assertFalse(view.auto_updates.get())
        view.show_log_checkbox.invoke()
        self.assertEqual(self.calls[-1][0], "toggle_log")
        self.assertTrue(self.state.show_log.get())
        panel.combo.event_generate("<<ComboboxSelected>>")
        self.root.update_idletasks()   # the theme clears the dropdown's selection at idle
        self.assertEqual(self.calls[-1][0], "select")
        view.mod_overrides_checkbox.invoke()
        self.assertTrue(view.allow_mod_overrides.get())
        self.assertEqual(self.calls[-1][0], "save_mod_preference")
        view.discord_presence_checkbox.invoke()
        self.assertFalse(view.discord_presence.get())
        self.assertEqual(self.calls[-1][0], "save_discord_preference")
        view.dlss_checkbox.invoke()
        self.assertTrue(view.nvidia_dlss.get())
        self.assertEqual(self.calls[-1][0], "save_dlss_preference")
        reached = {name for name, _args in self.calls}
        self.assertEqual({f.name for f in fields(SettingsActions)} - reached, set())

    def test_the_reshade_buttons_keep_the_app_state_surface(self):
        from dolly.gui import _set_widget_state
        view = self.build()
        disable, configure = view.reshade_disable_button, view.reshade_configure_button
        self.assertEqual(str(disable.cget("state")), "disabled")
        self.assertTrue(disable.instate(["disabled"]))
        self.assertEqual(str(configure.cget("state")), "normal")
        _set_widget_state(configure, "disabled")
        self.assertEqual(configure.cget("state"), "disabled")
        _set_widget_state(disable, "normal")
        self.assertFalse(disable.instate(["disabled"]))
        # update_ui relabels the update button; it hugs its new label.
        button, page = view.update_check_button, view.settings_page
        width = button.winfo_width()
        button.configure(text="View update")
        page.layout_now()
        self.assertEqual(button.cget("text"), "View update")
        self.assertNotEqual(button.winfo_width(), width)
        self.assertLessEqual(abs(button.winfo_width()
                                 - (kit_controls.fonts.hug_width(page, "body", "View update") + 2 * px(page.scale, 13))), 1)

    def test_reveal_brings_each_reshade_control_into_view(self):
        view = self.build(size=(948, 300))
        page = view.settings_page
        for name in ("reshade_path_entry", "reshade_configure_button", "reshade_disable_button",
                     "reshade_forget_button", "update_check_button"):
            page.scroll_to(0)
            target = getattr(view, name)
            page.reveal(target)
            self.root.update()
            self.assertTrue(self.in_view(page, page.rect_of(target)), name)
            self.assertGreater(target.winfo_width(), 1)

    def test_reveal_sizes_the_reshade_controls_in_a_withdrawn_window(self):
        # desktop.py's hidden bundle self-test: only the sizes are checked there.
        holder = ttk.Frame(self.root, width=1, height=200)
        holder.pack_propagate(False)
        holder.pack(anchor="nw")
        view = self.build(size=None, parent=holder)
        for name in ("reshade_path_entry", "reshade_configure_button", "reshade_disable_button",
                     "reshade_forget_button"):
            target = getattr(view, name)
            view.settings_page.reveal(target)
            self.root.update_idletasks()
            self.assertGreater(target.winfo_width(), 1, name)
            self.assertGreater(target.winfo_height(), 1, name)

    def test_tab_reads_the_page_top_to_bottom(self):
        view = self.build(size=(948, 300))
        page = view.settings_page
        order, widget = [], view.auto_updates_checkbox
        for _ in range(60):
            order.append(widget)
            widget = widget.tk_focusNext()
            if widget is None or widget is view.auto_updates_checkbox:
                break
        inputs = [view.auto_updates_checkbox, view.show_log_checkbox, view.game_path_entry,
                  view.replay_folder_entry, view.graphics_profiles.combo, view.reshade_path_entry]
        self.assertEqual([w for w in order if w in inputs], inputs)
        # The canvas buttons' groups interleave in reading order.
        proxies = [view.update_check_button.group.proxy, self.button(view, "Startup controls...").group.proxy,
                   self.button(view, "Browse game...").group.proxy, self.button(view, "Delete...").group.proxy,
                   view.reshade_forget_button.group.proxy, view.reshade_library_button.group.proxy]
        positions = [order.index(proxy) for proxy in proxies]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(order.index(view.update_check_button.group.proxy), order.index(view.show_log_checkbox))
        self.assertLess(order.index(self.button(view, "Export diagnostics").group.proxy),
                        order.index(view.show_log_checkbox))
        self.assertLess(order.index(view.show_log_checkbox), order.index(view.game_path_entry))
        self.assertLess(order.index(view.reshade_path_entry), order.index(view.reshade_forget_button.group.proxy))
        for widget in walk(page):
            if isinstance(widget, (tk.Canvas, ttk.Label)) and getattr(widget, "_kit_group", None) is None:
                self.assertIn(str(widget.cget("takefocus")), ("0", ""), widget)
        # Off-screen controls (ReShade is last now) are revealed when Tab reaches them.
        page.scroll_to(0)
        self.assertFalse(self.in_view(page, page.rect_of(view.reshade_path_entry)))
        script = str(view.reshade_path_entry.cget("takefocus"))
        self.assertEqual(str(self.root.tk.call(script, str(view.reshade_path_entry))), "1")
        self.assertTrue(self.in_view(page, page.rect_of(view.reshade_path_entry)))

    def test_the_page_stays_within_its_window_and_input_budgets(self):
        view = self.build()
        page = view.settings_page
        counts = page.window_counts()
        if page.sheet is not None:
            self.assertEqual(counts["page"], 1)       # the sheet (PR2)
            self.assertEqual(counts["moving"], 1)
        else:                                        # without window regions: one window per card
            self.assertEqual(counts["page"], len(page.cards))
        self.assertEqual(counts["inputs"], 9)         # Includes optional DLSS; stays within the kit's <=10 budget.
        for widget, element in page._widgets.items():
            self.assertIs(widget.master, element.card.host)
            self.assertEqual(widget.winfo_manager(), "place")
        for card in page.cards:
            for widget in walk(card.host):
                self.assertNotIsInstance(widget, (ttk.Frame, tk.Label, ttk.Button))
                if isinstance(widget, ttk.Label):
                    self.assertIsInstance(widget.master, ttk.Entry)   # the at-rest covers only
                elif isinstance(widget, ttk.Widget):
                    self.assertTrue(str(widget.cget("style")).startswith("Kit."), widget)
        self.assertTrue(page.scrollbar.winfo_ismapped())   # 1054 > 562: the page scrolls as one

    def test_the_info_bubbles_open_on_the_page(self):
        view = self.build()
        page = view.settings_page
        for icon, text in ((view.profiles_card.info, PROFILES_INFO), (view.updates_info, UPDATES_INFO)):
            icon.invoke()
            self.assertTrue(icon.shown)
            tip, _item = icon._bubble
            label = next(i for i in tip.find_all() if tip.type(i) == "text")
            self.assertEqual(tip.itemcget(label, "text"), text)
            icon._hide_bubble()
            self.assertFalse(icon.shown)
        self.assertEqual(page.window_counts()["page"], 1 if page.sheet is not None else len(page.cards))


class SettingsScrollTests(SettingsCase):
    def test_the_whole_page_scrolls_as_one_and_only_while_it_overflows(self):
        view = self.build(size=(948, 400))
        page = view.settings_page
        # Two cards in view (without window regions a card scrolled out of view is unmapped, its root y stale).
        tops = [card.host.winfo_rooty() for card in (view.updates_card, view.tools_card)]
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            self.assertEqual(page._wheel(SimpleNamespace(widget=view.game_card.host, delta=-120)), "break")
            self.root.update()
            moved = int(page.canvas.canvasy(0))
            self.assertEqual(moved, px(page.scale, 60))
            # The cards move together (one sheet), and the dropdown keeps its own wheel.
            self.assertEqual([tops[0] - view.updates_card.host.winfo_rooty(),
                              tops[1] - view.tools_card.host.winfo_rooty()], [moved, moved])
            self.assertIsNone(page._wheel(SimpleNamespace(widget=view.graphics_profiles.combo, delta=-120)))
            self.assertEqual(int(page.canvas.canvasy(0)), moved)
            # A window tall enough for all five cards (1054 + the gap above the footer) does not scroll.
            self.root.wm_maxsize(px(page.scale, 2000), px(page.scale, 2000))
            self.resize(view, 948, 1600)
            self.assertGreaterEqual(page.canvas.winfo_height(), page.content_height)
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
            self.assertFalse(page.scrollbar.winfo_ismapped())
            self.assertIsNone(page._wheel(SimpleNamespace(widget=view.game_card.host, delta=-120)))

    def test_the_backdrop_shows_between_the_cards_and_right_of_the_column(self):
        view = self.build()
        page, s = view.settings_page, view.settings_page.scale
        areas = page.open_areas()
        top = page.canvas.canvasy(0)
        # The 15 px gap under Updates runs across the scrolling canvas; the rail gutter right of the cards.
        gap = view.updates_card.rect[3] - top
        self.assertTrue(any(a[0] == 0 and a[2] >= px(s, 613) and a[1] <= gap < a[3] for a in areas))
        self.assertTrue(any(a[0] <= px(s, 613) and a[2] >= px(s, 613 + 18) for a in areas))
        for x0, y0, x1, y1 in areas:
            for card in page.cards:
                cx0, cy0, cx1, cy1 = card.rect
                self.assertFalse(x0 < cx1 and x1 > cx0 and y0 < cy1 - top and y1 > cy0 - top, card.title)
        # Right of the column is the static side canvas (backdrop only), as wide as the rest of the page.
        self.assertEqual(page.side.winfo_x(), page.canvas.winfo_width())
        self.assertEqual(page.side.winfo_width(), page.winfo_width() - page.canvas.winfo_width())


class SettingsInAppTests(unittest.TestCase):
    """The page as gui_layout.build_settings builds it in the real window."""

    def setUp(self):
        from dolly.gui import DollyApp
        from dolly.settings import AppSettings
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        patch.object(DollyApp, "_submit").start()
        patch.object(DollyApp, "_load_app_settings", return_value=AppSettings()).start()
        patch("dolly.graphics_profiles.load_library", return_value=EMPTY_LIBRARY).start()
        self.addCleanup(patch.stopall)
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.destroy()

    def test_the_app_exports_and_drives_the_kit_page(self):
        app = self.app
        self.assertIsInstance(app.settings_page, KitPage)
        self.assertIs(app.settings_page, app.settings_view.settings_page)
        for name in EXPORTS:
            self.assertIs(getattr(app, name), getattr(app.settings_view, name), name)
        self.assertEqual(str(app.settings_view.show_log_checkbox.cget("variable")), str(app.show_log))
        app.show_log.set(True)   # the footer link and the log window share the checkbox's variable
        self.assertTrue(app.settings_view.show_log_checkbox.instate(["selected"]))
        app.show_log.set(False)
        self.root.deiconify()
        self.root.geometry(app._main_size(1200, 810))
        app.notebook.select(app.settings_tab)
        self.root.update()
        page = app.settings_page
        page.layout_now()
        # One column like Replay's (UPDATE 17.4): Updates on top, 613 wide, at Replay's left edge.
        self.assertFalse(page.two_columns)
        self.assertIsNone(page.spec)
        self.assertEqual(app.settings_view.updates_card.rect[:2], (0, 0))
        self.assertEqual(page.content_width, px(page.scale, 613))
        self.assertIs(page.cards[0], app.settings_view.updates_card)
        self.assertIs(page.cards[-1], app.settings_view.mods_card)
        settings_x = page.canvas.winfo_rootx()
        app.notebook.select(app.setup_tab)
        self.root.update()
        replay = app.library_page
        self.assertEqual(replay.canvas.winfo_rootx(), settings_x)
        self.assertEqual(replay.card_width, page.content_width)

    def test_dlss_toggle_saves_for_next_launch_and_rolls_back_a_failed_save(self):
        app = self.app
        self.assertIs(app.nvidia_dlss, app.settings_view.nvidia_dlss)
        with patch('dolly.gui.save_settings') as save:
            app.settings_view.dlss_checkbox.invoke()
        self.assertTrue(app.app_settings.nvidia_dlss)
        self.assertTrue(save.call_args.args[0].nvidia_dlss)
        app.controller.launch = unittest.mock.Mock()
        with patch('dolly.gui.save_settings', side_effect=OSError('disk full')), patch.object(app, '_error') as error:
            app.settings_view.dlss_checkbox.invoke()
        self.assertTrue(app.nvidia_dlss.get())
        self.assertTrue(app.app_settings.nvidia_dlss)
        error.assert_called_once()
        app.controller.launch.assert_not_called()

    def test_reshade_controls_fit_the_minimum_window_once_revealed(self):
        # desktop.py's bundle self-test, shown (cloaked) at 1000 x 700.
        app, root = self.app, self.root
        root.deiconify()
        root.geometry(app._main_size(1000, 700))
        app.notebook.select(app.settings_tab)
        root.update()
        self.assertFalse(app.settings_page.two_columns)
        for name in ("reshade_path_entry", "reshade_configure_button", "reshade_disable_button",
                     "reshade_forget_button"):
            widget = getattr(app, name)
            app.settings_page.reveal(widget)
            root.update_idletasks()
            x = widget.winfo_rootx() - root.winfo_rootx()
            y = widget.winfo_rooty() - root.winfo_rooty()
            self.assertTrue(widget.winfo_ismapped(), name)
            self.assertGreaterEqual(x, 0, name)
            self.assertGreaterEqual(y, 0, name)
            self.assertLessEqual(x + widget.winfo_width(), root.winfo_width(), name)
            self.assertLessEqual(y + widget.winfo_height(), root.winfo_height(), name)

    def test_the_update_button_relabel_and_the_poll_states(self):
        from dolly.gui import _set_widget_state
        app = self.app
        app.update_check_button.configure(text="View update")
        self.assertEqual(app.update_check_button.cget("text"), "View update")
        for name in ("reshade_configure_button", "reshade_forget_button", "reshade_disable_button"):
            button = getattr(app, name)
            _set_widget_state(button, "disabled")
            self.assertEqual(button.cget("state"), "disabled")
            _set_widget_state(button, "normal")
            self.assertEqual(button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
