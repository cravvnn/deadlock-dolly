"""Smooth motion in the shell (USER-ADDITIONS UPDATE 4): sidebar, links, disclosures, info icons, rail.

Every test runs on a real (hidden) window with motion patched on or off through
``boil.motion_enabled`` and the shared animator on a clock the test sets: frames run only
when a test ticks them. The stage's rail gutter (PLAN-5 N8) is checked here too.
"""
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest
from unittest import mock

from dolly import gui_theme, ui_theme
from dolly.ui import art, boil, tween
from dolly.ui.canvas_controls import CanvasLink
from dolly.ui.chrome import Stage
from dolly.ui.scale import px
from dolly.ui.sidebar import LAST_STEP, Sidebar
from dolly.ui.stage_page import CanvasRail, StagePage
from dolly.ui.tween import ease_out

ART = Path(__file__).resolve().parents[1] / "assets" / "ui"
FONT = ("Segoe UI", -12)
TOKENS = ui_theme.TOKENS
FRAME = tween.FRAME_MS / 1000.0
_shipped = []


def shipped_art():
    """The verified art set, read once for the whole file."""
    if not _shipped:
        _shipped.append(art.load(directory=ART))
    return _shipped[0]


def canvas_calls():
    """Patch ``coords`` and ``itemconfigure`` on every canvas; the mocks record (canvas, item)."""
    return (mock.patch.object(tk.Canvas, "coords", autospec=True, side_effect=tk.Canvas.coords),
            mock.patch.object(tk.Canvas, "itemconfigure", autospec=True, side_effect=tk.Canvas.itemconfigure))


class MotionCase(unittest.TestCase):
    """A shown (harness-cloaked) root, motion on, the boil clock held, the animator on ``self.now``."""

    motion = True

    def setUp(self):
        patcher = mock.patch.object(boil, "motion_enabled", return_value=self.motion)
        patcher.start()
        self.addCleanup(patcher.stop)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.addCleanup(lambda: self.assertEqual(self.errors, [], "Tk callback raised an exception"))
        self.root.geometry("420x320")
        boil.BoilClock.of(self.root).hold("test")
        self.now = 0.0
        self.animator = tween.Animator.of(self.root)
        self.animator.clock = lambda: self.now

    def destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass   # the test destroyed it already

    def at(self, seconds):
        """One frame at ``seconds`` on the animator's clock."""
        self.now = seconds
        self.animator.tick(seconds)

    def settle(self):
        self.root.update()
        self.root.update_idletasks()

    def jobs(self):
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def ends_at(self, active, seconds, duration_ms):
        """The motion begun at ``seconds`` still moves just before ``duration_ms`` (its first
        frame was the event's own) and has ended just after."""
        end = seconds + (duration_ms - tween.FRAME_MS) / 1000.0
        self.at(end - 0.002)
        self.assertTrue(active())
        self.at(end + 0.001)
        self.assertFalse(active())


class SidebarCase(MotionCase):
    KEYS = ("replay", "camera", "look", "export", "controls", "settings")

    def sidebar(self, assets="real"):
        self.root.geometry("300x600")
        self.pages = []
        if assets == "real":
            assets = shipped_art()
            self.assertIsNotNone(assets, "the shipped art verifies")
        with mock.patch.object(art, "load", return_value=None):   # no fallback to the shipped art
            bar = Sidebar(self.root, scale=1.0, version="0.0", driver_var=tk.StringVar(self.root, "DRIVER · NATIVE"),
                          assets=assets)
        bar.pack(side="left", fill="y")
        for key in self.KEYS:
            # Like the app: a row's command switches the page, and the page switch selects the row.
            bar.add_item(key, key.title(), key, lambda key=key: (self.pages.append(key), bar.select(key)))
        bar.select("replay")
        self.settle()
        return bar

    def click(self, bar, key):
        bar.event_generate("<Button-1>", x=60, y=bar.items[key].top + 10)

    def glide_top(self, bar):
        return int(bar.coords(bar._glide)[1])

    def row(self, bar, key):
        item = bar.items[key]
        return bar.itemcget(item.strip, "image"), bar.itemcget(item.label, "fill")


class SidebarSlideTests(SidebarCase):
    def test_a_click_switches_at_once_and_the_highlight_slides_over_200_ms(self):
        bar = self.sidebar()
        replay, camera = bar.items["replay"], bar.items["camera"]
        selected_face = str(bar._row_image(camera.top, "selected"))
        self.click(bar, "camera")
        # The page, the selection and the labels: at once.
        self.assertEqual(self.pages, ["camera"])
        self.assertEqual(bar.selected, "camera")
        self.assertEqual(bar.itemcget(camera.label, "fill"), TOKENS["text"])
        self.assertEqual(bar.itemcget(replay.label, "fill"), TOKENS["muted"])
        self.assertEqual(bar.itemcget(camera.glyph, "image"), str(camera.images[1]))
        self.assertEqual(bar.itemcget(replay.strip, "image"), str(bar._row_image(replay.top, "idle")))
        # The highlight: its first step is drawn by the click, one frame along.
        distance = camera.top - replay.top
        self.assertEqual(bar.itemcget(bar._glide, "state"), "normal")
        self.assertEqual(self.glide_top(bar), int(replay.top + distance * ease_out(FRAME / .2) + .5))
        self.assertEqual(int(bar.coords(bar._bar)[1]), self.glide_top(bar), "the bar slides with it")
        self.assertNotEqual(bar.itemcget(camera.strip, "image"), selected_face)
        self.at(0.1)
        self.assertEqual(self.glide_top(bar), int(replay.top + distance * ease_out((0.1 + FRAME) / .2) + .5))
        self.ends_at(lambda: bar._glide_y is not None, 0.0, tween.SLIDE_MS)
        self.assertEqual(bar.itemcget(bar._glide, "state"), "hidden")
        self.assertEqual(bar.itemcget(camera.strip, "image"), selected_face)
        self.assertEqual(bar.coords(bar._bar), [0.0, float(camera.top), 3.0, float(camera.top + 40)])
        self.assertFalse(self.animator.active())

    def test_a_click_during_the_slide_retargets_from_where_it_is(self):
        bar = self.sidebar()
        replay, camera, settings = bar.items["replay"], bar.items["camera"], bar.items["settings"]
        self.click(bar, "camera")
        self.at(0.06)
        here = bar._glide_y
        self.assertTrue(replay.top < here < camera.top)
        self.click(bar, "settings")
        self.assertEqual(bar.selected, "settings")
        self.assertEqual(self.row(bar, "camera")[1], TOKENS["muted"], "the left row is plain at once")
        self.assertEqual(self.row(bar, "settings")[1], TOKENS["text"])
        # No jump back: on from where it was, one frame along towards the new row.
        self.assertAlmostEqual(bar._glide_y, here + (settings.top - here) * ease_out(FRAME / .2))
        self.ends_at(lambda: bar._glide_y is not None, 0.06, tween.SLIDE_MS)
        self.assertEqual(self.row(bar, "settings")[0], str(bar._row_image(settings.top, "selected")))
        self.assertEqual(self.pages, ["camera", "settings"])

    def test_motion_off_or_a_hidden_column_selects_without_a_slide(self):
        bar = self.sidebar()
        before = self.jobs()
        with mock.patch.object(boil, "motion_enabled", return_value=False):
            self.click(bar, "look")
        look = bar.items["look"]
        self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "selected")), TOKENS["text"]))
        self.assertIsNone(bar._glide)
        self.assertEqual(bar.coords(bar._bar)[1], float(look.top))
        self.assertFalse(self.animator.running)
        self.assertEqual(self.jobs() - before, set())
        self.root.withdraw()
        bar.select("export")
        self.assertIsNone(bar._glide_y)
        self.assertEqual(self.row(bar, "export")[1], TOKENS["text"])

    def test_the_row_under_the_pointer_keeps_ramping_while_the_highlight_comes(self):
        bar = self.sidebar()
        camera = bar.items["camera"]
        bar.event_generate("<Motion>", x=60, y=camera.top + 10)
        self.click(bar, "camera")
        self.at(0.06)
        self.assertEqual(bar.itemcget(camera.label, "fill"), TOKENS["text"])
        bar.event_generate("<Leave>", x=400, y=camera.top + 10)   # off the column before it arrives
        self.at(0.3)
        self.assertEqual(self.row(bar, "camera"), (str(bar._row_image(camera.top, "selected")), TOKENS["text"]))
        self.assertFalse(self.animator.active())


class SidebarHoverTests(SidebarCase):
    def test_hover_ramps_in_120_ms_and_out_180_ms_touching_only_that_row(self):
        bar = self.sidebar()
        look = bar.items["look"]
        coords, configure = canvas_calls()
        with coords as moved, configure as changed:
            bar.event_generate("<Motion>", x=60, y=look.top + 10)
            first = bar.itemcget(look.label, "fill")
            self.assertNotIn(first, (TOKENS["muted"], TOKENS["text"]))   # drawn by the event itself
            self.assertEqual(first, tween.mix(TOKENS["text"], TOKENS["muted"], ease_out(FRAME / .12)))
            self.at(0.05)
            self.ends_at(lambda: self.animator.active(look.motion), 0.0, tween.HOVER_IN_MS)
            self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "hover")), TOKENS["text"]))
            self.assertEqual(bar.itemcget(look.glyph, "image"), str(look.images[1]))
            self.now = 1.0
            bar.event_generate("<Leave>", x=400, y=look.top + 10)
            self.ends_at(lambda: self.animator.active(look.motion), 1.0, tween.HOVER_OUT_MS)
            touched = {call.args[1] for each in (moved, changed) for call in each.call_args_list
                       if call.args[0] is bar}
        self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "idle")), TOKENS["muted"]))
        self.assertEqual(bar.itemcget(look.glyph, "image"), str(look.images[0]))
        self.assertEqual(touched, {look.strip, look.label, look.glyph})

    def test_moving_between_rows_ramps_one_out_and_the_other_in(self):
        bar = self.sidebar()
        look, export = bar.items["look"], bar.items["export"]
        bar.event_generate("<Motion>", x=60, y=look.top + 10)
        self.at(0.05)
        level = look.level
        bar.event_generate("<Motion>", x=60, y=export.top + 10)
        self.assertLess(look.level, level, "back from where it was")
        self.assertGreater(export.level, 0.0)
        self.at(0.5)
        self.assertEqual((look.level, export.level), (0.0, 1.0))

    def test_the_ramp_faces_are_pre_warmed_and_no_frame_builds_an_image(self):
        bar = self.sidebar()
        for item in bar.items.values():
            self.assertLessEqual({(item.top, step) for step in range(1, LAST_STEP)}, set(bar._rows))
            self.assertEqual(sorted(item.cells), list(range(1, LAST_STEP)))
        created = []
        original = tk.PhotoImage.__init__

        def counting(image, *args, **kwargs):
            created.append(self.animator._ticking)
            original(image, *args, **kwargs)
        with mock.patch.object(tk.PhotoImage, "__init__", counting):
            for key in self.KEYS[1:]:
                item = bar.items[key]
                bar.event_generate("<Motion>", x=60, y=item.top + 10)
                for t in (0.02, 0.05, 0.08):
                    self.at(self.now + t)
            bar.event_generate("<Leave>", x=400, y=10)
            self.at(self.now + 1.0)
        self.assertNotIn(True, created)
        # The faces between are blends of the two icon cells and of the two fades.
        look = bar.items["look"]
        middle = look.cells[LAST_STEP // 2 + 1]
        lit = [middle.get(x, y) for y in range(16) for x in range(16)
               if not int(self.root.tk.call(middle.name, "transparency", "get", x, y))]
        self.assertTrue(lit)
        idle, bright = look.images
        self.assertTrue(all(i <= m <= b for i, m, b in zip(idle.get(7, 7), middle.get(7, 7), bright.get(7, 7))))

    def test_a_disabled_row_stops_ramping_and_shows_dim_at_once(self):
        bar = self.sidebar()
        look = bar.items["look"]
        bar.event_generate("<Motion>", x=60, y=look.top + 10)
        look.configure(state="disabled")
        self.assertFalse(self.animator.active(look.motion))
        self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "idle")), TOKENS["dim"]))

    def test_without_the_icon_sheet_the_glyph_ramps_with_the_label(self):
        bar = self.sidebar(assets=None)
        look = bar.items["look"]
        bar.event_generate("<Motion>", x=60, y=look.top + 10)
        self.assertEqual(bar.itemcget(look.glyph, "fill"), bar.itemcget(look.label, "fill"))
        self.at(0.3)
        self.assertEqual(bar.itemcget(look.glyph, "fill"), TOKENS["text"])


class ShellMotionOffTests(SidebarCase):
    motion = False

    def test_hover_lands_at_once_and_schedules_nothing(self):
        bar = self.sidebar()
        look = bar.items["look"]
        before = self.jobs()
        bar.event_generate("<Motion>", x=60, y=look.top + 10)
        self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "hover")), TOKENS["text"]))
        bar.event_generate("<Leave>", x=400, y=10)
        self.assertEqual(self.row(bar, "look"), (str(bar._row_image(look.top, "idle")), TOKENS["muted"]))
        self.assertFalse(self.animator.running)
        self.assertEqual(self.jobs() - before, set())


class CanvasControlRampTests(MotionCase):
    def canvas(self):
        canvas = tk.Canvas(self.root, width=300, height=120, highlightthickness=0, background=TOKENS["bg"])
        canvas.pack()
        self.settle()
        return canvas

    def test_link_ramps_on_the_pointer_and_shows_focus_and_state_at_once(self):
        canvas = self.canvas()
        link = CanvasLink(canvas, text="Open output folder", command=lambda: None, font=FONT, scale=1.0)
        link.place(20, 30)
        fill = lambda: canvas.itemcget(link.item, "fill")  # noqa: E731
        link._on_enter()
        self.assertEqual(fill(), tween.mix(TOKENS["text"], TOKENS["muted"], ease_out(FRAME / .12)))
        link.place(20, 30)                            # a relayout mid-ramp keeps its timing
        self.ends_at(lambda: link._ramp.active, 0.0, tween.HOVER_IN_MS)
        self.assertEqual(fill(), TOKENS["text"])
        self.now = 1.0
        link._on_leave()
        self.ends_at(lambda: link._ramp.active, 1.0, tween.HOVER_OUT_MS)
        self.assertEqual(fill(), TOKENS["muted"])
        link.set_focus(True)
        self.assertEqual(fill(), TOKENS["text"])
        link.set_focus(False)
        self.assertEqual(fill(), TOKENS["muted"])
        link._on_enter()
        link.configure(state="disabled")
        self.assertEqual(fill(), TOKENS["dim"])
        self.assertFalse(link._ramp.active)
        link.configure(state="normal")
        self.assertEqual(fill(), TOKENS["muted"])
        link._on_enter()
        link.destroy()
        self.assertFalse(self.animator.active())

    def test_a_wheel_ease_and_a_hover_ramp_share_one_after_loop(self):
        canvas = self.canvas()
        link = CanvasLink(canvas, text="Show log", command=lambda: None, font=FONT, scale=1.0)
        link.place(20, 30)
        moved = []
        wheel = tween.WheelScroller(canvas, lambda units: moved.append(units) or units, measure=False)
        before = self.jobs()
        wheel.add(60)
        link._on_enter()
        self.assertEqual(len(self.jobs() - before), 1)
        self.assertEqual(set(self.animator._steps), {wheel, link._ramp})
        self.at(0.05)
        self.assertTrue(moved)
        self.assertTrue(link._ramp.active and wheel.active)
        self.at(0.5)
        self.assertEqual(sum(moved), 60)
        self.assertFalse(self.animator.active())
        self.root.update_idletasks()                  # the loop's pending frame finds nothing and stops
        self.assertFalse(self.animator.running)
        self.assertEqual(self.jobs() - before, set())


class StagePageRampTests(MotionCase):
    def page(self):
        gui_theme.initialize(self.root)
        self.root.geometry("720x420")
        page = StagePage(self.root)
        page.pack(fill="both", expand=True)
        return page

    def test_disclosure_row_ramps_on_hover_and_lights_at_once_on_focus(self):
        page = self.page()
        disclosure = page.add_disclosure("More")
        self.settle()
        canvas = page.canvas
        fill = lambda: {canvas.itemcget(item, "fill") for item in canvas.find_withtag(disclosure.tag)}  # noqa: E731
        self.assertEqual(fill(), {TOKENS["muted"]})
        disclosure._entered()
        self.assertEqual(fill(), {tween.mix(TOKENS["text"], TOKENS["muted"], ease_out(FRAME / .12))})
        self.ends_at(lambda: disclosure._ramp.active, 0.0, tween.HOVER_IN_MS)
        self.assertEqual(fill(), {TOKENS["text"]})
        self.now = 1.0
        disclosure._left()
        self.root.update_idletasks()                  # the leave settles at idle
        self.ends_at(lambda: disclosure._ramp.active, 1.0, tween.HOVER_OUT_MS)
        self.assertEqual(fill(), {TOKENS["muted"]})
        disclosure._set_focus(True)
        self.assertEqual(fill(), {TOKENS["text"]})
        self.assertEqual(canvas.itemcget(disclosure._ring, "state"), "normal")
        self.assertFalse(self.animator.active())

    def test_info_icon_ramps_to_accent_and_stays_dim_with_its_bubble_on_focus(self):
        page = self.page()
        icon = page.add_card("Selected replay", info="What the selected replay is.").info
        self.settle()
        scheduled = []
        icon.after = lambda ms, func: (scheduled.append((ms, func)), "pending")[1]
        colour = lambda: str(icon.cget("foreground"))  # noqa: E731
        icon.event_generate("<Enter>")
        self.assertEqual(colour(), tween.mix(TOKENS["accent"], TOKENS["dim"], ease_out(FRAME / .12)))
        self.assertEqual([ms for ms, _ in scheduled], [150])
        self.ends_at(lambda: icon._ramp.active, 0.0, tween.HOVER_IN_MS)
        self.assertEqual(colour(), TOKENS["accent"])
        scheduled[0][1]()
        self.assertTrue(icon.shown)
        self.now = 1.0
        icon.event_generate("<Leave>")
        self.assertFalse(icon.shown, "the bubble closes at once")
        self.ends_at(lambda: icon._ramp.active, 1.0, tween.HOVER_OUT_MS)
        self.assertEqual(colour(), TOKENS["dim"])
        icon.event_generate("<FocusIn>")
        self.assertTrue(icon.shown, "keyboard focus opens the bubble at once")
        self.assertEqual(colour(), TOKENS["dim"], "and draws no focus state (PLAN-5 N4)")
        self.assertFalse(self.animator.active())
        icon.event_generate("<FocusOut>")
        self.assertFalse(icon.shown)

    def test_rail_faces_ramp_and_each_end_is_the_ttk_face(self):
        canvas = tk.Canvas(self.root, width=200, height=300, highlightthickness=0, background=TOKENS["bg"])
        canvas.pack()
        commands = []
        rail = CanvasRail(canvas, lambda *args: commands.append(args))
        rail.place(150, 300)
        rail.set(0.0, 0.5)
        self.settle()                                # mapped: the caps between the faces are built at idle
        between = set(tween.steps(TOKENS["edge"], TOKENS["scroll_hover"])
                      + tween.steps(TOKENS["scroll_hover"], TOKENS["accent"]))
        self.assertEqual(set(rail._ramp._built), between)
        top, _length = rail.thumb()
        on_thumb = SimpleNamespace(y=top + 5)

        def face():
            ends = (canvas.itemcget(rail._ends[0], "image"), canvas.itemcget(rail._ends[1], "image"))
            return canvas.itemcget(rail._middle, "fill"), ends

        def ttk_face(name):
            (cap_top, cap_bottom), colour = rail._faces[name]
            return colour, (str(cap_top), str(cap_bottom))
        self.assertEqual(face(), ttk_face("rest"))
        rail._entered(on_thumb)
        middle, ends = face()
        self.assertEqual(middle, tween.mix(TOKENS["scroll_hover"], TOKENS["edge"], ease_out(FRAME / .12)))
        self.assertIn(ends, [(str(a), str(b)) for _rgb, ((a, b), _colour) in rail._ramp._built.values()])
        self.ends_at(lambda: rail._ramp.active, 0.0, tween.HOVER_IN_MS)
        self.assertEqual(face(), ttk_face("hover"))
        self.now = 1.0
        rail._press(on_thumb)
        self.ends_at(lambda: rail._ramp.active, 1.0, tween.PRESS_MS)
        self.assertEqual(face(), ttk_face("pressed"))
        self.now = 2.0
        rail._release(on_thumb)
        self.ends_at(lambda: rail._ramp.active, 2.0, tween.PRESS_MS)
        self.assertEqual(face(), ttk_face("hover"))
        self.now = 3.0
        rail._left(None)
        self.root.update_idletasks()                  # the leave settles at idle
        self.ends_at(lambda: rail._ramp.active, 3.0, tween.HOVER_OUT_MS)
        self.assertEqual(face(), ttk_face("rest"))
        rail._entered(on_thumb)
        rail.place_forget()                           # hiding shows rest at once
        self.assertEqual(face(), ttk_face("rest"))
        self.assertFalse(self.animator.active())


class StageGutterTests(unittest.TestCase):
    """The page area's right gutter for the page rail (PLAN-5 N8, 4.3): 0 keeps today's geometry."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.addCleanup(lambda: self.assertEqual(self.errors, [], "Tk callback raised an exception"))
        boil.BoilClock.of(self.root).hold("test")

    def stage(self, scale, size):
        stage = Stage(self.root, scale=scale)
        stage.pack(fill="both", expand=True)
        self.root.geometry(size)
        self.root.update()
        return stage

    def bands(self, stage, rect):
        x0, y0, x1, y1 = rect
        width, height = stage.winfo_width(), stage.winfo_height()
        return [(0, 0, width, y0), (0, y0, x0, y1), (x1, y0, width, y1), (0, y1, width, height)]

    def test_the_gutter_widens_the_workspace_only(self):
        for scale, size, pad in ((1.0, "1200x830", 40), (1.0, "900x600", 24), (1.5, "1800x1245", 40)):
            with self.subTest(scale=scale, size=size):
                stage = self.stage(scale, size)
                width = stage.winfo_width()
                page = stage.page_rect()
                self.assertEqual(stage.workspace_rect(), page)          # default 0: today's geometry
                self.assertEqual(stage.workspace.winfo_width(), width - 2 * px(scale, pad))
                self.assertEqual(stage.open_areas(), self.bands(stage, page))
                stage.set_rail_gutter(18)
                self.root.update()
                x0, y0, x1, y1 = page
                self.assertEqual(stage.page_rect(), page)
                self.assertEqual(stage.workspace_rect(), (x0, y0, x1 + px(scale, 18), y1))
                self.assertEqual(stage.workspace.winfo_width(), width - 2 * px(scale, pad) + px(scale, 18))
                self.assertEqual((stage.workspace.winfo_x(), stage.workspace.winfo_y()), (x0, y0))
                self.assertEqual(stage.open_areas(), self.bands(stage, stage.workspace_rect()))
                stage.set_rail_gutter(0)
                self.root.update()
                self.assertEqual(stage.workspace_rect(), page)
                self.assertEqual(stage.workspace.winfo_width(), width - 2 * px(scale, pad))
                stage.destroy()


if __name__ == "__main__":
    unittest.main()
