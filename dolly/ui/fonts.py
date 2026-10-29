"""Named display fonts for the redesigned shell, with Kara's VALVE faces when available.

Every key of :data:`dolly.ui_theme.DISPLAY_FONTS` becomes a named Tk font per
root (``DollyWordmark``, ``DollyCardTitle``, ...), resolved to the first family
Tk lists and sized in design pixels times the display scale. Named fonts can be
reconfigured in place, so a late upgrade (the game path is often found after the
window is built) restyles every label and canvas text that uses them.

One face ships with Dolly: Inter, Kara's UI face (``assets/fonts/inter``, SIL Open
Font License). :func:`install` registers its static files privately for this
process (:func:`load_bundled`) before any font resolves, then sets
``ui_theme.FONT_FAMILY``/``FONT_VALUE`` (the ttk styles' faces) and Tk's standard
fonts (``TkDefaultFont``, ``TkTextFont`` for values, menus, tooltips, ...) to it.
A missing file leaves its face to the Segoe UI fallback. :func:`hug_width` sizes a box
that hugs its text (a kit button) the way Figma does, from the shipped face's own widths.

The VALVE faces are never shipped. They are used when Windows already has them,
or after :func:`request_private_fonts` registers the three audited files from the
user's own Deadlock install, privately for this process, through a verified copy
under ``%LOCALAPPDATA%\\DeadlockDolly\\ui-fonts-v1``. A private derivative corrects
their shallow Windows descent metrics, preserving outlines and widths. It uses
a distinct internal face name so installed originals cannot shadow it. A changed game font means a
silent fallback (Inter, then Segoe UI) by design; :func:`status` says why.
``DOLLY_UI_FONTS=fallback`` forces the Segoe UI fallbacks, Inter included
(deterministic tests).

Portions adapted from RenderLock 0.3.0b16 (agr/gui_fonts.py, agr/gui_theme.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from .. import ui_theme
from ..runtime import resource_root
from .scale import px, ui_scale

LOG = logging.getLogger(__name__)

NAMES = {"wordmark": "DollyWordmark", "page_title": "DollyPageTitle", "card_title": "DollyCardTitle",
         "sticker": "DollySticker", "body": "DollyBody", "small": "DollySmall", "icon": "DollyIcon"}
ROLES = {name: key for key, name in NAMES.items()}   # named font -> its key (for hug_width)

# Dolly's shipped UI faces: Inter 4's static files (rsms/inter release, extras/ttf).
# Windows lists a static weight that is not regular or bold as its own family.
BUNDLED_FACES = (
    {"family": "Inter", "name": "Inter-Regular.ttf"},
    {"family": "Inter Medium", "name": "Inter-Medium.ttf"},
    {"family": "Inter SemiBold", "name": "Inter-SemiBold.ttf"},
    {"family": "Inter", "name": "Inter-Bold.ttf", "weight": "bold"},   # "Inter" at weight bold
)
_BUNDLED_LIMIT = 4 * 1024 * 1024
_BUNDLED = {}  # folder -> per-face results; the files are registered once per process.
_ADVANCES = {}  # face file -> (units per em, {code point: advance}, .notdef advance) or None; read once.
# Tk's own fonts, used by every widget that names no font (entries, menus, plain Tk labels).
STANDARD_FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkCaptionFont",
                  "TkSmallCaptionFont", "TkIconFont")

# Exact installed-game identities, not redistributed font payloads. A changed
# game font needs a new audit before it is accepted.
FONT_SOURCES = (
    {"family": "VALVE Oracle Medium", "name": "valveoracle-medium.ttf",
     "bytes": 155484, "sha256": "93ab96ee6775596bb196797fbe4bd2f86c7cf0264c11230be63d51cd9e223c34"},
    {"family": "VALVE Oracle SemiBold", "name": "valveoracle-semibold.ttf",
     "bytes": 169704, "sha256": "52f5c587d31459e1a0ea4f420927b9d1744a672f175c3b7d5773137d2e483a50"},
    {"family": "VALVE Pulp Bold", "name": "valvepulp-bold.ttf",
     "bytes": 240028, "sha256": "60b904cb9353c2a8a8c7b9e6b052c9c368a8ea1e941dd4d4005a185822d205b9"},
)
_DISPLAY_FAMILIES = {spec["family"]: spec["family"].replace("VALVE", "DOLLY", 1) for spec in FONT_SOURCES}
FR_PRIVATE = 0x10
_FONT_LIMIT = 1024 * 1024
_REGISTERED = {}  # SHA-256 -> path; private registrations last until the process exits.
_REGISTER_LOCK = threading.Lock()
_GDI32 = None


class _FontState:
    """Per-root named fonts and what they resolved to."""

    def __init__(self, scale):
        self.scale = scale
        self.fonts = {}
        self.options = {}
        self.resolved = {}
        self.listeners = []
        self.status = "fallback"
        self.requests = {}
        self.detail = {}
        self.bundled = []       # load_bundled()'s results
        self.ui = {}            # the text faces resolved for the ttk styles and Tk's standard fonts
        self.standard = {}      # Tk standard font -> its family before Dolly changed it
        self.design = {}        # key -> (family, design px, weight) as resolved
        self.hugs = {}          # hug_width()'s answers


def install(root, scale: float) -> None:
    """Create (or re-resolve) this root's named display fonts; safe to call again.

    The shipped Inter faces are registered first, so they resolve with everything else.
    """
    root = _root_of(root)
    state = getattr(root, "_dolly_fonts", None)
    if state is None:
        state = root._dolly_fonts = _FontState(scale)
    state.scale = scale
    forced = _forced_fallback()
    if not forced:
        state.bundled = load_bundled()
    families = _logical_families(tkfont.families(root))
    state.status = "fallback" if forced else _availability(
        [spec["family"] for spec in FONT_SOURCES], families)
    _resolve_ui_faces(root, state, families, forced)
    if _apply(root, state, families):
        _notify(state)


def load_bundled(directory=None) -> list:
    """Register Dolly's shipped UI faces privately for this process, once; never raises.

    Returns one JSON-safe entry per face of :data:`BUNDLED_FACES`: ``registered`` (or
    ``already_registered``), ``missing`` (no file: that face falls back) or
    ``unavailable`` with the error. Non-Windows systems touch no files.
    """
    folder = Path(directory) if directory is not None else bundled_dir()
    key = str(folder)
    if key in _BUNDLED:
        return [dict(face) for face in _BUNDLED[key]]
    faces = []
    for spec in BUNDLED_FACES:
        face = {"family": spec["family"], "name": spec["name"], "status": "missing"}
        path = folder / spec["name"]
        try:
            if sys.platform != "win32":
                raise OSError("Private UI fonts are supported on Windows only")
            if path.exists() or path.is_symlink():
                data = _read_bounded(path, _BUNDLED_LIMIT)
                _check_tables(data)
                face["status"] = _register_private(path.resolve(), hashlib.sha256(data).hexdigest())
        except (OSError, ValueError, RuntimeError, AttributeError) as exc:
            face.update(status="unavailable", error=str(exc)[:240])
        faces.append(face)
    missing = [face["name"] for face in faces if face["status"] not in ("registered", "already_registered")]
    if missing:
        LOG.info("Shipped UI faces not loaded (%s): Segoe UI stands in for them", ", ".join(missing))
    _BUNDLED[key] = faces
    return [dict(face) for face in faces]


def bundled_dir() -> Path:
    """Where the shipped UI faces live: ``assets/fonts/inter`` (beside ``assets/ui``)."""
    return resource_root(Path(__file__).resolve().parents[2]) / "assets" / "fonts" / "inter"


def font(widget, key: str) -> tkfont.Font:
    """The named font for ``key``, installing the set on first use."""
    return _ensure(widget).fonts[key]


def family_available(root, key: str) -> str:
    """The family ``key`` resolved to, or ``""`` when Tk lists none of its candidates.

    The named font then carries the last candidate, which Tk substitutes.
    """
    return _ensure(root).resolved[key]


def hug_width(widget, key: str, text: str) -> int:
    """Px that a box hugging one line of ``text`` in the ``key`` role gives it, as Figma sizes it.

    Figma's auto-width text box is the face's own advance widths at the design size, rounded
    up to a whole design px, and a hugging button adds its paddings to that. GDI's widths at
    the drawn size round every glyph to a whole pixel and drift by up to 3 px on a label
    (Inter 12 "Open output folder": 105, Figma 108; "Stop / restore": 77, Figma 78). So the
    shipped Inter faces are counted from their own font file (Stop / restore: 78, its button
    13 + 78 + 13 = 104 as in Figma [design reference]). Any other face (the Segoe UI fallback) is measured
    as drawn, as before. Text that must fit where it is drawn (fields, elision) keeps measuring
    the font.
    """
    state = _ensure(widget)
    family, size, weight = state.design[key]
    cache = (family, size, weight, state.scale, text)
    width = state.hugs.get(cache)
    if width is None:
        face = _advances(_face_file(family, weight))
        if face is None:
            width = int(state.fonts[key].measure(text))
        else:
            upm, table, missing = face
            units = sum(table.get(ord(character), missing) for character in text)
            width = px(state.scale, -(-units * size // upm))   # whole design px, rounded up
        if len(state.hugs) > 512:
            state.hugs.clear()
        state.hugs[cache] = width
    return width


def descent_shortfall(widget, key: str) -> int:
    """Extra pixels below the reported descent that the resolved face really needs."""
    state = _ensure(widget)
    options = state.options[key]
    em = ui_theme.TRUE_DESCENT_EM.get(options["family"])
    if not em or state.resolved.get(key) != options["family"]:
        return 0
    size = int(options["size"])
    size_px = -size if size < 0 else round(size * float(_root_of(widget).tk.call("tk", "scaling")))
    return max(0, int(em * size_px + .5) - int(state.fonts[key].metrics("descent")))


def request_private_fonts(root, game_path: str) -> str:
    """Use the VALVE faces from ``game_path`` when possible; never raises.

    Returns ``"system"`` (Windows already lists all three faces), ``"ready"``
    (all three available after private registration), ``"partial"`` or
    ``"fallback"``. An empty path, ``DOLLY_UI_FONTS=fallback`` and non-Windows
    systems touch no files. The named fonts are re-resolved in place and
    :func:`on_change` listeners are told when anything changed.
    """
    try:
        root = _root_of(root)
        state = _ensure(root)
        if _forced_fallback():
            state.status = "fallback"
            return state.status
        wanted = [spec["family"] for spec in FONT_SOURCES]
        families = _logical_families(tkfont.families(root))
        path = str(game_path or "").strip()
        missing = [family for family in wanted if _DISPLAY_FAMILIES[family] not in families]
        if missing and path and sys.platform == "win32" and path not in state.requests:
            state.requests[path] = state.detail = _load_private(path, families=set(missing))
            families = _logical_families(tkfont.families(root))
        state.status = _availability(wanted, families)
        if _apply(root, state, families):
            _notify(state)
        return state.status
    except Exception as exc:  # A missing face is an ordinary fallback, never a UI failure.
        LOG.info("Display fonts unavailable: %s", exc)
        try:
            _ensure(root).status = "fallback"
        except Exception:
            pass
        return "fallback"


def on_change(root, callback: Callable[[], None]) -> Callable[[], None]:
    """Call ``callback()`` after this root's named fonts are reconfigured; returns a remover."""
    state = _ensure(root)
    state.listeners.append(callback)

    def remove():
        if callback in state.listeners:
            state.listeners.remove(callback)
    return remove


def status(root) -> dict:
    """JSON-safe summary: overall status, resolved families and per-font loading details.

    ``status`` is about the VALVE faces; ``ui`` names the text faces in use and
    ``bundled`` says how each shipped Inter file loaded.
    """
    state = _ensure(root)
    return {"status": state.status, "scale": state.scale, "families": dict(state.resolved),
            "fonts": [dict(face) for face in state.detail.get("fonts", ())],
            "errors": list(state.detail.get("errors", ())),
            "cache_dir": state.detail.get("cache_dir"),
            "ui": dict(state.ui), "bundled": [dict(face) for face in state.bundled]}


# Resolution ----------------------------------------------------------------

def _root_of(widget):
    root = widget
    while getattr(root, "master", None) is not None:
        root = root.master
    return root


def _ensure(widget) -> _FontState:
    root = _root_of(widget)
    state = getattr(root, "_dolly_fonts", None)
    if state is None or not state.fonts:
        install(root, ui_scale(root))
        state = root._dolly_fonts
    return state


def _forced_fallback():
    return os.environ.get("DOLLY_UI_FONTS", "").strip().lower() == "fallback"


def _choose(candidates, families, forced):
    """First listed candidate (skipping the VALVE and Inter faces when forced), else the last one."""
    private = {spec["family"] for spec in FONT_SOURCES + BUNDLED_FACES}
    for family, size, style in candidates:
        if forced and family in private:
            continue
        if family in families:
            return family, size, style, True
    family, size, style = candidates[-1]
    return family, size, style, False


def _logical_families(families):
    families = set(families)
    return families | {family for family, display in _DISPLAY_FAMILIES.items() if display in families}


def _apply(root, state, families):
    """Configure every named font; True when an existing font changed."""
    forced = _forced_fallback()
    changed = False
    for key, candidates in ui_theme.DISPLAY_FONTS.items():
        family, size, style, listed = _choose(candidates, families, forced)
        display = _DISPLAY_FAMILIES.get(family)
        options = {"family": display if display in families else family, "size": -px(state.scale, size),
                   "weight": "bold" if "bold" in style.split() else "normal", "slant": "roman"}
        state.resolved[key] = family if listed else ""
        state.design[key] = (family, size, options["weight"])
        current = state.fonts.get(key)
        if current is None:
            state.fonts[key] = _named(root, NAMES[key], options)
        elif state.options.get(key) != options:
            current.configure(**options)
            changed = True
        state.options[key] = options
    return changed


def _resolve_ui_faces(root, state, families, forced):
    """Set the ttk styles' text faces and point Tk's standard fonts at them (sizes kept).

    ``ui_theme.FONT_FAMILY`` is Inter (Regular) and ``ui_theme.FONT_VALUE`` Inter Medium
    once Tk lists them, else both the Segoe UI fallback; ``TkTextFont`` (entries,
    dropdowns, lists) takes the value face, the other standard fonts the text face.
    A fallback puts back each standard font's own family.
    """
    listed = not forced and ui_theme.UI_FACE in families
    family = ui_theme.UI_FACE if listed else ui_theme.FONT_FALLBACK
    value = ui_theme.UI_FACE_VALUE if listed and ui_theme.UI_FACE_VALUE in families else family
    ui_theme.FONT_FAMILY, ui_theme.FONT_VALUE = family, value
    state.ui = {"family": family, "value": value}
    for name in STANDARD_FONTS:
        try:
            standard = tkfont.nametofont(name, root=root)
            own = state.standard.setdefault(name, standard.cget("family"))
            wanted = (value if name == "TkTextFont" else family) if listed else own
            if standard.cget("family") != wanted:
                standard.configure(family=wanted)
        except tk.TclError:
            pass  # A Tk without this standard font keeps its default.


def _face_file(family, weight):
    """The shipped file of ``family`` at ``weight``, or None (a face Dolly does not ship)."""
    for spec in BUNDLED_FACES:
        if spec["family"] == family and spec.get("weight", "normal") == weight:
            return bundled_dir() / spec["name"]
    return None


def _advances(path):
    """(units per em, {code point: advance}, .notdef advance) of a shipped face, or None; read once.

    The face's Unicode cmap (format 12) and hmtx: the unhinted widths Figma lays text out
    with. A missing or unreadable file gives None (the caller measures the drawn font).
    """
    if path is None:
        return None
    key = str(path)
    if key not in _ADVANCES:
        try:
            _ADVANCES[key] = _read_advances(_read_bounded(path, _BUNDLED_LIMIT))
        except (OSError, ValueError, struct.error) as exc:
            LOG.info("No design widths from %s: %s", path.name, exc)
            _ADVANCES[key] = None
    return _ADVANCES[key]


def _read_advances(data):
    _check_tables(data)
    tables = {}
    for position in range(12, 12 + 16 * struct.unpack_from(">H", data, 4)[0], 16):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, position)
        tables[tag] = offset
    if not {b"head", b"hhea", b"hmtx", b"cmap"} <= set(tables):
        raise ValueError("TrueType font without head, hhea, hmtx or cmap")
    upm = struct.unpack_from(">H", data, tables[b"head"] + 18)[0]
    metrics = struct.unpack_from(">H", data, tables[b"hhea"] + 34)[0]
    if not 16 <= upm <= 16384 or not metrics:
        raise ValueError("Invalid units per em or horizontal metrics")

    def advance(glyph):
        return struct.unpack_from(">H", data, tables[b"hmtx"] + 4 * min(glyph, metrics - 1))[0]
    cmap = tables[b"cmap"]
    base = None
    for index in range(struct.unpack_from(">H", data, cmap + 2)[0]):
        platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * index)
        if (platform, encoding) in ((3, 10), (0, 4)) and struct.unpack_from(">H", data, cmap + offset)[0] == 12:
            base = cmap + offset
    if base is None:
        raise ValueError("No full Unicode cmap")
    table = {}
    for index in range(min(struct.unpack_from(">I", data, base + 12)[0], 8192)):
        start, end, first = struct.unpack_from(">III", data, base + 16 + 12 * index)
        for code in range(start, min(end, start + 0xFFFF, 0x10FFFF) + 1):
            table[code] = advance(first + code - start)
    return upm, table, advance(0)


def _named(root, name, options):
    try:
        named = tkfont.Font(root=root, name=name, exists=True)
    except tk.TclError:
        return tkfont.Font(root=root, name=name, exists=False, **options)
    named.configure(**options)
    return named


def _notify(state):
    for callback in list(state.listeners):
        try:
            callback()
        except tk.TclError:
            pass  # A listener's widgets may already be gone.


def _availability(wanted, families):
    available = [family for family in wanted if family in families]
    if len(available) == len(wanted):
        registered = {spec["family"] for spec in FONT_SOURCES if spec["sha256"] in _REGISTERED}
        return "ready" if registered else "system"
    return "partial" if available else "fallback"


# Private loading -------------------------------------------------------------

def _read_bounded(path, limit):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing file or unsupported symbolic link")
    if path.stat().st_size > limit:
        raise ValueError("File exceeds the font size limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds the font size limit")
    return data


def _verify_font(data, spec):
    """Check the pinned identity before examining a bounded TrueType table directory."""
    if len(data) != spec["bytes"] or len(data) > _FONT_LIMIT:
        raise ValueError("Font size does not match the audited game file")
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("Font SHA-256 does not match the audited game file")
    _check_tables(data, "an audited TrueType font is required")


def _check_tables(data, need="a TrueType font is required"):
    """A bounded look at a TrueType table directory: unique tags inside the file, a name table."""
    if len(data) < 12 or data[:4] != b"\x00\x01\x00\x00":
        raise ValueError("Unsupported font format; " + need)
    count = struct.unpack_from(">H", data, 4)[0]
    end = 12 + 16 * count
    if not 1 <= count <= 64 or end > len(data):
        raise ValueError("Invalid TrueType table directory")
    tags = set()
    for position in range(12, end, 16):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", data, position)
        if tag in tags or offset < end or offset + length > len(data):
            raise ValueError("Invalid TrueType table bounds")
        tags.add(tag)
    if b"name" not in tags:
        raise ValueError("TrueType font has no name table")


def _cached_font(cache, spec):
    path = cache / (spec["sha256"] + ".ttf")
    if path.resolve().parent != cache:
        raise ValueError("Font cache object escapes its directory")
    _verify_font(_read_bounded(path, min(spec["bytes"], _FONT_LIMIT)), spec)
    return path


def _atomic_write(path, data, limit):
    """Stage, verify and replace inside the private cache directory."""
    if len(data) > limit:
        raise ValueError("Font cache write exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".font-", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_bounded(staged, limit) != data:
            raise ValueError("Font cache staged write failed verification")
        os.replace(staged, path)
        if _read_bounded(path, limit) != data:
            raise ValueError("Font cache final write failed verification")
    finally:
        staged.unlink(missing_ok=True)


def _cache_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ValueError("LOCALAPPDATA is unavailable for the private font cache")
    return Path(local) / "DeadlockDolly" / "ui-fonts-v1"


def _checksum(data):
    padded = data + b"\0" * (-len(data) % 4)
    return sum(word[0] for word in struct.iter_unpack(">I", padded)) & 0xFFFFFFFF


def _display_metrics(data):
    """Give Tk the font's real descender depth without changing any glyphs.

    The audited VALVE faces' Windows descent is shallower than their outlines.
    Canvas text is clipped to that line box, so widget padding alone cannot fix it.
    Vertical metrics, private face names and TrueType checksums change locally.
    """
    _check_tables(data)
    tables = {}
    for position in range(12, 12 + 16 * struct.unpack_from(">H", data, 4)[0], 16):
        tag, _sum, offset, length = struct.unpack_from(">4sIII", data, position)
        tables[tag] = (position, offset, length)
    for tag, minimum in ((b"head", 54), (b"hhea", 36), (b"OS/2", 78), (b"name", 6)):
        if tag not in tables or tables[tag][2] < minimum:
            raise ValueError("Display font is missing its vertical metrics")
    head, hhea, os2 = (tables[tag][1] for tag in (b"head", b"hhea", b"OS/2"))
    lowest = struct.unpack_from(">h", data, head + 38)[0]
    depth = max(0, -lowest)
    if struct.unpack_from(">H", data, os2 + 76)[0] >= depth:
        return data
    fixed = bytearray(data)
    struct.pack_into(">H", fixed, os2 + 76, depth)
    for offset in (hhea + 6, os2 + 70):
        struct.pack_into(">h", fixed, offset, min(lowest, struct.unpack_from(">h", data, offset)[0]))
    # A distinct private face prevents Windows selecting an older/system copy of
    # the same font. Glyph outlines, widths, copyright and licence stay intact.
    name, length = tables[b"name"][1:]
    count, strings = struct.unpack_from(">HH", data, name + 2)
    if 6 + count * 12 > length:
        raise ValueError("Invalid display font name records")
    for index in range(count):
        platform, _encoding, _language, kind, size, offset = struct.unpack_from(">6H", data, name + 6 + index * 12)
        if strings + offset + size > length:
            raise ValueError("Invalid display font name string")
        if kind not in (1, 3, 4, 6, 16):
            continue
        start = name + strings + offset
        encoding = "utf-16-be" if platform in (0, 3) else "latin-1"
        old, new = (text.encode(encoding) for text in ("VALVE", "DOLLY"))
        fixed[start:start + size] = bytes(fixed[start:start + size]).replace(old, new)
    struct.pack_into(">I", fixed, head + 8, 0)
    for tag in (b"head", b"hhea", b"OS/2", b"name"):
        position, offset, length = tables[tag]
        struct.pack_into(">I", fixed, position + 4, _checksum(bytes(fixed[offset:offset + length])))
    struct.pack_into(">I", fixed, head + 8, (0xB1B0AFBA - _checksum(bytes(fixed))) & 0xFFFFFFFF)
    return bytes(fixed)


def _display_font(path, spec):
    """Verified local derivative; retain the original cache and game files exactly."""
    if spec["family"] not in ui_theme.TRUE_DESCENT_EM:
        return path
    data = _read_bounded(path, min(spec["bytes"], _FONT_LIMIT))
    _verify_font(data, spec)
    fixed = _display_metrics(data)
    if fixed == data:
        return path
    target = path.parent / "display-metrics-v1" / path.name
    if target.parent.resolve().parent != path.parent.resolve():
        raise ValueError("Display font cache escapes its directory")
    try:
        matches = _read_bounded(target, _FONT_LIMIT) == fixed
    except (OSError, ValueError):
        matches = False
    if not matches:
        _atomic_write(target, fixed, _FONT_LIMIT)
    return target


def _register_private(path, digest):
    global _GDI32
    with _REGISTER_LOCK:
        if digest in _REGISTERED:
            return "already_registered"
        if sys.platform != "win32":
            raise OSError("Private game fonts are supported on Windows only")
        if _GDI32 is None:
            import ctypes
            from ctypes import wintypes
            _GDI32 = ctypes.WinDLL("gdi32", use_last_error=True)
            _GDI32.AddFontResourceExW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
            _GDI32.AddFontResourceExW.restype = ctypes.c_int
        # FR_NOT_ENUM would hide the faces from Tk. No registry entry or global install.
        if _GDI32.AddFontResourceExW(str(path), FR_PRIVATE, None) <= 0:
            raise OSError("Windows could not register the private game font")
        _REGISTERED[digest] = str(path)
        return "registered"


def _load_private(game_path, *, families=None, cache_dir=None):
    """Verify, cache and privately register the audited faces; returns JSON-safe details.

    Cached copies are authorized by the compiled pins alone, so they are used
    even when the install no longer validates. Only ``families`` are loaded
    when given.
    """
    result = {"fonts": [], "errors": [], "cache_dir": None}
    installed = source_dir = None
    try:
        from ..game_installation import validate_game
        paths = validate_game(game_path)
        installed = paths.root.resolve()
        source_dir = paths.citadel_dir / "panorama" / "fonts"
    except Exception as exc:  # LaunchError and filesystem errors alike mean "no source".
        result["errors"].append(str(exc)[:240])
    cache = None
    try:
        cache = Path(cache_dir if cache_dir is not None else _cache_dir()).expanduser().resolve()
        if installed is not None and (cache == installed or installed in cache.parents):
            raise ValueError("The private font cache must be outside the game installation")
        result["cache_dir"] = str(cache)
    except (OSError, ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc)[:240])
        cache = None
    for spec in FONT_SOURCES:
        if families is not None and spec["family"] not in families:
            continue
        state = {"family": spec["family"], "status": "missing", "source": None}
        path = None
        if cache is not None:
            try:
                path = _cached_font(cache, spec)
                state["source"] = "cache"
            except (OSError, ValueError):
                path = None
            if path is None and source_dir is not None:
                try:
                    source = source_dir / spec["name"]
                    if not source.resolve().is_relative_to(installed):
                        raise ValueError("Font source escapes the selected game installation")
                    data = _read_bounded(source, min(spec["bytes"], _FONT_LIMIT))
                    _verify_font(data, spec)
                    _atomic_write(cache / (spec["sha256"] + ".ttf"), data, _FONT_LIMIT)
                    path = _cached_font(cache, spec)
                    state["source"] = "installed"
                except (OSError, ValueError, RuntimeError) as exc:
                    path = None
                    state.update(status="unavailable", error=str(exc)[:240])
        if path is not None:
            state["cache_path"] = str(path)
            try:
                display = _display_font(path, spec)
                state["display_path"] = str(display)
                state["status"] = _register_private(display, spec["sha256"])
            except (OSError, ValueError, RuntimeError, AttributeError) as exc:
                state.update(status="unavailable", error=str(exc)[:240])
        result["fonts"].append(state)
    return result
