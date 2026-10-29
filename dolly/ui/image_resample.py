"""Smooth Windows-native scaling for UI artwork without an optional Python dependency.

GDI+ performs high-quality bicubic interpolation on the verified PNG; Tk then
composites the resulting RGBA image as usual.  Returning None leaves the caller
free to use Tk's regular scaling on platforms where GDI+ isn't available.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class _StartupInput(ctypes.Structure):
    _fields_ = [("version", wintypes.UINT), ("callback", ctypes.c_void_p),
                ("suppress_background", wintypes.BOOL), ("suppress_codecs", wintypes.BOOL)]


class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int)]


class _BitmapData(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT), ("stride", ctypes.c_int),
                ("format", ctypes.c_int), ("scan0", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


_ARGB = 0x26200A


def scale_centered(path, width: int, height: int, full_height: int):
    """Return packed RGBA for a proportional, horizontally centered cover image.

    ``full_height`` is the artwork's intended drawn height. When it exceeds
    ``height`` only the top is shown, preserving the short-window foot crop.
    Returns None on unsupported platforms or graphics/decoding errors.
    """
    if sys.platform != "win32" or min(width, height, full_height) <= 0:
        return None
    if width > 16384 or height > 16384 or width * height > 16_000_000:
        return None
    gdip = None
    token = ctypes.c_size_t()
    src = ctypes.c_void_p()
    dst = ctypes.c_void_p()
    graphics = ctypes.c_void_p()
    data = _BitmapData()
    locked = False
    started = False
    try:
        gdip = ctypes.WinDLL("gdiplus")
        ptr = ctypes.c_void_p
        integer = ctypes.c_int
        real = ctypes.c_float
        def fn(name, args):
            f = getattr(gdip, name)
            f.argtypes = args
            f.restype = integer
            return f
        startup = fn("GdiplusStartup", [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(_StartupInput), ptr])
        shutdown = fn("GdiplusShutdown", [ctypes.c_size_t])
        shutdown.restype = None
        load = fn("GdipCreateBitmapFromFile", [wintypes.LPCWSTR, ctypes.POINTER(ptr)])
        size_x = fn("GdipGetImageWidth", [ptr, ctypes.POINTER(wintypes.UINT)])
        size_y = fn("GdipGetImageHeight", [ptr, ctypes.POINTER(wintypes.UINT)])
        create = fn("GdipCreateBitmapFromScan0", [integer, integer, integer, integer, ptr, ctypes.POINTER(ptr)])
        get_graphics = fn("GdipGetImageGraphicsContext", [ptr, ctypes.POINTER(ptr)])
        interpolation = fn("GdipSetInterpolationMode", [ptr, integer])
        pixel_offset = fn("GdipSetPixelOffsetMode", [ptr, integer])
        compositing = fn("GdipSetCompositingMode", [ptr, integer])
        draw = fn("GdipDrawImageRectRect", [ptr, ptr, real, real, real, real, real, real, real, real,
                                             integer, ptr, ptr, ptr])
        lock = fn("GdipBitmapLockBits", [ptr, ctypes.POINTER(_Rect), wintypes.UINT, integer,
                                          ctypes.POINTER(_BitmapData)])
        unlock = fn("GdipBitmapUnlockBits", [ptr, ctypes.POINTER(_BitmapData)])
        dispose = fn("GdipDisposeImage", [ptr])
        delete_graphics = fn("GdipDeleteGraphics", [ptr])
        if startup(ctypes.byref(token), ctypes.byref(_StartupInput(1, None, 0, 0)), None):
            return None
        started = True
        if load(str(path), ctypes.byref(src)):
            return None
        sw, sh = wintypes.UINT(), wintypes.UINT()
        if size_x(src, ctypes.byref(sw)) or size_y(src, ctypes.byref(sh)) or not sw.value or not sh.value:
            return None
        if create(width, height, 0, _ARGB, None, ctypes.byref(dst)) or get_graphics(dst, ctypes.byref(graphics)):
            return None
        # 7 = HighQualityBicubic, 4 = HighQuality pixel offset, 1 = SourceCopy.
        if interpolation(graphics, 7) or pixel_offset(graphics, 4) or compositing(graphics, 1):
            return None
        factor = max(width / sw.value, full_height / sh.value)
        visible_w = width / factor
        visible_h = height / factor
        x = (sw.value - visible_w) / 2.0
        if draw(graphics, src, 0, 0, width, height, x, 0, visible_w, visible_h, 2, None, None, None):
            return None
        rect = _Rect(0, 0, width, height)
        if lock(dst, ctypes.byref(rect), 1, _ARGB, ctypes.byref(data)):
            return None
        locked = True
        # GDI+ exposes straight-alpha BGRA rows; Tk's PNG helper takes RGBA.
        if not data.scan0 or abs(data.stride) < width * 4:
            return None
        bgra = b"".join(ctypes.string_at(data.scan0 + y * data.stride, width * 4) for y in range(height))
        rgba = bytearray(len(bgra))
        rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = bgra[2::4], bgra[1::4], bgra[0::4], bgra[3::4]
        return bytes(rgba)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError, MemoryError, ctypes.ArgumentError):
        return None
    finally:
        if gdip is not None and started:
            if locked:
                unlock(dst, ctypes.byref(data))
            if graphics:
                delete_graphics(graphics)
            if dst:
                dispose(dst)
            if src:
                dispose(src)
            shutdown(token)
