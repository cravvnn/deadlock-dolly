"""The shared smooth-motion driver (dolly.ui.tween): one frame loop per toplevel, eased wheel scrolling."""
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from dolly.ui import tween
from dolly.ui.tween import Animator, WheelScroller, ease_out


class FakeAnimator:
    """Steps registered by a scroller; the test runs frames at the times it chooses."""

    frame_ms = tween.FRAME_MS

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
        """One frame at ``at`` seconds; steps that end are dropped."""
        self.now = at
        for key, step in list(self.steps.items()):
            if not step(at):
                self.steps.pop(key, None)


class FakePage:
    """A view position in px with edges at 0 and ``end``; ``scroll`` is the page's immediate scroll."""

    def __init__(self, end=10000, start=0):
        self.y, self.end, self.calls = start, end, []

    def winfo_viewable(self):
        return True

    def scroll(self, units):
        self.calls.append(units)
        target = max(0, min(self.end, self.y + units))
        moved, self.y = target - self.y, target
        return moved


def scroller(page, animator, **options):
    return WheelScroller(page, page.scroll, animator=animator, clock=animator.clock, measure=False, **options)


class EaseTests(unittest.TestCase):
    def test_cubic_ease_out(self):
        self.assertEqual(ease_out(0), 0.0)
        self.assertEqual(ease_out(1), 1.0)
        self.assertEqual(ease_out(-1), 0.0)
        self.assertEqual(ease_out(2), 1.0)
        self.assertAlmostEqual(ease_out(0.5), 0.875)
        values = [ease_out(i / 20) for i in range(21)]
        self.assertEqual(values, sorted(values))

    def test_durations_follow_the_motion_contract(self):
        self.assertEqual((tween.WHEEL_MS, tween.HOVER_IN_MS, tween.HOVER_OUT_MS, tween.SLIDE_MS, tween.FADE_MS),
                         (140, 120, 180, 200, 150))
        self.assertEqual(tween.FRAME_MS, 16)


@patch.object(tween, "smooth", return_value=True)
class WheelScrollerTests(unittest.TestCase):
    def setUp(self):
        self.animator = FakeAnimator()
        self.page = FakePage()
        self.wheel = scroller(self.page, self.animator)

    def test_a_notch_is_travelled_over_140_ms_by_the_clock(self, _smooth):
        self.wheel.add(90)
        self.assertTrue(self.wheel.active)
        self.assertEqual(self.page.y, 0)            # nothing at once: the first frame draws
        self.animator.frame(0.035)
        self.assertEqual(self.page.y, round(90 * ease_out(0.25)))
        self.animator.frame(0.070)
        self.assertEqual(self.page.y, round(90 * ease_out(0.5)))
        self.animator.frame(0.140)
        self.assertEqual(self.page.y, 90)
        self.assertFalse(self.wheel.active)
        self.assertEqual(self.animator.steps, {})   # nothing left running

    def test_a_late_frame_skips_ahead_instead_of_slowing_down(self, _smooth):
        self.wheel.add(90)
        self.animator.frame(0.300)                  # the first frame comes very late
        self.assertEqual(self.page.calls, [90])     # one jump to the end, no slow tail
        self.assertFalse(self.wheel.active)

    def test_more_notches_retarget_from_where_the_view_is(self, _smooth):
        self.wheel.add(90)
        self.animator.frame(0.070)
        here = self.page.y
        self.wheel.add(90)                          # same way: the rest of the way plus a notch
        self.assertAlmostEqual(self.wheel.remaining, 180 - here, delta=1)
        self.animator.frame(0.105)
        self.assertGreater(self.page.y, here)
        self.animator.frame(0.070 + 0.140)
        self.assertEqual(self.page.y, 180)
        self.assertFalse(self.wheel.active)

    def test_a_notch_the_other_way_drops_the_rest(self, _smooth):
        self.wheel.add(90)
        self.animator.frame(0.035)
        here = self.page.y
        self.wheel.add(-90)
        self.animator.frame(0.035 + 0.140)
        self.assertEqual(self.page.y, max(0, round(90 * ease_out(0.25) - 90)))
        self.assertEqual(here, round(90 * ease_out(0.25)))

    def test_an_edge_ends_the_motion_and_keeps_no_debt(self, _smooth):
        self.page.end = 50
        self.wheel.add(90)
        self.animator.frame(0.070)                  # wants 79, the edge stops it at 50
        self.assertEqual(self.page.y, 50)
        self.assertFalse(self.wheel.active)
        self.assertEqual(self.wheel.remaining, 0.0)
        self.wheel.add(-30)                         # the way back starts from the edge
        self.animator.frame(0.070 + 0.140)
        self.assertEqual(self.page.y, 20)

    def test_stop_keeps_the_view_where_it_is(self, _smooth):
        self.wheel.add(90)
        self.animator.frame(0.035)
        here = self.page.y
        self.wheel.stop()
        self.assertFalse(self.wheel.active)
        self.animator.frame(0.200)
        self.assertEqual(self.page.y, here)

    def test_finish_travels_the_rest_at_once(self, _smooth):
        self.wheel.add(90)
        self.animator.frame(0.035)
        self.wheel.finish()
        self.assertEqual(self.page.y, 90)
        self.assertFalse(self.wheel.active)

    def test_one_scroll_per_frame_however_many_notches(self, _smooth):
        for _ in range(5):
            self.wheel.add(18)
        self.assertEqual(self.page.calls, [])
        self.animator.frame(0.016)
        self.assertEqual(len(self.page.calls), 1)
        self.animator.frame(0.500)
        self.assertEqual(self.page.y, 90)

    def test_expensive_frames_thin_out(self, _smooth):
        self.wheel.cost_ms = 12.0                   # a frame costs 12 ms: draw one every 24 ms
        self.assertEqual(self.wheel.interval_ms(), 24.0)
        self.wheel.add(90)
        self.animator.frame(0.016)
        first = list(self.page.calls)
        self.animator.frame(0.032)                  # 16 ms later: skipped
        self.assertEqual(self.page.calls, first)
        self.animator.frame(0.041)                  # 25 ms later: drawn
        self.assertEqual(len(self.page.calls), 2)
        self.animator.frame(0.140)                  # the end is always drawn, on time
        self.assertEqual(self.page.y, 90)

    def test_too_expensive_frames_scroll_at_once(self, _smooth):
        self.wheel.cost_ms = WheelScroller.INSTANT_MS + 1
        self.assertFalse(self.wheel.eases())
        self.wheel.add(90)
        self.assertEqual(self.page.calls, [90])
        self.assertFalse(self.wheel.active)

    def test_motion_off_scrolls_at_once_and_keeps_fractions(self, smooth):
        smooth.return_value = False
        self.wheel.add(45.4)
        self.assertEqual(self.page.calls, [45])
        self.assertFalse(self.wheel.active)
        self.assertEqual(self.animator.steps, {})
        self.wheel.add(0.4)
        self.assertEqual(self.page.calls, [45])     # 0.8 px pending
        self.wheel.add(0.4)
        self.assertEqual(self.page.calls, [45, 1])
        self.wheel.add(-0.4)                        # the other way drops what was pending
        self.assertEqual(self.page.calls, [45, 1])

    def test_motion_turned_off_mid_way_finishes_at_once(self, _smooth):
        page = self.page
        self.wheel.add(90)
        page.winfo_viewable = lambda: False         # e.g. the page was hidden
        self.animator.frame(0.016)
        self.assertEqual(page.y, 90)
        self.assertFalse(self.wheel.active)


class AnimatorTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass   # the test destroyed it already

    def test_one_loop_per_toplevel_that_runs_only_while_something_moves(self):
        import time
        animator = Animator.of(self.root)
        self.assertIs(Animator.of(self.root), animator)
        self.assertFalse(animator.running)
        seen = []
        animator.start("a", lambda now: seen.append(now) or len(seen) < 3)
        animator.start("b", lambda now: False)
        self.assertTrue(animator.running)
        # The first frame waits for idle (after the events already queued), once.
        self.assertIn("idle", str(self.root.tk.call("after", "info", animator._job)))
        deadline = time.monotonic() + 5
        while animator.active() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.002)
        self.assertEqual(len(seen), 3)
        self.assertFalse(animator.active())
        self.assertFalse(animator.running)          # stops when idle: no job left
        self.assertEqual(seen, sorted(seen))

    def test_a_failing_step_is_dropped_and_reported_while_the_others_go_on(self):
        animator = Animator(self.root, clock=lambda: 1.0)
        kept = []
        animator.start("bad", lambda now: 1 / 0)
        animator.start("good", lambda now: kept.append(now) or True)
        animator.tick()
        self.assertEqual(kept, [1.0])
        self.assertFalse(animator.active("bad"))
        self.assertTrue(animator.active("good"))
        self.assertEqual(len(self.errors), 1)
        self.assertIs(self.errors[0][0], ZeroDivisionError)
        animator.stop("good")
        self.assertFalse(animator.running)

    def test_destroying_the_toplevel_cancels_the_loop(self):
        animator = Animator.of(self.root)
        animator.start("a", lambda now: True)
        self.root.destroy()
        self.assertFalse(animator.running)
        self.assertFalse(animator.active())
        animator.start("b", lambda now: True)      # a dead animator schedules nothing
        self.assertFalse(animator.running)


class ColourTests(unittest.TestCase):
    def test_steps_name_both_ends_and_the_colours_between(self):
        self.assertEqual(tween.STEPS, 8)
        ramp = tween.steps("#000000", "#ffffff")
        self.assertEqual(len(ramp), tween.STEPS)
        self.assertEqual((ramp[0], ramp[-1]), ("#000000", "#ffffff"))
        self.assertEqual(ramp, tuple(sorted(ramp)))
        self.assertEqual(tween.steps("#202020", "#4a7bd8", 2), ("#202020", "#4a7bd8"))
        self.assertEqual(len(tween.steps("#202020", "#4a7bd8", 5)), 5)
        with self.assertRaises(ValueError):
            tween.steps("#202020", "#4a7bd8", 1)

    def test_mix_is_canvas_controls_mix(self):
        from dolly.ui import canvas_controls
        colours = ("#000000", "#ffffff", "#141414", "#4a7bd8", "#9a9a9b", " #1c1d20 ")
        for a in colours:
            for b in colours:
                for amount in (0.0, 0.1, 1 / 3, 0.5, 0.875, 1.0):
                    self.assertEqual(tween.mix(a, b, amount), canvas_controls.mix(a, b, amount))
        for bad in ("#12345", "white", "#ggg000"):
            with self.assertRaises(ValueError):
                canvas_controls.mix(bad, "#000000", .5)
            with self.assertRaises(ValueError):
                tween.mix(bad, "#000000", .5)

    def test_the_driver_imports_nothing_from_the_ui_but_boil(self):
        # Every control module (canvas_controls, sidebar, stage_page, the kit) imports tween: no cycle.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(tween))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(f"{'.' * node.level}{node.module or ''}:{alias.name}" for alias in node.names)
        self.assertEqual(names, {"sys", "time", "tkinter", "__future__:annotations", "typing:Callable", ".:boil"})


class TkCase(unittest.TestCase):
    """A root (withdrawn unless ``WITHDRAWN`` is False), collected callback errors, and an
    animator on a clock the test sets."""

    WITHDRAWN = True

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        if self.WITHDRAWN:
            self.root.withdraw()
        self.addCleanup(self.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.now = 0.0
        self.animator = Animator.of(self.root)
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

    def jobs(self):
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def idle_pass(self):
        """Serve the idle callbacks queued so far, once (as one turn of the event loop does)."""
        import _tkinter
        self.root.tk.dooneevent(_tkinter.IDLE_EVENTS | _tkinter.DONT_WAIT)

    def mapped_canvas(self):
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        canvas = tk.Canvas(self.root, width=40, height=20)
        canvas.pack()
        self.root.update()
        self.assertTrue(canvas.winfo_ismapped())
        return canvas


@patch.object(tween, "smooth", return_value=True)
class RunTests(TkCase):
    def test_the_first_step_is_drawn_at_once_then_one_per_frame(self, _smooth):
        seen, ended = [], []
        self.animator.run("a", 120, seen.append, done=lambda: ended.append(self.now))
        # The event's own frame: one frame along, before run() returns (PLAN-5 M1).
        self.assertEqual(seen, [ease_out(16 / 120)])
        self.assertTrue(0 < seen[0] < 1)
        self.assertTrue(self.animator.running)
        script = self.root.tk.splitlist(self.root.tk.call("after", "info", self.animator._job))
        self.assertEqual(str(script[1]), "timer")   # the next frame is a frame away, not at idle
        self.at(0.050)
        self.assertAlmostEqual(seen[-1], ease_out(0.066 / 0.120))
        self.at(0.102)
        self.assertLess(seen[-1], 1.0)
        self.assertEqual(ended, [])
        self.at(0.105)                              # 120 ms after the motion's first frame
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(ended, [0.105])
        self.assertFalse(self.animator.active("a"))
        self.assertEqual(seen, sorted(seen))

    def test_a_late_frame_skips_ahead(self, _smooth):
        seen = []
        self.animator.run("a", 180, seen.append, start=1.0, end=0.0)
        self.at(0.5)                                # the next frame comes very late: straight to the end
        self.assertEqual(seen[-1], 0.0)
        self.assertEqual(len(seen), 2)
        self.assertFalse(self.animator.active())

    def test_a_run_on_a_moving_key_retargets_from_its_value(self, _smooth):
        seen, ended = [], []
        self.animator.run("hover", 120, seen.append, start=0.0, end=1.0, done=lambda: ended.append("in"))
        self.at(0.040)
        here = seen[-1]
        self.now = 0.040
        self.animator.run("hover", 180, seen.append, start=1.0, end=0.0, done=lambda: ended.append("out"))
        # No jump back to 1: it leaves from where it was, its first step one frame along.
        self.assertAlmostEqual(seen[-1], here * (1 - ease_out(16 / 180)))
        self.at(0.040 + 0.180)
        self.assertEqual(seen[-1], 0.0)
        self.assertEqual(ended, ["out"])            # the replaced run never ends

    def test_stop_drops_and_finish_lands(self, _smooth):
        seen, ended = [], []
        self.animator.run("a", 120, seen.append, done=lambda: ended.append("a"))
        self.animator.stop("a")
        self.at(0.5)
        self.assertEqual(len(seen), 1)
        self.assertEqual(ended, [])
        self.assertFalse(self.animator.running)
        self.animator.run("a", 120, seen.append, done=lambda: ended.append("a"))
        self.animator.run("b", 120, seen.append, start=5.0, end=3.0, done=lambda: ended.append("b"))
        self.animator.finish("a")
        self.assertEqual(ended, ["a"])
        self.assertTrue(self.animator.active("b"))
        self.animator.finish()
        self.assertEqual((ended, seen[-1]), (["a", "b"], 3.0))
        self.assertFalse(self.animator.active())
        self.assertFalse(self.animator.running)

    def test_finish_also_ends_what_a_finished_motion_starts(self, _smooth):
        seen = []
        chain = lambda: self.animator.run("b", 150, seen.append, start=0.0, end=2.0)  # noqa: E731
        self.animator.run("a", 150, seen.append, done=chain)
        self.animator.finish()
        self.assertEqual(seen[-1], 2.0)
        self.assertFalse(self.animator.active())

    def test_finish_lets_a_wheel_scroller_travel_the_rest(self, _smooth):
        page = FakePage()
        wheel = WheelScroller(page, page.scroll, animator=self.animator, measure=False)
        wheel.add(90)
        self.at(0.035)
        self.assertLess(page.y, 90)
        self.animator.finish()
        self.assertEqual(page.y, 90)
        self.assertFalse(wheel.active)
        self.assertFalse(self.animator.running)

    def test_one_job_while_anything_moves_and_none_at_rest(self, _smooth):
        before = self.jobs()
        self.animator.run("a", 120, lambda value: None)
        self.animator.run("b", 180, lambda value: None)
        page = FakePage()
        wheel = WheelScroller(page, page.scroll, animator=self.animator, measure=False)
        wheel.add(60)                               # a wheel ease and two runs share the loop
        self.assertEqual(len(self.jobs() - before), 1)
        self.assertEqual(len(self.animator._steps), 3)
        self.at(0.050)
        self.assertGreater(page.y, 0)
        self.assertEqual(len(self.animator._steps), 3)
        self.animator.finish()
        self.assertEqual(self.jobs() - before, set())
        self.assertEqual(page.y, 60)

    def test_frames_go_through_the_toplevel_when_the_widget_after_is_patched(self, _smooth):
        canvas = tk.Canvas(self.root)
        item = canvas.create_rectangle(0, 0, 10, 10, fill="#202020", outline="")
        calls = []
        canvas.after = canvas.after_idle = lambda *args: calls.append(args)
        ramp = tween.ColorRamp(canvas, lambda colour: canvas.itemconfigure(item, fill=colour), "#202020", "#4a7bd8")
        ramp.enter()
        self.assertTrue(self.animator.running)
        self.animator.clock = time.perf_counter     # the real loop, real time
        deadline = time.monotonic() + 5
        while ramp.active and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.002)
        self.assertEqual(canvas.itemcget(item, "fill"), "#4a7bd8")
        self.assertEqual(calls, [])
        self.assertFalse(self.animator.running)

    def test_an_apply_error_drops_only_that_run(self, _smooth):
        seen = []

        def failing(value):
            if seen:
                raise ZeroDivisionError
            seen.append(value)

        kept = []
        self.animator.run("bad", 120, failing)
        self.animator.run("good", 120, kept.append)
        self.at(0.030)
        self.assertFalse(self.animator.active("bad"))
        self.assertTrue(self.animator.active("good"))
        self.assertEqual(len(self.errors), 1)
        self.assertIs(self.errors[0][0], ZeroDivisionError)
        self.at(0.200)
        self.assertEqual(kept[-1], 1.0)

    def test_destroying_the_toplevel_drops_runs_without_a_last_step(self, _smooth):
        seen, ended = [], []
        self.animator.run("a", 120, seen.append, done=lambda: ended.append(1))
        self.root.destroy()
        self.assertFalse(self.animator.active())
        self.assertFalse(self.animator.running)
        self.animator.tick(1.0)
        self.animator.run("b", 120, seen.append, done=lambda: ended.append(2))
        self.animator.finish()
        self.assertEqual((len(seen), ended), (1, []))


class RunAtOnceTests(TkCase):
    def test_motion_off_lands_at_once_and_schedules_nothing(self):
        seen, ended = [], []
        before = self.jobs()
        with patch.object(tween.boil, "motion_enabled", return_value=False):
            self.animator.run("a", 120, seen.append, start=0.0, end=1.0, done=lambda: ended.append(1))
        self.assertEqual((seen, ended), ([1.0], [1]))
        self.assertFalse(self.animator.running)
        self.assertEqual(self.jobs() - before, set())

    def test_a_withdrawn_root_lands_at_once(self):
        seen = []
        with patch.object(tween.boil, "motion_enabled", return_value=True):
            self.assertFalse(tween.smooth(self.root))
            self.animator.run("a", 180, seen.append, start=1.0, end=0.0)
        self.assertEqual(seen, [0.0])
        self.assertFalse(self.animator.running)

    def test_one_frame_or_nothing_to_travel_lands_at_once(self):
        seen = []
        with patch.object(tween, "smooth", return_value=True):
            self.animator.run("a", tween.FRAME_MS, seen.append)
            self.animator.run("b", 120, seen.append, start=0.5, end=0.5)
        self.assertEqual(seen, [1.0, 0.5])
        self.assertFalse(self.animator.running)


class PrewarmTests(TkCase):
    WITHDRAWN = False   # widgets map only in a mapped (cloaked) root

    def builder(self, log):
        def build(colour):
            log.append((colour, self.animator.active()))
            return tk.PhotoImage(master=self.root, width=1, height=1)
        return build

    def test_one_build_per_idle_callback(self):
        canvas = self.mapped_canvas()
        built = []
        colours = tween.steps("#202020", "#4a7bd8")[1:-1]
        tween.prewarm(canvas, self.builder(built), colours)
        self.assertEqual(built, [])                 # nothing at once
        for count in range(1, len(colours) + 1):
            self.idle_pass()
            self.assertEqual(len(built), count)
        self.assertEqual([colour for colour, _moving in built], list(colours))
        self.assertIsNone(self.animator._warm_job)

    def test_an_unmapped_widget_waits_for_its_first_map(self):
        canvas = tk.Canvas(self.root, width=40, height=20)
        built = []
        tween.prewarm(canvas, self.builder(built), ("#303030", "#404040"))
        for _ in range(3):
            self.idle_pass()
        self.assertEqual(built, [])
        canvas.pack()
        self.root.update_idletasks()                # maps it; update_idletasks serves every idle slice
        self.assertEqual([colour for colour, _moving in built], ["#303030", "#404040"])
        canvas.pack_forget()
        self.root.update_idletasks()
        canvas.pack()
        self.root.update_idletasks()
        self.assertEqual(len(built), 2)             # the first map only

    def test_nothing_is_built_while_something_moves(self):
        canvas = self.mapped_canvas()
        built = []
        with patch.object(tween, "smooth", return_value=True):
            self.animator.run("hover", 120, lambda value: None)
            tween.prewarm(canvas, self.builder(built), ("#303030", "#404040"))
            for _ in range(3):
                self.idle_pass()
            self.assertEqual(built, [])
            self.at(0.050)
            self.idle_pass()
            self.assertEqual(built, [])
            self.at(0.200)                          # the motion ends: the queue goes on
            self.idle_pass()
            self.idle_pass()
            self.assertEqual(built, [("#303030", False), ("#404040", False)])
            # A slice already queued when a motion starts gives way to it too.
            tween.prewarm(canvas, self.builder(built), ("#505050",))
            self.now = 1.0
            self.animator.run("hover", 120, lambda value: None)
            self.idle_pass()
            self.assertEqual(len(built), 2)
            self.at(1.200)
            self.idle_pass()
        self.assertEqual(built[2:], [("#505050", False)])

    def test_a_failing_build_is_reported_and_the_rest_still_build(self):
        canvas = self.mapped_canvas()
        built = []

        def build(colour):
            if colour == "#303030":
                raise ZeroDivisionError
            built.append(colour)

        tween.prewarm(canvas, build, ("#303030", "#404040"))
        self.root.update_idletasks()
        self.assertEqual(built, ["#404040"])
        self.assertEqual(len(self.errors), 1)
        self.assertIs(self.errors[0][0], ZeroDivisionError)

    def test_a_destroyed_widget_is_skipped(self):
        canvas = self.mapped_canvas()
        built = []
        tween.prewarm(canvas, built.append, ("#303030", "#404040"))
        canvas.destroy()
        self.root.update_idletasks()
        self.assertEqual((built, self.errors), ([], []))


@patch.object(tween, "smooth", return_value=True)
class ColorRampTests(TkCase):
    WITHDRAWN = False
    REST, HOVER, PRESSED = "#202020", "#4a7bd8", "#2f5bb0"

    def make(self, **options):
        canvas = tk.Canvas(self.root)
        item = canvas.create_rectangle(0, 0, 10, 10, fill=self.REST, outline="")
        ramp = tween.ColorRamp(canvas, lambda colour: canvas.itemconfigure(item, fill=colour),
                               self.REST, self.HOVER, pressed=self.PRESSED, **options)
        return ramp, (lambda: canvas.itemcget(item, "fill"))

    def lands(self, ramp, fill, colour, start, duration_ms):
        """The move begun at ``start`` ends on ``colour`` after ``duration_ms`` (the event's frame
        included), and is still moving just before."""
        end = start + (duration_ms - tween.FRAME_MS) / 1000.0
        self.at(end - 0.002)
        self.assertTrue(ramp.active)
        self.at(end + 0.001)
        self.assertFalse(ramp.active)
        self.assertEqual(fill(), colour)

    def test_hover_takes_120_ms_in_180_out_and_press_120(self, _smooth):
        ramp, fill = self.make()
        ramp.enter()
        first = fill()
        self.assertNotIn(first, (self.REST, self.HOVER))    # the first step is drawn by the event itself
        self.assertEqual(first, tween.mix(self.HOVER, self.REST, ease_out(16 / 120)))
        self.lands(ramp, fill, self.HOVER, 0.0, tween.HOVER_IN_MS)
        self.now = 1.0
        ramp.press()
        self.lands(ramp, fill, self.PRESSED, 1.0, tween.PRESS_MS)
        self.now = 2.0
        ramp.release()
        self.lands(ramp, fill, self.HOVER, 2.0, tween.PRESS_MS)
        self.now = 3.0
        ramp.leave()
        self.lands(ramp, fill, self.REST, 3.0, tween.HOVER_OUT_MS)
        self.assertEqual((tween.HOVER_IN_MS, tween.HOVER_OUT_MS, tween.PRESS_MS), (120, 180, 120))

    def test_a_move_retargets_from_where_the_colour_is(self, _smooth):
        ramp, fill = self.make()
        ramp.enter()
        self.at(0.030)
        here = ramp.value
        self.now = 0.030
        ramp.leave()
        self.assertLess(ramp.value, here)           # heads back from where it was, no jump to hover
        self.assertGreater(ramp.value, 0.0)
        self.at(0.030 + 0.180)
        self.assertEqual(fill(), self.REST)

    def test_a_move_to_the_end_it_heads_for_keeps_its_timing(self, _smooth):
        ramp, fill = self.make()
        ramp.enter()
        self.at(0.050)
        self.now = 0.050
        ramp.enter()                                # a redraw while still hovered: not a new move
        self.lands(ramp, fill, self.HOVER, 0.0, tween.HOVER_IN_MS)
        self.now = 1.0
        ramp.leave()
        self.at(1.050)
        self.now = 1.050
        ramp.enter()                                # back the other way: a new move from where it is
        self.lands(ramp, fill, self.HOVER, 1.050, tween.HOVER_IN_MS)

    def test_leaving_while_pressed_goes_back_through_hover(self, _smooth):
        ramp, fill = self.make()
        ramp.jump(self.HOVER)
        ramp.press()
        self.at(0.200)
        self.assertEqual((fill(), ramp.value), (self.PRESSED, 2.0))
        self.now = 0.2
        ramp.leave()
        self.at(0.260)
        self.assertTrue(0.0 < ramp.value < 2.0)
        self.at(0.400)
        self.assertEqual(fill(), self.REST)

    def test_jump_is_immediate_and_ends_the_motion(self, _smooth):
        ramp, fill = self.make()
        ramp.enter()
        ramp.jump("#131416")                        # e.g. disabled: at once, no motion
        self.assertEqual(fill(), "#131416")
        self.assertFalse(ramp.active)
        self.assertEqual(ramp.value, 0.0)
        ramp.jump(self.HOVER)
        self.assertEqual(ramp.value, 1.0)
        ramp.enter()                                # already there: nothing to do
        self.assertFalse(ramp.active)

    def test_motion_off_lands_at_once(self, smooth):
        smooth.return_value = False
        ramp, fill = self.make()
        before = self.jobs()
        ramp.enter()
        self.assertEqual(fill(), self.HOVER)
        ramp.press()
        self.assertEqual(fill(), self.PRESSED)
        self.assertFalse(self.animator.running)
        self.assertEqual(self.jobs() - before, set())

    def test_new_colours_take_over_a_move_under_way(self, _smooth):
        ramp, fill = self.make()
        ramp.enter()
        self.at(0.040)
        ramp.set_colours("#101010", "#ffffff")
        self.at(0.200)
        self.assertEqual(fill(), "#ffffff")
        ramp.finish()

    def test_image_faces_use_pre_warmed_steps_and_never_build_in_a_frame(self, _smooth):
        built = []

        def images(colour):
            built.append((colour, self.animator._ticking))
            image = tk.PhotoImage(master=self.root, width=1, height=1)
            image.colour = colour
            return image

        canvas = self.mapped_canvas()
        item = canvas.create_rectangle(0, 0, 10, 10, fill=self.REST, outline="")
        shown = []

        def apply(colour):
            canvas.itemconfigure(item, fill=colour)
            shown.append(ramp.image(colour).colour)

        ramp = tween.ColorRamp(canvas, apply, self.REST, self.HOVER, pressed=self.PRESSED, images=images)
        self.assertEqual([colour for colour, _ in built], [self.REST, self.HOVER, self.PRESSED])  # ends at once
        ramp.enter()
        self.at(0.050)
        self.at(0.200)
        self.assertEqual(len(built), 3)             # nothing built during the motion
        self.assertEqual(shown[-1], self.HOVER)
        self.assertIn(shown[0], (self.REST, self.HOVER))   # nearest built so far
        self.root.update_idletasks()                # the queue goes on once nothing moves
        between = set(tween.steps(self.REST, self.HOVER) + tween.steps(self.HOVER, self.PRESSED))
        self.assertEqual({colour for colour, _ in built}, between)
        self.assertEqual(len(built), len(between))  # each colour once
        self.assertFalse(any(ticking for _, ticking in built))
        step = tween.steps(self.REST, self.HOVER)[3]
        self.assertEqual(ramp.image(step).colour, step)
        near = tween.mix(self.HOVER, self.REST, 0.44)
        self.assertEqual(ramp.image(near).colour, step)    # 0.44 is closest to step 3 of 7 (0.43)

    def test_new_colours_build_new_faces(self, _smooth):
        built = []
        canvas = tk.Canvas(self.root)
        ramp = tween.ColorRamp(canvas, lambda colour: None, self.REST, self.HOVER,
                               images=lambda colour: built.append(colour) or colour)
        ramp.set_colours("#000000", "#ffffff")
        self.assertEqual(built, [self.REST, self.HOVER, "#000000", "#ffffff"])
        self.assertEqual(ramp.image("#f0f0f0"), "#ffffff")


if __name__ == "__main__":
    unittest.main()
