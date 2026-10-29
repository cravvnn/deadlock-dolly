"""Camera scroll-edge fades (PLAN-5 N11, 2.10, 5.2): row rendering, guards, a fake backend, the alpha ramp."""
import os
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.ui import fade
from dolly.ui.kit_page import ColumnSpec, KitPage
from dolly.ui.scale import px
from dolly.ui.tween import Animator


class FakeBackend:
    """Records every call a band makes to the window system."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def _log(self, name, *args):
        if self.fail:
            raise OSError("backend failed")
        self.calls.append((name, *args))

    def create(self, owner):
        self._log("create", owner)
        return {"id": len([c for c in self.calls if c[0] == "create"])}

    def update(self, handle, x, y, width, height, data, alpha):
        self._log("update", handle["id"], x, y, width, height, len(data), alpha)

    def blend(self, handle, alpha):
        self._log("blend", handle["id"], alpha)

    def move(self, handle, x, y):
        self._log("move", handle["id"], x, y)

    def show(self, handle):
        self._log("show", handle["id"])

    def hide(self, handle):
        self._log("hide", handle["id"])

    def uncloak(self, handle):
        self._log("uncloak", handle["id"])

    def destroy(self, handle):
        self._log("destroy", handle["id"])

    def names(self):
        return [call[0] for call in self.calls]


class RenderTests(unittest.TestCase):
    def test_rows_are_premultiplied_smoothstep_and_only_over_the_spans(self):
        rgb = (8, 9, 11)
        data = fade.render_rows(10, 5, ((2, 6, 0, 5),), rgb, "top")
        self.assertEqual(len(data), 10 * 5 * 4)

        def pixel(x, y):
            return tuple(data[(y * 10 + x) * 4:(y * 10 + x) * 4 + 4])
        self.assertEqual(pixel(0, 0), (0, 0, 0, 0))
        self.assertEqual(pixel(7, 2), (0, 0, 0, 0))
        b, g, r, a = pixel(3, 0)
        self.assertEqual(a, 255)                        # the cut edge is opaque
        self.assertEqual((r, g, b), rgb)
        alphas = [pixel(3, y)[3] for y in range(5)]
        self.assertEqual(alphas, sorted(alphas, reverse=True))
        self.assertEqual(alphas[-1], 0)
        self.assertEqual(alphas[2], round(255 * .5))      # smoothstep(0.5)
        b, g, r, a = pixel(3, 1)
        self.assertEqual(b, (rgb[2] * a + 127) // 255)  # premultiplied
        bottom = fade.render_rows(10, 5, ((0, 10, 0, 5),), rgb, "bottom")
        self.assertEqual(bottom[-1], 255)
        self.assertEqual(bottom[3], 0)
        # Spans that cover only some rows.
        partial = fade.render_rows(4, 4, ((0, 4, 2, 4),), rgb, "bottom")
        self.assertEqual(partial[:2 * 4 * 4], bytes(2 * 4 * 4))

    def test_a_wide_band_renders_fast_from_row_segments(self):
        spans = ((0, 919, 0, 30), (949, 1395, 0, 60))
        timings = []
        for _ in range(7):
            start = time.perf_counter()
            fade.render_rows(1395, 60, spans, ui_theme.TOKENS["backdrop"], "top")
            timings.append(time.perf_counter() - start)
        self.assertLess(sorted(timings)[3] * 1000, 2.0)


class FadeCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback raised an exception")

    def page(self, size="948x400", backend=None):
        gui_theme.initialize(self.root)
        self.root.deiconify()
        self.root.geometry(size)
        page = KitPage(self.root, columns=ColumnSpec(main_min=460), pinned=True, fade=True)
        page.fade.backend = backend
        page.pack(fill="both", expand=True)
        main, side = page.columns()
        for title in ("Capture", "Cameras", "Curves", "Selected"):
            card = main.add_card(title) if title != "Selected" else side.add_card(title)
            for _ in range(4):
                card.row().label("row")
        self.root.update()
        page.layout_now()
        # The window just appeared and was sized: the bands wait for it to settle (250 ms); skip the wait.
        if page.fade._move_job is not None:
            self.root.after_cancel(page.fade._move_job)
            page.fade._move_job = None
        page.fade._held.clear()
        page.fade._remeasure()
        return page


class GuardTests(FadeCase):
    def test_available_is_false_when_turned_off_withdrawn_cloaked_or_off_screen(self):
        with patch.dict(os.environ, {"DOLLY_UI_FADE": "0"}):
            self.assertFalse(fade.available(self.root))
        self.assertFalse(fade.available(self.root))           # withdrawn
        self.root.deiconify()
        self.root.update()
        with patch.object(fade, "_cloaked", return_value=True), patch.object(fade, "_on_monitor", return_value=True):
            self.assertFalse(fade.available(self.root))
        with patch.object(fade, "_cloaked", return_value=False), patch.object(fade, "_on_monitor", return_value=False):
            self.assertFalse(fade.available(self.root))
        with patch.object(fade, "_cloaked", return_value=False), patch.object(fade, "_on_monitor", return_value=True):
            self.assertTrue(fade.available(self.root))
        self.root.withdraw()
        self.root.update_idletasks()
        self.assertFalse(fade.available(self.root))

    def test_the_real_backend_is_never_touched_for_a_hidden_window(self):
        touched = []

        class Guard:
            def __init__(self):
                touched.append("Win32Backend")
        # Explicitly model the cloaked test surface rather than relying on an external harness.
        with patch.object(fade, "Win32Backend", Guard), patch.object(fade, "_cloaked", return_value=True):
            page = self.page()
            for _ in range(5):
                page._scroll_units(20)
                self.root.update()
            page.scroll_to(0)
            self.root.withdraw()
            self.root.update()
            page.fade.update()
        self.assertEqual(touched, [])
        self.assertFalse(page.fade.broken)


class BandTests(FadeCase):
    def setUp(self):
        super().setUp()
        self.patcher = patch.object(fade, "available", return_value=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_bands_follow_the_view_and_the_cards(self):
        backend = FakeBackend()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            page = self.page(backend=backend)
            scroll = page.fade
            self.assertTrue(scroll.bands["bottom"].shown)
            self.assertFalse(scroll.bands["top"].shown)
            self.assertIn("uncloak", backend.names())
            height = scroll.band_height(page.canvas.winfo_height())
            self.assertEqual(height, min(px(page.scale, 40), page.canvas.winfo_height() // 3))
            page._scroll_units(30)
            self.assertTrue(scroll.bands["top"].shown)
            signature = scroll.bands["top"].signature
            self.assertEqual(signature[2], scroll.spans("top", int(page.canvas.canvasy(0)), height,
                                                        page.canvas.winfo_height()))
            self.assertTrue(signature[2])                      # cards under the band
            page.scroll_to(0)
            self.assertFalse(scroll.bands["top"].shown)
            self.assertEqual(backend.calls[-1][0], "hide")

    def test_bitmaps_are_rebuilt_only_when_the_spans_change_and_reused_scrolling_back(self):
        backend = FakeBackend()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            page = self.page(backend=backend)
            scroll = page.fade
            signatures = {band.signature for band in scroll.bands.values() if band.signature}
            for _ in range(24):
                page._scroll_units(7)
                signatures |= {band.signature for band in scroll.bands.values() if band.signature}
            self.assertEqual(scroll.renders, len(signatures))    # one render per distinct signature
            self.assertGreater(len(signatures), 2)               # card edges crossed the bands
            renders, updates = scroll.renders, backend.names().count("update")
            for _ in range(24):
                page._scroll_units(-7)
            for _ in range(24):
                page._scroll_units(7)
            self.assertEqual(scroll.renders, renders)            # scrolling back: all from the cache
            self.assertGreater(backend.names().count("update"), updates)

    def test_a_move_hides_at_once_and_shows_again_after_settling(self):
        backend = FakeBackend()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            page = self.page(backend=backend)
            scroll = page.fade
            self.assertTrue(scroll.bands["bottom"].shown)
            with patch.object(self.root, "after", lambda ms, func: "job"):
                scroll._root_configured(type("E", (), {"widget": self.root})())
            self.assertFalse(scroll.bands["bottom"].shown)
            self.assertEqual(backend.calls[-1][0], "hide")
            page._scroll_units(5)
            self.assertFalse(scroll.bands["bottom"].shown)
            scroll._moved()
            self.assertTrue(scroll.bands["bottom"].shown)

    def test_any_error_leaves_a_hard_edge_for_good(self):
        backend = FakeBackend()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            page = self.page(backend=backend)
            backend.fail = True
            page._scroll_units(30)
            self.assertTrue(page.fade.broken)
            backend.fail = False
            count = len(backend.calls)
            page._scroll_units(30)
            page.fade.update()
            self.assertEqual(len(backend.calls), count)

    def test_bands_ramp_their_alpha_with_blend_only_calls(self):
        backend = FakeBackend()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            page = self.page(backend=backend)
        scroll = page.fade
        animator = Animator.of(self.root)
        clock = [100.0]
        animator.clock = lambda: clock[0]
        with patch("dolly.ui.tween.smooth", return_value=True):
            backend.calls.clear()
            page._scroll_units(30)                              # the top edge is cut now
            band = scroll.bands["top"]
            self.assertTrue(band.shown)
            first = [c for c in backend.calls if c[0] in ("update", "blend")]
            self.assertEqual(first[0][0], "update")
            self.assertEqual(first[0][-1], 0)                   # shown at alpha 0 ...
            self.assertEqual(first[1][0], "blend")              # ... and the first step in the same call
            self.assertGreater(first[1][2], 0)
            backend.calls.clear()
            clock[0] += .075
            animator.tick(clock[0])
            clock[0] += .1
            animator.tick(clock[0])
            self.assertEqual({c[0] for c in backend.calls}, {"blend"})   # no bitmap, no move
            self.assertEqual(backend.calls[-1][2], 255)
            self.assertFalse(animator.active((scroll, "top")))
            # Uncut: ramps to 0, then hides; a reversal mid-ramp retargets from where it is.
            page.scroll_to(0)
            clock[0] += .05
            animator.tick(clock[0])
            middle = band.alpha
            self.assertTrue(0 < middle < 1)
            page._scroll_units(30)
            self.assertGreaterEqual(band.alpha, middle)
            clock[0] += .2
            animator.tick(clock[0])
            self.assertEqual(band.alpha, 1.0)
            page.scroll_to(0)
            clock[0] += .2
            animator.tick(clock[0])
            self.assertFalse(band.shown)
            self.assertEqual(backend.calls[-1][0], "hide")
        animator.finish()


if __name__ == "__main__":
    unittest.main()
