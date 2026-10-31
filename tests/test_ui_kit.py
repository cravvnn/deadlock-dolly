"""The page kit (PLAN-5 2.1-2.6, 5.2): column rule, a throwaway Settings demo page on the sheet model,
scrolling, reveal and focus, host cards, the desktop tokens and the hex scan of the kit's files."""
import os
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.ui import art, fade, kit_controls, kit_page, stage_page
from dolly.ui.canvas_controls import mix
from dolly.ui.kit_controls import KitStrip
from dolly.ui.kit_page import ColumnSpec, KitPage, column_layout
from dolly.ui.scale import hairline, px
from dolly.ui.stage_page import CanvasRail
from dolly.ui.tween import Animator, ease_out

ROOT = Path(__file__).resolve().parents[1]

# The five pages' column specs (PLAN-5 3.0.2).
SPECS = {
    "camera": ColumnSpec(main=613, side=297, gap=20, main_min=460, side_min=297),
    "look": ColumnSpec(main=613, side=297, gap=20, main_min=520, side_min=297),
    "export": ColumnSpec(main=613, side=302, gap=15, main_min=613, side_min=260),
    "controls": ColumnSpec(main=613, side=297, gap=20, main_min=400, side_min=297),
    "settings": ColumnSpec(main=613, side=297, gap=20, main_min=584, side_min=260),
}
UPDATES_INFO = "Nothing downloads or installs until you choose Update now."
PROFILES_INFO = "Profiles apply when Dolly starts a replay."
UPDATE_STATUS = "Checks published Latest releases; experimental pre-releases are ignored."


class FakeAnimator:
    """Stands in for the page scroller's frame loop: the test runs frames at the times it picks."""

    frame_ms = 16

    def __init__(self):
        self.now = 0.0
        self.steps = {}

    def clock(self):
        return self.now

    def start(self, key, step):
        self.steps[key] = step

    def stop(self, key):
        self.steps.pop(key, None)

    def frame(self, at):
        self.now = at
        for key, step in list(self.steps.items()):
            if not step(at):
                self.steps.pop(key, None)


class FakeBackdrop:
    def __init__(self):
        self.attached = []
        self.synced = []

    def attach(self, canvas, areas=None):
        self.attached.append((canvas, areas))

    def sync(self, canvas=None, *, redraw=False):
        self.synced.append((canvas, redraw))

    def detach(self, canvas):
        pass


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def design_columns(spec, c):
    """PLAN-5 3.0.2 in design px, written out independently of the kit."""
    need = spec.main + spec.gap + spec.side
    if c >= need:
        mw, sw = spec.main, spec.side
    else:
        sw = max(spec.side_min, c - spec.gap - spec.main)
        mw = min(spec.main, c - spec.gap - sw)
    if c >= need or mw >= spec.main_min:
        return True, mw, sw
    return False, min(c, spec.main), 0


class ColumnRuleTests(unittest.TestCase):
    def test_the_column_rule_for_every_page_and_width(self):
        table = {(name, 930): (True, 613, SPECS[name].side) for name in SPECS}
        table.update({("camera", 762): (False, 613, 0), ("look", 762): (False, 613, 0),
                      ("export", 762): (False, 613, 0), ("controls", 762): (True, 445, 297),
                      ("settings", 762): (False, 613, 0), ("export", 888): (True, 613, 260),
                      ("settings", 864): (True, 584, 260), ("export", 887): (False, 613, 0),
                      ("settings", 863): (False, 613, 0)})
        for name, spec in SPECS.items():
            for c in (930, 900, 888, 887, 864, 863, 800, 762, 700):
                expected = design_columns(spec, c)
                if (name, c) in table:
                    self.assertEqual(expected, table[name, c], (name, c))
                for scale in (1.0, 1.5):
                    with self.subTest(page=name, c=c, scale=scale):
                        two, main, side_x, block = column_layout(spec, px(scale, c), scale)
                        self.assertEqual(two, expected[0])
                        self.assertLessEqual(abs(main - px(scale, expected[1])), 1)
                        if two:
                            self.assertLessEqual(abs(side_x - px(scale, expected[1] + spec.gap)), 1)
                            self.assertLessEqual(abs(block - side_x - px(scale, expected[2])), 1)
                            self.assertLessEqual(block, px(scale, c))
                        else:
                            self.assertEqual(block, min(px(scale, c), px(scale, spec.main)))


class TokenTests(unittest.TestCase):
    NEW = ("card_edge_pinned", "field_disabled", "muted_disabled", "text_disabled", "edge_disabled",
           "accent_disabled", "curve_key")

    def test_new_tokens_are_desktop_only_valid_and_pre_mixed(self):
        tokens, native = ui_theme.TOKENS, ui_theme.NATIVE_TOKENS
        for name in self.NEW + ("curve_x",):
            self.assertRegex(tokens[name], r"^#[0-9a-f]{6}$")
        for name in self.NEW:
            self.assertNotIn(name, native)
        for base in ("field", "muted", "text", "edge", "accent"):
            self.assertEqual(tokens[base + "_disabled"], mix(tokens[base], tokens["panel"], .5), base)
        # The desktop overrides the Camera curve; the in-game panel keeps its own colour.
        self.assertNotEqual(tokens["curve_x"], native["curve_x"])

    def test_the_native_palette_and_its_header_are_unchanged(self):
        native = ui_theme.NATIVE_TOKENS
        self.assertEqual(len(native), 36)
        header = (ROOT / "native" / "src" / "dolly_ui_tokens_generated.hpp").read_text(encoding="utf-8")
        rendered = dict(re.findall(r"constexpr unsigned (\w+) = 0x([0-9a-f]{6});", header))
        self.assertEqual(rendered, {name.upper(): value[1:] for name, value in native.items()})


class HexScanTests(unittest.TestCase):
    def test_kit_files_and_the_page_helpers_take_colours_from_ui_theme(self):
        names = ["dolly/ui/kit_page.py", "dolly/ui/kit_card.py", "dolly/ui/kit_controls.py", "dolly/ui/kit_fields.py",
                 "dolly/ui/kit_styles.py", "dolly/ui/fade.py", "dolly/ui/art.py", "dolly/curve_workspace.py",
                 "dolly/screenshot_ui.py", "dolly/graphics_profiles_ui.py"]
        for name in names:
            path = ROOT / name
            if not path.exists():
                continue
            found = re.findall(r"#[0-9a-fA-F]{6}", path.read_text(encoding="utf-8"))
            self.assertEqual(found, [], f"{name} must take colours from dolly/ui_theme.py: {found}")


class KitCase(unittest.TestCase):
    """A standalone kit page on its own root (helpers)."""

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

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback raised an exception")

    def record(self, name):
        return lambda *args, **kwargs: self.calls.append(name)

    def settings_demo(self, *, shown="948x591", parent=None, theme=True):
        """The Settings frame ([design reference]) rebuilt with the kit: 613 + 20 + 297 at C 930."""
        if theme:
            gui_theme.initialize(self.root)
        if shown:
            self.root.deiconify()
            self.root.geometry(shown)
        page = KitPage(parent or self.root, columns=SPECS["settings"])
        page.pack(fill="both", expand=True)
        v = SimpleNamespace(page=page)
        main, side = page.columns()
        var = lambda value="": tk.StringVar(self.root, value)  # noqa: E731
        v.game_path, v.replay_folder, v.profile, v.runtime = var("C:/Games/deadlock.exe"), var(), var(), var()
        v.auto_updates, v.show_log = tk.BooleanVar(self.root, False), tk.BooleanVar(self.root, False)
        v.game = main.add_card("Game & Files")
        v.rows = []
        for label, variable, button in (("Deadlock executable", v.game_path, "Browse game..."),
                                        ("Replay folder", v.replay_folder, "Browse folder...")):
            row = v.game.row()
            row.label(label, width=125)
            row.gap(10)
            entry = row.widget(ttk.Entry(row.host, textvariable=variable, style="Kit.TEntry"), width="fill")
            row.gap(10)
            row.button(button, self.record(button), width=125)
            v.rows.append((row, entry))
        v.game_entry = v.rows[0][1]
        row = v.paths_row = v.game.row()
        row.gap(135)
        v.save_paths = row.button("Save paths", self.record("save_paths"))
        row.gap(10)
        row.button("Display launch options...", self.record("launch_options"))
        v.profiles = main.add_card("Replay Graphics Profiles", info=PROFILES_INFO)
        row = v.profile_row = v.profiles.row()
        row.label("Next replay session", width=125)
        row.gap(10)
        v.combo = row.widget(ttk.Combobox(row.host, textvariable=v.profile, state="readonly",
                                          values=("Use current game settings",), style="Kit.TCombobox"),
                             width="fill")
        for texts in (("Save current graphics...", "Import video.txt..."),
                      ("Preview changes...", "Rename...", "Delete...")):
            row = v.profiles.row()
            row.gap(135)
            for index, text in enumerate(texts):
                if index:
                    row.gap(10)
                row.button(text, self.record(text))
        v.profile_status = v.profiles.text("0 local profile(s). Selection applies to the next launch.", top=15)
        v.reshade = main.add_card("ReShade")
        row = v.reshade.row()
        row.label("Runtime DLL", width=125)
        row.gap(10)
        v.runtime_entry = row.widget(ttk.Entry(row.host, textvariable=v.runtime, style="Kit.TEntry"), width="fill")
        row.gap(10)
        v.browse_runtime = row.button("Browse runtime...", self.record("browse_runtime"), width=125)
        row = v.reshade.row()
        row.gap(135)
        v.enable = row.button("Enable ReShade", self.record("enable"))
        row.gap(10)
        v.disable = row.button("Disable for this session", self.record("disable"), state="disabled")
        row.gap(10)
        v.forget = row.button("Forget runtime", self.record("forget"))
        row = v.reshade.row()
        row.gap(135)
        row.button("Browse FX library...", self.record("fx"))
        row.gap(10)
        row.button("Menu shortcut...", self.record("menu"))
        v.reshade_status = v.reshade.text("Choose the ReShade runtime to enable its in-game menu.", top=15)
        v.updates = side.add_card("Updates")
        row = v.updates_row = v.updates.row(height=18)
        v.auto_check = row.widget(ttk.Checkbutton(row.host, text="Automatically check for updates",
                                                  variable=v.auto_updates, style="Kit.TCheckbutton"))
        row.gap(5)
        v.updates_info = row.info(UPDATES_INFO)
        row = v.updates.row()
        v.check = row.button("Check for updates", self.record("check"))
        v.update_status = v.updates.text(UPDATE_STATUS, top=15)
        v.tools = side.add_card("Troubleshooting & Recovery")
        for text in ("Startup controls...", "Recover configuration...", "Export diagnostics"):
            v.tools.row().button(text, self.record(text))
        row = v.tools.row(height=18)
        v.show_log_check = row.widget(ttk.Checkbutton(row.host, text="Show log", variable=v.show_log,
                                                      style="Kit.TCheckbutton"))
        page.watch_focus()
        if shown:
            self.root.update()
        else:
            self.root.update_idletasks()
        page.layout_now()
        self.root.update_idletasks()
        return v

    def d(self, page, value):
        """Page px back to design px."""
        return value / page.scale

    def assertNear(self, page, actual_px, design, tolerance=1, msg=None):
        self.assertLessEqual(abs(self.d(page, actual_px) - design), tolerance, msg or (actual_px, design))

    def need_sheet(self, page):
        if page.sheet is None:
            self.skipTest("window regions unavailable: the per-card fallback (FallbackTests)")

    def windows(self, canvas):
        return [item for item in canvas.find_all() if canvas.type(item) == "window"]

    def eased(self, page):
        animator = FakeAnimator()
        page.scroller._animator = animator
        page.scroller._clock = animator.clock
        page.scroller.measure = False
        return animator


class DemoGeometryTests(KitCase):
    def test_the_settings_frame_cards_within_a_design_pixel(self):
        v = self.settings_demo()
        page = v.page
        self.assertTrue(page.two_columns)
        expected = {v.game: (0, 0, 613, 185), v.profiles: (0, 200, 613, 216), v.reshade: (0, 431, 613, 216),
                    v.updates: (633, 0, 297, 180), v.tools: (633, 195, 297, 213)}
        for card, (x, y, w, h) in expected.items():
            x0, y0, x1, y1 = card.rect
            with self.subTest(card=card.title):
                for actual, design in ((x0, x), (y0, y), (x1 - x0, w), (y1 - y0, h)):
                    self.assertNear(page, actual, design)
        self.assertNear(page, page.content_height, 647)
        self.assertNear(page, page.content_width, 930)

    def test_rows_titles_and_cells_follow_the_frame(self):
        v = self.settings_demo()
        page = v.page
        card = v.game
        ox, oy = card.origin()
        # Title on the centre of an 18 px line 21 px in.
        tx, ty = card.host.coords(card.title_item)
        self.assertNear(page, ox + tx, 21)
        self.assertNear(page, oy + ty, 21 + 9)
        rect = page.rect_of(v.rows[0][0])
        self.assertNear(page, rect[1], 54)
        self.assertNear(page, rect[3] - rect[1], 30)
        self.assertNear(page, page.rect_of(v.rows[1][0])[1], 94)
        self.assertNear(page, page.rect_of(v.paths_row)[1], 134)
        # Fixed label 125, field fills from 156 to 457, fixed 125 button at 467.
        entry = page.rect_of(v.game_entry)
        self.assertNear(page, entry[0], 156)
        self.assertNear(page, entry[2] - entry[0], 301)
        self.assertNear(page, entry[3] - entry[1], 30)
        browse = page.rect_of(kit_controls.buttons(card.host)[0])
        self.assertNear(page, browse[0], 467)
        self.assertNear(page, browse[2] - browse[0], 125)
        self.assertNear(page, browse[3] - browse[1], 30)
        # A hugging button sits after the 135 px indent.
        self.assertNear(page, page.rect_of(v.save_paths)[0], 156)
        # Profiles: the dropdown fills to the content edge; the status 15 below the last row.
        combo = page.rect_of(v.combo)
        self.assertNear(page, combo[0], 156)
        self.assertNear(page, combo[2] - combo[0], 436)
        status = page.rect_of(v.profile_status)
        self.assertNear(page, status[1] - v.profiles.rect[1], 179)
        # The side column: Updates' option row (18), its button and the two-line status.
        self.assertNear(page, page.rect_of(v.updates_row)[1], 54)
        self.assertNear(page, page.rect_of(v.updates_row)[3] - page.rect_of(v.updates_row)[1], 18)
        self.assertNear(page, page.rect_of(v.check)[1], 82)
        self.assertNear(page, page.rect_of(v.check)[0], 633 + 21)
        self.assertNear(page, page.rect_of(v.update_status)[1], 127)
        info = page.rect_of(v.updates_info)
        check = page.rect_of(v.auto_check)
        self.assertNear(page, info[0] - check[2], 5)

    def test_a_card_title_info_icon_sits_6_px_after_the_title(self):
        v = self.settings_demo()
        card, page = v.profiles, v.page
        title = card.host.bbox(card.title_item)
        icon = card.info.box()
        self.assertNear(page, icon[0] - title[2], 6)
        self.assertEqual(icon[2] - icon[0], max(1, int(13 * page.scale + .5)))

    def test_kit_card_titles_are_15_px_like_replays(self):
        # Kara, UPDATE 25 ("no make it 15px"): every card title is the 15 px card_title role.
        from dolly.ui import fonts
        v = self.settings_demo()
        page = v.page
        self.assertTrue(page.cards)
        for card in page.cards:
            self.assertEqual(str(card.host.itemcget(card.title_item, "font")), fonts.NAMES["card_title"])
        self.assertEqual(int(fonts.font(page, "card_title").cget("size")), -px(page.scale, 15))

    def test_hidden_sections_rows_and_cards_take_no_space(self):
        v = self.settings_demo()
        page = v.page
        before = v.reshade.rect
        v.paths_row.set_visible(False)
        page.layout_now()
        self.assertEqual(v.paths_row.winfo_manager(), "")
        self.assertNear(page, v.game.rect[3] - v.game.rect[1], 185 - 40)
        self.assertNear(page, v.reshade.rect[1] - before[1], -40)
        for button in kit_controls.buttons(v.game.host)[2:]:
            self.assertFalse(button.winfo_ismapped())
        v.paths_row.pack()
        v.profiles.set_visible(False)
        page.layout_now()
        self.assertIsNone(v.profiles.rect)
        self.assertNear(page, v.reshade.rect[1], 200)
        self.assertEqual(v.profiles.winfo_manager(), "")

    def test_one_column_below_the_main_minimum(self):
        v = self.settings_demo(shown="780x591")   # C 762: one column 613 wide
        page = v.page
        self.assertFalse(page.two_columns)
        cards = (v.game, v.profiles, v.reshade, v.updates, v.tools)
        top = 0
        for card in cards:
            self.assertNear(page, card.rect[0], 0)
            self.assertNear(page, card.rect[2] - card.rect[0], 613)
            self.assertNear(page, card.rect[1], top, 2)
            top = self.d(page, card.rect[3]) + 15


class SectionAndSplitTests(KitCase):
    def build(self):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry("948x700")
        page = KitPage(self.root, columns=SPECS["controls"])
        page.pack(fill="both", expand=True)
        return page

    def test_sections_flows_splits_and_disclosures(self):
        page = self.build()
        card = page.add_card("Movement")
        first, second = card.split(("fill", 62, 255))
        for section, count in ((first, 3), (second, 2)):
            for index in range(count):
                section.row().label(f"row {index}")
        flow = card.flow(top=15)
        presets = [flow.button(name, self.record(name)) for name in ("Very slow", "Slow", "Normal", "Fast")]
        disclosure = card.disclosure("Rotation & transition", top=15)
        inner = disclosure.body.row()
        inner.label("Yaw", width=60)
        self.root.update()
        page.layout_now()
        s = page.scale
        # Side by side: the second section 62 px after the first's fill, the divider centred between them.
        split = card.elements[0]
        self.assertFalse(split.stacked)
        x0, width = card.content_box()
        self.assertEqual(second.x0, x0 + width - px(s, 255))
        self.assertEqual(split.height, 3 * px(s, 30) + 2 * px(s, 10))
        divider = card.host.coords(split.divider)
        self.assertEqual(card.host.itemcget(split.divider, "state"), "normal")
        self.assertLessEqual(abs((divider[0] + divider[2]) / 2 - (x0 + width - px(s, 255) - px(s, 31))), 2)
        # Flow buttons hug and wrap on one line here.
        boxes = [button.box() for button in presets]
        self.assertEqual(len({box[1] for box in boxes}), 1)
        for a, b in zip(boxes, boxes[1:]):
            self.assertEqual(b[0] - a[2], px(s, 10))
        # The disclosure is closed: its body takes no space; opening it grows the card.
        height = card.rect[3] - card.rect[1]
        self.assertIsNone(inner.y)
        disclosure.set_open(True)
        page.layout_now()
        self.assertEqual(card.rect[3] - card.rect[1], height + px(s, 15) + px(s, 30))
        disclosure.flip()
        page.layout_now()
        self.assertEqual(card.rect[3] - card.rect[1], height)
        # Below 717 px of content the split stacks and its divider hides.
        self.root.geometry("700x700")
        self.root.update()
        page.layout_now()
        self.assertTrue(split.stacked)
        self.assertEqual(card.host.itemcget(split.divider, "state"), "hidden")
        self.assertEqual(second.y, first.y + first.height + px(s, 15))

    def test_a_widget_must_be_created_on_its_row_host(self):
        page = self.build()
        card = page.add_card("Card")
        row = card.row()
        with self.assertRaises(ValueError):
            row.widget(ttk.Entry(self.root))
        with self.assertRaises(ValueError):
            card.block(ttk.Entry(page))

    def test_row_cells_hug_fix_fill_and_push(self):
        page = self.build()
        card = page.add_card("Cells")
        row = card.row()
        hug = row.label("Hug")
        row.gap(10)
        fixed = row.label("A fixed label that is far too long for its cell", width=60)
        row.gap(10)
        fill = row.widget(ttk.Entry(row.host, style="Kit.TEntry"), width="fill")
        row.gap(10)
        quiet = row.button("Reset", self.record("reset"), kind="quiet")
        pushed = card.row()
        left = pushed.button("Left", self.record("left"))
        pushed.push()
        right = pushed.button("Right", self.record("right"))
        self.root.update()
        page.layout_now()
        s = page.scale
        x0, width = card.content_box()
        self.assertEqual(card.host.coords(hug.item)[0], x0)
        self.assertTrue(fixed.elided)
        self.assertTrue(fixed.cget("text").startswith("A fixed"))
        self.assertLessEqual(fixed.size()[0], px(s, 60))
        quiet_box = quiet.box()
        self.assertEqual(quiet_box[2], x0 + width)
        self.assertEqual(page.rect_of(fill)[2] - card.origin()[0], quiet_box[0] - px(s, 10))
        self.assertEqual(left.box()[0], x0)
        self.assertEqual(right.box()[2], x0 + width)


class HostingTests(KitCase):
    """PERF-NOTES R1-R7 (mirrors ReplayScrollTests): one sheet, strips, rail, placed inputs."""

    def test_the_scrolling_canvas_is_the_block_and_the_gutter(self):
        backdrop = self.root._dolly_backdrop = FakeBackdrop()
        v = self.settings_demo(shown="1200x591")
        page = v.page
        self.assertEqual(page.content_width, px(page.scale, 930))   # the block never grows past the design
        self.assertEqual(page.canvas.winfo_width(), page.content_width + px(page.scale, 18))
        self.assertGreater(page.side.winfo_width(), 1)
        self.assertEqual(page.side.winfo_width(), page.winfo_width() - page.canvas.winfo_width())
        # The canvases that never scroll show the backdrop everywhere: the side and the foot (UPDATE 9).
        self.assertEqual([canvas for canvas, _ in backdrop.attached], [page.canvas, page.side, page.foot])
        self.assertIsNone(backdrop.attached[1][1])
        self.assertIsNone(backdrop.attached[2][1])

    @unittest.skipUnless(sys.platform == "win32", "window regions are a Windows feature")
    def test_one_retained_sheet_is_the_only_window_and_carries_every_card(self):
        v = self.settings_demo()
        page, canvas = v.page, v.page.canvas
        self.need_sheet(page)
        self.assertEqual(self.windows(canvas), [page._sheet_item])
        counts = page.window_counts()
        self.assertEqual(counts["page"], 1)
        self.assertLessEqual(counts["moving"], 2)
        self.assertLessEqual(counts["inputs"], 10)
        self.assertEqual(page.retained, os.environ.get("DOLLY_UI_LAYERED", "1") != "0")
        r = px(page.scale, page.CARD_RADIUS)
        for card in page.cards:
            self.assertIs(card.holder.master, page.sheet)
            self.assertIs(card.holder, card.host)
            self.assertEqual(card.holder.winfo_manager(), "place")
            self.assertEqual(str(card.holder.cget("background")), ui_theme.TOKENS["panel"])
            x, y = card.holder.winfo_x(), card.holder.winfo_y()
            self.assertEqual((x, y, card.holder.winfo_width()), (card.rect[0], card.rect[1],
                                                                  card.rect[2] - card.rect[0] - 2 * r))
            # The card canvas draws the plate's 1 px top and bottom edges; no window items on it.
            line = hairline(page.scale)
            edges = [card.host.coords(item) for item in card._edges]
            height = card.rect[3] - card.rect[1]
            self.assertEqual(sorted(int(e[1]) for e in edges), [0, height - line])
            self.assertEqual({card.host.itemcget(item, "fill") for item in card._edges}, {ui_theme.TOKENS["card_edge"]})
            self.assertEqual(self.windows(card.host), [])
        # Every input is a child of its card's canvas, placed on it.
        for widget, element in page._widgets.items():
            self.assertIs(widget.master, element.card.host)
            self.assertEqual(widget.winfo_manager(), "place")

    @unittest.skipUnless(sys.platform == "win32", "window regions are a Windows feature")
    def test_the_sheet_region_covers_the_plate_middles_only_and_follows_a_hidden_card(self):
        import ctypes
        from ctypes import wintypes
        v = self.settings_demo()
        page = v.page
        self.need_sheet(page)
        user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        user32.GetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN)
        gdi32.PtInRegion.argtypes = (wintypes.HRGN, ctypes.c_int, ctypes.c_int)
        hwnd = wintypes.HWND(page.sheet.winfo_id())

        def shape():
            region = gdi32.CreateRectRgn(0, 0, 0, 0)
            self.addCleanup(gdi32.DeleteObject, region)
            self.assertNotEqual(user32.GetWindowRgn(hwnd, region), 0)
            return lambda x, y: bool(gdi32.PtInRegion(region, int(x), int(y)))
        inside = shape()
        r = px(page.scale, page.CARD_RADIUS)
        for card in page.cards:
            x0, y0, x1, y1 = card.rect
            self.assertTrue(inside(x0 + 1, y0 + 1) and inside(x1 - 2 * r - 1, y1 - 1))
        game, profiles, updates = v.game, v.profiles, v.updates
        self.assertFalse(inside(10, (game.rect[3] + profiles.rect[1]) // 2))     # the gap between two cards
        self.assertFalse(inside(game.rect[2] - r + 2, 10))                        # the column gutter
        self.assertFalse(inside(updates.rect[0] + 10, updates.rect[3] + 10))      # under the shorter column
        v.profiles.set_visible(False)
        page.layout_now()
        inside = shape()
        self.assertFalse(inside(10, profiles.rect[1] + 10 if profiles.rect else v.game.rect[3] + 5))
        self.assertTrue(inside(10, v.reshade.rect[1] + 1))

    def test_the_plates_ends_are_at_most_four_shared_strips(self):
        v = self.settings_demo()
        page, canvas, t = v.page, v.page.canvas, ui_theme.TOKENS
        images = [i for i in canvas.find_all() if canvas.type(i) == "image" and canvas.itemcget(i, "state") != "hidden"
                  and not {"bd", "page_rail"} & set(canvas.gettags(i))]
        strips = [i for i in images if "plate_strip" in canvas.gettags(i)]
        self.assertEqual(images, strips)
        self.assertEqual(len(strips), 4)
        self.assertEqual([i for i in canvas.find_all() if canvas.type(i) == "rectangle"
                          and "kitplate" in canvas.gettags(i)], [])
        r, line = px(page.scale, page.CARD_RADIUS), hairline(page.scale)
        xs = sorted(canvas.coords(i)[0] for i in strips)
        main = page.main_width
        self.assertEqual(xs, [0.0, float(main - r), float(main + px(page.scale, 20)), float(page.content_width - r)])
        left = page._strip_images[0]
        middle = (v.profiles.rect[1] + v.profiles.rect[3]) // 2
        rgb = lambda token: ui_theme.rgb(t[token])  # noqa: E731
        self.assertEqual(left.get(0, middle), rgb("card_edge"))
        self.assertEqual(left.get(line, middle), rgb("panel"))
        self.assertTrue(left.transparency_get(0, v.game.rect[3] + 2))
        # Scrolling never rebuilds them.
        built = []
        original = art.plate_strip
        with patch.object(art, "plate_strip", lambda *a, **k: built.append(a) or original(*a, **k)):
            for _ in range(5):
                page._scroll_units(12)
                self.root.update()
        self.assertEqual(built, [])
        # A hidden card leaves them (one layout, one rebuild).
        v.tools.set_visible(False)
        page.layout_now()
        self.assertEqual(page._strip_key[2][-1][2][-1][1], v.updates.rect[3])

    def test_full_width_cards_share_the_outer_strips_and_other_looks_are_drawn_in_them(self):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry("948x700")
        page = KitPage(self.root, columns=SPECS["controls"])
        page.pack(fill="both", expand=True)
        search = tk.StringVar(self.root)
        band = page.band()
        field = band.search(search, placeholder="Find an action or shortcut")
        main, side = page.columns()
        main.add_card("Camera").row().label("x")
        side.add_card("Camera Feel").row().label("y")
        full = page.add_card("Movement")
        full.row().label("z")
        host = page.host_card("Shot Playback", where="flow")
        frame = tk.Frame(self.root, background="", width=200, height=40)
        host.adopt(frame)
        self.root.update()
        page.layout_now()
        canvas, t, s = page.canvas, ui_theme.TOKENS, page.scale
        strips = [i for i in page._strip_items if canvas.itemcget(i, "state") != "hidden"]
        self.assertEqual(len(strips), 4)
        r = px(s, page.CARD_RADIUS)
        groups = {(side_name, x): spans for side_name, x, spans in page._strip_key[2]}
        left = groups[("left", 0)]
        right = groups[("right", page.content_width - r)]
        self.assertIn((full.rect[1], full.rect[3], None), left)
        self.assertIn((full.rect[1], full.rect[3], None), right)
        self.assertIn((host.card.rect[1], host.card.rect[3], "pinned"), left)
        self.assertIn((field.rect[1], field.rect[3], "search"), left)
        # The search field's end is field-coloured and has no edge; the pinned card's edge is #373737's token.
        image = page._strip_images[[k[:2] for k in page._strip_key[2]].index(("left", 0))]
        self.assertEqual(image.get(r - 1, (field.rect[1] + field.rect[3]) // 2), ui_theme.rgb(t["field"]))
        self.assertEqual(image.get(0, (field.rect[1] + field.rect[3]) // 2), ui_theme.rgb(t["field"]))
        mid = (host.card.rect[1] + host.card.rect[3]) // 2
        self.assertEqual(image.get(0, mid), ui_theme.rgb(t["card_edge_pinned"]))
        self.assertEqual(image.get(hairline(s), mid), ui_theme.rgb(t["panel"]))
        # The search field's middle is on the sheet and part of its region.
        if page.sheet is not None:
            self.assertIs(field.canvas.master, page.sheet)
            self.assertIn(field.middle(), list(page._region_key[2]))

    def test_the_rail_is_canvas_items_and_shows_only_while_overflowing(self):
        v = self.settings_demo()
        page, canvas = v.page, v.page.canvas
        self.assertIsInstance(page.scrollbar, CanvasRail)
        self.assertTrue(page.scrollbar.winfo_ismapped())
        self.assertFalse(any(isinstance(c, ttk.Scrollbar) for c in canvas.winfo_children()))
        self.assertEqual(page.scrollbar._place[0], page.content_width + px(page.scale, 8))
        self.root.geometry("948x900")
        self.root.update()
        self.assertFalse(page.scrollbar.winfo_ismapped())

    def test_no_ttk_frames_or_labels_inside_card_canvases(self):
        v = self.settings_demo()
        for card in v.page.cards:
            for widget in walk(card.host):
                self.assertNotIsInstance(widget, (ttk.Frame, tk.Label))
                if isinstance(widget, ttk.Label):
                    # Only the at-rest covers and placeholders that live inside an entry.
                    self.assertIsInstance(widget.master, ttk.Entry)

    def test_focus_stand_ins_are_placed_frames_never_window_items(self):
        v = self.settings_demo()
        page = v.page
        proxies = [w for w in walk(page) if getattr(w, "_kit_group", None) is not None]
        self.assertTrue(proxies)
        self.assertEqual(page.window_counts()["proxies"], len(proxies))
        for proxy in proxies:
            self.assertEqual(proxy.winfo_manager(), "place")
            self.assertEqual((proxy.winfo_reqwidth(), proxy.winfo_reqheight()), (1, 1))
        if page.sheet is not None:
            self.assertLessEqual(len(self.windows(page.canvas)), 1)
        else:   # without regions: one window per card, never one per widget
            self.assertEqual(len(self.windows(page.canvas)), len(page.cards))

    def test_open_areas_are_the_viewport_minus_the_plates(self):
        backdrop = self.root._dolly_backdrop = FakeBackdrop()
        v = self.settings_demo()
        page = v.page
        areas = backdrop.attached[0][1]()
        top = page.canvas.canvasy(0)
        for x0, y0, x1, y1 in areas:
            for card in page.cards:
                cx0, cy0, cx1, cy1 = card.rect
                overlaps = x0 < cx1 and x1 > cx0 and y0 < cy1 - top and y1 > cy0 - top
                self.assertFalse(overlaps, ((x0, y0, x1, y1), card.title))
        gutter = (page.main_width, 0, page.main_width + px(page.scale, 20))
        self.assertTrue(any(a[0] <= gutter[0] and a[2] >= gutter[2] for a in areas))
        width = page.canvas.winfo_width()
        self.assertTrue(any(a[2] == width and a[0] <= page.content_width for a in areas))   # the rail gutter
        covered = sum((a[2] - a[0]) * (a[3] - a[1]) for a in areas)
        plates = sum((min(c.rect[2], width) - c.rect[0]) * (max(0, min(c.rect[3] - top, page.canvas.winfo_height())
                                                                    - max(0, c.rect[1] - top)))
                     for c in page.cards)
        self.assertEqual(covered + plates, width * page.canvas.winfo_height())


class FallbackTests(KitCase):
    def test_without_window_regions_every_card_is_its_own_retained_window(self):
        with patch("dolly.ui.kit_page.set_window_region", return_value=False):
            v = self.settings_demo()
        page, canvas = v.page, v.page.canvas
        self.assertIsNone(page.sheet)
        self.assertEqual(self.windows(canvas), [card.window for card in page.cards])
        r = px(page.scale, page.CARD_RADIUS)
        expected = {v.game: (0, 0, 613, 185), v.updates: (633, 0, 297, 180), v.tools: (633, 195, 297, 213)}
        for card, (x, y, w, h) in expected.items():
            self.assertEqual(canvas.coords(card.window), [float(card.rect[0] + r), float(card.rect[1])])
            self.assertNear(page, card.rect[2] - card.rect[0], w)
            self.assertNear(page, card.rect[3] - card.rect[1], h)
        if sys.platform == "win32" and os.environ.get("DOLLY_UI_LAYERED", "1") != "0":
            self.assertTrue(all(card.retained for card in page.cards))
        page.reveal(v.forget)
        self.root.update()
        rect = page.rect_of(v.forget)
        view = page.canvas.canvasy(0)
        self.assertTrue(view <= rect[1] and rect[3] <= view + page.canvas.winfo_height())

    @unittest.skipUnless(sys.platform == "win32", "window regions are a Windows feature")
    def test_layering_off_keeps_the_region_shaped_sheet(self):
        with patch.dict(os.environ, {"DOLLY_UI_LAYERED": "0"}):
            v = self.settings_demo()
        page = v.page
        self.need_sheet(page)
        self.assertFalse(page.retained)
        self.assertEqual(self.windows(page.canvas), [page._sheet_item])


class ScrollTests(KitCase):
    """The wheel through tween.WheelScroller (motion on and off), immediate scrolls, the scroll path."""

    def test_the_wheel_eases_by_the_clock_and_only_while_overflowing(self):
        v = self.settings_demo(shown="948x400")
        page = v.page
        animator = self.eased(page)
        distance = 60 * page.scale
        layouts = []
        page._update.callback = lambda: layouts.append(True)
        label_target = v.game.host
        with patch("dolly.ui.tween.smooth", return_value=True):
            self.assertEqual(page._wheel(SimpleNamespace(widget=label_target, delta=-120)), "break")
            self.assertEqual(page.canvas.canvasy(0), 0)      # the first frame draws, not the wheel event
            self.assertTrue(page.scroller.active)
            animator.frame(0.070)
            self.assertEqual(page.canvas.canvasy(0), round(distance * ease_out(0.5)))
            animator.frame(0.140)
            self.assertEqual(page.canvas.canvasy(0), round(distance))
            self.assertFalse(page.scroller.active)
            self.assertEqual(animator.steps, {})
        self.assertEqual(layouts, [])
        with patch("dolly.ui.boil.motion_enabled", return_value=False):   # motion off: at once
            page._wheel(SimpleNamespace(widget=v.updates.host, delta=-120))
            self.assertEqual(page.canvas.canvasy(0), round(2 * distance))
            self.assertEqual(animator.steps, {})
            before = page.canvas.yview()
            # A widget that owns the wheel keeps it: a combobox, a table, its own binding.
            tree = ttk.Treeview(v.reshade.host)
            own = tk.Canvas(v.reshade.host)
            own.bind("<MouseWheel>", lambda e: None)
            for owner in (v.combo, tree, own):
                self.assertIsNone(page._wheel(SimpleNamespace(widget=owner, delta=-120)))
            self.assertEqual(page.canvas.yview(), before)
            # The rail is canvas items: the wheel over the page canvas scrolls the page.
            self.assertEqual(page._wheel(SimpleNamespace(widget=page.canvas, delta=120)), "break")
            self.root.geometry("948x900")
            self.root.update()
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
            self.assertIsNone(page._wheel(SimpleNamespace(widget=v.game.host, delta=-120)))

    def test_rail_reveal_scroll_to_configure_and_unmap_stop_the_motion(self):
        v = self.settings_demo(shown="948x400")
        page = v.page
        animator = self.eased(page)
        with patch("dolly.ui.tween.smooth", return_value=True):
            for stop in (lambda: page.scrollbar.command("moveto", 0), lambda: page.reveal(v.forget),
                         lambda: page.scroll_to(0), lambda: page._resized(None),
                         lambda: page.event_generate("<Unmap>")):
                page.scroll_page_wheel(SimpleNamespace(delta=-120, num=0))
                animator.frame(animator.now + 0.03)
                self.assertTrue(page.scroller.active)
                stop()
                self.assertFalse(page.scroller.active)
                animator.frame(animator.now + 0.5)

    def test_one_scroll_step_lays_nothing_out_and_builds_nothing(self):
        backdrop = self.root._dolly_backdrop = FakeBackdrop()
        v = self.settings_demo(shown="948x400")
        page = v.page
        self.root.update()
        calls = []
        page._update.callback = lambda: calls.append("layout")
        with patch.object(kit_page, "set_window_region", lambda *a: calls.append("region")), \
                patch.object(art, "plate_strip", lambda *a, **k: calls.append("strip")), \
                patch.object(art, "corner_face", lambda *a, **k: calls.append("image")), \
                patch.object(tk.Place, "place_configure", lambda *a, **k: calls.append("place")), \
                patch.object(tk.Place, "place", lambda *a, **k: calls.append("place")), \
                patch.object(page, "_layout", lambda: calls.append("layout")):
            backdrop.synced.clear()
            moved = page._scroll_units(12)
            self.assertEqual(moved, 12)
            # Rail and backdrop follow in the same call, before Tk repaints.
            self.assertIn((page.canvas, False), backdrop.synced)
            first, last = page.canvas.yview()
            self.assertEqual(page.scrollbar.thumb()[0],
                             int((page.canvas.winfo_height() - page.scrollbar.minimum) * first))
            self.root.update()
        self.assertEqual(calls, [])

    def test_the_report_discards_the_duplicate_expose_only_when_the_view_changed(self):
        v = self.settings_demo(shown="948x400")
        page = v.page
        discarded = []
        with patch.object(kit_page, "_discard_expose", lambda widget: discarded.append(widget)):
            page._scrolled(*page.canvas.yview())
            self.assertEqual(discarded, [])
            page._scroll_units(10)
            self.root.update()
        self.assertEqual(discarded, [page.canvas])


class RevealAndFocusTests(KitCase):
    def test_reveal_scrolls_widgets_rows_cards_and_controls_into_view(self):
        v = self.settings_demo(shown="948x300")
        page = v.page

        def in_view(rect):
            view = page.canvas.canvasy(0)
            return view <= rect[1] and rect[3] <= view + page.canvas.winfo_height()
        for target in (v.reshade_status, v.runtime_entry, v.forget, v.tools, v.paths_row, v.updates_info):
            page.reveal(target)
            self.root.update()
            self.assertTrue(in_view(page.rect_of(target)), target)

    def test_reveal_opens_a_closed_disclosure(self):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry("948x200")
        page = KitPage(self.root, columns=SPECS["camera"])
        page.pack(fill="both", expand=True)
        main, _side = page.columns()
        card = main.add_card("Attach Camera")
        for index in range(4):
            card.row().label(f"row {index}")
        disclosure = card.disclosure("Rotation & transition")
        row = disclosure.body.row()
        entry = row.widget(ttk.Entry(row.host, style="Kit.TEntry"), width="fill")
        self.root.update()
        page.reveal(entry)
        self.root.update()
        self.assertTrue(disclosure.opened)
        rect = page.rect_of(entry)
        view = page.canvas.canvasy(0)
        self.assertTrue(view <= rect[1] and rect[3] <= view + page.canvas.winfo_height())

    def test_reveal_sizes_controls_in_a_withdrawn_window(self):
        for regions in (True, False):
            with self.subTest(sheet=regions):
                root = tk.Tk()
                self.addCleanup(root.destroy)
                root.withdraw()
                holder = ttk.Frame(root, width=1, height=26)
                holder.pack_propagate(False)
                holder.pack(anchor="nw")
                with patch("dolly.ui.kit_page.set_window_region", return_value=False) if not regions else \
                        patch.object(kit_page, "WheelScroller", kit_page.WheelScroller):
                    page = KitPage(holder, columns=SPECS["settings"])
                page.pack(fill="both", expand=True)
                main, _side = page.columns()
                card = main.add_card("ReShade")
                for _ in range(6):
                    card.row().label("filler")
                row = card.row()
                entry = row.widget(ttk.Entry(row.host, style="Kit.TEntry"), width="fill")
                button = row.button("Forget runtime", lambda: None)
                page.reveal(entry)
                self.assertGreater(entry.winfo_width(), 1)
                self.assertGreater(entry.winfo_height(), 1)
                self.assertGreater(button.winfo_width(), 1)
                self.assertGreater(button.winfo_height(), 1)

    def test_tab_order_is_build_order_and_skips_hidden_and_disabled(self):
        v = self.settings_demo(shown="948x300")
        page = v.page

        def takes_focus(widget):
            value = str(widget.cget("takefocus"))
            return str(self.root.tk.call(value, str(widget))) == "1"
        order, widget = [], v.game_entry
        for _ in range(40):
            order.append(widget)
            widget = widget.tk_focusNext()
            if widget is None or widget is v.game_entry:
                break
        game_group = v.rows[0][0].core.groups[0].proxy
        self.assertLess(order.index(v.game_entry), order.index(game_group))
        self.assertLess(order.index(game_group), order.index(v.rows[1][1]))
        self.assertLess(order.index(v.runtime_entry), order.index(v.auto_check))
        for widget in walk(page):
            if isinstance(widget, (tk.Canvas, ttk.Label)) and getattr(widget, "_kit_group", None) is None \
                    and not isinstance(widget, kit_controls.KitSlider):
                self.assertIn(str(widget.cget("takefocus")), ("0", ""), widget)
        v.combo.state(["disabled"])
        self.assertFalse(takes_focus(v.combo))
        v.combo.state(["!disabled"])
        page.scroll_to(0)
        self.assertTrue(takes_focus(v.show_log_check))   # off-screen: revealed, then focused
        view = page.canvas.canvasy(0)
        self.assertGreater(view, 0)
        v.tools.set_visible(False)
        page.layout_now()
        self.assertFalse(takes_focus(v.show_log_check))

    def test_a_focus_group_stand_in_reveals_its_first_control(self):
        v = self.settings_demo(shown="948x300")
        page = v.page
        group = v.forget.group
        page.scroll_to(0)
        script = str(group.proxy.cget("takefocus"))
        self.assertEqual(str(self.root.tk.call(script, str(group.proxy))), "1")
        rect = page.rect_of(group.first())
        view = page.canvas.canvasy(0)
        self.assertTrue(view <= rect[1] and rect[3] <= view + page.canvas.winfo_height())


class HostCardTests(KitCase):
    def build(self, size="948x700"):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry(size)
        frame = tk.Frame(self.root, background="", borderwidth=0, highlightthickness=0, takefocus=0)
        body = tk.Frame(frame, background=ui_theme.TOKENS["panel"])
        body.pack(fill="both", expand=True)
        strip = KitStrip(body)
        strip.pack(fill="x")
        play = strip.button("Play shot", self.record("play"))
        entry = ttk.Entry(body, style="Kit.TEntry")
        entry.pack(fill="x", pady=(10, 0))
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        camera = KitPage(notebook, columns=SPECS["camera"], pinned=True)
        look = KitPage(notebook, columns=SPECS["look"])
        notebook.add(camera)
        notebook.add(look)
        tk.Misc.lift(frame)
        for page in (camera, look):
            main, side = page.columns()
            for name in ("Capture", "Cameras", "Curves"):
                main.add_card(name).row().label("content")
            side.add_card("Selected").row().label("content")
        dock = camera.host_card("Shot Playback", where="dock")
        flow = look.host_card("Shot Playback", where="flow")
        self.root.update()
        return SimpleNamespace(frame=frame, body=body, strip=strip, play=play, entry=entry, notebook=notebook,
                               camera=camera, look=look, dock=dock, flow=flow)

    def test_adopt_and_release_between_a_dock_and_a_flow_card(self):
        h = self.build()
        frame = h.frame
        h.dock.adopt(frame)
        self.root.update()
        self.assertIs(frame._kit_host, h.dock)
        self.assertEqual(frame.winfo_manager(), "place")
        self.assertEqual(str(frame.place_info()["in"]), str(h.camera.dock))
        self.assertEqual(frame.scroll_page_wheel, h.camera.scroll_page_wheel)
        self.assertTrue(h.camera._dock_packed)
        self.assertEqual(h.dock.mode, "dock")
        script = str(h.entry.cget("takefocus"))
        self.assertNotIn(script, ("ttk::takefocus", "1"))
        # Look adopts it: Camera releases it first (one host at a time).
        h.notebook.select(h.look)
        h.flow.adopt(frame)
        self.root.update()
        self.assertIsNone(h.dock.adopted)
        self.assertFalse(h.camera._dock_packed)
        self.assertEqual(str(frame.place_info()["in"]), str(h.flow.card.host))
        self.assertIs(h.flow.card.host.master, h.look.sheet if h.look.sheet is not None else h.look.canvas)
        self.assertEqual(frame.scroll_page_wheel, h.look.scroll_page_wheel)
        self.assertTrue(h.flow.card.visible)
        self.assertIs(h.look.all_cards()[-1], h.flow.card)
        moving = h.look.window_counts()["moving"]
        # The sheet and the adopted frame (without regions: one window per card and the frame).
        self.assertLessEqual(moving, 2 if h.look.sheet is not None else len(h.look.all_cards()) + 1)
        # Its takefocus scripts reveal it on the new page; the old values come back on release.
        h.flow.release(frame)
        self.root.update()
        self.assertEqual(frame.winfo_manager(), "")
        self.assertFalse(hasattr(frame, "scroll_page_wheel"))
        self.assertIn(str(h.entry.cget("takefocus")), ("ttk::takefocus", ""))
        self.assertFalse(h.flow.card.visible)
        self.assertEqual(self.errors, [])

    def test_the_flow_frame_moves_with_the_sheet_and_stays_mapped_while_scrolled_away(self):
        h = self.build(size="948x300")
        h.notebook.select(h.look)
        h.flow.adopt(h.frame)
        self.root.update()
        page = h.look
        rect = page.rect_of(h.flow.card)
        self.assertGreater(rect[3], page.canvas.winfo_height())
        if page.sheet is not None:   # (without regions the card is a window the canvas unmaps off-view)
            before = h.frame.winfo_rooty()
            page.scroll_to(rect[1])
            self.root.update()
            self.assertEqual(before - h.frame.winfo_rooty(), int(page.canvas.canvasy(0)))
            page.scroll_to(0)
            self.root.update()
            self.assertTrue(h.frame.winfo_ismapped())
        # The wheel over the adopted frame scrolls its host page; reveal reaches into it.
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            self.assertEqual(page._wheel(SimpleNamespace(widget=h.entry, delta=-120)), "break")
            self.assertIsNone(h.camera._wheel(SimpleNamespace(widget=h.entry, delta=-120)))
        page.reveal(h.entry)
        self.root.update()
        entry = page.rect_of(h.entry)
        view = page.canvas.canvasy(0)
        self.assertTrue(view <= entry[1] and entry[3] <= view + page.canvas.winfo_height())
        page.reveal(h.play)
        self.root.update()

    def test_the_dock_unpins_below_160_px_of_viewport_and_pins_again(self):
        h = self.build(size="948x700")
        h.dock.adopt(h.frame)
        self.root.update()
        self.assertEqual(h.dock.mode, "dock")
        height = h.dock.dock_height()
        self.root.geometry(f"948{'x'}{height + px(h.camera.scale, 120)}")
        self.root.update()
        h.camera.layout_now()
        self.assertEqual(h.dock.mode, "flow")
        self.assertEqual(str(h.frame.place_info()["in"]), str(h.dock.card.host))
        self.assertTrue(h.dock.card.visible)
        self.root.geometry("948x700")
        self.root.update()
        h.camera.layout_now()
        self.assertEqual(h.dock.mode, "dock")
        self.assertEqual(str(h.frame.place_info()["in"]), str(h.camera.dock))



class GapFadeBentoTests(KitCase):
    """USER-ADDITIONS UPDATE 7-9: the end gap under the viewport, the fade on every page, bento rows, card art."""

    def test_every_page_fades_and_ends_its_viewport_the_end_gap_above_the_links(self):
        v = self.settings_demo(shown="948x400")
        page = v.page
        self.assertIsInstance(page.fade, fade.ScrollFade)              # on by default (Settings, Controls, Export)
        self.assertIsNone(KitPage(self.root, fade=False).fade)
        self.assertEqual(page.END_GAP, stage_page.END_GAP)
        self.assertEqual(page.end_gap(), stage_page.end_gap(page.scale))
        self.assertTrue(page._foot_packed)
        self.assertEqual(page.foot.winfo_height(), page.end_gap())
        self.assertEqual(page.canvas.winfo_height(), page.winfo_height() - page.end_gap())
        self.assertEqual(page.foot.winfo_width(), page.winfo_width())
        # Overflowing: no end margin any more, the last card ends on the viewport's bottom at the end.
        self.assertGreater(page.content_height, page.canvas.winfo_height())
        self.assertEqual(float(page.canvas.cget("scrollregion").split()[3]), page.content_height)
        page.scroll_to(page.content_height)
        self.root.update()
        self.assertEqual(page.canvas.canvasy(0) + page.canvas.winfo_height(), page.content_height)
        # A hidden (cloaked) test window never gets a fade window: nothing shown, no backend made.
        self.assertFalse(any(band.shown for band in page.fade.bands.values()))
        self.assertIsNone(page.fade.backend)

    def test_the_compact_shell_uses_the_compact_gap(self):
        holder = tk.Frame(self.root)
        holder.mode = "compact"
        holder.pack(fill="both", expand=True)
        v = self.settings_demo(shown="948x400", parent=holder)
        self.assertEqual(v.page.end_gap(), stage_page.end_gap(v.page.scale, compact=True))
        self.assertEqual(v.page.foot.winfo_height(), v.page.end_gap())

    def test_bento_rows_share_their_edges_and_the_spare_goes_to_the_growable_block(self):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry("948x700")
        page = KitPage(self.root, columns=SPECS["look"])
        page.pack(fill="both", expand=True)
        page.CARD_GAP = 20
        main, side = page.columns()
        a = main.add_card("A")
        a.row().label("row")
        table = tk.Frame(a.host, background=ui_theme.TOKENS["panel"])
        block = a.block(table, top=15, height=40)
        b = side.add_card("B")
        for _ in range(3):
            b.row().label("row")
        c = main.add_card("C")
        c.row().label("row")
        d, e = side.add_card("D"), side.add_card("E")
        d.row().label("row")
        e.row().label("row")
        page.bento([((a,), (b,)), ((c,), (d, e))], grow={a: block})
        self.root.update()
        page.layout_now()
        s, gap = page.scale, px(page.scale, 20)
        self.assertEqual((a.rect[1], a.rect[3]), (b.rect[1], b.rect[3]))
        self.assertEqual((c.rect[1], c.rect[3]), (d.rect[1], e.rect[3]))
        self.assertEqual((c.rect[1] - a.rect[3], e.rect[1] - d.rect[3]), (gap, gap))
        # A's table grew (more of it shows); C has nothing to grow, so its bottom inset takes the rest.
        self.assertGreater(block.extra, 0)
        self.assertEqual(table.winfo_height(), px(s, 40) + block.extra)
        self.assertGreater(c.extra, 0)
        self.assertEqual((b.extra, d.extra, e.extra), (0, 0, 0))
        self.assertEqual(page.content_height, e.rect[3])
        # One column: every card at its own height again.
        self.root.geometry("700x700")
        self.root.update()
        page.layout_now()
        self.assertFalse(page.two_columns)
        self.assertEqual((block.extra, c.extra), (0, 0))

    def test_a_host_card_with_art_draws_it_under_the_card_and_hands_the_frame_its_offset(self):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry("948x300")             # too short to pin: the card scrolls (flow)
        page = KitPage(self.root, columns=SPECS["camera"], pinned=True)
        page.pack(fill="both", expand=True)
        main, side = page.columns()
        main.add_card("Capture").row().label("row")
        side.add_card("Selected").row().label("row")
        host = page.host_card("Shot Playback", where="dock", art=art.playback_art)
        handed = []
        frame = tk.Frame(self.root, background="", borderwidth=0, highlightthickness=0)
        frame.card_backdrop = lambda image, x, y: handed.append((image, x, y))
        body = tk.Frame(frame, background=ui_theme.TOKENS["panel"], height=px(page.scale, 103))
        body.pack_propagate(False)
        body.pack(fill="both", expand=True)
        host.adopt(frame)
        self.root.update()
        self.assertEqual(host.mode, "flow")
        card = host.card
        s, r = page.scale, px(page.scale, page.CARD_RADIUS)
        width, height = card.rect[2] - card.rect[0], card.rect[3] - card.rect[1]
        image, x, y = handed[-1]
        self.assertEqual((image.width(), image.height()), (width, height))
        self.assertEqual((x, y), (host._placed[1] + r, host._placed[2]))
        item = host._flow_art
        self.assertEqual(card.host.find_all()[0], item)             # the bottom item, under the edges
        self.assertEqual(card.host.coords(item), [float(-r), 0.0])  # the card canvas starts a radius in
        self.assertEqual(card.host.itemcget(item, "image"), str(image))
        host.release(frame)
        self.root.update()
        self.assertEqual(handed[-1], (None, 0, 0))
        self.assertEqual(card.host.itemcget(item, "state"), "hidden")


class CompatTests(KitCase):
    def test_rows_cards_texts_and_sections_answer_like_packed_frames(self):
        v = self.settings_demo()
        for element in (v.paths_row, v.game, v.update_status):
            self.assertEqual(element.winfo_manager(), "canvas")
            self.assertTrue(element.winfo_ismapped())
            element.pack_forget()
            self.assertEqual(element.winfo_manager(), "")
            element.pack(fill="x")
            self.assertEqual(element.winfo_manager(), "canvas")
        v.update_status.configure(text="Update installation is available in the packaged Windows build.")
        self.assertEqual(v.update_status.cget("text"), "Update installation is available in the packaged Windows build.")
        v.update_status.configure(foreground=ui_theme.TOKENS["dim"])
        self.assertEqual(v.update_status.cget("foreground"), ui_theme.TOKENS["dim"])
        section = v.tools.section(top=15)
        section.row().label("in a section")
        v.page.layout_now()
        self.assertEqual(section.winfo_manager(), "canvas")
        height = v.tools.rect[3]
        section.set_visible(False)
        v.page.layout_now()
        self.assertLess(v.tools.rect[3], height)

    def test_a_textvariable_text_follows_and_its_trace_goes_with_it(self):
        v = self.settings_demo()
        status = tk.StringVar(self.root, "one line")
        text = v.updates.text(textvariable=status, top=15)
        v.page.layout_now()
        status.set("now a much longer status line that wraps onto a second line in the side column")
        v.page.layout_now()
        self.assertGreater(text.size()[1], text.linespace())
        text.destroy()
        self.assertEqual(status.trace_info(), [])

    def test_nothing_runs_at_rest(self):
        from dolly.ui.boil import BoilClock
        with patch("dolly.ui.boil.motion_enabled", return_value=True):
            v = self.settings_demo()
            for _ in range(40):          # the ramps' faces warm in idle slices, then nothing is left
                self.root.update()
            animator = Animator.of(self.root)
            self.assertFalse(animator.running)
            self.assertFalse(animator.active())
            self.assertFalse(animator._warm)
            self.assertEqual(BoilClock.of(self.root).members, ())
            v.check._on_enter()
            self.assertTrue(animator.running)
            animator.finish()
            self.assertFalse(animator.running)

    def test_kit_styles_are_installed_by_the_page(self):
        page = KitPage(self.root)
        style = ttk.Style(self.root)
        self.assertTrue(style.layout("Kit.TEntry"))
        self.assertTrue(style.layout("Kit.TCombobox"))
        del page


if __name__ == "__main__":
    unittest.main()
