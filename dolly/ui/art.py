"""Desktop UI art: the generated ``assets/ui`` set and small stdlib image helpers.

``tools/build_ui_art.py`` writes the PNGs and ``manifest.json``; :func:`load`
verifies every file against the manifest's SHA-256 before anything uses them,
and returns None otherwise so the shell falls back to flat colours. Everything
here is stdlib + Tk: PNGs are decoded by Tk itself, the backdrop levels by a
filter-0 fast path, and the small generated images (gradients, corners, dots)
are written as PNG data in pure Python.

Images belong to one Tk interpreter. Caches live on the root object
(``root._dolly_art_cache``), never in module globals, because image names are
reused after a root is destroyed. Every returned ``PhotoImage`` must stay
referenced by the caller (cached images are kept by the cache).

Portions adapted from RenderLock 0.3.0b16 (agr/gui_widgets.py, agr/gui_backdrop.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
import logging
import math
from pathlib import Path
import struct
import tkinter as tk
import zlib

from .. import ui_theme
from ..runtime import resource_root
from .scale import bucket

LOG = logging.getLogger(__name__)

MANIFEST = "manifest.json"
MANIFEST_VERSION = 1
_MANIFEST_LIMIT = 256 * 1024
_FILE_LIMIT = 16 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SUPERSAMPLE = 4
_REPORTED = set()


def assets_dir() -> Path:
    return resource_root(Path(__file__).resolve().parents[2]) / "assets" / "ui"


class Assets:
    """A verified ``assets/ui`` manifest and its files."""

    def __init__(self, directory: Path, manifest: dict):
        self.directory = Path(directory)
        self.manifest = manifest
        self._levels = {}

    def path(self, name: str) -> Path:
        if name not in self.manifest["files"]:
            raise KeyError(f"{name} is not listed in {MANIFEST}")
        return self.directory / name

    def photo(self, root, name: str) -> tk.PhotoImage:
        """A file's PNG decoded by Tk, cached on ``root``."""
        key = ("file", str(self.directory), name)
        cache = _cache(root)
        image = cache.get(key)
        if image is None:
            image = cache[key] = tk.PhotoImage(master=_root_of(root), file=str(self.path(name)))
        return image

    def bucket_photo(self, root, group: str, scale: float) -> tk.PhotoImage:
        """The ``"sidebar_art"`` or ``"nav_icons"`` file for the largest scale not above ``scale``."""
        files = self.manifest[group]["files"]
        name = files.get(bucket(scale))
        if name is None:
            fitting = sorted((float(key), key) for key in files if float(key) <= scale + .01)
            name = files[fitting[-1][1] if fitting else min(files, key=float)]
        return self.photo(root, name)

    def level_rgb(self, index: int) -> tuple[int, int, bytes]:
        """Backdrop level ``index`` as ``(width, height, packed RGB rows)``."""
        level = self._levels.get(index)
        if level is None:
            name = self.manifest["levels"]["files"][index]
            level = self._levels[index] = read_png_rgb_filter0(self.path(name))
        return level


def load(root=None, *, directory=None) -> Assets | None:
    """The verified art set, or None (reported once) when it is missing, stale or altered.

    With ``root`` the result is cached on it, so every caller in one window
    shares a single verification.
    """
    folder = Path(directory) if directory is not None else assets_dir()
    if root is not None:
        cached = getattr(_root_of(root), "_dolly_assets", None)
        if cached is not None and cached[0] == folder:
            return cached[1]
    try:
        assets = Assets(folder, _verified_manifest(folder))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        message = f"Desktop UI art unavailable ({folder}): {exc}"
        if message not in _REPORTED:
            _REPORTED.add(message)
            LOG.warning("%s; using flat colours", message)
        assets = None
    if root is not None:
        _root_of(root)._dolly_assets = (folder, assets)
    return assets


def _verified_manifest(folder):
    path = folder / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{MANIFEST} is missing")
    if path.stat().st_size > _MANIFEST_LIMIT:
        raise ValueError(f"{MANIFEST} is too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported {MANIFEST} version")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError(f"{MANIFEST} lists no files")
    levels = manifest["levels"]
    referenced = list(levels["files"])
    if not referenced or len(levels["cycle"]) == 0 or any(
            not isinstance(step, int) or not 0 <= step < len(referenced) for step in levels["cycle"]):
        raise ValueError("invalid backdrop levels")
    if not isinstance(manifest["shapes"]["poses"], list):
        raise ValueError("invalid backdrop shapes")
    for group in ("sidebar_art", "nav_icons", "playback_art"):
        if group == "playback_art" and group not in manifest:
            continue            # an older set: the Shot Playback card is then plain
        group_files = manifest[group]["files"]
        if not isinstance(group_files, dict) or not group_files:
            raise ValueError(f"no {group} files")
        for key, name in group_files.items():
            float(key)
            referenced.append(name)
    for name in referenced:
        if name not in files:
            raise ValueError(f"{name} is referenced but not listed")
    for name, entry in files.items():
        if not isinstance(name, str) or Path(name).name != name or name in (".", "..", MANIFEST):
            raise ValueError(f"unsafe file name {name!r}")
        file = folder / name
        if file.is_symlink() or not file.is_file():
            raise ValueError(f"{name} is missing")
        size = file.stat().st_size
        if size != entry["bytes"] or size > _FILE_LIMIT:
            raise ValueError(f"{name} has {size} bytes, expected {entry['bytes']}")
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"{name} does not match its SHA-256")
        expected = entry.get("px")
        if expected is not None and name.lower().endswith(".png"):
            if data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
                raise ValueError(f"{name} is not a PNG")
            if list(struct.unpack(">II", data[16:24])) != list(expected):
                raise ValueError(f"{name} is not {expected[0]}x{expected[1]}")
    return manifest


# PNG ---------------------------------------------------------------------

def read_png_rgb_filter0(path) -> tuple[int, int, bytes]:
    """Decode an 8-bit RGB PNG whose every row uses filter 0; anything else raises ValueError.

    The build tool writes the backdrop levels this way so the runtime can strip
    the row bytes at C speed instead of unfiltering in Python.
    """
    data = Path(path).read_bytes()
    if data[:8] != _PNG_SIGNATURE:
        raise ValueError("not a PNG file")
    position, header, compressed = 8, None, []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        crc = data[position + 8 + length:position + 12 + length]
        if len(body) != length or len(crc) != 4 or struct.unpack(">I", crc)[0] != zlib.crc32(kind + body):
            raise ValueError(f"damaged {kind.decode('latin-1')} chunk")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed.append(body)
        elif kind == b"IEND":
            break
        position += 12 + length
    if header is None or not compressed:
        raise ValueError("PNG has no image data")
    width, height, depth, colour, method, filtering, interlace = header
    if (depth, colour, method, filtering, interlace) != (8, 2, 0, 0, 0) or not width or not height:
        raise ValueError("expected a non-interlaced 8-bit RGB PNG")
    stride = 1 + 3 * width
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b"".join(compressed), stride * height + 1)
    if len(raw) != stride * height or not decoder.eof:
        raise ValueError("PNG image data has the wrong size")
    if raw[::stride] != bytes(height):
        raise ValueError("PNG rows must all use filter 0")
    return width, height, b"".join(raw[row + 1:row + stride] for row in range(0, len(raw), stride))


def png_rgba(width: int, height: int, rows: bytes) -> bytes:
    """PNG bytes (8-bit RGBA, filter 0) from packed rows of ``width * 4`` bytes."""
    stride = 4 * width
    if len(rows) != stride * height:
        raise ValueError("RGBA data does not match the image size")
    raw = b"".join(b"\0" + rows[row:row + stride] for row in range(0, len(rows), stride))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (_PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def photo_rgba(root, width: int, height: int, rows: bytes) -> tk.PhotoImage:
    """A Tk image from packed RGBA rows; alpha blends over canvas items below it."""
    return tk.PhotoImage(master=_root_of(root), data=base64.b64encode(png_rgba(width, height, rows)),
                         format="png")


def stretch_rgb(src: bytes, sw: int, sh: int, tw: int, th: int, map_x=None, map_y=None) -> bytes:
    """Nearest-neighbour stretch of packed RGB rows to ``tw`` x ``th``.

    ``map_x``/``map_y`` give the source column/row for each target column/row,
    as a sequence or a function of the target index (default: proportional).
    Indices are clamped. Each distinct source row is mapped once, at C speed.
    """
    def indices(mapping, target, source):
        if mapping is None:
            mapping = lambda index: int((index + .5) * source / target)
        values = mapping if not callable(mapping) else [mapping(index) for index in range(target)]
        if len(values) != target:
            raise ValueError("pixel map does not match the target size")
        return [min(source - 1, max(0, int(value))) for value in values]
    stride = 3 * sw
    if len(src) != stride * sh:
        raise ValueError("RGB data does not match the source size")
    xmap = []
    for column in indices(map_x, tw, sw):
        xmap += (3 * column, 3 * column + 1, 3 * column + 2)
    rows, cache = [], {}
    for row in indices(map_y, th, sh):
        line = cache.get(row)
        if line is None:
            packed = src[row * stride:(row + 1) * stride]
            line = cache[row] = bytes(map(packed.__getitem__, xmap))
        rows.append(line)
    return b"".join(rows)


# Generated images -----------------------------------------------------------

def column_gradient(root, width: int, height: int, top, bottom, ramp_px) -> tk.PhotoImage:
    """``top`` at row 0 blending linearly to ``bottom`` at ``ramp_px``, then solid.

    One pixel column is computed and Tk tiles it across the width. Colours are
    ``"#rrggbb"`` values or :data:`dolly.ui_theme.TOKENS` names.
    """
    width, height = max(1, int(width)), max(1, int(height))
    first, last = _rgb(top), _rgb(bottom)
    ramp = max(1.0, float(ramp_px))
    column = []
    for row in range(height):
        t = min(1.0, row / ramp)
        column.append("{" + _hex(a + (b - a) * t for a, b in zip(first, last)) + "}")
    image = tk.PhotoImage(master=_root_of(root), width=width, height=height)
    image.put(" ".join(column), to=(0, 0, width, height))
    return image


def h_gradient_strip(root, width: int, height: int, stops) -> tk.PhotoImage:
    """A horizontal RGBA strip; ``stops`` are ``(t, colour, alpha)`` with t and alpha in 0..1.

    Colour and alpha interpolate independently (as Figma fades to a coloured
    transparent end), sampled at pixel centres.
    """
    width, height = max(1, int(width)), max(1, int(height))
    stops = sorted((float(t), _rgb(colour), float(alpha)) for t, colour, alpha in stops)
    if not stops:
        raise ValueError("a gradient needs at least one stop")
    row = bytearray()
    for column in range(width):
        t = (column + .5) / width
        colour, alpha = _sample(stops, t)
        row += bytes((*(max(0, min(255, round(value))) for value in colour),
                      max(0, min(255, round(255 * alpha)))))
    return photo_rgba(root, width, height, bytes(row) * height)


def corner_rgba(root, radius: int, fill, outline, which: str) -> tk.PhotoImage:
    """An anti-aliased ``radius`` x ``radius`` corner (``nw``/``ne``/``sw``/``se``), transparent outside.

    The optional ``outline`` is a 1 px ring inside the edge. Cached on ``root``.
    """
    radius = max(1, int(radius))
    key = ("corner", radius, _hex(_rgb(fill)), _hex(_rgb(outline)) if outline else "", which)
    cache = _cache(root)
    image = cache.get(key)
    if image is not None:
        return image
    if which not in ("nw", "ne", "sw", "se"):
        raise ValueError(f"unknown corner {which!r}")
    paint, ring_paint = _rgb(fill), _rgb(outline) if outline else None
    samples = _SUPERSAMPLE * _SUPERSAMPLE
    rows = []
    for y in range(radius):
        row = bytearray()
        for x in range(radius):
            inner = ring = 0
            for sy in range(_SUPERSAMPLE):
                py = y + (sy + .5) / _SUPERSAMPLE
                for sx in range(_SUPERSAMPLE):
                    distance = math.hypot(radius - (x + (sx + .5) / _SUPERSAMPLE), radius - py)
                    if distance > radius:
                        continue
                    if ring_paint is not None and distance > radius - 1:
                        ring += 1
                    else:
                        inner += 1
            covered = inner + ring
            if not covered:
                row += b"\0\0\0\0"
                continue
            colour = [(paint[i] * inner + (ring_paint[i] * ring if ring else 0)) / covered for i in range(3)]
            row += bytes((*(round(value) for value in colour), round(255 * covered / samples)))
        rows.append(bytes(row))
    if which in ("ne", "se"):
        rows = [b"".join(row[i:i + 4] for i in range(len(row) - 4, -1, -4)) for row in rows]
    if which in ("sw", "se"):
        rows.reverse()
    image = cache[key] = photo_rgba(root, radius, radius, b"".join(rows))
    return image


def plate_strip(root, height: int, radius: int, line: int, fill, outline, side: str, spans,
                styles=None) -> tk.PhotoImage:
    """The ``side`` ("left" or "right") ``radius`` px column of every rounded plate in ``spans``.

    ``spans`` lists each plate's ``(top, bottom)`` rows (px, top to bottom, not
    overlapping) in an image ``height`` px tall. Per plate: its two corners on that
    side (:func:`corner_rgba`, copied with their alpha) and, between them, the
    ``line`` px edge on the outer side and the fill; transparent between plates.
    With rectangles for the rest of each plate (the fill between the columns and
    the top and bottom edge lines) it draws exactly the pixels of four corner
    images and their edges. Tk blends every partly transparent image it draws on
    its own (it reads the pixels under it back from the drawing), so a page's
    plates cost two such images however many cards it has. Not cached: it changes
    whenever a card moves within the page, which is never during a scroll.

    ``styles`` (optional, one entry per span, None = the strip's own look) lets a
    plate of another look share the strip: ``(radius, fill, outline, line)`` with a
    radius up to the strip's (its corners sit on the outer side, the fill runs to the
    strip's inner side) and no edge when ``outline`` is None. Without it the result
    is exactly the strip above.
    """
    if styles is not None and any(style is not None for style in styles):
        return _styled_strip(root, height, radius, line, fill, outline, side, spans, styles)
    radius, line = max(1, int(radius)), max(1, int(line))
    image = tk.PhotoImage(master=_root_of(root), width=radius, height=max(1, int(height)))
    paint, edge = _hex(_rgb(fill)), _hex(_rgb(outline))
    top_corner, bottom_corner = ("nw", "sw") if side == "left" else ("ne", "se")
    edge_x = (0, line) if side == "left" else (radius - line, radius)
    fill_x = (line, radius) if side == "left" else (0, radius - line)
    for top, bottom in spans:
        top, bottom = int(top), int(bottom)
        if bottom - top > 2 * radius:
            image.put(edge, to=(edge_x[0], top + radius, edge_x[1], bottom - radius))
            if fill_x[1] > fill_x[0]:
                image.put(paint, to=(fill_x[0], top + radius, fill_x[1], bottom - radius))
        for corner, y in ((top_corner, top), (bottom_corner, bottom - radius)):
            image.tk.call(str(image), "copy", str(corner_rgba(root, radius, fill, outline, corner)),
                          "-to", 0, y, "-compositingrule", "set")
    return image


def playback_art(root, scale: float, width: int, height: int, box=None) -> tk.PhotoImage | None:
    """The Shot Playback card's background (Figma [design reference]) for a ``width`` x ``height`` px card, or None.

    Opaque: the card colour, Figma's halftone dots and its faint drawing, as the build
    tool composited them (``playback_art`` in the manifest). The shipped picture is the
    design card (930 x 178) with spare rows above and below; a card of another size
    shows its centre, so the drawing stays centred as Figma's cover fill keeps it (a
    narrower card crops the sides where Figma would scale the drawing down). At the
    1, 1.5 and 2 buckets the file is used as it is; at another scale the next larger
    bucket is resampled once (nearest neighbour). ``box`` (x0, y0, x1, y1), card px,
    returns that part only (the dock's plate middle). Each crop is one Tk image copy,
    made when a card changes size (never per frame or scroll); the last few sizes are
    kept, so a caller keeps a reference to the image it shows. None without the art set.
    """
    width, height = max(1, int(width)), max(1, int(height))
    x0, y0, x1, y1 = (0, 0, width, height) if box is None else (int(value) for value in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, max(x0 + 1, x1)), min(height, max(y0 + 1, y1))
    cache = _cache(root)
    crops = cache.setdefault("playback_art_crops", OrderedDict())
    key = (round(float(scale), 4), width, height, (x0, y0, x1, y1))
    if key in crops:
        crops.move_to_end(key)
        return crops[key]
    source = _playback_picture(root, scale)
    if source is None:
        return None
    full_w, full_h = source.width(), source.height()
    # The card's top left in the whole picture (centred), then the box's.
    left, top = (full_w - width) // 2 + x0, (full_h - height) // 2 + y0
    image = tk.PhotoImage(master=_root_of(root), width=x1 - x0, height=y1 - y0)
    # What of the box lies inside the picture (a card larger than it keeps the panel at its edges).
    sx0, sy0 = max(0, left), max(0, top)
    sx1, sy1 = min(full_w, left + x1 - x0), min(full_h, top + y1 - y0)
    if sx1 > sx0 and sy1 > sy0:
        image.tk.call(str(image), "copy", str(source), "-from", sx0, sy0, sx1, sy1, "-to", sx0 - left, sy0 - top)
    crops[key] = image
    while len(crops) > _PLAYBACK_CROPS:
        crops.popitem(last=False)
    return image


_PLAYBACK_CROPS = 8


def _playback_picture(root, scale: float):
    """The whole shipped Shot Playback picture at ``scale`` (cached per scale), or None."""
    cache = _cache(root)
    key = ("playback_picture", round(float(scale), 4))
    if key in cache:
        return cache[key]
    assets = load(root)
    section = assets.manifest.get("playback_art") if assets is not None else None
    picture = None
    if section:
        files = section["files"]
        exact = next((name for bucket_, name in files.items() if abs(float(bucket_) - scale) < .001), None)
        if exact is not None:
            picture = assets.photo(root, exact)
        else:
            larger = sorted((float(bucket_), name) for bucket_, name in files.items() if float(bucket_) >= scale)
            name = (larger[0] if larger else max((float(bucket_), name) for bucket_, name in files.items()))[1]
            src_w, src_h, rgb = read_png_rgb_filter0(assets.path(name))
            design_w, design_h = section["size"]
            full_w, full_h = int(round(design_w * scale)), int(round(design_h * scale))
            body = stretch_rgb(rgb, src_w, src_h, full_w, full_h)
            picture = tk.PhotoImage(master=_root_of(root), data=b"P6 %d %d 255\n" % (full_w, full_h) + body,
                                    format="ppm")
    cache[key] = picture
    return picture


def dot_rgba(root, diameter: int, color) -> tk.PhotoImage:
    """An anti-aliased filled circle with a transparent surround; cached on ``root``."""
    diameter = max(1, int(diameter))
    key = ("dot", diameter, _hex(_rgb(color)))
    cache = _cache(root)
    image = cache.get(key)
    if image is not None:
        return image
    r, g, b = _rgb(color)
    centre, samples = diameter / 2, _SUPERSAMPLE * _SUPERSAMPLE
    rows = bytearray()
    for y in range(diameter):
        for x in range(diameter):
            inside = sum(1 for sy in range(_SUPERSAMPLE) for sx in range(_SUPERSAMPLE)
                         if math.hypot(x + (sx + .5) / _SUPERSAMPLE - centre,
                                       y + (sy + .5) / _SUPERSAMPLE - centre) <= centre)
            rows += bytes((r, g, b, round(255 * inside / samples)))
    image = cache[key] = photo_rgba(root, diameter, diameter, bytes(rows))
    return image


# Kit images (PLAN-5 2.4) --------------------------------------------------------
#
# Every one is cached on the root by all of its arguments and built from 4 x 4
# supersampled coverage. ``surface=None`` gives RGBA, transparent outside (for the
# backdrop); ``surface=<colour>`` the same pixels precomposed opaque over that colour
# (for card canvases, which are always ``panel``), so Tk takes its opaque image path.
# ``binary=True`` (RGBA only) rounds the alpha to 0 or 255: Tk then draws the image
# through a mask instead of blending it, for small faces over the backdrop that
# would otherwise exceed a page's per-frame alpha budget.

def _styled_strip(root, height, radius, line, fill, outline, side, spans, styles):
    radius, line = max(1, int(radius)), max(1, int(line))
    image = tk.PhotoImage(master=_root_of(root), width=radius, height=max(1, int(height)))
    top_corner, bottom_corner = ("nw", "sw") if side == "left" else ("ne", "se")
    for index, (top, bottom) in enumerate(spans):
        style = styles[index] if index < len(styles) else None
        r, paint, edge, width = (radius, fill, outline, line) if style is None else style
        r, width = max(1, min(radius, int(r))), max(0, int(width))
        top, bottom = int(top), int(bottom)
        colour, ring = _hex(_rgb(paint)), (_hex(_rgb(edge)) if edge else None)
        # The corners sit on the outer side; the fill runs on to the strip's inner side.
        corner_x = 0 if side == "left" else radius - r
        inner = (r, radius) if side == "left" else (0, radius - r)
        if inner[1] > inner[0]:
            image.put(colour, to=(inner[0], top, inner[1], bottom))
            if ring is not None and width:   # the plate's top and bottom edges run on through it
                image.put(ring, to=(inner[0], top, inner[1], min(bottom, top + width)))
                image.put(ring, to=(inner[0], max(top, bottom - width), inner[1], bottom))
        if bottom - top > 2 * r:
            edge_x = (0, width) if side == "left" else (radius - width, radius)
            fill_x = (width, r) if side == "left" else (radius - r, radius - width)
            if ring is not None and width:
                image.put(ring, to=(edge_x[0], top + r, edge_x[1], bottom - r))
            else:
                fill_x = (0, r) if side == "left" else (radius - r, radius)
            if fill_x[1] > fill_x[0]:
                image.put(colour, to=(fill_x[0], top + r, fill_x[1], bottom - r))
        for corner, y in ((top_corner, top), (bottom_corner, bottom - r)):
            image.tk.call(str(image), "copy", str(corner_rgba(root, r, paint, edge, corner)),
                          "-to", corner_x, y, "-compositingrule", "set")
    return image


def _covered(width, height, inside):
    """Per pixel, the share of its 4 x 4 samples for which ``inside(x, y)`` holds (row by row)."""
    n = _SUPERSAMPLE
    offsets = [(i + .5) / n for i in range(n)]
    total = n * n
    values = []
    for y in range(height):
        for x in range(width):
            hits = 0
            for oy in offsets:
                for ox in offsets:
                    if inside(x + ox, y + oy):
                        hits += 1
            values.append(hits / total)
    return values


def _face(root, key, width, height, coverage, color, surface, binary):
    """A cached image of ``color`` at ``coverage`` (one value per pixel)."""
    cache = _cache(root)
    image = cache.get(key)
    if image is not None:
        return image
    paint = _rgb(color)
    under = _rgb(surface) if surface is not None else None
    rows = bytearray()
    for value in coverage:
        if under is not None:
            rows += bytes((*(round(p * value + u * (1 - value)) for p, u in zip(paint, under)), 255))
        else:
            alpha = (255 if value >= .5 else 0) if binary else round(255 * value)
            rows += bytes((*paint, alpha)) if alpha else b"\0\0\0\0"
    image = cache[key] = photo_rgba(root, width, height, bytes(rows))
    return image


def _segment_distance(x, y, a, b, butt=False):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = ((x - a[0]) * dx + (y - a[1]) * dy) / length if length else 0.0
    if butt and not 0.0 <= t <= 1.0:
        return math.inf
    t = max(0.0, min(1.0, t))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def _near_polylines(polylines, half):
    segments = [(a, b) for line in polylines for a, b in zip(line, line[1:])]
    return lambda x, y: any(_segment_distance(x, y, a, b) <= half for a, b in segments)


def _key_colour(colour):
    return _hex(_rgb(colour)) if colour is not None else ""


def stroke_rgba(root, width, height, polylines, stroke, color, *, surface=None, binary=False) -> tk.PhotoImage:
    """``polylines`` (sequences of (x, y) px points) stroked ``stroke`` px wide with round caps and joins."""
    width, height = max(1, int(width)), max(1, int(height))
    lines = tuple(tuple((round(float(x), 3), round(float(y), 3)) for x, y in line) for line in polylines)
    key = ("stroke", width, height, lines, round(float(stroke), 3), _key_colour(color), _key_colour(surface), binary)
    if key in _cache(root):
        return _cache(root)[key]
    coverage = _covered(width, height, _near_polylines(lines, float(stroke) / 2))
    return _face(root, key, width, height, coverage, color, surface, binary)


def info_icon_rgba(root, scale, color, *, surface=None, binary=False) -> tk.PhotoImage:
    """The kit Info icon ([design reference]), 13 x 13 design px: a ring, a stem and a dot."""
    s = float(scale)
    size = max(1, int(13 * s + .5))
    key = ("info_icon", size, round(s, 3), _key_colour(color), _key_colour(surface), binary)
    if key in _cache(root):
        return _cache(root)[key]
    centre, ring, ring_half = 6.5 * s, 6.0 * s, .5 * s
    stem, stem_half = ((6.5 * s, 5.6 * s), (6.5 * s, 9.2 * s)), .55 * s
    dot, dot_r = (6.5 * s, 4.0 * s), .7 * s

    def inside(x, y):
        if abs(math.hypot(x - centre, y - centre) - ring) <= ring_half:
            return True
        if _segment_distance(x, y, *stem, butt=True) <= stem_half:
            return True
        return math.hypot(x - dot[0], y - dot[1]) <= dot_r
    return _face(root, key, size, size, _covered(size, size, inside), color, surface, binary)


def chevron_rgba(root, scale, color, opened, *, surface=None, binary=False) -> tk.PhotoImage:
    """The disclosure chevron in an 8 x 8 design box: closed points right, open points down; stroke 1.4."""
    s = float(scale)
    size = max(1, int(8 * s + .5))
    points = ((1, 2.75), (4, 5.75), (7, 2.75)) if opened else ((2.75, 1), (5.75, 4), (2.75, 7))
    line = tuple((x * s, y * s) for x, y in points)
    return stroke_rgba(root, size, size, (line,), 1.4 * s, color, surface=surface, binary=binary)


def dropdown_chevron_rgba(root, scale, color, *, surface=None) -> tk.PhotoImage:
    """The dropdown chevron ([design reference]): ``M0 0 L4 4 L8 0``, stroke 1.4, in a 10 x 6 design box (1 px around)."""
    s = float(scale)
    width, height = max(1, int(10 * s + .5)), max(1, int(6 * s + .5))
    line = ((1 * s, 1 * s), (5 * s, 5 * s), (9 * s, 1 * s))
    return stroke_rgba(root, width, height, (line,), 1.4 * s, color, surface=surface)


def magnifier_rgba(root, scale, color, *, surface=None, binary=False) -> tk.PhotoImage:
    """The search magnifier, 8 x 8 design px: a circle (r 3.33) and its handle to (8, 8), stroke 1."""
    s = float(scale)
    size = max(1, int(9 * s + .5))
    key = ("magnifier", size, round(s, 3), _key_colour(color), _key_colour(surface), binary)
    if key in _cache(root):
        return _cache(root)[key]
    centre, r, half = (.5 + 3.33) * s, 3.33 * s, .5 * s
    handle = ((centre + r * .7071, centre + r * .7071), (8 * s, 8 * s))

    def inside(x, y):
        return abs(math.hypot(x - centre, y - centre) - r) <= half or _segment_distance(x, y, *handle) <= half
    return _face(root, key, size, size, _covered(size, size, inside), color, surface, binary)


def corner_face(root, radius: int, fill, which: str, *, outline=None, surface=None, binary=False) -> tk.PhotoImage:
    """A ``radius`` px corner like :func:`corner_rgba`, opaque over ``surface`` or with binary alpha.

    Without either it is :func:`corner_rgba` itself. For canvas-drawn controls: on a
    card canvas the corners are precomposed over ``panel``; over the backdrop a face
    that ramps may use binary alpha (PLAN-5 2.4).
    """
    if surface is None and not binary:
        return corner_rgba(root, radius, fill, outline, which)
    radius = max(1, int(radius))
    key = ("corner_face", radius, _key_colour(fill), _key_colour(outline), which, _key_colour(surface), binary)
    cache = _cache(root)
    image = cache.get(key)
    if image is not None:
        return image
    if which not in ("nw", "ne", "sw", "se"):
        raise ValueError(f"unknown corner {which!r}")
    paint, ring_paint = _rgb(fill), _rgb(outline) if outline else None
    under = _rgb(surface) if surface is not None else None
    samples = _SUPERSAMPLE * _SUPERSAMPLE
    rows = []
    for y in range(radius):
        row = bytearray()
        for x in range(radius):
            inner = ring = 0
            for sy in range(_SUPERSAMPLE):
                py = y + (sy + .5) / _SUPERSAMPLE
                for sx in range(_SUPERSAMPLE):
                    distance = math.hypot(radius - (x + (sx + .5) / _SUPERSAMPLE), radius - py)
                    if distance > radius:
                        continue
                    if ring_paint is not None and distance > radius - 1:
                        ring += 1
                    else:
                        inner += 1
            covered = inner + ring
            colour = ([(paint[i] * inner + (ring_paint[i] * ring if ring else 0)) / covered for i in range(3)]
                      if covered else list(paint))
            share = covered / samples
            if under is not None:
                row += bytes((*(round(c * share + u * (1 - share)) for c, u in zip(colour, under)), 255))
            elif share >= .5:
                row += bytes((*(round(c) for c in colour), 255))
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    if which in ("ne", "se"):
        rows = [b"".join(row[i:i + 4] for i in range(len(row) - 4, -1, -4)) for row in rows]
    if which in ("sw", "se"):
        rows.reverse()
    image = cache[key] = photo_rgba(root, radius, radius, b"".join(rows))
    return image


# Helpers ------------------------------------------------------------------

def _root_of(widget):
    root = widget
    while getattr(root, "master", None) is not None:
        root = root.master
    return root


def _cache(root) -> dict:
    root = _root_of(root)
    cache = getattr(root, "_dolly_art_cache", None)
    if cache is None:
        cache = root._dolly_art_cache = {}
    return cache


def _rgb(colour):
    """``"#rrggbb"`` or a token name -> ``(r, g, b)``."""
    if isinstance(colour, (tuple, list)):
        return tuple(int(value) for value in colour[:3])
    colour = str(colour)
    return ui_theme.rgb(colour if colour.startswith("#") else ui_theme.TOKENS[colour])


def _hex(values):
    return "#" + "".join(f"{max(0, min(255, round(value))):02x}" for value in values)


def _sample(stops, t):
    if t <= stops[0][0]:
        return stops[0][1], stops[0][2]
    for (t0, c0, a0), (t1, c1, a1) in zip(stops, stops[1:]):
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return [a + (b - a) * k for a, b in zip(c0, c1)], a0 + (a1 - a0) * k
    return stops[-1][1], stops[-1][2]
