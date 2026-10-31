"""Animated window background behind the redesigned shell (Figma [design reference], "BG").

Tk widgets are opaque, so the background is painted on every canvas that shows it: the stage
(header, footer, margins) and the Replay page's canvas (the gaps between its cards). Each
canvas paints it at its own offset, so it reads as one layer fixed to the window, also while
a page scrolls. Three layers, bottom to top, on every attached canvas:

* ``bd_base``: the screen gradient (``stage_top`` to ``bg``) as the few solid bands an 8-bit
  screen shows, seen only while the picture is not (during a resize, or without the art).
  Rectangles instead of an image: a hidden full-window image would still hold its pixels;
* ``bd_pic``: the still picture (gradient, photo, ink wash, sigil and the halftone rim), one
  opaque image shared by every canvas. ``assets/ui`` holds it at the design size; it is
  stretched once for each stage size (stdlib nearest neighbour, at most at the art's own
  resolution) and enlarged with Tk's ``copy -zoom``, and it never changes in between;
* ``bd_vec``: the dark shard, a polygon in eight hand-cut poses. It is clipped to where each
  canvas actually shows the background, pooled, and moved with ``coords``.

Motion stays small. Tk repaints one rectangle around everything that changed on a canvas, and
a picture that changes is a whole-window repaint on every canvas that shows it, twice a second.
So only the shard boils (on screen it is the bottom right corner, beside the stickers), and an
item is sent to Tk only when its look changes. Poses snap on the root's 500 ms boil clock: held,
never tweened. While the window is being resized the picture and shapes step aside, and catch
up once the size has settled. With motion off the design pose stays put; without the art
(``assets`` is None) only the gradient and the design's shard remain.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_backdrop.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import logging
import math
import random
import tkinter as tk

from .. import ui_theme
from . import art, boil

LOG = logging.getLogger(__name__)

DESIGN_W, DESIGN_H = 1010, 810          # Screen / Replay, design px
GRADIENT_ROWS = 452                     # the screen gradient reaches ``bg`` here (design px)
POSE_SEED = 1717
# The Figma shard (Vector 7) in screen design px: pose 0, and the only pose when the generated
# art is missing.
DESIGN_POSE = {"shard": ((381.0, 884.0), (700.0, 772.0), (700.0, 690.5), (1023.5, 741.5), (1175.0, 101.0),
                         (1356.0, 1008.5))}


# --------------------------------------------------------------------------
# Geometry.


def clip_polygon(points, a, b, c):
    """The part of ``points`` where ``a*x + b*y + c >= 0`` (one Sutherland-Hodgman pass)."""
    out = []
    count = len(points)
    for i in range(count):
        p, q = points[i], points[(i + 1) % count]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            k = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k))
    return out


def clip_to_box(points, box):
    """``points`` clipped to the rectangle ``(x0, y0, x1, y1)``; [] when nothing is left."""
    x0, y0, x1, y1 = box
    for a, b, c in ((1, 0, -x0), (-1, 0, x1), (0, 1, -y0), (0, -1, y1)):
        points = clip_polygon(points, a, b, c)
        if len(points) < 3:
            return []
    return points


def bounds(points):
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def cover_scale(width, height) -> float:
    """Design px to stage px: the design frame covers the stage, anchored at its top-left."""
    return max(width / DESIGN_W, height / DESIGN_H)


def working_zoom(cover, source_scale=1.0) -> int:
    """Stage px per working px. The picture is stretched in Python at no more than the art's own
    resolution (``source_scale`` art px per design px), and Tk enlarges it the rest of the way."""
    return max(1, math.ceil(cover / source_scale - 1e-6))


def source_map(count, zoom, cover, source_scale, source_size) -> list[int]:
    """Source pixel for each of ``count`` working pixels (nearest neighbour)."""
    last = source_size - 1
    step = zoom / cover * source_scale
    return [min(last, max(0, int((i + .5) * step))) for i in range(count)]


# --------------------------------------------------------------------------


class _Surface:
    """One canvas the background is painted on. ``areas()`` lists where it can be seen, as
    (x0, y0, x1, y1) in the canvas's viewport pixels; None means everywhere.

    Every item's last look is remembered, so nothing is sent to Tk twice: Tk repaints an
    item's whole area for any ``coords`` or ``itemconfigure``, even one that changes nothing.
    """

    __slots__ = ("canvas", "areas", "base", "base_looks", "pic", "pic_look", "items", "looks", "offset", "views",
                 "dirty")

    def __init__(self, canvas, areas):
        self.canvas, self.areas = canvas, areas
        self.base: list[int] = []      # one rectangle per gradient band
        self.base_looks: list = []
        self.pic = None
        self.pic_look = None
        # Polygons are reused from tick to tick (moving one is much cheaper than making one);
        # ``looks`` holds each one's coordinates, or None while it is hidden.
        self.items: list[int] = []
        self.looks: list = []
        self.offset = None
        self.views: list = []          # open areas in stage px, as of the last draw
        self.dirty = True

    def forget_looks(self) -> None:
        """Send everything again on the next draw (after items were changed behind our back)."""
        self.base_looks = [None] * len(self.base)
        self.pic_look = None
        self.looks = [None] * len(self.items)


class Backdrop:
    """Paints and animates the background on the attached canvases of one window."""

    @classmethod
    def of(cls, widget) -> "Backdrop | None":
        try:
            top = widget.winfo_toplevel()
        except tk.TclError:
            return None
        return getattr(top, "_dolly_backdrop", None)

    def __init__(self, root, *, scale: float, assets: "art.Assets | None"):
        self.root = root
        self.scale = scale
        self.assets = assets
        manifest = getattr(assets, "manifest", None) or {}
        self._poses = self._read_poses(manifest)
        self._fill = ui_theme.TOKENS.get((manifest.get("shapes") or {}).get("shard_fill") or "",
                                         ui_theme.TOKENS["backdrop_shard"])
        levels = manifest.get("levels") or {}
        files = list(levels.get("files") or ()) if assets is not None else []
        # The picture is the stack of every level file (bands, top to bottom) or, from an
        # older set, the first level of the cycle.
        cycle = levels.get("cycle") or (0,)
        self._bands = list(range(len(files))) if levels.get("stacked") else ([cycle[0]] if files else [])
        self._source_scale = float(levels.get("scale") or 1.0)
        self._motion = boil.motion_enabled()
        self._rng = random.Random(POSE_SEED)
        self._pose = 0
        self._frozen = False
        self._destroyed = False
        self._stage = None
        self._size = None                 # stage size the picture was built for
        self._cover = 1.0
        self.surfaces: list[_Surface] = []
        self.base_runs: list[tuple[int, int, str]] = []   # (top, bottom, colour) bands of the gradient
        self.display = None               # the picture on show, shared by every surface
        self.renders = 0                  # how often the picture was built (once per stage size)
        self._catch_up_job = None
        self._unlisten = None

    # -- set-up --------------------------------------------------------------------------

    @staticmethod
    def _read_poses(manifest):
        poses = []
        for pose in (manifest.get("shapes") or {}).get("poses") or ():
            try:
                shard = tuple((float(x), float(y)) for x, y in pose["shard"])
            except (KeyError, TypeError, ValueError):
                shard = ()
            if len(shard) < 3:
                LOG.warning("Ignoring malformed backdrop poses")
                return [DESIGN_POSE]
            poses.append({"shard": shard})
        return poses or [DESIGN_POSE]

    def install(self) -> None:
        """Make this the window's backdrop and join its boil clock (first, so the background
        changes in the same moment as everything drawn over it)."""
        self.root._dolly_backdrop = self
        clock = boil.BoilClock.of(self.root)
        clock.register(self)
        self._unlisten = clock.add_settle_listener(self._settle)
        self.root.bind("<Destroy>", self._root_destroyed, add="+")

    def set_frame(self, stage: tk.Canvas) -> None:
        """The background covers ``stage`` (the stage canvas right of the sidebar)."""
        self._stage = stage
        stage.bind("<Configure>", self._stage_configured, add="+")
        self._schedule_catch_up()

    def attach(self, canvas: tk.Canvas, areas=None) -> None:
        """Paint the background on ``canvas`` wherever ``areas()`` says it shows."""
        for surface in self.surfaces:
            if surface.canvas is canvas:
                surface.areas, surface.dirty = areas, True
                self._schedule_catch_up()
                return
        surface = _Surface(canvas, areas)
        surface.pic = canvas.create_image(0, 0, anchor="nw", state="hidden", tags=("bd", "bd_pic"))
        canvas.tag_lower("bd_pic")
        self.surfaces.append(surface)
        canvas.bind("<Map>", lambda _e, c=canvas: self.sync(c, redraw=True), add="+")
        canvas.bind("<Destroy>", lambda e, c=canvas: self.detach(c) if e.widget is c else None, add="+")
        self._schedule_catch_up()

    def detach(self, canvas) -> None:
        for surface in list(self.surfaces):
            if surface.canvas is canvas:
                self.surfaces.remove(surface)
                try:
                    canvas.delete(surface.pic, *surface.base, *surface.items)
                except tk.TclError:
                    pass

    def invalidate(self, canvas=None) -> None:
        """Where the background shows on ``canvas`` (or on every canvas) has changed."""
        for surface in self.surfaces:
            if canvas is None or surface.canvas is canvas:
                surface.dirty = True
        self._schedule_catch_up()

    # -- the boil clock's interface ------------------------------------------------------

    @property
    def pose(self) -> int:
        return self._pose

    @property
    def poses(self):
        return self._poses

    @property
    def frozen(self) -> bool:
        return self._frozen

    def picture_at(self, x, y):
        """The picture's (r, g, b) at stage pixel (x, y), or None without a picture."""
        return self.display.get(int(x), int(y)) if self.display is not None else None

    def winfo_exists(self) -> bool:
        if self._destroyed:
            return False
        try:
            return bool(self.root.winfo_exists())
        except tk.TclError:
            return False

    def advance(self) -> None:
        """One boil tick: another shard pose (never the same twice). The picture holds still."""
        if self._frozen or self._destroyed or not self._motion or len(self._poses) < 2:
            return
        self._pose = self._rng.choice([i for i in range(len(self._poses)) if i != self._pose])
        for surface in self.surfaces:
            try:
                mapped = surface.canvas.winfo_ismapped()
            except tk.TclError:
                continue
            if mapped and surface.offset is not None:
                surface.views = self._views(surface)
                self._draw_vectors(surface)
            else:
                surface.dirty = True

    # -- holding still while the window is resized ---------------------------------------

    def _settle(self, frozen: bool) -> None:
        if self._destroyed:
            return
        if frozen:
            self._frozen = True
            # Shapes and picture drawn for the old size would only slow every step of the
            # resize down; the gradient takes their place.
            for surface in self.surfaces:
                try:
                    surface.canvas.itemconfigure("bd_vec", state="hidden")
                    surface.looks = [None] * len(surface.items)
                    if surface.offset is not None:
                        self._place_images(surface)
                except tk.TclError:
                    surface.forget_looks()
                surface.dirty = True
        else:
            self._frozen = False
            self._schedule_catch_up()

    def _stage_configured(self, event) -> None:
        if event.widget is not self._stage:
            return
        if not self._frozen:
            self._schedule_catch_up()

    def _schedule_catch_up(self) -> None:
        if self._catch_up_job is None and not self._destroyed:
            try:
                self._catch_up_job = self.root.after_idle(self._catch_up)
            except tk.TclError:
                pass

    def _catch_up(self) -> None:
        self._catch_up_job = None
        if self._frozen or self._destroyed:
            return
        size = self._frame_size()
        if size is None:
            return
        if size != self._size:
            self._resize(size)
        self.sync()

    def _frame_size(self):
        frame = self._stage if self._stage is not None else self.root
        try:
            width, height = frame.winfo_width(), frame.winfo_height()
        except tk.TclError:
            return None
        return (width, height) if width > 1 and height > 1 else None

    def _root_destroyed(self, event) -> None:
        if event.widget is not self.root:
            return
        self._destroyed = True
        if self._catch_up_job is not None:
            try:
                self.root.after_cancel(self._catch_up_job)
            except tk.TclError:
                pass
            self._catch_up_job = None
        if self._unlisten is not None:
            self._unlisten()
            self._unlisten = None

    # -- art for a stage size -------------------------------------------------------------

    def _build_base(self, size) -> None:
        """The gradient's 8-bit bands: ``stage_top`` at the top to ``bg`` at the ramp's end."""
        width, height = size
        ramp = max(1, int(GRADIENT_ROWS * cover_scale(width, height) + .5))
        top, bottom = ui_theme.rgb(ui_theme.TOKENS["stage_top"]), ui_theme.rgb(ui_theme.TOKENS["bg"])
        runs, start, current = [], 0, None
        for row in range(min(height, ramp + 1)):
            t = row / ramp
            color = "#%02x%02x%02x" % tuple(int(a + (b - a) * t + .5) for a, b in zip(top, bottom))
            if color != current:
                if current is not None:
                    runs.append((start, row, current))
                start, current = row, color
        runs.append((start, height, current))
        self.base_runs = runs

    def _resize(self, size) -> None:
        self._size = size
        self._cover = cover_scale(*size)
        self._build_base(size)
        self.display = None
        if self._bands:
            try:
                self.display = self._render_picture(size)
            except (OSError, ValueError, KeyError, IndexError, tk.TclError):
                LOG.warning("Could not prepare the backdrop picture; showing the plain gradient", exc_info=True)
                self._bands = []
        for surface in self.surfaces:
            surface.dirty = True

    def _source(self):
        """The design-size picture as (width, height, packed RGB rows)."""
        width, height, rows = None, 0, []
        for index in self._bands:
            band_w, band_h, rgb = self.assets.level_rgb(index)
            if width is not None and band_w != width:
                raise ValueError("backdrop bands differ in width")
            width, height = band_w, height + band_h
            rows.append(rgb)
        return width, height, b"".join(rows)

    def _render_picture(self, size) -> tk.PhotoImage:
        """The opaque picture covering a ``size`` stage (made once per size, never redrawn)."""
        width, height = size
        source_w, source_h, rgb = self._source()
        zoom = working_zoom(self._cover, self._source_scale)
        tw, th = math.ceil(width / zoom), math.ceil(height / zoom)
        map_x = source_map(tw, zoom, self._cover, self._source_scale, source_w)
        map_y = source_map(th, zoom, self._cover, self._source_scale, source_h)
        if map_x == list(range(source_w)):    # one design px per stage px: whole rows, as they are
            stride = 3 * source_w
            body = b"".join(rgb[row * stride:(row + 1) * stride] for row in map_y)
        else:
            body = art.stretch_rgb(rgb, source_w, source_h, tw, th, map_x, map_y)
        working = tk.PhotoImage(master=self.root, data=b"P6 %d %d 255\n" % (tw, th) + body, format="ppm")
        self.renders += 1
        if zoom == 1:
            return working
        # Tk's default compositing rule: the picture is opaque, and "-compositingrule set" costs
        # about twice as much.
        display = tk.PhotoImage(master=self.root, width=width, height=height)
        display.tk.call(display.name, "copy", working.name, "-zoom", zoom, zoom)
        return display

    # -- drawing -------------------------------------------------------------------------

    def _offset(self, canvas):
        """(vx, vy, sx, sy): the canvas viewport's position in the stage, and its scroll."""
        frame = self._stage if self._stage is not None else self.root
        return (canvas.winfo_rootx() - frame.winfo_rootx(), canvas.winfo_rooty() - frame.winfo_rooty(),
                canvas.canvasx(0), canvas.canvasy(0))

    def sync(self, canvas=None, *, redraw: bool = False) -> None:
        """Keep the background aligned to the stage after a canvas moved, scrolled or changed
        (``redraw``: its open areas may have changed). Only what differs is sent to Tk."""
        if self._destroyed:
            return
        for surface in list(self.surfaces):
            if canvas is not None and surface.canvas is not canvas:
                continue
            try:
                if not surface.canvas.winfo_ismapped():
                    surface.dirty = True
                    continue
                offset = self._offset(surface.canvas)
            except tk.TclError:
                self.surfaces.remove(surface)
                continue
            if redraw or surface.dirty or offset != surface.offset:
                surface.offset = offset
                surface.views = self._views(surface)
                self._place_images(surface)
                self._draw_vectors(surface)

    def _views(self, surface) -> list:
        """Where ``surface`` shows the background, in stage px."""
        c = surface.canvas
        vx, vy, _sx, _sy = surface.offset
        rects = surface.areas() if surface.areas else [(0, 0, c.winfo_width(), c.winfo_height())]
        return [(x0 + vx, y0 + vy, x1 + vx, y1 + vy) for x0, y0, x1, y1 in rects if x1 > x0 and y1 > y0]

    def _place_images(self, surface) -> None:
        c = surface.canvas
        vx, vy, sx, sy = surface.offset
        x, y = int(round(sx - vx)), int(round(sy - vy))
        shown = self.display is not None and not self._frozen
        # The gradient is seen only while the picture is not (it would be drawn twice otherwise).
        base, looks, runs = surface.base, surface.base_looks, self.base_runs
        created = False
        while len(base) < len(runs):
            base.append(c.create_rectangle(0, 0, 0, 0, width=0, state="hidden", tags=("bd", "bd_base")))
            looks.append(None)
            created = True
        while len(base) > len(runs):
            c.delete(base.pop())
            looks.pop()
        right = x + (self._size[0] if self._size else c.winfo_width())
        state = "hidden" if shown else "disabled"
        for index, (item, (top, bottom, color)) in enumerate(zip(base, runs)):
            old = looks[index]
            if shown and old is not None and old[2] == "hidden":
                continue   # a hidden band is placed when it is shown again
            look = ((x, y + top, right, y + bottom), color, state)
            if old == look:
                continue
            if old is None or old[0] != look[0]:
                c.coords(item, *look[0])
            if old is None or old[1:] != look[1:]:
                c.itemconfigure(item, fill=color, state=state)
            looks[index] = look
        look = ((x, y), str(self.display or ""), "disabled" if shown else "hidden")
        old = surface.pic_look
        if old != look:
            if old is None or old[0] != look[0]:
                c.coords(surface.pic, x, y)
            if old is None or old[1:] != look[1:]:
                c.itemconfigure(surface.pic, image=look[1], state=look[2])
            surface.pic_look = look
        if created:   # new items start on top; everything else is drawn over the background
            self._lower(c)

    def _draw_vectors(self, surface) -> None:
        if self._frozen:
            surface.dirty = True
            return
        surface.dirty = False
        c = surface.canvas
        vx, vy, sx, sy = surface.offset
        s = self._cover if self._size is not None else cover_scale(*(self._frame_size() or (DESIGN_W, DESIGN_H)))
        points = [(x * s, y * s) for x, y in self._poses[self._pose]["shard"]]
        bx0, by0, bx1, by1 = bounds(points)
        items, looks = surface.items, surface.looks
        used, created = 0, False
        # Padded by a pixel, so a shard clipped on two canvases meets without a seam.
        for x0, y0, x1, y1 in surface.views:
            view = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
            if bx1 < view[0] or bx0 > view[2] or by1 < view[1] or by0 > view[3]:
                continue
            piece = clip_to_box(points, view)
            if not piece:
                continue
            coords = []
            for x, y in piece:
                coords += (round(x - vx + sx, 1), round(y - vy + sy, 1))
            coords = tuple(coords)
            if used < len(items):
                if looks[used] != coords:
                    c.coords(items[used], coords)
                    if looks[used] is None:
                        c.itemconfigure(items[used], state="disabled")
                    looks[used] = coords
            else:
                items.append(c.create_polygon(coords, fill=self._fill, outline="", state="disabled",
                                              tags=("bd", "bd_vec")))
                looks.append(coords)
                created = True
            used += 1
        for index in range(used, len(items)):
            if looks[index] is not None:
                c.itemconfigure(items[index], state="hidden")
                looks[index] = None
        if created:
            self._lower(c)

    @staticmethod
    def _lower(canvas) -> None:
        """Background items under everything else, picture first."""
        canvas.tag_lower("bd_vec")
        canvas.tag_lower("bd_pic")
        canvas.tag_lower("bd_base")
