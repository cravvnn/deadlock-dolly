"""Sticker buttons: an uppercase label on a slightly tilted paper that snaps between poses.

Like Deadlock's hero tags, every sticker in a window snaps to a new pose on every third tick of
the shared 500 ms boil clock (about once every 1.5 seconds), all at the same moment, and takes a fresh
pose at once when the pointer arrives; nothing is tweened. With motion off a sticker keeps its
design pose. Only the paper moves (an anti-aliased RGBA image, so the background shows around
its tilted corners); the label is canvas text that never rotates and ignores the pointer, so
crossing it does not count as leaving the paper. Pressing dips the paper by a pixel.

A sticker mirrors an :class:`~dolly.ui.action_proxy.ActionProxy`: STOP/RESTORE, START (the
Replay page's "Open replay in Dolly") and CANCEL (shown in START's place only while a startup
can be cancelled). A disabled sticker shows its normal paper at 40 % over the background, as
START does while the game is still starting (Figma [design reference]), and still boils.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import math
import random
import tkinter as tk

from .. import ui_theme
from . import art, boil, fonts
from .canvas_controls import CanvasControl, font_object, mix
from .scale import hairline, px

POSES = 6
BOIL_EVERY = 3                   # hold each pose for 1.5 seconds on the shared 500 ms clock
PAPER_CACHE_LIMIT = 256
DISABLED_ALPHA = .4
HEIGHT = 36                      # sticker frame height, design px

# (normal, hover, pressed) paper tokens and the ink token, per kind.
_FACES = {"primary": (("accent", "accent_hover", "accent_active"), "text"),
          "secondary": (("button", "button_hover", "button_active"), "muted")}
# Least paper around a label (design px): the papers keep their design widths unless a
# label is wider (another font), then grow to label width + this. Tk measures Pulp Bold's
# START 2 px wider than Figma, so START keeps STOP/RESTORE's margin, not its own 23.5.
_LABEL_PAD = {"primary": 21, "secondary": 21}


def paper_rows(size, rect, angle, dx, dy, rgb, alpha=1.0) -> bytes:
    """RGBA rows of a ``rect`` (w, h) rectangle rotated by ``angle`` degrees (positive =
    counter-clockwise on screen), centred in ``size`` (w, h) and moved by (dx, dy).

    Coverage is the analytic box filter ``clamp(w/2 - |u| + .5) * clamp(h/2 - |v| + .5)`` in
    paper space. Only the pixels along the edges are computed one by one; the fully covered
    run of each row is filled at once.
    """
    cw, ch = size
    half_w, half_h = rect[0] / 2.0, rect[1] / 2.0
    a = math.radians(angle)
    cos_a, sin_a = math.cos(a), math.sin(a)
    cx, cy = cw / 2.0 + dx, ch / 2.0 + dy
    r, g, b = rgb
    solid = bytes((r, g, b, int(255 * alpha + .5)))
    row_bytes = cw * 4
    out = bytearray(row_bytes * ch)

    def interval(py, reach_u, reach_v):
        """Range of qx where |u| < reach_u and |v| < reach_v on the row at ``py``."""
        if reach_u <= 0 or reach_v <= 0:
            return None
        lo, hi = (py * sin_a - reach_u) / cos_a, (py * sin_a + reach_u) / cos_a
        if abs(sin_a) < 1e-12:
            if abs(py * cos_a) >= reach_v:
                return None
        else:
            v0, v1 = (-py * cos_a - reach_v) / sin_a, (-py * cos_a + reach_v) / sin_a
            lo, hi = max(lo, min(v0, v1)), min(hi, max(v0, v1))
        return (lo, hi) if lo < hi else None

    for y in range(ch):
        py = y + .5 - cy
        nonzero = interval(py, half_w + .5, half_h + .5)
        if nonzero is None:
            continue
        # Pixel x has its centre at qx = x + .5 - cx.
        x0 = max(0, math.floor(nonzero[0] + cx - .5) + 1)
        x1 = min(cw, math.ceil(nonzero[1] + cx - .5))
        full = interval(py, half_w - .5, half_h - .5)
        if full is not None:
            s0 = min(max(x0, math.floor(full[0] + cx - .5) + 1), x1)
            s1 = max(s0, min(x1, math.ceil(full[1] + cx - .5)))
        else:
            s0 = s1 = x1
        base = y * row_bytes
        for x in list(range(x0, s0)) + list(range(s1, x1)):
            qx = x + .5 - cx
            u = qx * cos_a - py * sin_a
            v = qx * sin_a + py * cos_a
            cover = min(1.0, max(0.0, half_w - abs(u) + .5)) * min(1.0, max(0.0, half_h - abs(v) + .5))
            if cover > 0:
                out[base + 4 * x:base + 4 * x + 4] = bytes((r, g, b, int(cover * alpha * 255 + .5)))
        if s1 > s0:
            out[base + 4 * s0:base + 4 * s1] = solid * (s1 - s0)
    return bytes(out)


def paper_image(root, size, rect, angle, dx, dy, fill, alpha=1.0) -> tk.PhotoImage:
    """Cached :func:`paper_rows` image for ``root``'s window (the cache lives on the toplevel,
    so images never outlive their Tk interpreter)."""
    top = root if isinstance(root, (tk.Tk, tk.Toplevel)) else root.winfo_toplevel()
    cache = getattr(top, "_dolly_paper_cache", None)
    if cache is None:
        cache = top._dolly_paper_cache = {}
    key = (tuple(size), (round(rect[0], 3), round(rect[1], 3)), round(angle, 3), round(dx, 2), round(dy, 2),
           fill, round(alpha, 3))
    image = cache.get(key)
    if image is None:
        if len(cache) >= PAPER_CACHE_LIMIT:
            cache.clear()   # stickers keep their current image referenced, so nothing goes blank
        rows = paper_rows(size, rect, angle, dx, dy, ui_theme.rgb(fill), alpha)
        image = cache[key] = art.photo_rgba(top, size[0], size[1], rows)
    return image


class CanvasSticker(CanvasControl):
    """A sticker on a host canvas (the stage), following ``action``.

    ``kind`` is ``primary`` (accent paper, white label) or ``secondary`` (button paper, muted
    label). ``tilt`` is the paper's resting angle in degrees (positive = counter-clockwise on
    screen), ``paper`` its design size and ``paper_offset`` where its centre sits relative to the
    label's (design px; chrome.md 4.3 has the Figma values). ``place`` puts the sticker frame's
    top-left corner; the label is centred in the frame.
    """

    boil_every = BOIL_EVERY      # read by the boil clock: advance on every third tick only

    def __init__(self, canvas, *, action, label: str, kind: str, tilt: float, seed: int,
                 paper: tuple[float, float], paper_offset: tuple[float, float], scale: float, font):
        if kind not in _FACES:
            raise ValueError(f"unknown sticker kind {kind!r}")
        super().__init__(canvas, action=action, scale=scale)
        self.kind = kind
        self.label_text = label.upper()
        self.tilt = float(tilt)
        self._font = font
        self._measure = font_object(canvas, font)
        self._paper_design = paper
        self._offset = (paper_offset[0] * scale, paper_offset[1] * scale)
        self._unit = max(1, px(scale, 1))
        self._motion = boil.motion_enabled()
        self._rng = random.Random(seed)
        self.poses = [(0.0, 0, 0)]   # the first pose is the design at rest
        for _ in range(POSES - 1):
            self.poses.append((self._rng.uniform(-1.1, 1.1), self._rng.choice((-self._unit, 0, self._unit)),
                               self._rng.choice((-self._unit, 0, self._unit))))
        self.pose = 0
        self._x = self._y = 0
        self._label_width = None
        self._image = None
        self._sent = {}               # item -> options last sent, so a tick sends only what changed
        self._kept = {}               # paper image name -> hidden item holding its drawable (see _keep)
        self._warm_job = None
        self._warm_queue = None
        self._top = canvas.winfo_toplevel()
        self._layout()
        self.paper = canvas.create_image(0, 0, anchor="nw", tags=(self.tag,))
        self.ring = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="", outline="", width=hairline(scale), state="hidden")
        self.label = canvas.create_text(0, 0, text=self.label_text, font=font, anchor="center", state="disabled")
        self._connect()
        self._draw()
        boil.BoilClock.of(canvas).register(self)
        self._unfont = fonts.on_change(canvas, self._font_changed)
        canvas.bind("<Map>", self._mapped, add="+")
        if canvas.winfo_ismapped():
            self._mapped()

    # -- geometry ------------------------------------------------------------------------

    def _layout(self):
        """Paper, frame and image sizes for the label as the font measures it now."""
        s = self.scale
        self._label_width = self._measure.measure(self.label_text)
        self._paper_w = max(px(s, self._paper_design[0]), self._label_width + px(s, _LABEL_PAD[self.kind]))
        self._paper_h = px(s, self._paper_design[1])
        self._frame_w = self._paper_w
        self._frame_h = px(s, HEIGHT)
        reach = math.sin(math.radians(abs(self.tilt) + 2.0))   # room for the tilt plus a pose's extra
        margin = px(s, 4) + 2 * max(abs(v) for v in self._offset) + 2 * self._unit
        self._cw = int(self._paper_w + self._paper_h * reach + margin) + 2
        self._ch = int(self._paper_h + self._paper_w * reach + margin) + 2

    def _refresh_metrics(self):
        if self._measure.measure(self.label_text) != self._label_width:
            self._layout()

    def _font_changed(self):
        # The private display font arrived (fonts reconfigures the named font in place).
        if not self._destroyed:
            self._refresh_metrics()
            self._draw()

    def width(self) -> int:
        self._refresh_metrics()
        return self._frame_w

    def height(self) -> int:
        return self._frame_h

    def place(self, x, y):
        """Put the sticker frame's top-left corner at (x, y)."""
        self._x, self._y = int(round(x)), int(round(y))
        self._refresh_metrics()
        self._draw()

    # -- state ---------------------------------------------------------------------------

    def set_shown(self, shown: bool) -> None:
        shown = bool(shown)
        if shown == self._shown:
            return
        self._shown = shown
        if not shown:
            self._drop_pointer()
        self._draw()

    def shown(self) -> bool:
        return self._shown

    def face(self, pressed=None) -> tuple[str, str, float]:
        """(paper colour, label colour, paper opacity) for the current state."""
        t = ui_theme.TOKENS
        papers, ink_key = _FACES[self.kind]
        ink = t[ink_key]
        if not self.enabled():
            return t[papers[0]], mix(ink, t["backdrop"], DISABLED_ALPHA), DISABLED_ALPHA
        pressed = self._pressed if pressed is None else pressed
        paper = papers[2] if pressed else (papers[1] if self._hover else papers[0])
        return t[paper], ink, 1.0

    def advance(self):
        """Snap to another pose (never the same one twice in a row); still with motion off.
        Called by the boil clock on every ``boil_every``-th tick and at once when the pointer
        arrives."""
        if not self._motion:
            return
        self.pose = self._rng.choice([i for i in range(len(self.poses)) if i != self.pose])
        if self._shown:
            self._draw()

    def _entered(self):
        self.advance()   # a fresh pose under the pointer, like a tag being nudged

    # -- drawing -------------------------------------------------------------------------

    def _paper_args(self, pose_index, pressed):
        """(left, top, image size, paper size, angle, dx, dy) of the paper image for one pose."""
        extra, dx, dy = self.poses[pose_index]
        if pressed:
            dy += self._unit   # the paper dips when pressed; the label stays
        cx = self._x + self._frame_w / 2.0
        cy = self._y + self._frame_h / 2.0
        left = int(math.floor(cx - self._cw / 2.0))
        top = int(math.floor(cy - self._ch / 2.0))
        # The image's own centre can sit half a pixel off the frame's; carry that in dx/dy.
        dx += cx + self._offset[0] - (left + self._cw / 2.0)
        dy += cy + self._offset[1] - (top + self._ch / 2.0)
        return left, top, (self._cw, self._ch), (self._paper_w, self._paper_h), self.tilt + extra, dx, dy

    def _send(self, item, coords=None, **options):
        """Move or reconfigure ``item`` only where something changed since the last draw."""
        sent = self._sent.setdefault(item, {})
        if coords is not None and sent.get("coords") != coords:
            self.canvas.coords(item, *coords)
            sent["coords"] = coords
        changed = {key: value for key, value in options.items() if sent.get(key) != value}
        if changed:
            self.canvas.itemconfigure(item, **changed)
            sent.update(changed)

    def _draw(self):
        if self._destroyed:
            return
        if not self.visible():
            for item in (self.paper, self.label, self.ring):
                self._send(item, state="hidden")
            return
        left, top, size, rect, angle, dx, dy = self._paper_args(self.pose, self._pressed)
        paper, ink, alpha = self.face()
        self._image = paper_image(self._top, size, rect, angle, dx, dy, paper, alpha)
        self._keep(self._image)
        self._send(self.paper, (left, top), image=self._image, state="normal")
        self._send(self.label, (self._x + self._frame_w / 2.0, self._y + self._frame_h / 2.0),
                   fill=ink, disabledfill=ink, state="disabled")
        if self._focused and self.focusable():
            a = math.radians(angle)
            cos_a, sin_a = math.cos(a), math.sin(a)
            cx, cy = left + size[0] / 2.0 + dx, top + size[1] / 2.0 + dy
            hw, hh = rect[0] / 2.0 + px(self.scale, 2), rect[1] / 2.0 + px(self.scale, 2)
            points = []
            for u, v in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
                points += [cx + u * cos_a + v * sin_a, cy - u * sin_a + v * cos_a]
            color = ui_theme.TOKENS["text"] if self.kind == "primary" else ui_theme.TOKENS["accent"]
            self._send(self.ring, tuple(points), outline=color, state="disabled")
        else:
            self._send(self.ring, state="hidden")

    # -- pre-warm ------------------------------------------------------------------------

    def _mapped(self, _event=None):
        """After the first map, render the resting paper of every pose in idle slices (one
        image per slice), so the first snaps never wait for a paper."""
        if self._warm_queue is not None or self._destroyed:
            return
        self._warm_queue = list(range(POSES))
        self._warm_job = self.canvas.after_idle(self._warm)

    def _warm(self):
        self._warm_job = None
        if self._destroyed or not self._warm_queue:
            return
        index = self._warm_queue.pop(0)
        try:
            _left, _top, size, rect, angle, dx, dy = self._paper_args(index, False)
            paper, _ink, alpha = self.face(pressed=False)
            self._keep(paper_image(self._top, size, rect, angle, dx, dy, paper, alpha))
        except tk.TclError:
            return
        if self._warm_queue:
            self._warm_job = self.canvas.after_idle(self._warm)

    def destroy(self) -> None:
        if self._destroyed:
            return
        if self._warm_job is not None:
            try:
                self.canvas.after_cancel(self._warm_job)
            except tk.TclError:
                pass
            self._warm_job = None
        if self._unfont is not None:
            self._unfont()
            self._unfont = None
        try:
            boil.BoilClock.of(self.canvas).unregister(self)
        except tk.TclError:
            pass
        super().destroy()
        self._image = None

    def _keep(self, image):
        """Keep ``image`` drawable on this canvas between snaps.

        Tk frees a photo's drawable once no item shows it and converts every pixel
        again on its next use: about a millisecond per snap for a 150 % paper, most
        of a boil tick. A hidden item per paper this sticker has shown holds it.
        """
        name = str(image)
        if name in self._kept:
            return
        if len(self._kept) >= PAPER_CACHE_LIMIT:
            self.canvas.delete(*self._kept.values())   # the cache was cleared: drop the old papers too
            self._kept.clear()
        self._kept[name] = self.canvas.create_image(0, 0, anchor="nw", image=image, state="hidden")

    def _delete_items(self):
        self.canvas.delete(self.paper, self.ring, self.label, *self._kept.values())
        self._kept.clear()

    def _text_value(self) -> str:
        return self.action.cget("text")
