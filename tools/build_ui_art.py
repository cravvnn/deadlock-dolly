"""Build the desktop UI art in ``assets/ui`` from Kara's Figma exports.

Source: Figma file ``design source``, frame "Dolly / Replay" ([design reference]), exported
into a folder with ``assets/layers``, ``assets/plates`` and ``assets/icons``. Building needs
numpy (tooling Python only; Dolly never imports this module)::

    python -I -B tools/build_ui_art.py --source <exports>     # write assets/ui, then verify it
    python -I -B tools/build_ui_art.py --verify               # stdlib only: re-hash and check

Outputs (the contract with :mod:`dolly.ui.art`, described in ``assets/ui/manifest.json``):

* ``backdrop_l0..4.png``: the still screen picture (gradient, photo, ink wash, sigil and the
  halftone rim) at the design size, cut into five horizontal bands of 162 rows that Dolly
  stacks again. It holds still: a picture that changes repaints the whole window. The dark
  shard is not baked in: :mod:`dolly.ui.backdrop` draws it as a canvas polygon, in eight
  hand-cut poses, so its small corner of the screen is all that moves.
* ``sidebar_art@{1,1.5,2}x.png``: the masked statue art (alpha only, a few levels deep).
* ``nav_icons@{1,1.5,2}x.png``: the six nav icons, one row per text colour token.
* ``playback_art@{1,1.5,2}x.png``: the Shot Playback card's background (component [design reference]):
  the card colour, Figma's halftone tile and its faint magic-circle drawing, composited
  opaque from ``<source>/playback-art`` and checked against Figma's own 2x render of the card.

Every PNG is 8-bit, one IDAT, every row filter 0, so Dolly reads the picture bands without
a PNG decoder. ``tests/test_ui_assets.py`` runs :func:`verify`, so a hand-edited file, a stale
manifest or icons left behind by a palette change fail the suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dolly import ui_theme  # noqa: E402  (path setup precedes the import)

OUT = ROOT / "assets" / "ui"
GENERATOR = "tools/build_ui_art.py"
VERSION = 1

SCREEN = (1010, 810)                  # Screen / Replay, design px
SIDEBAR = (190, 810)
# Figma linear gradients (gradientTransform first row, stops) of the BG frame (1040x949 box),
# of the sidebar (190x810) and of the sidebar's 1 px right edge stroke. Figma design data, not
# Dolly colours.
BG_BOX = (1040, 949)
BG_GRADIENT = ((0.0, 0.42, 0.58), ((0.0, "#2C477D"), (0.78, "#070809")))
SIDEBAR_GRADIENT = ((0.0, 0.42, 0.58), ((0.0, "#2C477D"), (0.81, "#070809")))
SIDEBAR_EDGE = ((-0.1509, 1.1056, 0.3389), ((0.0, "#373737"), (1.0, "#060709")))

# The still picture, cut into equal horizontal bands (Dolly stacks them again).
BANDS = 5
BAND_SIZE = (SCREEN[0], SCREEN[1] // BANDS)
SIGIL_AT = (369, 216)
RIM_AT = (311, 83)                    # Vector 6 (halftone rim), baked into the picture
SHARD_AT = (381, 101)                 # Vector 7 (dark shard), drawn by Dolly
# Vector 7's node polygon (node-local design px). Vector 6 is the same outline stretched to
# 1310 x 908 (Figma, 2026-10-24); its render comes from Figma, so only the shard is listed.
SHAPE = ((0.0, 783.0), (319.0, 671.0), (319.0, 589.5), (642.5, 640.5), (794.0, 0.0), (975.0, 907.5))
POSES = 8
POSE_SEED = 4242

SIDEBAR_ART_RECT = (0, 260, 190, 550)
BUCKETS = ("1", "1.5", "2")
NAV_ORDER = ("replay", "camera", "look", "export", "controls", "settings")
NAV_ROWS = ("muted", "text")
NAV_CELL = 16
NAV_STROKE = 1.4
NAV_SUPERSAMPLE = 4
CURVE_STEPS = 16

# The Shot Playback card (component [design reference]; the Look instance [design reference] is the same). Its
# fills, bottom to top (Figma design data): SOLID rgb(15, 16, 18) (the panel token); IMAGE
# f7bc20e8c7 (820 x 820, black halftone dots) TILE at scalingFactor .5 from the card's top
# left, opacity .10; IMAGE d60530ded9 (1024 x 512, a magic-circle drawing) FILL (cover,
# centred), opacity .05, blend COLOR_DODGE, filters contrast -1, highlights -.42, shadows -.88;
# the same drawing again FILL, opacity .015, blend HARD_LIGHT, filters exposure -.43, contrast -.5,
# saturation -1, highlights .11, shadows -.76 (added 2026-10-27; it lifts the drawing's lines).
# Sources: <source>/playback-art (raw_1 = the tile, raw_3 = the drawing, export2x = Figma's
# 2x render of the whole card with its children).
PLAYBACK_CARD = (930, 178)            # the design card, Playback options closed
PLAYBACK_MARGIN = 60                  # shipped rows above and below it: a taller card (options open) crops more
PLAYBACK_SIZE = (PLAYBACK_CARD[0], PLAYBACK_CARD[1] + 2 * PLAYBACK_MARGIN)
PLAYBACK_TILE = ("raw_1.png", 410, .10)          # file, tile size in design px (820 x .5), opacity
PLAYBACK_DRAWING = ("raw_3.png", .05)            # file, opacity (colour dodge)
# Figma's contrast/highlights/shadows on the drawing, as one tone curve from the drawing's grey
# to the colour-dodge source (monotone, piecewise linear), fitted to export2x.png outside the
# card's children: the shadows are crushed to 0, the contrast cut flattens the mid tones. Both
# drawing fills read the same grey, so the curve also carries the faint hard-light copy.
PLAYBACK_CURVE = ((0.0, 0.0), (0.25, 0.0), (0.32, 0.003), (0.40, 0.383), (0.48, 0.549), (0.60, 0.558),
                  (0.75, 0.558), (0.88, 0.649), (1.0, 0.857))
# The card's children in export2x (design px): the title (18 px, its descenders below the line)
# and the rows frame; the check runs outside them.
PLAYBACK_CHILDREN = ((21, 21, 113, 24), (21, 54, 888, 103))
PLAYBACK_LIMITS = (1.0, 4)            # mean and max abs difference allowed against export2x (2x, 0..255)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


# --------------------------------------------------------------------------
# PNG (stdlib).


def _chunk(tag: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def png_bytes(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """8-bit PNG, one IDAT, every row filter 0, zlib level 9."""
    color_type = {3: 2, 4: 6}[channels]
    stride = width * channels
    if len(pixels) != stride * height:
        raise ValueError("pixel data does not match the image size")
    raw = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (PNG_SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """``[(tag, body), ...]``; raises ValueError on a bad signature, length or CRC."""
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("not a PNG file")
    chunks, pos = [], 8
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("truncated chunk")
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise ValueError("truncated chunk")
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        if crc != zlib.crc32(tag + body) & 0xFFFFFFFF:
            raise ValueError(f"bad CRC in {tag!r}")
        chunks.append((tag, body))
        pos += 12 + length
        if tag == b"IEND":
            break
    return chunks


def png_header(data: bytes) -> tuple[int, int, int, int, int]:
    """(width, height, bit depth, colour type, interlace) from the IHDR chunk."""
    chunks = png_chunks(data)
    if not chunks or chunks[0][0] != b"IHDR":
        raise ValueError("IHDR is not the first chunk")
    width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    return width, height, depth, color_type, interlace


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytes:
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 255
        elif kind == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif kind == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
        elif kind == 4:
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = prev[x]
                c = prev[x - channels] if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        elif kind != 0:
            raise ValueError(f"unknown PNG filter {kind}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return bytes(out)


def read_png(path: Path) -> tuple[int, int, int, bytes]:
    """(width, height, channels, pixels) of an 8-bit non-interlaced, non-palette PNG."""
    data = Path(path).read_bytes()
    width, height, depth, color_type, interlace = png_header(data)
    if depth != 8 or interlace != 0 or color_type not in (0, 2, 4, 6):
        raise ValueError(f"{path}: unsupported PNG format")
    channels = CHANNELS[color_type]
    raw = zlib.decompress(b"".join(body for tag, body in png_chunks(data) if tag == b"IDAT"))
    if len(raw) != height * (1 + width * channels):
        raise ValueError(f"{path}: image data has the wrong size")
    return width, height, channels, _unfilter(raw, width, height, channels)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Shapes (pure Python: the poses are part of the contract and verified without numpy).


def _half(value: float) -> float:
    return round(value * 2.0) / 2.0


def design_pose() -> dict:
    """Pose 0: the Figma shard itself, in screen design px."""
    return {"shard": [[x + SHARD_AT[0], y + SHARD_AT[1]] for x, y in SHAPE]}


def shape_poses() -> list[dict]:
    """Eight hand-cut poses: the design, then seven redrawn by a seeded hand.

    Only what can be seen moves: the lower-left edge, the notch and the diagonal to the right
    edge of the screen (the spike and the bottom edge lie beyond it at every window size). The
    notch (vertices 1 and 2) slides sideways together, the corners move up to 4 px, the
    diagonal's far end (vertex 3) moves up or down, and each long visible edge gets one kink,
    so the cut looks redrawn by hand rather than shifted.
    """
    rng = random.Random(POSE_SEED)
    poses = [design_pose()]
    for _ in range(POSES - 1):
        points = [[x + SHARD_AT[0], y + SHARD_AT[1]] for x, y in SHAPE]
        notch = rng.uniform(-10, 10)
        points[1][0] += notch
        points[2][0] += notch
        points[3][1] += rng.uniform(-8, 8)
        for point in points[:4]:
            point[0] += rng.uniform(-4, 4)
            point[1] += rng.uniform(-4, 4)
        shard = []
        for index, (x, y) in enumerate(points):
            shard.append([_half(x), _half(y)])
            if index in (0, 2):     # the kink on the lower-left edge, then the one on the diagonal
                (nx, ny) = points[index + 1]
                t = rng.uniform(.3, .7)
                length = ((nx - x) ** 2 + (ny - y) ** 2) ** .5
                bend = rng.uniform(-6, 6) / length
                shard.append([_half(x + (nx - x) * t - (ny - y) * bend), _half(y + (ny - y) * t + (nx - x) * bend)])
        poses.append({"shard": shard})
    return poses


# --------------------------------------------------------------------------
# Compositing (numpy, imported lazily: --verify must run without it).


def _np():
    import numpy
    return numpy


def _array(path: Path):
    np = _np()
    width, height, channels, pixels = read_png(path)
    array = np.frombuffer(pixels, np.uint8).reshape(height, width, channels)
    if channels == 4:
        return array
    if channels == 3:
        return np.dstack([array, np.full((height, width), 255, np.uint8)])
    gray = array[..., 0]
    alpha = array[..., 1] if channels == 2 else np.full((height, width), 255, np.uint8)
    return np.dstack([gray, gray, gray, alpha])


def _hex_rgb(color: str):
    np = _np()
    return np.array(ui_theme.rgb(color.lower()), np.float64) / 255.0


def linear_gradient(width: int, height: int, row, stops):
    """Figma GRADIENT_LINEAR over a width x height box: t = row . (x/w, y/h, 1), opaque stops."""
    np = _np()
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    t = row[0] * (xx + 0.5) / width + row[1] * (yy + 0.5) / height + row[2]
    positions = np.array([position for position, _color in stops])
    colors = np.array([_hex_rgb(color) for _position, color in stops])
    out = np.empty((height, width, 3))
    for channel in range(3):
        out[..., channel] = np.interp(t, positions, colors[:, channel])
    return out


def over(dst, src, x: int, y: int, blend: str = "NORMAL", opacity: float = 1.0) -> None:
    """Composite a straight-alpha uint8 RGBA layer onto an opaque float RGB canvas in place."""
    np = _np()
    sh, sw = src.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + sw, dst.shape[1]), min(y + sh, dst.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    region = dst[y0:y1, x0:x1]
    layer = src[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float64) / 255.0
    color, alpha = layer[..., :3], layer[..., 3:4] * opacity
    if blend == "OVERLAY":
        mixed = np.where(region <= 0.5, 2 * region * color, 1 - 2 * (1 - region) * (1 - color))
    elif blend == "NORMAL":
        mixed = color
    else:
        raise ValueError(f"unsupported blend mode {blend}")
    region[...] = region * (1 - alpha) + mixed * alpha


def _quantize(image):
    np = _np()
    return np.clip(image * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _box_down(image, factor: int = 2):
    height, width = image.shape[:2]
    return image.reshape(height // factor, factor, width // factor, factor, -1).mean(axis=(1, 3))


def polygon_mask(points, width: int, height: int):
    """Even-odd inside test at pixel centres."""
    np = _np()
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64) + 0.5
    inside = np.zeros((height, width), bool)
    count = len(points)
    for i in range(count):
        (xa, ya), (xb, yb) = points[i], points[i - 1]
        if ya == yb:
            continue
        crosses = (ya > yy) != (yb > yy)
        at = (xb - xa) * (yy - ya) / (yb - ya) + xa
        inside ^= crosses & (xx < at)
    return inside


def _compare(mine, reference) -> tuple[float, float]:
    np = _np()
    diff = np.abs(mine.astype(np.float64) - reference.astype(np.float64))
    return float(diff.mean()), float(np.percentile(diff, 99))


def build_picture(source: Path) -> list[bytes]:
    """The still picture in its five bands (1010x162 RGB each), after checking it, with the
    shard, against Figma's own render of the whole background."""
    np = _np()
    layers = source / "assets" / "layers"
    width, height = SCREEN
    image = linear_gradient(BG_BOX[0], BG_BOX[1], BG_GRADIENT[0], BG_GRADIENT[1])[:height, :width].copy()
    over(image, _array(layers / "bg_mvp@1x.png"), 0, 0, "NORMAL")      # the photo's 0.75 is baked into its alpha
    over(image, _array(layers / "bg_inkwash@1x.png"), 0, 0, "OVERLAY")
    over(image, _array(layers / "bg_sigil@1x.png"), SIGIL_AT[0], SIGIL_AT[1], "NORMAL")
    over(image, _array(layers / "bg_vector6_halftone@1x.png"), RIM_AT[0], RIM_AT[1], "NORMAL")
    picture = _quantize(image)
    reference = _box_down(_array(source / "assets" / "plates" / "screen_bg@2x.png")[..., :3].astype(np.float64))
    check = image.copy()
    over(check, _array(layers / "bg_vector7_shard@1x.png"), SHARD_AT[0], SHARD_AT[1])
    mean, p99 = _compare(_quantize(check), reference)
    print(f"backdrop picture + Figma shard vs plate: mean {mean:.2f}/255, p99 {p99:.1f}")
    if mean > 1.0:
        raise SystemExit(f"backdrop picture does not match Figma's plate (mean {mean:.2f}/255 > 1.0)")
    shard_fill = ui_theme.TOKENS.get("backdrop_shard")
    if shard_fill:
        runtime = picture.astype(np.float64) / 255.0
        runtime[polygon_mask(design_pose()["shard"], width, height)] = _hex_rgb(shard_fill)
        mean, p99 = _compare(_quantize(runtime), reference)
        print(f"  (info) runtime look, picture + design pose in {shard_fill} vs plate: "
              f"mean {mean:.2f}/255, p99 {p99:.1f}")
    else:
        print("  (info) the backdrop_shard token is not defined yet; runtime-look check skipped")
    rows = BAND_SIZE[1]
    return [picture[band * rows:(band + 1) * rows].tobytes() for band in range(BANDS)]


def _upsample(rgba, scale: float):
    """Bilinear (premultiplied) enlargement of an RGBA uint8 image."""
    np = _np()
    height, width = rgba.shape[:2]
    th, tw = int(round(height * scale)), int(round(width * scale))
    alpha = rgba[..., 3:4].astype(np.float64) / 255.0
    pre = np.concatenate([rgba[..., :3].astype(np.float64) / 255.0 * alpha, alpha], axis=2)
    fx = np.clip((np.arange(tw) + 0.5) / scale - 0.5, 0, width - 1)
    fy = np.clip((np.arange(th) + 0.5) / scale - 0.5, 0, height - 1)
    i0 = fx.astype(np.int64)
    i1 = np.minimum(i0 + 1, width - 1)
    j0 = fy.astype(np.int64)
    j1 = np.minimum(j0 + 1, height - 1)
    wx = (fx - i0)[None, :, None]
    wy = (fy - j0)[:, None, None]
    top = pre[j0][:, i0] * (1 - wx) + pre[j0][:, i1] * wx
    bottom = pre[j1][:, i0] * (1 - wx) + pre[j1][:, i1] * wx
    mixed = top * (1 - wy) + bottom * wy
    out_alpha = mixed[..., 3:4]
    color = np.where(out_alpha > 0, mixed[..., :3] / np.maximum(out_alpha, 1e-12), 0.0)
    return _quantize(np.concatenate([np.clip(color, 0, 1), out_alpha], axis=2))


def _sidebar_with_art(art, scale: int):
    """The sidebar at ``scale`` as Figma draws it: gradient, the art, then the edge stroke on top."""
    width, height = SIDEBAR[0] * scale, SIDEBAR[1] * scale
    image = linear_gradient(width, height, SIDEBAR_GRADIENT[0], SIDEBAR_GRADIENT[1])
    over(image, art, SIDEBAR_ART_RECT[0] * scale, SIDEBAR_ART_RECT[1] * scale, "NORMAL")
    edge = linear_gradient(width, height, SIDEBAR_EDGE[0], SIDEBAR_EDGE[1])
    image[:, -scale:] = edge[:, -scale:]
    return image


def _sidebar_art_highres(source: Path, art):
    """Restore source detail while retaining the approved alpha mask and placement."""
    np = _np()
    raw = _array(source / "assets" / "raw" / "frame25_statue_art_ac534adb.png")
    if raw.shape[:2] != (2726, 885):
        raise ValueError("Unexpected sidebar source dimensions")
    out = _upsample(art, 4.0)
    # Visible source is a 245x670 art frame at x=-32, y=355, clipped by
    # the 190x550 sidebar art area beginning at y=260. The source image
    # uses a vertical crop of 0.888, offset 0.008.
    x = np.clip(((np.arange(760) + .5) / 4 + 32) * 885 / 245 - .5, 0, 884)
    y = np.clip(((np.arange(1820) + .5) / 4) * 2726 * .888 / 670 + .008 * 2726 - .5, 0, 2725)
    x0=x.astype(np.int64); x1=np.minimum(x0+1,884); wx=(x-x0)[None,:,None]
    y0=y.astype(np.int64); y1=np.minimum(y0+1,2725); wy=(y-y0)[:,None,None]
    top=raw[y0][:,x0,:3]*(1-wx)+raw[y0][:,x1,:3]*wx
    bot=raw[y1][:,x0,:3]*(1-wx)+raw[y1][:,x1,:3]*wx
    out[380:2200,:,:3]=np.clip(np.rint(top*(1-wy)+bot*wy),0,255).astype(np.uint8)
    return out


def build_sidebar_art(source: Path) -> dict[str, tuple[int, int, bytes]]:
    np = _np()
    art = _array(source / "assets" / "layers" / "sidebar_art@1x.png")
    x, y, width, height = SIDEBAR_ART_RECT
    if art.shape[:2] != (height, width):
        raise SystemExit(f"sidebar_art@1x.png is {art.shape[1]}x{art.shape[0]}, expected {width}x{height}")
    plate = _array(source / "assets" / "plates" / "sidebar_art@2x.png")[..., :3].astype(np.float64)
    mine = _sidebar_with_art(art, 1)[y:y + height, x:x + width]
    mean, p99 = _compare(_quantize(mine), _box_down(plate))
    print(f"sidebar art over the sidebar gradient vs plate: mean {mean:.2f}/255, p99 {p99:.1f}")
    if mean > 1.0:
        raise SystemExit(f"sidebar art does not match Figma's plate (mean {mean:.2f}/255 > 1.0)")
    out = {"1": (width, height, art.tobytes())}
    for bucket in BUCKETS[1:]:
        big = _upsample(art, float(bucket))
        out[bucket] = (big.shape[1], big.shape[0], big.tobytes())
        if bucket == "2":
            mine = _sidebar_with_art(big, 2)[2 * y:2 * (y + height), 2 * x:2 * (x + width)]
            mean, p99 = _compare(_quantize(mine), plate)
            print(f"  (info) 2x art (enlarged) over the 2x gradient vs plate: mean {mean:.2f}/255, p99 {p99:.1f}")
    hires = _sidebar_art_highres(source, art)
    out["4"] = (hires.shape[1], hires.shape[0], hires.tobytes())
    return out


# --------------------------------------------------------------------------
# Nav icons: the Figma SVG strokes rasterised with round caps and joins.

_PATH = re.compile(r"<path\b([^>]*)/?>")
_ATTRIBUTE = re.compile(r'([a-zA-Z_:-]+)="([^"]*)"')
_TOKEN = re.compile(r"[MLCZmlcz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def svg_polylines(text: str) -> list[list[tuple[float, float]]]:
    """Polylines of an icon SVG (subset: absolute M, L, C, Z; ``translate(x y)``)."""
    lines = []
    for match in _PATH.finditer(text):
        attributes = dict(_ATTRIBUTE.findall(match.group(1)))
        tx = ty = 0.0
        transform = attributes.get("transform", "").strip()
        if transform:
            moved = re.fullmatch(r"translate\(\s*([-\d.eE]+)[\s,]+([-\d.eE]+)\s*\)", transform)
            if not moved:
                raise ValueError(f"unsupported transform {transform!r}")
            tx, ty = float(moved.group(1)), float(moved.group(2))
        tokens = _TOKEN.findall(attributes.get("d", ""))
        pos, command, current, start = 0, None, None, None

        def number():
            nonlocal pos
            value = float(tokens[pos])
            pos += 1
            return value

        while pos < len(tokens):
            if tokens[pos].isalpha():
                command = tokens[pos]
                pos += 1
                if command in "Zz":
                    if current and start and len(current) > 1:
                        current.append(start)
                    command = None
                    continue
            if command is None:
                raise ValueError("path data without a command")
            if command == "M":
                start = (number() + tx, number() + ty)
                current = [start]
                lines.append(current)
                command = "L"                     # implicit lineto after a moveto
            elif command == "L":
                current.append((number() + tx, number() + ty))
            elif command == "C":
                (x0, y0) = current[-1]
                x1, y1, x2, y2 = number() + tx, number() + ty, number() + tx, number() + ty
                x3, y3 = number() + tx, number() + ty
                for step in range(1, CURVE_STEPS + 1):
                    t = step / CURVE_STEPS
                    m = 1 - t
                    current.append((m ** 3 * x0 + 3 * m * m * t * x1 + 3 * m * t * t * x2 + t ** 3 * x3,
                                    m ** 3 * y0 + 3 * m * m * t * y1 + 3 * m * t * t * y2 + t ** 3 * y3))
            else:
                raise ValueError(f"unsupported path command {command!r}")
    return lines


def stroke_coverage(lines, scale: float, size: int, pad: int = 0):
    """Coverage (0..1) of the stroked polylines on a (size + 2*pad)^2 pixel grid."""
    np = _np()
    segments = [(a, b) for line in lines for a, b in zip(line, line[1:])]
    segments += [(line[0], line[0]) for line in lines if len(line) == 1]
    a = np.array([s[0] for s in segments], np.float64) * scale
    b = np.array([s[1] for s in segments], np.float64) * scale
    n = NAV_SUPERSAMPLE
    count = size + 2 * pad
    offsets = (np.arange(count * n) + 0.5) / n - pad
    py, px_ = np.meshgrid(offsets, offsets, indexing="ij")
    points = np.stack([px_.ravel(), py.ravel()], axis=1)
    best = np.full(len(points), np.inf)
    ab = b - a
    length2 = (ab * ab).sum(axis=1)
    for start in range(0, len(segments), 32):
        sa, sab, sl = a[start:start + 32], ab[start:start + 32], length2[start:start + 32]
        rel = points[:, None, :] - sa[None, :, :]
        t = np.where(sl > 0, (rel * sab[None]).sum(axis=2) / np.where(sl > 0, sl, 1), 0.0)
        t = np.clip(t, 0.0, 1.0)
        nearest = rel - t[..., None] * sab[None]
        best = np.minimum(best, np.sqrt((nearest * nearest).sum(axis=2)).min(axis=1))
    sample = np.clip(NAV_STROKE / 2 * scale - best + 0.5, 0.0, 1.0)
    return sample.reshape(count, n, count, n).mean(axis=(1, 3))


def build_nav_icons(source: Path) -> dict[str, tuple[int, int, bytes]]:
    np = _np()
    icons = {name: svg_polylines((source / "assets" / "icons" / f"nav_{name}_default.svg").read_text(encoding="utf-8"))
             for name in NAV_ORDER}
    colors = [np.array(ui_theme.rgb(ui_theme.TOKENS[token]), np.uint8) for token in NAV_ROWS]
    out = {}
    for bucket in BUCKETS:
        scale = float(bucket)
        cell = int(round(NAV_CELL * scale))
        sheet = np.zeros((cell * len(NAV_ROWS), cell * len(NAV_ORDER), 4), np.uint8)
        for column, name in enumerate(NAV_ORDER):
            pad = 2
            coverage = stroke_coverage(icons[name], scale, cell, pad)
            inner = coverage[pad:pad + cell, pad:pad + cell]
            if inner.max() <= 0:
                raise SystemExit(f"nav icon {name} has no coverage at {bucket}x")
            if coverage.sum() - inner.sum() > 1e-9:
                raise SystemExit(f"nav icon {name} does not fit its {cell} px cell at {bucket}x")
            alpha = np.clip(inner * 255.0 + 0.5, 0, 255).astype(np.uint8)
            for row, color in enumerate(colors):
                block = sheet[row * cell:(row + 1) * cell, column * cell:(column + 1) * cell]
                block[..., :3] = np.where(alpha[..., None] > 0, color, 0)
                block[..., 3] = alpha
        out[bucket] = (sheet.shape[1], sheet.shape[0], sheet.tobytes())
    return out


# --------------------------------------------------------------------------
# Shot Playback card background.


def _area_resize(plane, width: int, height: int):
    """Area-average resampling of a 2-D float array to ``width`` x ``height`` (identity at the same size)."""
    np = _np()

    def weights(source, target):
        edges = np.arange(target + 1) * source / target
        matrix = np.zeros((target, source))
        for index in range(target):
            start, end = edges[index], edges[index + 1]
            for column in range(int(start), min(source, int(np.ceil(end)))):
                matrix[index, column] = min(end, column + 1) - max(start, column)
        return matrix / matrix.sum(axis=1, keepdims=True)
    rows, columns = plane.shape
    if (rows, columns) == (height, width):
        return plane.copy()
    return weights(rows, height) @ plane @ weights(columns, width).T


def _bilinear(image, fx, fy):
    """``image`` (h x w x c) sampled at columns ``fx`` and rows ``fy`` (source px, edges clamped)."""
    np = _np()
    height, width = image.shape[:2]
    fx, fy = np.clip(fx, 0, width - 1), np.clip(fy, 0, height - 1)
    x0, y0 = fx.astype(np.int64), fy.astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (fx - x0)[None, :, None], (fy - y0)[:, None, None]
    top = image[y0][:, x0] * (1 - wx) + image[y0][:, x1] * wx
    bottom = image[y1][:, x0] * (1 - wx) + image[y1][:, x1] * wx
    return top * (1 - wy) + bottom * wy


def playback_layers(source: Path, scale: float):
    """(base, drawing) of the shipped Shot Playback background at ``scale``, floats 0..1.

    ``base`` is the card colour under the tiled dots (RGB), ``drawing`` the drawing's grey
    (RGB) cover-scaled to the design card and centred on it, both ``PLAYBACK_SIZE`` x scale.
    The design card is the middle ``PLAYBACK_CARD[1]`` rows: the tile starts at its top left.
    """
    np = _np()
    folder = source / "playback-art"
    width, height = (int(round(v * scale)) for v in PLAYBACK_SIZE)
    card_top = int(round(PLAYBACK_MARGIN * scale))
    name, tile_design, _opacity = PLAYBACK_TILE
    tile = _area_resize(_array(folder / name)[..., 3].astype(np.float64) / 255.0,
                        int(round(tile_design * scale)), int(round(tile_design * scale)))
    rows = (np.arange(height) - card_top) % tile.shape[0]
    columns = np.arange(width) % tile.shape[1]
    dots = tile[rows][:, columns]
    base = _hex_rgb(ui_theme.TOKENS["panel"])[None, None, :] * (1.0 - PLAYBACK_TILE[2] * dots[..., None])
    art = _array(folder / PLAYBACK_DRAWING[0])[..., :3].astype(np.float64) / 255.0
    card_w, card_h = PLAYBACK_CARD[0] * scale, PLAYBACK_CARD[1] * scale
    cover = max(card_w / art.shape[1], card_h / art.shape[0])
    left = (card_w - art.shape[1] * cover) / 2
    top = card_top + (card_h - art.shape[0] * cover) / 2
    drawing = _bilinear(art, (np.arange(width) + .5 - left) / cover - .5, (np.arange(height) + .5 - top) / cover - .5)
    return base, drawing


def playback_composite(base, drawing):
    """Figma's stack: the drawing through the fitted tone curve, colour-dodged at its opacity over ``base``."""
    np = _np()
    knots = np.array(PLAYBACK_CURVE)
    source = np.interp(drawing, knots[:, 0], knots[:, 1])
    dodge = np.minimum(1.0, base / np.maximum(1e-9, 1.0 - source))
    opacity = PLAYBACK_DRAWING[1]
    return base * (1.0 - opacity) + dodge * opacity


def _playback_mask(width: int, height: int, scale: int, children: bool):
    """Pixels of the card inside its 1 px edge and rounded corners (and, with ``children``, outside them)."""
    np = _np()
    mask = np.zeros((height, width), bool)
    line, corner = scale, 7 * scale        # the inside stroke, and a margin around the radius-6 corners
    mask[line:height - line, line:width - line] = True
    for y in (0, height - corner):
        for x in (0, width - corner):
            mask[y:y + corner, x:x + corner] = False
    if children:
        for x, y, w, h in PLAYBACK_CHILDREN:
            mask[y * scale:(y + h) * scale, x * scale:(x + w) * scale] = False
    return mask


def build_playback_art(source: Path) -> dict[str, tuple[int, int, bytes]]:
    np = _np()
    reference = _array(source / "playback-art" / "export2x.png")[..., :3].astype(np.float64)
    out = {}
    for bucket in BUCKETS:
        scale = float(bucket)
        image = _quantize(playback_composite(*playback_layers(source, scale)))
        out[bucket] = (image.shape[1], image.shape[0], image.tobytes())
        if bucket == "2":
            top = 2 * PLAYBACK_MARGIN
            card = image[top:top + 2 * PLAYBACK_CARD[1]].astype(np.float64)
            if card.shape != reference.shape:
                raise SystemExit(f"export2x.png is {reference.shape[1]}x{reference.shape[0]}, "
                                 f"expected {card.shape[1]}x{card.shape[0]}")
            diff = np.abs(card - reference)
            outside = diff[_playback_mask(card.shape[1], card.shape[0], 2, True)]
            mean, peak = float(outside.mean()), float(outside.max())
            print(f"playback art vs Figma's 2x card outside its children: mean {mean:.3f}/255, max {peak:.0f}")
            if mean > PLAYBACK_LIMITS[0] or peak > PLAYBACK_LIMITS[1]:
                raise SystemExit(f"playback art does not match export2x.png (mean {mean:.2f}, max {peak:.0f})")
            inside = diff[_playback_mask(card.shape[1], card.shape[0], 2, False)]
            print(f"  (info) whole card inside its edge, children included: mean {float(inside.mean()):.2f}/255")
    return out


def _playback_section() -> dict:
    return {"card": list(PLAYBACK_CARD), "size": list(PLAYBACK_SIZE), "margin": PLAYBACK_MARGIN,
            "surface": ui_theme.TOKENS["panel"].lower(),
            "files": {bucket: f"playback_art@{bucket}x.png" for bucket in BUCKETS}}


# --------------------------------------------------------------------------


def _levels_section() -> dict:
    """The picture: ``files`` are its bands, top to bottom (``stacked``), each ``size`` at
    ``scale`` picture px per design px. ``cycle`` lists the one picture shown: it holds still."""
    return {"files": [f"backdrop_l{i}.png" for i in range(BANDS)], "size": list(BAND_SIZE), "scale": 1.0,
            "stacked": True, "cycle": [0]}


def _manifest_text(manifest: dict) -> str:
    text = json.dumps(manifest, indent=1)
    # Lists of plain values (points, sizes, file names) on one line keep the poses readable.
    return re.sub(r"\[\s+([^\[\]{}]*?)\s+\]",
                  lambda match: "[" + ", ".join(re.split(r",\s+", match.group(1))) + "]", text) + "\n"


def build(source: Path, out: Path = OUT) -> int:
    source, out = Path(source), Path(out)
    images: dict[str, tuple[int, int, int, bytes]] = {}
    for index, pixels in enumerate(build_picture(source)):
        images[f"backdrop_l{index}.png"] = (BAND_SIZE[0], BAND_SIZE[1], 3, pixels)
    for bucket, (width, height, pixels) in build_sidebar_art(source).items():
        images[f"sidebar_art@{bucket}x.png"] = (width, height, 4, pixels)
    for bucket, (width, height, pixels) in build_nav_icons(source).items():
        images[f"nav_icons@{bucket}x.png"] = (width, height, 4, pixels)
    for bucket, (width, height, pixels) in build_playback_art(source).items():
        images[f"playback_art@{bucket}x.png"] = (width, height, 3, pixels)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, (width, height, channels, pixels) in images.items():
        data = png_bytes(width, height, channels, pixels)
        (out / name).write_bytes(data)
        files[name] = {"sha256": _sha256(data), "bytes": len(data), "px": [width, height]}
    for stale in out.iterdir():
        if stale.is_file() and stale.name not in files and stale.name != "manifest.json":
            stale.unlink()
            print(f"Removed stale {stale}")
    manifest = {
        "version": VERSION, "generator": GENERATOR,
        "design": {"screen": list(SCREEN), "sidebar": list(SIDEBAR)},
        "levels": _levels_section(),
        "shapes": {"poses": shape_poses(), "shard_fill": "backdrop_shard"},
        "sidebar_art": {"rect": list(SIDEBAR_ART_RECT),
                        "files": {bucket: f"sidebar_art@{bucket}x.png" for bucket in (*BUCKETS, "4")}},
        "nav_icons": {"order": list(NAV_ORDER), "cell": NAV_CELL, "rows": list(NAV_ROWS),
                      "files": {bucket: f"nav_icons@{bucket}x.png" for bucket in BUCKETS}},
        "playback_art": _playback_section(),
        "files": files,
    }
    (out / "manifest.json").write_text(_manifest_text(manifest), encoding="utf-8", newline="\n")
    print(f"Wrote {len(files)} images and manifest.json to {out}")
    problems = verify(out)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


# --------------------------------------------------------------------------
# Verification (stdlib only).


def _check_png(data: bytes, channels: int) -> tuple[int, int, bytes]:
    """(width, height, pixels) of one of our outputs; raises ValueError if it breaks the format."""
    chunks = png_chunks(data)
    width, height, depth, color_type, interlace = png_header(data)
    if depth != 8 or interlace != 0 or color_type != {3: 2, 4: 6}[channels]:
        raise ValueError(f"expected 8-bit {'RGB' if channels == 3 else 'RGBA'}, non-interlaced")
    tags = [tag for tag, _body in chunks]
    if tags.count(b"IDAT") != 1 or tags[-1] != b"IEND":
        raise ValueError("expected exactly one IDAT and a final IEND")
    raw = zlib.decompress(dict(chunks)[b"IDAT"])
    stride = width * channels
    if len(raw) != height * (1 + stride):
        raise ValueError("image data has the wrong size")
    if any(raw[y * (1 + stride)] != 0 for y in range(height)):
        raise ValueError("every row must use PNG filter 0")
    return width, height, b"".join(raw[y * (1 + stride) + 1:(y + 1) * (1 + stride)] for y in range(height))


def _token_rgb(name: str):
    color = ui_theme.TOKENS.get(name)
    return ui_theme.rgb(color) if color else None


def _check_icons(problems: list[str], name: str, width: int, height: int, pixels: bytes, cell: int,
                 order: list, rows: list) -> None:
    colors = [_token_rgb(token) for token in rows]
    for row, token in enumerate(rows):
        if colors[row] is None:
            problems.append(f"{name}: nav icon row colour {token!r} is not a ui_theme token")
            return
    planes = []
    for row in range(len(rows)):
        plane = []
        for column, icon in enumerate(order):
            covered = 0
            for y in range(row * cell, (row + 1) * cell):
                start = (y * width + column * cell) * 4
                for x in range(cell):
                    r, g, b, a = pixels[start + 4 * x:start + 4 * x + 4]
                    plane.append(a)
                    if a:
                        covered += 1
                        if (r, g, b) != colors[row]:
                            problems.append(f"{name}: {icon} row {rows[row]!r} is not drawn in that token's colour "
                                            "(rebuild after a palette change)")
                            return
            if not covered:
                problems.append(f"{name}: {icon} row {rows[row]!r} has no coverage")
        planes.append(plane)
    if any(plane != planes[0] for plane in planes):
        problems.append(f"{name}: icon rows differ in shape")


def verify(out: Path = OUT) -> list[str]:
    """Problems with ``assets/ui`` (an empty list means it is complete and current)."""
    out = Path(out)
    problems: list[str] = []
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"manifest.json: {error}"]
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        return [f"manifest.json: expected version {VERSION}"]
    if manifest.get("generator") != GENERATOR:
        problems.append(f"manifest.json: generator must be {GENERATOR!r}")
    if manifest.get("design") != {"screen": list(SCREEN), "sidebar": list(SIDEBAR)}:
        problems.append("manifest.json: unexpected design sizes")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return problems + ["manifest.json: no files listed"]
    expected = {f"backdrop_l{i}.png": (BAND_SIZE, 3) for i in range(BANDS)}
    for bucket in BUCKETS:
        s = float(bucket)
        expected[f"sidebar_art@{bucket}x.png"] = ((int(round(SIDEBAR_ART_RECT[2] * s)),
                                                   int(round(SIDEBAR_ART_RECT[3] * s))), 4)
        cell = int(round(NAV_CELL * s))
        expected[f"nav_icons@{bucket}x.png"] = ((cell * len(NAV_ORDER), cell * len(NAV_ROWS)), 4)
        expected[f"playback_art@{bucket}x.png"] = (tuple(int(round(v * s)) for v in PLAYBACK_SIZE), 3)
    expected["sidebar_art@4x.png"] = ((760, 2200), 4)
    if set(files) != set(expected):
        problems.append(f"manifest.json: files must be exactly {sorted(expected)}")
    on_disk = {path.name for path in out.iterdir() if path.is_file()} if out.is_dir() else set()
    for stray in sorted(on_disk - set(files) - {"manifest.json"}):
        problems.append(f"{stray}: not listed in manifest.json")
    decoded = {}
    for name, entry in sorted(files.items()):
        if "/" in name or "\\" in name or name.startswith("."):
            problems.append(f"{name}: unsafe file name")
            continue
        path = out / name
        try:
            data = path.read_bytes()
        except OSError:
            problems.append(f"{name}: missing")
            continue
        if not isinstance(entry, dict) or entry.get("sha256") != _sha256(data) or entry.get("bytes") != len(data):
            problems.append(f"{name}: sha256 or size does not match manifest.json")
        size, channels = expected.get(name, (None, 4))
        try:
            width, height, pixels = _check_png(data, channels)
        except (ValueError, zlib.error, struct.error) as error:
            problems.append(f"{name}: {error}")
            continue
        if [width, height] != entry.get("px") or (size is not None and (width, height) != tuple(size)):
            problems.append(f"{name}: {width}x{height} does not match manifest.json / the expected size")
        decoded[name] = (width, height, pixels)
    if manifest.get("levels") != _levels_section():
        problems.append("manifest.json: levels section does not match the generator")
    shapes = manifest.get("shapes") or {}
    poses = shapes.get("poses")
    if not isinstance(poses, list) or len(poses) != POSES:
        problems.append(f"manifest.json: expected {POSES} shape poses")
    else:
        for index, pose in enumerate(poses):
            ok = (isinstance(pose, dict) and set(pose) == {"shard"} and isinstance(pose["shard"], list)
                  and len(pose["shard"]) >= len(SHAPE)
                  and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p)
                          for p in pose["shard"]))
            if not ok:
                problems.append(f"manifest.json: shape pose {index} is malformed")
        if poses[0] != design_pose():
            problems.append("manifest.json: shape pose 0 must be the Figma design")
        if poses != shape_poses():
            problems.append("manifest.json: shape poses do not match the generator")
    if set(shapes) != {"poses", "shard_fill"}:
        problems.append("manifest.json: shapes must hold exactly poses and shard_fill")
    token = shapes.get("shard_fill")
    if not isinstance(token, str) or token not in ui_theme.TOKENS:
        problems.append(f"manifest.json: shapes.shard_fill must name a ui_theme token, got {token!r}")
    art = manifest.get("sidebar_art") or {}
    if art.get("rect") != list(SIDEBAR_ART_RECT) or art.get("files") != {b: f"sidebar_art@{b}x.png" for b in (*BUCKETS, "4")}:
        problems.append("manifest.json: sidebar_art section does not match the generator")
    icons = manifest.get("nav_icons") or {}
    if (icons.get("order") != list(NAV_ORDER) or icons.get("cell") != NAV_CELL or icons.get("rows") != list(NAV_ROWS)
            or icons.get("files") != {b: f"nav_icons@{b}x.png" for b in BUCKETS}):
        problems.append("manifest.json: nav_icons section does not match the generator")
    else:
        for bucket in BUCKETS:
            name = f"nav_icons@{bucket}x.png"
            if name in decoded:
                width, height, pixels = decoded[name]
                _check_icons(problems, name, width, height, pixels, int(round(NAV_CELL * float(bucket))),
                             list(NAV_ORDER), list(NAV_ROWS))
    playback = manifest.get("playback_art") or {}
    if playback != _playback_section():
        if {key: value for key, value in playback.items() if key != "surface"} == \
                {key: value for key, value in _playback_section().items() if key != "surface"}:
            problems.append("manifest.json: playback_art was composited over another panel colour "
                            "(rebuild after a palette change)")
        else:
            problems.append("manifest.json: playback_art section does not match the generator")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, help="Folder with the Figma exports (assets/layers, plates, icons).")
    parser.add_argument("--out", type=Path, default=OUT, help="Output folder (default: assets/ui).")
    parser.add_argument("--verify", action="store_true", help="Only check the output folder (no numpy needed).")
    args = parser.parse_args(argv)
    if args.verify:
        problems = verify(args.out)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print(f"{args.out} is complete and current")
        return 1 if problems else 0
    if args.source is None:
        parser.error("--source is required to build (or pass --verify)")
    return build(args.source, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
