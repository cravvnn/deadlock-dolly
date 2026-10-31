"""Scroll-edge fades for the Camera page (Kara's comment #5; PLAN-5 N1, N11, 2.10).

Tk cannot blend over window items, so the fade is two owned, layered, click-through,
never-activated popups over the scrolling viewport's top and bottom edges (the
RenderLock ``gui_fade`` technique): premultiplied BGRA bitmaps pushed with
``UpdateLayeredWindow``, a window procedure that is ``DefWindowProcW`` itself (no
Python, no Tk ever runs in it). A band is drawn only over the cards on its rows (the
backdrop between them is fixed to the window) in the backdrop colour, its alpha
``255 · smoothstep(t)`` with t = 1 at the cut edge.

Guards (Dolly's hidden test windows must stay invisible): a popup is never created
while the root is withdrawn, iconic, not viewable, DWM-cloaked or off every monitor,
under ``DOLLY_UI_FADE=0`` or off Windows; it is created cloaked and uncloaked only when
:func:`available` holds at that moment. Any error hides the bands for good (a hard
edge). Moving the window hides them at once; they come back 250 ms after the last move.

Motion (USER-ADDITIONS UPDATE 4 (d)): a band that appears ramps its constant alpha
0 → 255 over 150 ms on the shared :class:`.tween.Animator` and ramps back to 0 before
it hides; a ramp frame is one blend-only ``UpdateLayeredWindow`` call. With motion off
it shows and hides at once. While scrolling, a band's bitmap changes only when a card
edge crosses it: bitmaps are built from cached row segments by byte repetition and
kept in a small LRU keyed by their signature, so scrolling back reuses them.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_fade.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

from collections import OrderedDict
import os
import sys
import tkinter as tk

from .. import ui_theme
from . import boil
from .scale import px
from .tween import FADE_MS, Animator

DWMWA_CLOAK, DWMWA_CLOAKED = 13, 14
LRU_SIZE = 24


def _cloaked(hwnd) -> bool:
    """True when DWM hides the window (or cannot say)."""
    import ctypes
    value = ctypes.c_int(0)
    result = ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED,
                                                            ctypes.byref(value), ctypes.sizeof(value))
    return result != 0 or bool(value.value)


def _on_monitor(hwnd) -> bool:
    import ctypes
    user32 = ctypes.WinDLL("user32")
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    return bool(user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 0))   # MONITOR_DEFAULTTONULL


def available(root) -> bool:
    """True when a fade popup may exist for ``root``'s window right now (see the module guards)."""
    if os.environ.get("DOLLY_UI_FADE", "1") == "0" or sys.platform != "win32":
        return False
    try:
        top = root.winfo_toplevel()
        if str(top.wm_state()) in ("withdrawn", "iconic") or not top.winfo_viewable():
            return False
        hwnd = int(str(top.wm_frame()), 16)
        return not _cloaked(hwnd) and _on_monitor(hwnd)
    except (AttributeError, OSError, ValueError, tk.TclError):
        return False


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def render_rows(width, height, spans, rgb, edge) -> bytes:
    """A band's premultiplied BGRA rows (top-down), ``width`` x ``height`` px.

    ``spans``: (x0, x1, y0, y1) band px where a card lies under the band; elsewhere the
    band is transparent. ``rgb``: (r, g, b) or ``#rrggbb``. ``edge``: ``"top"`` (cut at
    row 0) or ``"bottom"`` (cut at the last row); alpha = 255 · smoothstep(t), t = 1 at
    the cut edge, 0 at the other.
    """
    width, height = max(0, int(width)), max(0, int(height))
    if isinstance(rgb, str):
        rgb = ui_theme.rgb(rgb)
    r, g, b = rgb
    clear = b"\0\0\0\0"
    rows = []
    last = max(1, height - 1)
    for y in range(height):
        t = 1.0 - y / last if edge == "top" else y / last
        a = round(255 * _smoothstep(t)) if height > 1 else 255
        pixel = bytes(((b * a + 127) // 255, (g * a + 127) // 255, (r * a + 127) // 255, a))
        covered = sorted((max(0, int(x0)), min(width, int(x1))) for x0, x1, y0, y1 in spans
                         if y0 <= y < y1 and x1 > x0)
        parts, x = [], 0
        for x0, x1 in covered:
            if x1 <= x:
                continue
            x0 = max(x0, x)
            parts.append(clear * (x0 - x))
            parts.append(pixel * (x1 - x0))
            x = x1
        parts.append(clear * (width - x))
        rows.append(b"".join(parts))
    return b"".join(rows)


class Win32Backend:
    """The layered popups (ctypes). Every call is plain Win32; nothing calls back into Python."""

    _class_atom = None

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        self.user32, self.gdi32, self.dwm = user32, gdi32, ctypes.WinDLL("dwmapi")
        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", self.WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                        ("hIconSm", wintypes.HICON)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        self.BITMAPINFOHEADER, self.BLENDFUNCTION = BITMAPINFOHEADER, BLENDFUNCTION
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        user32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                               wintypes.HDC, ctypes.c_void_p, wintypes.COLORREF, ctypes.c_void_p,
                                               wintypes.DWORD)
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wintypes.UINT)
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                           ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        self.dwm.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        self.instance = kernel32.GetModuleHandleW(None)
        self.class_name = "DollyScrollFade"
        if Win32Backend._class_atom is None:
            # The window procedure is DefWindowProcW itself: a raw function pointer, no Python callback.
            self._proc = ctypes.cast(user32.DefWindowProcW, self.WNDPROC)
            klass = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc,
                                hInstance=self.instance, lpszClassName=self.class_name)
            user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
            atom = user32.RegisterClassExW(ctypes.byref(klass))
            if not atom and ctypes.get_last_error() not in (0, 1410):   # 1410: class already exists
                raise OSError("RegisterClassExW failed")
            Win32Backend._class_atom = atom or True
            Win32Backend._class_ref = (klass, self._proc)

    def _cloak(self, hwnd, value):
        flag = self.ctypes.c_int(1 if value else 0)
        self.dwm.DwmSetWindowAttribute(hwnd, DWMWA_CLOAK, self.ctypes.byref(flag), self.ctypes.sizeof(flag))

    def create(self, owner):
        ex = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000   # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
        hwnd = self.user32.CreateWindowExW(ex, self.class_name, None, 0x80000000, 0, 0, 1, 1, owner, None,
                                           self.instance, None)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        self._cloak(hwnd, True)   # never seen before the guard says so
        return {"hwnd": hwnd, "dc": None, "bitmap": None, "old": None, "bits": None, "size": None}

    def _surface(self, handle, width, height):
        if handle["size"] == (width, height):
            return
        self._free(handle)
        ctypes = self.ctypes
        header = self.BITMAPINFOHEADER(biSize=ctypes.sizeof(self.BITMAPINFOHEADER), biWidth=width,
                                       biHeight=-height, biPlanes=1, biBitCount=32, biCompression=0)
        info = (ctypes.c_byte * (ctypes.sizeof(header) + 16))()
        ctypes.memmove(info, ctypes.byref(header), ctypes.sizeof(header))
        bits = ctypes.c_void_p()
        dc = self.gdi32.CreateCompatibleDC(None)
        bitmap = self.gdi32.CreateDIBSection(dc, info, 0, ctypes.byref(bits), None, 0)
        if not dc or not bitmap or not bits.value:
            raise OSError("CreateDIBSection failed")
        handle.update(dc=dc, bitmap=bitmap, old=self.gdi32.SelectObject(dc, bitmap), bits=bits.value,
                      size=(width, height))

    def _free(self, handle):
        if handle.get("dc"):
            if handle.get("old"):
                self.gdi32.SelectObject(handle["dc"], handle["old"])
            self.gdi32.DeleteDC(handle["dc"])
        if handle.get("bitmap"):
            self.gdi32.DeleteObject(handle["bitmap"])
        handle.update(dc=None, bitmap=None, old=None, bits=None, size=None)

    def _blend(self, alpha):
        return self.BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)   # AC_SRC_OVER, AC_SRC_ALPHA

    def update(self, handle, x, y, width, height, data, alpha):
        ctypes, wintypes = self.ctypes, self.wintypes
        self._surface(handle, width, height)
        ctypes.memmove(handle["bits"], data, len(data))
        position, size, source = wintypes.POINT(x, y), wintypes.SIZE(width, height), wintypes.POINT(0, 0)
        blend = self._blend(alpha)
        if not self.user32.UpdateLayeredWindow(handle["hwnd"], None, ctypes.byref(position), ctypes.byref(size),
                                               handle["dc"], ctypes.byref(source), 0, ctypes.byref(blend), 2):
            raise OSError("UpdateLayeredWindow failed")

    def blend(self, handle, alpha):
        blend = self._blend(alpha)
        self.user32.UpdateLayeredWindow(handle["hwnd"], None, None, None, None, None, 0,
                                        self.ctypes.byref(blend), 2)

    def move(self, handle, x, y):
        self.user32.SetWindowPos(handle["hwnd"], None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)

    def show(self, handle):
        self.user32.ShowWindow(handle["hwnd"], 4)          # SW_SHOWNOACTIVATE

    def hide(self, handle):
        self.user32.ShowWindow(handle["hwnd"], 0)          # SW_HIDE

    def uncloak(self, handle):
        self._cloak(handle["hwnd"], False)

    def destroy(self, handle):
        self._free(handle)
        if handle.get("hwnd"):
            self.user32.DestroyWindow(handle["hwnd"])
            handle["hwnd"] = None


class _Band:
    __slots__ = ("edge", "handle", "shown", "wanted", "alpha", "signature", "position", "uncloaked")

    def __init__(self, edge):
        self.edge = edge
        self.handle = None
        self.shown = False       # the popup is visible (perhaps at alpha 0 while ramping)
        self.wanted = False      # the edge is cut
        self.alpha = 0.0         # constant alpha, 0..1
        self.signature = None
        self.position = None
        self.uncloaked = False


class ScrollFade:
    """The two fade bands of one :class:`~dolly.ui.kit_page.KitPage`'s scrolling viewport.

    ``update()`` runs from the page's scroll path and layout: a band shows while its
    edge is cut (top: ``yview()[0] > 0``; bottom: ``yview()[1] < 1``), ``min(px(40),
    viewport / 3)`` tall. It reads only cached card rects, the view and the cached
    viewport origin; a bitmap is pushed only when its signature or position changed.
    ``backend``: the Win32 popups by default, injectable for tests.
    """

    RAMP_MS = FADE_MS
    SETTLE_MS = 250

    def __init__(self, page, *, height=40, backend=None):
        self.page = page
        self.height = height
        self.backend = backend
        self.broken = False
        self.renders = 0
        self.bands = {"top": _Band("top"), "bottom": _Band("bottom")}
        self._lru: OrderedDict = OrderedDict()
        self._origin = None          # the scrolling viewport's screen (x, y, width, height)
        self._held = set()           # "moving", "settle", "unmapped"
        self._move_job = None
        self._available = None
        self._destroyed = False
        root = page.winfo_toplevel()
        self._root = root
        page.canvas.bind("<Configure>", lambda _e: self._remeasure(), add="+")
        page.bind("<Map>", lambda e: self._release("unmapped") if e.widget is page else None, add="+")
        page.bind("<Unmap>", lambda e: self._hold("unmapped") if e.widget is page else None, add="+")
        page.bind("<Destroy>", lambda e: self.destroy() if e.widget is page else None, add="+")
        self._root_binding = root.bind("<Configure>", self._root_configured, add="+")
        clock = boil.BoilClock.of(root)
        self._unlisten = clock.add_settle_listener(
            lambda frozen: self._hold("settle") if frozen else self._release("settle"))

    # -- guards ---------------------------------------------------------------------------------
    def _hold(self, reason):
        if reason not in self._held:
            self._held.add(reason)
            self._available = None
            self.hide()

    def _release(self, reason):
        if reason in self._held:
            self._held.discard(reason)
            self._available = None
            self._remeasure()

    def _root_configured(self, event):
        if event.widget is not self._root or self._destroyed:
            return
        self._hold("moving")
        try:
            if self._move_job is not None:
                self._root.after_cancel(self._move_job)
            self._move_job = self._root.after(self.SETTLE_MS, self._moved)
        except tk.TclError:
            self._move_job = None

    def _moved(self):
        self._move_job = None
        self._release("moving")

    def _remeasure(self):
        """Cache where the viewport is on screen (a configure, map or settle), then follow the view."""
        try:
            canvas = self.page.canvas
            self._origin = (canvas.winfo_rootx(), canvas.winfo_rooty(), canvas.winfo_width(), canvas.winfo_height())
        except tk.TclError:
            self._origin = None
        self._available = None
        self.update()

    def _allowed(self):
        if self._available is None:
            self._available = not self._held and available(self._root)
        return self._available

    # -- bands ----------------------------------------------------------------------------------
    def band_height(self, viewport) -> int:
        return max(0, min(px(self.page.scale, self.height), viewport // 3))

    def spans(self, edge, top, band, viewport):
        """The card spans (band px) under the ``edge`` band for the view starting at page y ``top``."""
        y0 = top if edge == "top" else top + viewport - band
        spans = []
        for x0, py0, x1, py1 in self.page._plate_rects:
            a, b = max(0, py0 - y0), min(band, py1 - y0)
            if b > a:
                spans.append((x0, x1, a, b))
        return tuple(sorted(spans))

    def bitmap(self, signature):
        bits = self._lru.get(signature)
        if bits is None:
            width, height, spans, rgb, edge = signature
            bits = self._lru[signature] = render_rows(width, height, spans, rgb, edge)
            self.renders += 1
            while len(self._lru) > LRU_SIZE:
                self._lru.popitem(last=False)
        else:
            self._lru.move_to_end(signature)
        return bits

    def update(self) -> None:
        """Follow the view: show, move, re-render or hide the bands (cheap; from the scroll path)."""
        if self.broken or self._destroyed:
            return
        try:
            canvas = self.page.canvas
            first, last = canvas.yview()
            wanted = {"top": first > 0.0, "bottom": last < 1.0}
            if not self._allowed() or self._origin is None:
                for band in self.bands.values():
                    band.wanted = False
                self.hide()
                return
            ox, oy, width, viewport = self._origin
            height = self.band_height(viewport)
            top = int(canvas.canvasy(0))
            rgb = ui_theme.TOKENS["backdrop"]
            for edge, band in self.bands.items():
                want = wanted[edge] and height > 0 and width > 0
                if not want:
                    self._unwant(band)
                    continue
                signature = (width, height, self.spans(edge, top, height, viewport), rgb, edge)
                position = (ox, oy if edge == "top" else oy + viewport - height)
                self._want(band, signature, position)
        except Exception:  # noqa: BLE001 - any failure: a hard edge from now on
            self._break()

    def _backend(self):
        if self.backend is None:
            self.backend = Win32Backend()
        return self.backend

    def _want(self, band, signature, position):
        backend = self._backend()
        if band.handle is None:
            band.handle = backend.create(int(str(self._root.wm_frame()), 16))
        width, height = signature[0], signature[1]
        if signature != band.signature:
            band.signature, band.position = signature, position
            backend.update(band.handle, position[0], position[1], width, height, self.bitmap(signature),
                           round(255 * band.alpha))
        elif position != band.position:
            band.position = position
            backend.move(band.handle, *position)
        if not band.uncloaked:
            if not available(self._root):
                return
            backend.uncloak(band.handle)
            band.uncloaked = True
        if not band.shown:
            band.shown = True
            backend.show(band.handle)
        if not band.wanted:
            band.wanted = True
            self._ramp(band, 1.0)

    def _unwant(self, band):
        if band.wanted:
            band.wanted = False
            if band.shown:
                self._ramp(band, 0.0)

    def _ramp(self, band, target):
        key = (self, band.edge)

        def apply(value, band=band):
            band.alpha = value
            if band.handle is not None and not self.broken:
                try:
                    self._backend().blend(band.handle, round(255 * value))
                except Exception:  # noqa: BLE001 - a hard edge from now on
                    self._break()

        def done(band=band):
            if not band.wanted and band.shown and band.handle is not None and not self.broken:
                band.shown = False
                try:
                    self._backend().hide(band.handle)
                except Exception:  # noqa: BLE001
                    self._break()
        Animator.of(self._root).run(key, self.RAMP_MS, apply, start=band.alpha, end=target, done=done)

    def hide(self) -> None:
        """Hide both bands at once (no ramp)."""
        for edge, band in self.bands.items():
            Animator.of(self._root).stop((self, edge))
            band.alpha = 0.0
            band.wanted = False
            if band.shown and band.handle is not None and self.backend is not None:
                band.shown = False
                try:
                    self.backend.hide(band.handle)
                except Exception:  # noqa: BLE001
                    self.broken = True

    def _break(self):
        self.broken = True
        try:
            self.hide()
        except Exception:  # noqa: BLE001
            pass

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.hide()
        self._unlisten()
        try:
            if self._move_job is not None:
                self._root.after_cancel(self._move_job)
        except tk.TclError:
            pass
        from .kit_controls import safe_unbind
        safe_unbind(self._root, "<Configure>", self._root_binding)
        for band in self.bands.values():
            if band.handle is not None and self.backend is not None:
                try:
                    self.backend.destroy(band.handle)
                except Exception:  # noqa: BLE001
                    pass
                band.handle = None
