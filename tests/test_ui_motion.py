"""Posterized motion of the redesigned shell: backdrop, stickers and canvas controls.

The windows are real (the backdrop needs mapped canvases to place itself) but every test
holds the boil clock and ticks it by hand, so nothing moves on its own.
"""
import json
import os
from pathlib import Path
import time
import tkinter as tk
import unittest
from unittest import mock

from dolly import ui_theme
from dolly.ui import art, boil, stickers, tween
from dolly.ui.action_proxy import ActionProxy, ProgressProxy
from dolly.ui.backdrop import (DESIGN_POSE, Backdrop, clip_polygon, clip_to_box, cover_scale, source_map,
                               working_zoom)
from dolly.ui.canvas_controls import CanvasDot, CanvasFocus, CanvasLink, CanvasProgress, CanvasText, mix
from dolly.ui.sidebar import ART_TOP, Sidebar
from dolly.ui.stickers import POSES, CanvasSticker, paper_image, paper_rows

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "assets" / "ui"
FONT = ("Segoe UI", -12)
STICKER_FONT = ("Segoe UI", -16, "bold")
TOKENS = ui_theme.TOKENS


class FakeAssets:
    """A tiny still picture in five solid-colour bands (11 x 2 px each, one picture px per
    100 design px) and the real shard poses."""

    COLORS = ((200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0), (0, 200, 200))
    WIDTH, ROWS = 11, 2

    def __init__(self, scale=.01, width=WIDTH, rows=ROWS):
        manifest = json.loads((ART / "manifest.json").read_text(encoding="utf-8"))
        self.width, self.rows = width, rows
        self.manifest = {"levels": {"files": [f"band{i}" for i in range(5)], "size": [width, rows], "scale": scale,
                                    "stacked": True, "cycle": [0]},
                         "shapes": manifest["shapes"]}
        self.reads = []

    def level_rgb(self, index):
        self.reads.append(index)
        return self.width, self.rows, bytes(self.COLORS[index]) * (self.width * self.rows)


def canvas_calls():
    """Patch ``coords`` and ``itemconfigure`` on every canvas; the mocks record (canvas, item)."""
    return (mock.patch.object(tk.Canvas, "coords", autospec=True, side_effect=tk.Canvas.coords),
            mock.patch.object(tk.Canvas, "itemconfigure", autospec=True, side_effect=tk.Canvas.itemconfigure))


def touched(*mocks):
    return [(call.args[0], call.args[1]) for each in mocks for call in each.call_args_list]


def _destroy(root):
    try:
        root.destroy()
    except tk.TclError:
        pass      # a test destroyed it already


class TkCase(unittest.TestCase):
    motion = True

    def setUp(self):
        patcher = mock.patch.object(boil, "motion_enabled", return_value=self.motion)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.make_root()

    def make_root(self, size="420x300"):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(_destroy, root)
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        self.addCleanup(lambda: self.assertEqual(errors, [], "Tk callback raised an exception"))
        root.geometry(size)
        self.clock = boil.BoilClock.of(root)
        self.clock.hold("test")     # ticks only when a test calls tick()
        return root

    def settle(self, root=None):
        root = root or self.root
        root.update()
        clock = boil.BoilClock.of(root)
        if "settle" in clock.holds:
            clock.release("settle")
        root.update()

    def canvas(self, **options):
        canvas = tk.Canvas(self.root, highlightthickness=0, borderwidth=0, background=TOKENS["bg"], **options)
        canvas.pack(fill="both", expand=True)
        return canvas


class PolygonClipTests(unittest.TestCase):
    def test_half_plane_keeps_the_inside_and_adds_the_crossings(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertEqual(clip_polygon(square, 1, 0, -5), [(5.0, 0.0), (10, 0), (10, 10), (5.0, 10.0)])
        self.assertEqual(clip_polygon(square, 1, 0, 5), square)
        self.assertEqual(clip_polygon(square, -1, 0, -20), [])

    def test_box_clip_cuts_a_triangle_and_drops_what_is_outside(self):
        triangle = [(0, 0), (20, 0), (0, 20)]
        piece = clip_to_box(triangle, (5, 5, 30, 30))
        self.assertEqual(sorted((round(x, 6), round(y, 6)) for x, y in piece), [(5, 5), (5, 15), (15, 5)])
        self.assertEqual(clip_to_box(triangle, (40, 40, 50, 50)), [])
        self.assertEqual(clip_to_box(triangle, (-1, -1, 30, 30)), triangle)

    def test_cover_scale_and_source_map(self):
        self.assertEqual(cover_scale(1010, 810), 1.0)
        self.assertEqual(cover_scale(2020, 810), 2.0)
        self.assertEqual(cover_scale(1010, 1620), 2.0)
        # Working pixel i at zoom 2 covers design px 2i..2i+2; with art at half size that is art px i.
        self.assertEqual(source_map(4, 2, 1.0, .5, 505), [0, 1, 2, 3])
        self.assertEqual(source_map(3, 2, 1.0, .5, 2), [0, 1, 1])
        # Design-size art on a 150 % stage at zoom 2: three of every four art px (nearest neighbour).
        self.assertEqual(source_map(6, 2, 1.5, 1.0, 1010), [0, 2, 3, 4, 6, 7])

    def test_the_picture_is_never_stretched_above_the_art_resolution(self):
        self.assertEqual(working_zoom(1.0), 1)       # 100 %: the art's own pixels
        self.assertEqual(working_zoom(.8), 1)        # a small window: shrink once
        self.assertEqual(working_zoom(1.25), 2)      # larger: Tk enlarges a smaller working copy
        self.assertEqual(working_zoom(1.5), 2)
        self.assertEqual(working_zoom(2.0), 2)
        self.assertEqual(working_zoom(2.4), 3)
        self.assertEqual(working_zoom(1.0, .5), 2)   # half-size art


class BackdropTests(TkCase):
    # The Figma shard sits at the bottom right of the screen; the page covers part of it.
    PAGE = (430, 380, 220, 110)

    def build(self, assets="fake"):
        self.root.geometry("660x500")
        x, y, width, height = self.PAGE
        stage = self.canvas(width=660, height=500)
        page = tk.Canvas(stage, width=width, height=height, highlightthickness=0, borderwidth=0,
                         scrollregion=(0, 0, width, 1000), yscrollincrement=1)
        stage.create_window(x, y, window=page, anchor="nw")
        backdrop = Backdrop(self.root, scale=1.0, assets=FakeAssets() if assets == "fake" else assets)
        backdrop.install()
        backdrop.set_frame(stage)
        backdrop.attach(stage, lambda: [(0, 0, 660, 60), (0, 210, 660, 500)])
        backdrop.attach(page, lambda: [(0, 0, width, height)])
        self.settle()
        return backdrop, stage, page

    def test_install_makes_it_the_window_backdrop_and_the_first_boil_member(self):
        backdrop, stage, page = self.build()
        self.assertIs(Backdrop.of(page), backdrop)
        self.assertIs(self.clock.members[0], backdrop)

    def test_two_canvases_share_one_picture_and_stay_aligned_while_scrolling(self):
        backdrop, stage, page = self.build()
        self.assertIsNotNone(backdrop.display)
        (stage_pic,) = stage.find_withtag("bd_pic")
        (page_pic,) = page.find_withtag("bd_pic")
        self.assertEqual(stage.itemcget(stage_pic, "image"), str(backdrop.display))
        self.assertEqual(page.itemcget(page_pic, "image"), str(backdrop.display))
        self.assertEqual(stage.itemcget(stage_pic, "state"), "disabled")
        self.assertEqual(stage.coords(stage_pic), [0.0, 0.0])
        self.assertEqual(page.coords(page_pic), [-430.0, -380.0])
        page_base = page.find_withtag("bd_base")
        self.assertEqual(len(page_base), len(backdrop.base_runs))
        self.assertEqual({page.itemcget(item, "state") for item in page_base}, {"hidden"},
                         "the gradient hides under the picture")
        page.yview_scroll(40, "units")
        backdrop.sync(page)
        x, y = page.coords(page_pic)
        self.assertEqual((x - page.canvasx(0), y - page.canvasy(0)), (-430.0, -380.0))
        vectors = page.find_withtag("bd_vec")
        self.assertTrue(vectors)
        for item in vectors:     # shapes stay inside the page's visible area, in scrolled coordinates
            if page.itemcget(item, "state") != "hidden":
                ys = page.coords(item)[1::2]
                self.assertGreaterEqual(min(ys), page.canvasy(0) - 1.01)
                self.assertLessEqual(max(ys), page.canvasy(0) + 111.01)
        backdrop_items = stage.find_withtag("bd")
        self.assertEqual(stage.find_all()[:len(backdrop_items)], backdrop_items, "the backdrop sits at the bottom")

    def test_the_picture_is_the_stacked_bands_and_one_opaque_image(self):
        backdrop, stage, page = self.build()
        self.assertEqual(sorted(backdrop.assets.reads), [0, 1, 2, 3, 4], "every band is read once")
        self.assertEqual(backdrop.renders, 1)
        # One picture px per 100 design px, two rows per band; the stage shows design px at 0.65.
        self.assertEqual(backdrop.picture_at(10, 10), FakeAssets.COLORS[0])
        self.assertEqual(backdrop.picture_at(400, 140), FakeAssets.COLORS[1])
        self.assertEqual(backdrop.picture_at(10, 490), FakeAssets.COLORS[3])
        self.assertEqual((backdrop.display.width(), backdrop.display.height()), (660, 500))
        for x, y in ((0, 0), (659, 0), (0, 499), (659, 499), (330, 250)):
            self.assertEqual(self.root.tk.call(backdrop.display.name, "transparency", "get", x, y), 0,
                             "an opaque picture is copied to the screen, never blended")

    def test_ticks_move_only_the_shard_and_never_touch_the_picture(self):
        backdrop, stage, page = self.build()
        display, renders, reads = backdrop.display, backdrop.renders, list(backdrop.assets.reads)
        before = backdrop.picture_at(400, 140)
        shard = set(stage.find_withtag("bd_vec")) | set(page.find_withtag("bd_vec"))
        seen = {backdrop.pose}
        coords, configure = canvas_calls()
        with coords as moved, configure as changed:
            for _ in range(24):
                pose = backdrop.pose
                self.clock.tick()
                self.assertNotEqual(backdrop.pose, pose, "a pose never repeats")
                seen.add(backdrop.pose)
            calls = touched(moved, changed)
        self.assertGreater(len(seen), 3)
        self.assertTrue(calls, "the shard moved")
        for canvas, item in calls:
            self.assertIn("bd_vec", canvas.gettags(item), "only the shard is sent to Tk on a tick")
        self.assertLessEqual({item for _canvas, item in calls}, shard | set(stage.find_withtag("bd_vec"))
                             | set(page.find_withtag("bd_vec")))
        self.assertIs(backdrop.display, display)
        self.assertEqual((backdrop.renders, backdrop.assets.reads), (renders, reads))
        self.assertEqual(backdrop.picture_at(400, 140), before)

    def test_a_tick_changes_only_a_small_corner_of_the_stage(self):
        backdrop, stage, page = self.build()

        def shard_boxes():
            boxes = {}
            for item in stage.find_withtag("bd_vec"):
                if stage.itemcget(item, "state") != "hidden":
                    xs, ys = stage.coords(item)[::2], stage.coords(item)[1::2]
                    boxes[item] = (min(xs), min(ys), max(xs), max(ys))
            return boxes

        for _ in range(8):
            old = shard_boxes()
            self.clock.tick()
            new = shard_boxes()
            # Tk repaints one rectangle around every change: here, the old and new shard pieces.
            boxes = [box for item in set(old) | set(new) for box in (old.get(item), new.get(item)) if box]
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            self.assertLess((x1 - x0) * (y1 - y0), .1 * 660 * 500)
            self.assertGreater(y0, 380, "the shard boils in the bottom right corner")

    def test_vectors_follow_the_pose_on_every_canvas(self):
        backdrop, stage, page = self.build()
        before = [stage.coords(item) for item in stage.find_withtag("bd_vec")]
        self.clock.tick()
        after = [stage.coords(item) for item in stage.find_withtag("bd_vec")]
        self.assertNotEqual(before, after)
        count = len(stage.find_withtag("bd_vec"))
        for _ in range(10):      # items are pooled, not created per tick
            self.clock.tick()
        self.assertLessEqual(len(stage.find_withtag("bd_vec")), count + 4)

    def test_a_sync_without_changes_sends_nothing(self):
        backdrop, stage, page = self.build()
        coords, configure = canvas_calls()
        with coords as moved, configure as changed:
            backdrop.sync(redraw=True)
            backdrop.sync(stage, redraw=True)
            backdrop.invalidate()
            self.root.update()
            self.assertEqual(touched(moved, changed), [], "Tk repaints an item for any change, even to the same value")
        page.yview_scroll(40, "units")
        with coords as moved, configure as changed:
            backdrop.sync(page)
            items = {item for _canvas, item in touched(moved, changed)}
        self.assertIn(page.find_withtag("bd_pic")[0], items, "a scroll moves the page's picture with the window")
        self.assertFalse(items & set(page.find_withtag("bd_base")), "the hidden gradient stays where it is")

    def test_settle_hides_picture_and_shapes_then_restores_them(self):
        backdrop, stage, page = self.build()
        self.clock.hold("settle")
        self.assertTrue(backdrop.frozen)
        for canvas in (stage, page):
            self.assertEqual({canvas.itemcget(item, "state") for item in canvas.find_withtag("bd_pic")}, {"hidden"})
            self.assertEqual({canvas.itemcget(item, "state") for item in canvas.find_withtag("bd_vec")}, {"hidden"})
            self.assertEqual(canvas.itemcget(canvas.find_withtag("bd_base")[0], "state"), "disabled")
        pose = backdrop.pose
        backdrop.advance()
        self.assertEqual(backdrop.pose, pose, "nothing moves while the size settles")
        self.clock.release("settle")
        self.root.update()
        self.assertFalse(backdrop.frozen)
        self.assertEqual(backdrop.renders, 1, "the same size needs no new picture")
        for canvas in (stage, page):
            self.assertEqual({canvas.itemcget(item, "state") for item in canvas.find_withtag("bd_pic")}, {"disabled"})
            self.assertIn("disabled", {canvas.itemcget(item, "state") for item in canvas.find_withtag("bd_vec")})
            self.assertEqual(canvas.itemcget(canvas.find_withtag("bd_base")[0], "state"), "hidden")

    def test_a_resize_rebuilds_the_picture_once_for_the_new_size(self):
        backdrop, stage, page = self.build()
        self.root.geometry("760x560")
        self.settle()
        self.assertEqual(backdrop.renders, 2)
        self.assertEqual(backdrop._size, (stage.winfo_width(), stage.winfo_height()))
        self.assertEqual((backdrop.display.width(), backdrop.display.height()),
                         (stage.winfo_width(), stage.winfo_height()))
        self.assertEqual(stage.itemcget(stage.find_withtag("bd_pic")[0], "image"), str(backdrop.display))
        self.assertEqual(backdrop.base_runs[-1][1], stage.winfo_height())
        self.assertEqual(stage.coords(stage.find_withtag("bd_base")[-1])[2:], [760.0, float(stage.winfo_height())])

    def test_one_stage_px_per_design_px_shows_the_art_rows_as_they_are(self):
        assets = FakeAssets(scale=1.0, width=6, rows=2)
        backdrop = Backdrop(self.root, scale=1.0, assets=assets)
        backdrop._cover = 1.0
        with mock.patch.object(art, "stretch_rgb", side_effect=AssertionError("no stretch needed")):
            picture = backdrop._render_picture((6, 7))
        self.assertEqual((picture.width(), picture.height()), (6, 7))
        self.assertEqual([picture.get(0, row) for row in range(7)],
                         [FakeAssets.COLORS[row // 2] for row in range(7)])

    def test_flat_fallback_without_art_keeps_the_gradient_and_design_shard(self):
        backdrop, stage, page = self.build(assets=None)
        self.assertIsNone(backdrop.display)
        self.assertTrue(backdrop.base_runs)
        self.assertEqual(stage.itemcget(stage.find_withtag("bd_pic")[0], "state"), "hidden")
        self.assertEqual(stage.itemcget(stage.find_withtag("bd_base")[0], "state"), "disabled")
        self.assertEqual(backdrop.poses, [DESIGN_POSE])
        self.assertTrue(stage.find_withtag("bd_vec"))
        self.assertEqual(stage.itemcget(stage.find_withtag("bd_vec")[0], "fill"), TOKENS["backdrop_shard"])
        self.clock.tick()
        self.assertEqual(backdrop.pose, 0)
        bases = stage.find_withtag("bd_base")
        self.assertEqual(stage.itemcget(bases[0], "fill"), TOKENS["stage_top"])
        self.assertEqual(stage.itemcget(bases[-1], "fill"), TOKENS["bg"])
        self.assertEqual(stage.coords(bases[0])[:2], [0.0, 0.0])
        self.assertEqual(stage.coords(bases[-1])[3], 500.0)
        tops = [top for top, _bottom, _color in backdrop.base_runs]
        self.assertEqual(tops, sorted(tops))
        self.assertLess(len(bases), 60, "a few bands, not one rectangle per row")

    def test_hidden_canvases_are_only_marked_dirty(self):
        backdrop, stage, page = self.build()
        hidden = tk.Canvas(self.root, width=50, height=50)
        backdrop.attach(hidden, lambda: [(0, 0, 50, 50)])
        self.root.update()
        self.clock.tick()
        self.assertEqual(hidden.find_withtag("bd_vec"), ())
        surface = next(s for s in backdrop.surfaces if s.canvas is hidden)
        self.assertTrue(surface.dirty)
        hidden.destroy()
        self.root.update()
        self.assertNotIn(hidden, [s.canvas for s in backdrop.surfaces])


class StillBackdropTests(TkCase):
    motion = False

    def test_motion_off_shows_the_design_pose_and_never_moves(self):
        stage = self.canvas(width=300, height=200)
        backdrop = Backdrop(self.root, scale=1.0, assets=FakeAssets())
        backdrop.install()
        backdrop.set_frame(stage)
        backdrop.attach(stage, lambda: [(0, 0, 300, 200)])
        self.settle()
        # Cancelled before the root goes: a later test's update() would run it on a dead root.
        self.addCleanup(self.root.after_cancel, self.root.after(100, lambda: None))
        self.root.update()
        self.assertEqual(sorted(backdrop.assets.reads), [0, 1, 2, 3, 4])
        coords = [stage.coords(item) for item in stage.find_withtag("bd_vec")]
        for _ in range(4):
            self.clock.tick()
        self.assertEqual((backdrop.pose, backdrop.renders), (0, 1))
        self.assertEqual([stage.coords(item) for item in stage.find_withtag("bd_vec")], coords)
        self.assertFalse(self.clock.running)


class SidebarArtTests(TkCase):
    """The sidebar shows opaque images only: the statue art and the edge line are composited
    into the gradient, and each row's fade into the part of it the row covers."""

    def sidebar(self, assets="real", height=600, scale=1.0):
        """A sidebar with the shipped art, or (``assets=None``) the plain column without any.

        The window is wide enough for every column a test packs side by side: a column left
        without room is never mapped and keeps its requested height, no reference for the art.
        """
        self.root.geometry(f"1000x{height}")
        shipped = art.load(directory=ART) if assets == "real" else None
        with mock.patch.object(art, "load", return_value=None):   # no fallback to the shipped art
            bar = Sidebar(self.root, scale=scale, version="0.0",
                          driver_var=tk.StringVar(self.root, "DRIVER · NATIVE"), assets=shipped)
        bar.pack(side="left", fill="y")
        for key in ("replay", "camera", "look", "export", "controls", "settings"):
            bar.add_item(key, key.title(), key, lambda: None)
        bar.select("replay")
        self.settle()
        self.assertEqual(bar.winfo_height(), height, "the column is laid out at the window's height")
        return bar

    def first_row(self, fill, plain, start):
        """The first row from ``start`` where the statue shows: the fill differs from the plain column."""
        for y in range(start, min(fill.height(), plain.height())):
            if any(fill.get(x, y) != plain.get(x, y) for x in range(0, fill.width() - 1, 3)):
                return y
        return None

    def transparent(self, image, x, y):
        return bool(int(self.root.tk.call(image.name, "transparency", "get", x, y)))

    def test_fill_is_opaque_with_the_art_composited_and_the_edge_on_top(self):
        bar = self.sidebar()
        fill = bar._fill_image
        self.assertEqual((fill.width(), fill.height()), (190, bar.winfo_height()))
        for x, y in ((0, 0), (189, 0), (95, 300), (0, bar.winfo_height() - 1), (189, bar.winfo_height() - 1)):
            self.assertFalse(self.transparent(fill, x, y))
        self.assertEqual("#%02x%02x%02x" % fill.get(189, 0), TOKENS["sidebar_edge_top"], "the edge line covers the art")
        plain = self.sidebar(assets=None)._fill_image
        top = ART_TOP   # the art's box always starts here (UPDATE 22)
        moved = 0
        for y in range(top, bar.winfo_height(), 7):
            for x in range(0, 189, 7):
                difference = max(abs(a - b) for a, b in zip(fill.get(x, y), plain.get(x, y)))
                self.assertLessEqual(difference, 7, "the statue is faint")
                moved += difference > 0
        self.assertGreater(moved, 20, "the statue is there")
        for y in range(0, top, 9):
            self.assertEqual(fill.get(40, y), plain.get(40, y), "nothing above the art's box")
        images = [item for item in bar.find_all() if bar.type(item) == "image"]
        self.assertEqual(len(images), 1 + 1 + 2 * 6, "the fill, Dolly's mark (UPDATE 21), then a fade and an icon per row")

    def test_rows_are_opaque_fades_over_the_fill_and_change_only_when_their_look_does(self):
        bar = self.sidebar()
        replay, camera = bar.items["replay"], bar.items["camera"]
        for item in (replay, camera):
            name = bar.itemcget(item.strip, "image")
            for x in (0, 95, 189):
                self.assertEqual(int(self.root.tk.call(name, "transparency", "get", x, 20)), 0)
        self.assertNotEqual(bar.itemcget(replay.strip, "image"), bar.itemcget(camera.strip, "image"))
        configure = mock.patch.object(tk.Canvas, "itemconfigure", autospec=True, side_effect=tk.Canvas.itemconfigure)
        with configure as changed:
            bar._paint()
            self.assertEqual([call for call in changed.call_args_list if call.args[1] != bar._bar
                              and call.args[1] != bar._ring], [], "nothing changed, nothing sent")
            bar._hover = "camera"
            bar._paint()
            items = {call.args[1] for call in changed.call_args_list}
        self.assertLessEqual(items - {bar._bar, bar._ring}, {camera.strip, camera.label, camera.glyph})
        self.assertIn(camera.strip, items)

    def test_a_taller_window_scales_the_art_to_fill_its_box_keeping_her_proportions(self):
        # UPDATE 22 (Kara): "scale her with the window so she fills the sidebar like in the
        # default frame, keeping her proportions (crop the sides a bit)".
        bar = self.sidebar(height=600)
        short = bar._fill_image
        at_rest = self.first_row(short, self.sidebar(assets=None, height=600)._fill_image, ART_TOP)
        self.assertIsNotNone(at_rest, "a short column shows her at her own size from ART_TOP")
        self.root.geometry("1000x900")
        self.settle()
        tall = bar._fill_image
        self.assertIsNot(tall, short)
        self.assertEqual(tall.height(), bar.winfo_height())
        plain = self.sidebar(assets=None, height=900)._fill_image
        self.assertEqual([tall.get(60, y) for y in range(0, ART_TOP, 11)],
                         [plain.get(60, y) for y in range(0, ART_TOP, 11)], "nothing above the art's box")
        from dolly.ui.sidebar import _ratio
        self.assertGreater((bar.winfo_height() - ART_TOP) / 550, 1.1)
        zoom, subsample = _ratio(190, bar.winfo_height() - ART_TOP, 380, 1100)   # drawn from the 2x file
        grown = self.first_row(tall, plain, ART_TOP)
        # The crown starts lower by the same factor her whole figure grew: scaled, not moved or stretched.
        self.assertAlmostEqual(grown - ART_TOP, (at_rest - ART_TOP) * 2 * zoom / subsample, delta=3)
        bottom = tall.height() - 1
        self.assertTrue(any(tall.get(x, bottom) != plain.get(x, bottom) for x in range(189)), "her box is filled")

    def test_at_125_percent_she_keeps_her_own_size_across_the_whole_column(self):
        # The file shown at 125 % is the 1x one (190 x 550). Copied as it is, she was smaller than her
        # own size and stopped short of the column's right part; she is drawn from the 2x file now.
        scale = 1.25
        bar = self.sidebar(height=600, scale=scale)
        fill, plain = bar._fill_image, self.sidebar(assets=None, height=600, scale=scale)._fill_image
        top, width = int(ART_TOP * scale + .5), int(190 * scale + .5)
        self.assertEqual(fill.width(), width)
        at_rest = self.first_row(fill, plain, top)
        self.assertAlmostEqual(at_rest - top, 95 * scale, delta=3, msg="her crown 95 design px into the box")
        right = sum(fill.get(x, y) != plain.get(x, y) for y in range(top, fill.height(), 4)
                    for x in range(192, width - 1, 2))
        self.assertGreater(right, 20, "she reaches the column's right part")

    def test_the_art_ratio_covers_her_box_with_small_whole_numbers(self):
        from dolly.ui.sidebar import _ratio

        def written(size, zoom, subsample):   # Tk keeps every subsample-th pixel, each repeated zoom times
            return -(-size // subsample) * zoom
        expected = {(190, 550): (1, 2),    # 100 %: half the 2x file is her own size
                    (190, 640): (3, 5),    # 100 %, a 900 px column
                    (238, 688): (5, 8),    # 125 %: 690 rows for her 688 (the bare ratio .6255 would ask for 2/3)
                    (333, 963): (7, 8),    # 175 %: 966 for her 963
                    (285, 1050): (1, 1)}   # 150 % at Look's height: the 2x file as it is
        for (width, height), pair in expected.items():
            self.assertEqual(_ratio(width, height, 380, 1100), pair, (width, height))
        for width, height in ((190, 551), (190, 700), (238, 760), (285, 900), (285, 1260), (333, 1225), (380, 1400)):
            zoom, subsample = _ratio(width, height, 380, 1100)
            self.assertLessEqual(subsample, 8)
            self.assertGreaterEqual(written(380, zoom, subsample), width, "it covers the column")
            self.assertGreaterEqual(written(1100, zoom, subsample), height, "it covers her box")
            self.assertLessEqual(written(1100, zoom, subsample) / height, 1.15, "and not much more")


class SidebarDriverLineTests(TkCase):
    """Figma [design reference]: "<version>  ·  " in dim, then the driver in use; only "Console driver" is warn."""

    def test_the_driver_follows_the_version_on_its_baseline_also_after_a_font_change(self):
        from dolly.ui import fonts
        driver = tk.StringVar(self.root, "DRIVER · NATIVE")
        with mock.patch.object(art, "load", return_value=None):
            bar = Sidebar(self.root, scale=1.0, version="0.6.63-alpha", driver_var=driver, assets=None)
        bar.pack(side="left", fill="y")
        self.settle()

        def line():
            return [(bar.itemcget(item, "text"), bar.itemcget(item, "fill"))
                    for item in (bar.version_item, bar.driver_item)]

        def placed():
            x, y = bar.coords(bar.version_item)
            return bar.coords(bar.driver_item) == [x + fonts.font(bar, "small").measure("0.6.63-alpha  ·  "), y]
        self.assertEqual(line(), [("0.6.63-alpha  ·  ", TOKENS["dim"]), ("Native driver", TOKENS["dim"])])
        self.assertTrue(placed())
        driver.set("DRIVER · CONSOLE LEGACY")
        self.assertEqual(line(), [("0.6.63-alpha  ·  ", TOKENS["dim"]), ("Console driver", TOKENS["warn"])])
        self.assertTrue(placed())
        for option in ("anchor", "font"):
            self.assertEqual(bar.itemcget(bar.driver_item, option), bar.itemcget(bar.version_item, option))
        before = bar.coords(bar.driver_item)[0]
        fonts.install(self.root, 2.0)   # every named font doubles, as when a larger face arrives
        self.assertTrue(placed())
        self.assertGreater(bar.coords(bar.driver_item)[0], before)
        driver.set("DRIVER · NATIVE")
        self.assertEqual(line()[1], ("Native driver", TOKENS["dim"]))
        listeners = self.root._dolly_fonts.listeners
        self.assertIn(bar._place_driver, listeners)
        bar.destroy()
        self.assertNotIn(bar._place_driver, listeners, "a destroyed sidebar stops listening")


class StickerTests(TkCase):
    def sticker(self, canvas, action, **options):
        spec = {"label": "START", "kind": "primary", "tilt": .87, "seed": 7, "paper": (78.47, 34.72),
                "paper_offset": (-.6, -.1), "scale": 1.0, "font": STICKER_FONT}
        spec.update(options)
        sticker = CanvasSticker(canvas, action=action, **spec)
        self.addCleanup(sticker.destroy)
        return sticker

    def placed(self, **options):
        canvas = self.canvas(width=400, height=200)
        calls = []
        action = ActionProxy("Open replay in Dolly", lambda: calls.append(1), label="START")
        sticker = self.sticker(canvas, action, **options)
        sticker.place(100, 80)
        self.settle()
        return canvas, action, sticker, calls

    def centre(self, sticker):
        return sticker._x + sticker.width() // 2, sticker._y + sticker.height() // 2

    def test_six_deterministic_poses_per_seed_and_a_resting_first_pose(self):
        canvas = self.canvas()
        action = ActionProxy("Stop / restore", label="STOP/RESTORE")
        first = self.sticker(canvas, action, seed=3)
        again = self.sticker(canvas, action, seed=3)
        other = self.sticker(canvas, action, seed=7)
        self.assertEqual(len(first.poses), POSES)
        self.assertEqual(first.poses[0], (0.0, 0, 0))
        self.assertEqual(first.poses, again.poses)
        self.assertNotEqual(first.poses, other.poses)
        for extra, dx, dy in first.poses[1:]:
            self.assertLessEqual(abs(extra), 1.1)
            self.assertIn(dx, (-1, 0, 1))
            self.assertIn(dy, (-1, 0, 1))

    def test_a_new_pose_on_every_third_tick_never_the_same_twice_and_every_sticker_joins_the_clock(self):
        canvas, action, sticker, _calls = self.placed()
        self.assertIn(sticker, self.clock.members)
        self.assertEqual(sticker.boil_every, 3)
        self.assertEqual(boil.INTERVAL_MS * sticker.boil_every, 1500, "one pose every 1.5 seconds")
        for _ in range(40):
            before = sticker.pose
            self.clock.tick()
            if self.clock.frame % sticker.boil_every:
                self.assertEqual(sticker.pose, before, "intermediate ticks hold the pose")
            else:
                self.assertNotEqual(sticker.pose, before, "third ticks snap to another pose")
        sticker.destroy()
        self.assertNotIn(sticker, self.clock.members)
        self.assertEqual(canvas.find_withtag(sticker.tag), ())

    def test_paper_size_follows_the_label_and_frame_is_36_tall(self):
        canvas, action, sticker, _calls = self.placed()
        label = stickers.font_object(canvas, STICKER_FONT).measure("START")
        self.assertEqual(sticker.width(), max(78, label + 25))
        self.assertEqual(sticker.height(), 36)
        x0, y0, x1, y1 = canvas.bbox(sticker.label)
        self.assertAlmostEqual((x0 + x1) / 2, 100 + sticker.width() / 2, delta=1.5)
        self.assertAlmostEqual((y0 + y1) / 2, 80 + 18, delta=1.5)

    def test_entering_takes_a_fresh_pose_and_the_hover_face(self):
        canvas, action, sticker, _calls = self.placed()
        x, y = self.centre(sticker)
        before = sticker.pose
        canvas.event_generate("<Motion>", x=x, y=y)
        self.assertTrue(sticker._hover, "the label must not hide the paper from the pointer")
        self.assertNotEqual(sticker.pose, before)
        self.assertEqual(sticker.face(), (TOKENS["accent_hover"], TOKENS["text"], 1.0))
        self.assertEqual(canvas.cget("cursor"), "hand2")
        canvas.event_generate("<Motion>", x=5, y=5)
        self.assertFalse(sticker._hover)
        self.assertEqual(sticker.face(), (TOKENS["accent"], TOKENS["text"], 1.0))
        self.assertEqual(canvas.cget("cursor"), "")

    def test_the_hover_nudge_is_drawn_at_once_between_the_slower_boil_snaps(self):
        canvas, action, sticker, _calls = self.placed()
        while self.clock.frame % sticker.boil_every:
            self.clock.tick()            # a boil snap just happened; the next is three ticks away
        x, y = self.centre(sticker)
        before, image = sticker.pose, canvas.itemcget(sticker.paper, "image")
        canvas.event_generate("<Motion>", x=x, y=y)
        nudged = sticker.pose
        self.assertNotEqual(nudged, before, "the pointer nudges the paper without waiting for the clock")
        self.assertNotEqual(canvas.itemcget(sticker.paper, "image"), image, "and the new pose is drawn in the event")
        self.assertEqual(canvas.itemcget(sticker.paper, "image"), str(sticker._image))
        self.clock.tick()
        self.assertEqual(sticker.pose, nudged, "the next tick holds the nudged pose")
        self.clock.tick()
        self.assertEqual(sticker.pose, nudged, "the second tick holds the nudged pose")
        self.clock.tick()
        self.assertNotEqual(sticker.pose, nudged, "the boil goes on at its own pace")
        canvas.event_generate("<Motion>", x=5, y=5)
        moved = sticker.pose
        canvas.event_generate("<Motion>", x=x, y=y)
        self.assertNotEqual(sticker.pose, moved, "every arrival nudges, whatever the tick")

    def test_stickers_snap_together_on_every_third_tick_while_the_other_members_keep_their_pace(self):
        canvas, action, sticker, _calls = self.placed()
        other = self.sticker(canvas, ActionProxy("Stop / restore", label="STOP/RESTORE"), label="STOP/RESTORE",
                             kind="secondary", tilt=-.65, seed=3, paper=(155.10, 34.58))
        other.place(200, 80)
        backdrop = Backdrop(self.root, scale=1.0, assets=FakeAssets())
        backdrop.install()
        backdrop.set_frame(canvas)
        backdrop.attach(canvas, lambda: [(0, 0, 400, 200)])
        dot = CanvasDot(canvas, scale=1.0)
        self.addCleanup(dot.destroy)
        dot.configure(state="busy")
        # Any other member (the Listening keycap's pulse joins like this) has no boil_every.
        member = mock.Mock(spec=["advance", "winfo_exists"], **{"winfo_exists.return_value": True})
        self.clock.register(member)
        self.settle()
        self.assertFalse(backdrop.frozen)
        snaps, frames = [], []
        for _ in range(12):
            poses, shard, blink, calls = (sticker.pose, other.pose), backdrop.pose, dot.color(), member.advance.call_count
            self.clock.tick()
            self.assertNotEqual(backdrop.pose, shard, "the backdrop shard moves on every tick")
            self.assertNotEqual(dot.color(), blink, "the busy dot blinks on every tick")
            self.assertEqual(member.advance.call_count, calls + 1, "a plain member advances on every tick")
            moved = (sticker.pose != poses[0], other.pose != poses[1])
            self.assertEqual(moved[0], moved[1], "every sticker snaps in the same tick")
            snaps.append(moved[0])
            frames.append(self.clock.frame)
        self.assertEqual(snaps, [frame % sticker.boil_every == 0 for frame in frames])
        self.assertEqual(snaps.count(True), 4, "twelve ticks (6 s), four sticker poses")

    def test_press_dips_the_paper_and_release_inside_invokes(self):
        canvas, action, sticker, calls = self.placed()
        x, y = self.centre(sticker)
        label = canvas.coords(sticker.label)
        canvas.event_generate("<Motion>", x=x, y=y)
        with mock.patch.object(stickers, "paper_image", wraps=paper_image) as drawn:
            sticker._draw()
            rest = drawn.call_args.args
            canvas.event_generate("<ButtonPress-1>", x=x, y=y)
            pressed = drawn.call_args.args
        self.assertAlmostEqual(pressed[5] - rest[5], 1, msg="the paper dips one pixel")
        self.assertEqual(pressed[6], TOKENS["accent_active"])
        self.assertEqual(canvas.coords(sticker.label), label, "the label never moves")
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assertEqual(calls, [1])

    def test_release_outside_cancels(self):
        canvas, action, sticker, calls = self.placed()
        x, y = self.centre(sticker)
        canvas.event_generate("<Motion>", x=x, y=y)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<ButtonRelease-1>", x=5, y=5)
        self.assertEqual(calls, [])
        self.assertFalse(sticker._pressed)

    def test_disabled_face_is_forty_percent_and_never_invokes_but_still_boils(self):
        canvas, action, sticker, calls = self.placed()
        action.configure(state="disabled")
        self.assertEqual(sticker.cget("state"), "disabled")
        ink = mix(TOKENS["text"], TOKENS["backdrop"], .4)
        self.assertEqual(sticker.face(), (TOKENS["accent"], ink, .4))
        self.assertEqual(canvas.itemcget(sticker.label, "fill"), ink)
        x, y = self.centre(sticker)
        canvas.event_generate("<Motion>", x=x, y=y)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assertEqual(sticker.invoke(), "")
        self.assertEqual(calls, [])
        self.assertFalse(sticker._hover)
        before = sticker.pose
        for _ in range(sticker.boil_every):
            self.clock.tick()
        self.assertNotEqual(sticker.pose, before, "a disabled sticker boils at the same pace")
        self.assertEqual(sticker.face(), (TOKENS["accent"], ink, .4), "and keeps its disabled face")
        rows = paper_rows((40, 30), (30, 20), 1.0, 0, 0, (10, 20, 30), .4)
        self.assertEqual(max(rows[3::4]), 102)
        action.configure(state="normal")
        self.assertEqual(sticker.face(), (TOKENS["accent"], TOKENS["text"], 1.0))

    def test_secondary_faces_use_the_button_paper_and_muted_ink(self):
        canvas = self.canvas()
        sticker = self.sticker(canvas, ActionProxy("Stop / restore", label="STOP/RESTORE"),
                               label="STOP/RESTORE", kind="secondary", tilt=-.65, seed=3, paper=(155.10, 34.58))
        self.assertEqual(sticker.face(), (TOKENS["button"], TOKENS["muted"], 1.0))
        sticker._set_hover(True)
        self.assertEqual(sticker.face()[0], TOKENS["button_hover"])
        sticker._on_press()
        self.assertEqual(sticker.face()[0], TOKENS["button_active"])
        self.assertEqual(sticker.label_text, "STOP/RESTORE")

    def test_cancel_sticker_shows_only_while_shown_and_disables_after_a_click(self):
        canvas = self.canvas(width=400, height=200)
        cancelled = []
        action = ActionProxy("Cancel startup", state="disabled")
        action.configure(command=lambda: (cancelled.append(1), action.configure(state="disabled")))
        sticker = self.sticker(canvas, action, label="CANCEL", kind="secondary", tilt=-1.0, seed=5, paper=(78, 34.6))
        sticker.place(20, 20)
        sticker.set_shown(False)
        self.settle()
        for item in (sticker.paper, sticker.label):
            self.assertEqual(canvas.itemcget(item, "state"), "hidden")
        self.assertIsNone(sticker.bbox())
        action.configure(state="normal")
        self.assertFalse(sticker.focusable(), "a hidden sticker is not in the Tab order")
        self.assertEqual(sticker.invoke(), "")
        sticker.set_shown(True)
        self.assertEqual(canvas.itemcget(sticker.paper, "state"), "normal")
        self.assertEqual(canvas.itemcget(sticker.label, "state"), "disabled")
        self.assertTrue(sticker.focusable())
        x, y = self.centre(sticker)
        canvas.event_generate("<Motion>", x=x, y=y)
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.assertEqual(cancelled, [1])
        self.assertEqual(sticker.cget("state"), "disabled")
        self.assertEqual(sticker.face()[2], .4)

    def test_canvas_focus_moves_between_controls_and_activates_them(self):
        canvas, action, sticker, calls = self.placed()
        opened = []
        link = CanvasLink(canvas, text="Open output folder", command=lambda: opened.append(1), font=FONT, scale=1.0)
        link.place(10, 20)
        hidden = CanvasLink(canvas, text="Cancel startup", action=ActionProxy("Cancel startup", state="disabled"),
                            font=FONT, scale=1.0)
        focus = CanvasFocus(canvas, [link, hidden, sticker])
        self.root.update()
        focus.proxy.focus_force()
        self.root.update()
        self.assertIs(focus.focused(), link)
        self.assertEqual(canvas.itemcget(link.ring, "state"), "disabled")
        focus.proxy.event_generate("<Return>")
        self.assertEqual(opened, [1])
        self.assertEqual(focus._move(1), "break")
        self.assertIs(focus.focused(), sticker, "hidden controls are skipped")
        self.assertEqual(canvas.itemcget(sticker.ring, "state"), "disabled")
        self.assertEqual(canvas.itemcget(link.ring, "state"), "hidden")
        focus.proxy.event_generate("<space>")
        self.assertEqual(calls, [1])
        self.assertIsNone(focus._move(1), "past the last control Tk moves on")
        self.assertIsNone(focus.focused())
        self.root._dolly_tab_backward = True
        focus._focus_in()
        self.assertIs(focus.focused(), sticker, "Shift+Tab arrives on the last control")

    def test_shown_papers_stay_drawable_between_snaps_without_new_items_per_tick(self):
        # Tk frees a photo's drawable once no item shows it; converting it again on the next
        # snap was most of a boil tick at 150 %. Each paper shown is held by a hidden item.
        canvas, action, sticker, _calls = self.placed()
        shown = set()
        for _ in range(3 * POSES):
            sticker.advance()
            shown.add(canvas.itemcget(sticker.paper, "image"))
        held = {canvas.itemcget(item, "image") for item in canvas.find_all()
                if item != sticker.paper and canvas.type(item) == "image"}
        self.assertEqual(len(shown), POSES)
        self.assertLessEqual(shown, held)
        self.assertTrue(all(canvas.itemcget(item, "state") == "hidden" for item in canvas.find_all()
                            if item != sticker.paper and canvas.type(item) == "image"))
        items = canvas.find_all()
        for _ in range(3 * POSES):
            sticker.advance()
        self.assertEqual(canvas.find_all(), items, "a snap creates no canvas items")
        sticker.destroy()
        self.assertEqual(canvas.find_all(), ())

    def test_paper_cache_lives_on_each_root(self):
        canvas, action, sticker, _calls = self.placed()
        first = self.root._dolly_paper_cache
        self.assertTrue(first)
        other = self.make_root("200x100")
        other_canvas = tk.Canvas(other, width=200, height=100)
        other_canvas.pack()
        twin = CanvasSticker(other_canvas, action=ActionProxy("Open replay in Dolly", label="START"), label="START",
                             kind="primary", tilt=.87, seed=7, paper=(78.47, 34.72), paper_offset=(-.6, -.1),
                             scale=1.0, font=STICKER_FONT)
        twin.place(100, 80)
        self.root.destroy()
        other.update()
        for _ in range(8):           # the same papers again, now from the second root only
            boil.BoilClock.of(other).tick()
        self.assertIsNot(other._dolly_paper_cache, first)
        self.assertTrue(other._dolly_paper_cache)
        twin.destroy()

    def test_pre_warm_renders_every_resting_pose_after_the_first_map(self):
        canvas, action, sticker, _calls = self.placed()
        self.root.update()
        keys = {key for key in self.root._dolly_paper_cache if key[5] == TOKENS["accent"] and key[6] == 1.0}
        self.assertGreaterEqual(len(keys), POSES)

    def test_paper_rows_match_the_analytic_coverage_everywhere(self):
        import math
        size, rect, angle, dx, dy = (61, 27), (48.3, 17.6), 2.3, .4, -.7
        rows = paper_rows(size, rect, angle, dx, dy, (1, 2, 3), .8)
        a = math.radians(angle)
        cx, cy = size[0] / 2 + dx, size[1] / 2 + dy
        for y in range(size[1]):
            for x in range(size[0]):
                qx, py = x + .5 - cx, y + .5 - cy
                u = qx * math.cos(a) - py * math.sin(a)
                v = qx * math.sin(a) + py * math.cos(a)
                cover = (min(1, max(0, rect[0] / 2 - abs(u) + .5)) * min(1, max(0, rect[1] / 2 - abs(v) + .5)))
                expected = int(cover * .8 * 255 + .5)
                got = rows[(y * size[0] + x) * 4 + 3]
                self.assertEqual(got, expected, (x, y))


class CanvasControlTests(TkCase):
    def test_link_colours_follow_hover_focus_and_state(self):
        canvas = self.canvas(width=300, height=100)
        calls = []
        link = CanvasLink(canvas, text="Export Diagnostics", command=lambda: calls.append(1), font=FONT, scale=1.0)
        link.place(20, 30)
        self.settle()
        fill = lambda: canvas.itemcget(link.item, "fill")
        animator = tween.Animator.of(self.root)
        self.assertEqual(fill(), TOKENS["muted"])
        link._set_hover(True)
        # Hover ramps (PLAN-5 2.11): its first step is drawn by the event itself.
        self.assertNotIn(fill(), (TOKENS["muted"], TOKENS["text"]))
        animator.finish()
        self.assertEqual(fill(), TOKENS["text"])
        link._set_hover(False)
        animator.finish()
        self.assertEqual(fill(), TOKENS["muted"])
        link.set_focus(True)
        self.assertEqual(fill(), TOKENS["text"])
        self.assertEqual(canvas.itemcget(link.ring, "outline"), TOKENS["accent"])
        link.set_focus(False)
        link._set_hover(True)
        link.configure(state="disabled")
        self.assertEqual(fill(), TOKENS["dim"], "disabled shows at once, mid-ramp too")
        self.assertFalse(animator.active())
        self.assertEqual(link.invoke(), "")
        link.configure(state="normal")
        link.invoke()
        self.assertEqual(calls, [1])
        self.assertEqual(link.cget("text"), "Export Diagnostics")
        self.assertGreater(link.width(), 0)

    def test_action_link_is_visible_only_while_its_action_is_enabled(self):
        canvas = self.canvas(width=300, height=100)
        cancelled = []
        action = ActionProxy("Cancel startup", lambda: cancelled.append(1), state="disabled")
        link = CanvasLink(canvas, action=action, font=FONT, scale=1.0)
        link.place(20, 30)
        self.assertEqual(link.cget("text"), "Cancel startup")
        self.assertEqual(canvas.itemcget(link.item, "state"), "hidden")
        self.assertFalse(link.focusable())
        action.configure(state="normal")
        self.assertEqual(canvas.itemcget(link.item, "state"), "normal")
        link.invoke()
        self.assertEqual(cancelled, [1])
        link.configure(state="disabled")
        self.assertEqual(action.cget("state"), "disabled", "state belongs to the action")
        self.assertEqual(canvas.itemcget(link.item, "state"), "hidden")
        action.configure(text="Stop starting")
        self.assertEqual((link.cget("text"), canvas.itemcget(link.item, "text")), ("Stop starting", "Stop starting"))

    def test_link_follows_its_text_variable(self):
        canvas = self.canvas()
        text = tk.StringVar(self.root, value="Show log")
        link = CanvasLink(canvas, textvariable=text, command=lambda: None, font=FONT, scale=1.0)
        text.set("Hide log")
        self.assertEqual(canvas.itemcget(link.item, "text"), "Hide log")
        self.assertEqual(link.cget("text"), "Hide log")
        link.destroy()
        text.set("Show log")
        self.assertEqual(canvas.find_all(), ())

    def test_text_elides_to_two_lines_and_keeps_the_full_message(self):
        canvas = self.canvas(width=400, height=200)
        message = "Launching Deadlock and preparing the hideout while the replay loads its first tick " * 3
        label = CanvasText(canvas, text=message, font=FONT, fill=TOKENS["muted"], wraplength=160, max_lines=2)
        label.place(10, 10, anchor="nw")
        shown = canvas.itemcget(label.item, "text")
        self.assertTrue(label.elided())
        self.assertTrue(shown.endswith("…"))
        self.assertEqual(label.full_text(), message)
        self.assertEqual(label.cget("text"), message)
        self.assertLessEqual(label._lines(), 2)
        label.configure(text="Ready")
        self.assertFalse(label.elided())
        self.assertEqual(canvas.itemcget(label.item, "text"), "Ready")
        label.configure(foreground=TOKENS["status_error"], wraplength=300)
        self.assertEqual(canvas.itemcget(label.item, "fill"), TOKENS["status_error"])
        self.assertEqual(label.cget("wraplength"), 300)

    def test_sticker_and_status_text_follow_a_named_font_reconfigured_in_place(self):
        from dolly.ui import fonts
        canvas = self.canvas(width=600, height=200)
        sticker = CanvasSticker(canvas, action=ActionProxy("Stop / restore", label="STOP/RESTORE"), label="STOP/RESTORE",
                                kind="secondary", tilt=-.65, seed=3, paper=(40, 34.58), paper_offset=(0, 0), scale=1.0,
                                font=fonts.font(canvas, "sticker"))
        self.addCleanup(sticker.destroy)
        message = "Launching Deadlock and preparing the hideout for the replay"
        label = CanvasText(canvas, text=message, font=fonts.font(canvas, "body"), fill=TOKENS["muted"],
                           wraplength=320, max_lines=2)
        before = sticker._frame_w
        self.assertFalse(label.elided())
        fonts.install(self.root, 2.0)        # every named font doubles, as when a larger face arrives
        self.assertGreater(sticker._frame_w, before, "the paper grows with its label")
        self.assertTrue(label.elided())

    def test_status_dot_states_and_busy_blink(self):
        canvas = self.canvas()
        dot = CanvasDot(canvas, scale=1.0)
        dot.place(10, 10)
        self.assertEqual(dot.color(), TOKENS["status_ready"])
        self.clock.tick()
        self.assertEqual(dot.color(), TOKENS["status_ready"], "a ready dot is steady")
        dot.configure(state="busy")
        colors = [dot.color()]
        for _ in range(3):
            self.clock.tick()
            colors.append(dot.color())
        half = mix(TOKENS["status_busy"], TOKENS["backdrop"], .5)
        self.assertEqual(colors, [TOKENS["status_busy"], half, TOKENS["status_busy"], half])
        dot.configure(state="error")
        self.assertEqual(dot.color(), TOKENS["status_error"])
        self.assertEqual(dot.cget("state"), "error")
        self.assertTrue(canvas.itemcget(dot.item, "image"))

    def test_progress_steps_six_held_positions_and_hides_on_stop(self):
        canvas = self.canvas(width=500, height=100)
        progress = CanvasProgress(canvas, scale=1.0)
        bar = ProgressProxy()
        progress.bind(bar)
        progress.place(10, 410, 50)
        self.assertEqual(canvas.itemcget(progress.item, "state"), "hidden")
        self.assertEqual(progress.positions(), [10, 49, 88, 127, 166, 205])
        bar.start(15)
        self.assertNotEqual(canvas.itemcget(progress.item, "state"), "hidden")
        lefts = []
        for _ in range(7):
            lefts.append(canvas.coords(progress.item)[0])
            self.clock.tick()
        self.assertEqual(lefts, [10, 49, 88, 127, 166, 205, 10])
        x0, y0, x1, y1 = canvas.coords(progress.item)
        self.assertEqual((x1 - x0, y1 - y0), (205, 3))
        bar.stop()
        self.assertEqual(canvas.itemcget(progress.item, "state"), "hidden")
        self.clock.tick()
        self.assertEqual(canvas.itemcget(progress.item, "state"), "hidden")


class StillControlTests(TkCase):
    motion = False

    def test_progress_rests_on_the_third_step_with_motion_off(self):
        canvas = self.canvas(width=500, height=100)
        progress = CanvasProgress(canvas, scale=1.0)
        bar = ProgressProxy()
        progress.bind(bar)
        progress.place(10, 410, 50)
        bar.start()
        for _ in range(3):
            self.clock.tick()
        self.assertEqual(canvas.coords(progress.item)[0], 88)

    def test_stickers_keep_the_design_pose_with_motion_off_but_still_show_the_hover_face(self):
        canvas = self.canvas(width=400, height=200)
        sticker = CanvasSticker(canvas, action=ActionProxy("Open replay in Dolly", label="START"), label="START",
                                kind="primary", tilt=.87, seed=7, paper=(78.47, 34.72), paper_offset=(-.6, -.1),
                                scale=1.0, font=STICKER_FONT)
        self.addCleanup(sticker.destroy)
        sticker.place(100, 80)
        self.settle()
        for _ in range(4):
            self.clock.tick()
        self.assertEqual(sticker.pose, 0, "no boil")
        x, y = sticker._x + sticker.width() // 2, sticker._y + sticker.height() // 2
        canvas.event_generate("<Motion>", x=x, y=y)
        self.assertTrue(sticker._hover)
        self.assertEqual(sticker.pose, 0, "no nudge either")
        self.assertEqual(sticker.face(), (TOKENS["accent_hover"], TOKENS["text"], 1.0))


@unittest.skipUnless(os.environ.get("DOLLY_UI_PERF") == "1", "set DOLLY_UI_PERF=1 to measure the boil clock")
class BoilPerformanceProbe(TkCase):
    """Budgets at 1200x830 (100 %) with every animated control running (three stickers, the busy
    dot, the progress line): the members' tick <= 5 ms mean; with Tk's drawing <= 8 ms mean and
    <= 25 ms max; process CPU <= 3 % of one core. Timings depend on the machine and its load.
    The whole app with its status loop is measured by the QA tool ``profile_poll.py``."""

    def shell(self):
        self.root.geometry("1200x830")
        stage = tk.Canvas(self.root, highlightthickness=0, borderwidth=0, background=TOKENS["bg"])
        stage.place(x=190, y=0, relwidth=1, relheight=1, width=-190)
        page = tk.Frame(stage, background=TOKENS["bg"])     # the page rectangle, as the workspace covers it
        stage.create_window(40, 107, window=page, anchor="nw", width=930, height=596)
        assets = art.load(directory=ART)
        self.assertIsNotNone(assets)
        started = time.perf_counter()
        backdrop = Backdrop(self.root, scale=1.0, assets=assets)
        backdrop.install()
        backdrop.set_frame(stage)
        backdrop.attach(stage, lambda: [(0, 0, 1010, 107), (0, 703, 1010, 830), (0, 107, 40, 703), (970, 107, 1010, 703)])
        self.settle()
        self.assertEqual(backdrop.renders, 1)
        self.render_ms = (time.perf_counter() - started) * 1000
        specs = (("STOP/RESTORE", "secondary", -.65, 3, (155.10, 34.58)), ("CANCEL", "secondary", -1.0, 5, (78, 34.6)),
                 ("START", "primary", .87, 7, (78.47, 34.72)))
        x = 600
        for label, kind, tilt, seed, paper in specs:
            sticker = CanvasSticker(stage, action=ActionProxy(label), label=label, kind=kind, tilt=tilt, seed=seed,
                                    paper=paper, paper_offset=(0, 0), scale=1.0, font=STICKER_FONT)
            sticker.place(x, 748)
            x += sticker.width() + 10
        dot = CanvasDot(stage, scale=1.0)
        dot.configure(state="busy")
        progress = CanvasProgress(stage, scale=1.0)
        bar = ProgressProxy()
        progress.bind(bar)
        progress.place(40, 970, 806)
        bar.start()
        self.root.update()
        for _ in range(16):     # every paper and pose rendered once
            self.clock.tick()
            self.root.update()
        return stage

    def test_tick_cost_at_the_default_window_size(self):
        self.shell()
        times, redraws, members = [], [], {}
        for _ in range(40):
            began = time.perf_counter()
            for member in self.clock.members:
                started = time.perf_counter()
                member.advance()
                name = type(member).__name__
                members[name] = members.get(name, 0.0) + (time.perf_counter() - started) * 1000 / 40
            ticked = time.perf_counter()
            self.root.update_idletasks()
            done = time.perf_counter()
            times.append((done - began) * 1000)
            redraws.append((done - ticked) * 1000)
        mean, peak = sum(times) / len(times), max(times)
        detail = ", ".join(f"{name} {cost:.2f}" for name, cost in members.items())
        print(f"\nboil tick at 1200x830: mean {mean:.2f} ms, max {peak:.2f} ms, drawing {sum(redraws) / 40:.2f} ms "
              f"({detail}); first picture {self.render_ms:.0f} ms")
        self.assertLessEqual(sum(members.values()), 5)
        self.assertLessEqual(members["Backdrop"], 1, "the backdrop only moves the shard")
        self.assertLessEqual(mean, 8)
        self.assertLessEqual(peak, 25)

    def test_idle_cpu_with_the_clock_running(self):
        self.shell()
        self.clock.release("test")
        self.assertTrue(self.clock.running)
        cpu, wall = time.process_time(), time.perf_counter()
        self.root.after(10_000, self.root.quit)
        self.root.mainloop()
        share = (time.process_time() - cpu) / (time.perf_counter() - wall) * 100
        print(f"\nidle process CPU with the boil clock running: {share:.2f} % of one core over 10 s")
        self.assertLessEqual(share, 3)


if __name__ == "__main__":
    unittest.main()
