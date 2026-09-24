"""Native Windows message dialog with the standard OK plus a copy-details button."""

from __future__ import annotations

import ctypes
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from . import __version__
from .branding import ASSETS

COPY_LABEL = "Copy error details"
COPIED_LABEL = "Copied"
OK_LABEL = "OK"
_COPY_ID = 1001
_IDOK = 1
_IDCANCEL = 2
_IDI_ERROR = 32513
_COPIED_TIMER = 1
_TEXT_WIDTH = 340
_PADDING = 16
_ICON_SIZE = 32
_BUTTON_GAP = 7
_BUTTON_PADDING = 24
_MIN_BUTTON_WIDTH = 75
_BOTTOM_MARGIN = 11

_WS_POPUP = 0x80000000
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_TABSTOP = 0x00010000
_WS_EX_DLGMODALFRAME = 0x00000001
_SS_LEFT = 0x00000000
_SS_ICON = 0x00000003
_SS_NOPREFIX = 0x00000080
_BS_DEFPUSHBUTTON = 0x00000001
_WM_COMMAND = 0x0111
_WM_SETFONT = 0x0030
_WM_TIMER = 0x0113
_WM_CLOSE = 0x0010
_WM_SETICON = 0x0080
_WM_NCDESTROY = 0x0082
_STM_SETICON = 0x0170
_ICON_SMALL = 0
_ICON_BIG = 1
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010
_DT_CALCRECT = 0x0400
_DT_WORDBREAK = 0x0010
_DT_NOPREFIX = 0x0800
_DT_EXPANDTABS = 0x0040
_SPI_GETNONCLIENTMETRICS = 0x0029
_BN_CLICKED = 0
_SW_SHOW = 5
_DIALOG_CLASS = "#32770"

if sys.platform == "win32":
    from ctypes import wintypes

    _LRESULT = ctypes.c_ssize_t
    _SUBCLASSPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _LOGFONTW(ctypes.Structure):
        _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long), ("lfEscapement", ctypes.c_long),
                    ("lfOrientation", ctypes.c_long), ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                    ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte), ("lfCharSet", ctypes.c_byte),
                    ("lfOutPrecision", ctypes.c_byte), ("lfClipPrecision", ctypes.c_byte),
                    ("lfQuality", ctypes.c_byte), ("lfPitchAndFamily", ctypes.c_byte),
                    ("lfFaceName", ctypes.c_wchar * 32)]

    class _NONCLIENTMETRICSW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("iBorderWidth", ctypes.c_int), ("iScrollWidth", ctypes.c_int),
                    ("iScrollHeight", ctypes.c_int), ("iCaptionWidth", ctypes.c_int),
                    ("iCaptionHeight", ctypes.c_int), ("lfCaptionFont", _LOGFONTW),
                    ("iSmCaptionWidth", ctypes.c_int), ("iSmCaptionHeight", ctypes.c_int),
                    ("lfSmCaptionFont", _LOGFONTW), ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
                    ("lfMenuFont", _LOGFONTW), ("lfStatusFont", _LOGFONTW), ("lfMessageFont", _LOGFONTW)]

    class _TEXTMETRICW(ctypes.Structure):
        _fields_ = [("tmHeight", ctypes.c_long), ("tmAscent", ctypes.c_long), ("tmDescent", ctypes.c_long),
                    ("tmInternalLeading", ctypes.c_long), ("tmExternalLeading", ctypes.c_long),
                    ("tmAveCharWidth", ctypes.c_long), ("tmMaxCharWidth", ctypes.c_long),
                    ("tmWeight", ctypes.c_long), ("tmOverhang", ctypes.c_long),
                    ("tmDigitizedAspectX", ctypes.c_long), ("tmDigitizedAspectY", ctypes.c_long),
                    ("tmFirstChar", ctypes.c_wchar), ("tmLastChar", ctypes.c_wchar),
                    ("tmDefaultChar", ctypes.c_wchar), ("tmBreakChar", ctypes.c_wchar),
                    ("tmItalic", ctypes.c_byte), ("tmUnderlined", ctypes.c_byte), ("tmStruckOut", ctypes.c_byte),
                    ("tmPitchAndFamily", ctypes.c_byte), ("tmCharSet", ctypes.c_byte)]


_ACTIVE: dict[int, object] = {}


def format_error_details(title: str, message: str, version: str = __version__) -> str:
    headline = f"Deadlock Dolly {version}"
    if str(title).strip():
        headline += f" | {str(title).strip()}"
    body = str(message).strip()
    return f"{headline}\n{body}" if body else headline


def show_error(parent, title, message, version: str = __version__) -> None:
    details = format_error_details(title, message, version)
    if sys.platform == "win32":
        try:
            if _native_dialog(parent, str(title), str(message).strip(), details):
                return
        except Exception:
            pass
    try:
        messagebox.showerror(title, str(message), parent=parent)
    except tk.TclError:
        pass


def _font_and_metrics(user32, gdi32, window):
    metrics = _NONCLIENTMETRICSW()
    metrics.cbSize = ctypes.sizeof(_NONCLIENTMETRICSW)
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    if not user32.SystemParametersInfoW(_SPI_GETNONCLIENTMETRICS, metrics.cbSize, ctypes.byref(metrics), 0):
        raise OSError("Could not read the system message font")
    gdi32.CreateFontIndirectW.argtypes = [_LOGFONTW]
    gdi32.CreateFontIndirectW.restype = wintypes.HFONT
    font = gdi32.CreateFontIndirectW(metrics.lfMessageFont)
    if not font:
        raise OSError("Could not create the dialog font")
    dc = user32.GetDC(window)
    if not dc:
        gdi32.DeleteObject(font)
        raise OSError("Could not measure the dialog text")
    previous = gdi32.SelectObject(dc, font)
    try:
        gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(_TEXTMETRICW)]
        text_metrics = _TEXTMETRICW()
        if not gdi32.GetTextMetricsW(dc, ctypes.byref(text_metrics)):
            raise OSError("Could not read the dialog font metrics")
    except Exception:
        gdi32.SelectObject(dc, previous)
        user32.ReleaseDC(window, dc)
        gdi32.DeleteObject(font)
        raise
    return font, dc, previous, text_metrics


def _measure(user32, gdi32, dc, message, text_width):
    user32.DrawTextW.argtypes = [wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int,
                                 ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    rect = wintypes.RECT(0, 0, text_width, 0)
    user32.DrawTextW(dc, message, -1, ctypes.byref(rect), _DT_CALCRECT | _DT_WORDBREAK | _DT_NOPREFIX | _DT_EXPANDTABS)
    text_height = rect.bottom - rect.top
    gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(_SIZE)]
    widths = {}
    for label in (COPY_LABEL, OK_LABEL):
        size = _SIZE()
        gdi32.GetTextExtentPoint32W(dc, label, len(label), ctypes.byref(size))
        widths[label] = size.cx
    return text_height, widths


def _native_dialog(parent, title: str, message: str, details: str) -> bool:
    if parent is None:
        return _run_native_dialog(None, title, message, details)
    # A ctypes GetMessage/DispatchMessage loop on Tk's thread can dispatch a
    # Tcl callback without _tkinter's saved Python thread state. That is a
    # fatal PyEval_RestoreThread error, not a catchable Python exception.
    # Keep every native dialog operation on its own thread; Tk owns the main
    # thread's modal wait and continues servicing timers and worker results.
    owner = parent.winfo_id()
    finished = threading.Event()
    result = []
    thread_id = []
    ready = tk.BooleanVar(master=parent, value=False)

    def run():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        thread_id.append(kernel32.GetCurrentThreadId())
        try:
            result.append(_run_native_dialog(owner, title, message, details))
        except Exception as exc:
            result.append(exc)
        finally:
            finished.set()

    timer = None

    def poll():
        nonlocal timer
        if finished.is_set():
            ready.set(True)
        else:
            timer = parent.after(25, poll)

    threading.Thread(target=run, name="DollyErrorDialog", daemon=True).start()
    try:
        timer = parent.after(25, poll)
        parent.wait_variable(ready)
    finally:
        if timer is not None:
            parent.after_cancel(timer)
        if not finished.is_set() and thread_id:
            # If Tk is shutting down, wake the native pump so its finally
            # block destroys the dialog and releases its callback and font.
            ctypes.WinDLL("user32").PostThreadMessageW(thread_id[0], 0x0012, 0, 0)
    if result and isinstance(result[0], Exception):
        raise result[0]
    return bool(result and result[0])


def _run_native_dialog(owner, title: str, message: str, details: str) -> bool:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, ctypes.c_wchar_p, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p]
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.MSG)]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
    comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t]
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = ctypes.c_ssize_t

    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    if owner:
        owner = user32.GetAncestor(owner, 2) or owner  # GA_ROOT: disable the whole Tk window.
    scale = 1.0
    try:
        dpi = user32.GetDpiForWindow(owner) if owner else user32.GetDpiForSystem()
        if dpi:
            scale = dpi / 96.0
    except AttributeError:
        pass
    pad = int(round(_PADDING * scale))
    gap = int(round(_BUTTON_GAP * scale))
    bottom_margin = int(round(_BOTTOM_MARGIN * scale))
    icon_size = int(round(_ICON_SIZE * scale))
    text_width = int(round(_TEXT_WIDTH * scale))

    font, dc, previous, text_metrics = _font_and_metrics(user32, gdi32, owner)
    try:
        text_height, label_widths = _measure(user32, gdi32, dc, message, text_width)
    finally:
        gdi32.SelectObject(dc, previous)
        user32.ReleaseDC(owner, dc)

    button_height = int(round(text_metrics.tmHeight + 7 * scale))
    copy_width = max(int(round(_MIN_BUTTON_WIDTH * scale)),
                     label_widths[COPY_LABEL] + int(round(_BUTTON_PADDING * scale)))
    ok_width = max(int(round(_MIN_BUTTON_WIDTH * scale)),
                   label_widths[OK_LABEL] + int(round(_BUTTON_PADDING * scale)))
    content_height = max(icon_size, text_height)
    client_width = max(pad + icon_size + int(round(12 * scale)) + text_width + pad,
                       pad + copy_width + gap + ok_width + pad)
    client_height = pad + content_height + pad + button_height + bottom_margin

    style = _WS_POPUP | _WS_CAPTION | _WS_SYSMENU
    exstyle = _WS_EX_DLGMODALFRAME
    window_rect = wintypes.RECT(0, 0, client_width, client_height)
    user32.AdjustWindowRectEx(ctypes.byref(window_rect), style, False, exstyle)
    window_width = window_rect.right - window_rect.left
    window_height = window_rect.bottom - window_rect.top
    x = y = 0
    if owner:
        owner_rect = wintypes.RECT()
        user32.GetWindowRect(owner, ctypes.byref(owner_rect))
        x = owner_rect.left + max((owner_rect.right - owner_rect.left - window_width) // 2, 0)
        y = owner_rect.top + max((owner_rect.bottom - owner_rect.top - window_height) // 3, 0)

    module = kernel32.GetModuleHandleW(None)
    window = user32.CreateWindowExW(exstyle, _DIALOG_CLASS, title, style, x, y, window_width, window_height,
                                    owner, None, module, None)
    if not window:
        gdi32.DeleteObject(font)
        return False

    icon = user32.CreateWindowExW(
        0, "Static", None, _WS_CHILD | _WS_VISIBLE | _SS_ICON | _SS_NOPREFIX,
        pad, pad, icon_size, icon_size, window, 200, module, None)
    if icon:
        user32.SendMessageW(icon, _STM_SETICON, user32.LoadIconW(None, ctypes.c_void_p(_IDI_ERROR)), 0)
    text_x = pad + icon_size + int(round(12 * scale))
    text = user32.CreateWindowExW(0, "Static", message, _WS_CHILD | _WS_VISIBLE | _SS_LEFT | _SS_NOPREFIX,
                                  text_x, pad, text_width, content_height, window, 201, module, None)
    buttons_y = client_height - bottom_margin - button_height
    copy_button = user32.CreateWindowExW(0, "Button", COPY_LABEL, _WS_CHILD | _WS_VISIBLE | _WS_TABSTOP,
                                         client_width - pad - ok_width - gap - copy_width, buttons_y,
                                         copy_width, button_height, window, _COPY_ID, module, None)
    ok_button = user32.CreateWindowExW(0, "Button", OK_LABEL, _WS_CHILD | _WS_VISIBLE | _WS_TABSTOP
                                       | _BS_DEFPUSHBUTTON, client_width - pad - ok_width, buttons_y,
                                       ok_width, button_height, window, _IDOK, module, None)
    for control in (icon, text, copy_button, ok_button):
        if not control:
            gdi32.DeleteObject(font)
            user32.DestroyWindow(window)
            return False
        user32.SendMessageW(control, _WM_SETFONT, font, True)

    icon_path = str(ASSETS / "dolly.ico")
    for size, which in ((16, _ICON_SMALL), (32, _ICON_BIG)):
        handle = user32.LoadImageW(None, icon_path, _IMAGE_ICON, size, size, _LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(window, _WM_SETICON, which, handle)

    def proc(hwnd, msg, wparam, lparam, _subclass_id, _data):
        try:
            if msg == _WM_COMMAND:
                command = int(wparam) & 0xFFFF
                code = (int(wparam) >> 16) & 0xFFFF
                if code == _BN_CLICKED and command == _COPY_ID:
                    _copy_text(details)
                    user32.SetWindowTextW(copy_button, COPIED_LABEL)
                    user32.SetTimer(hwnd, _COPIED_TIMER, 1500, None)
                    return 0
                if code == _BN_CLICKED and command in (_IDOK, _IDCANCEL):
                    user32.DestroyWindow(hwnd)
                    return 0
            if msg == _WM_TIMER and wparam == _COPIED_TIMER:
                user32.SetWindowTextW(copy_button, COPY_LABEL)
                user32.KillTimer(hwnd, _COPIED_TIMER)
                return 0
            if msg == _WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_NCDESTROY:
                comctl32.RemoveWindowSubclass(hwnd, callback, _COPY_ID)
                _ACTIVE.pop(int(hwnd), None)
        except Exception:
            pass
        return comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)

    callback = _SUBCLASSPROC(proc)
    if not comctl32.SetWindowSubclass(window, callback, _COPY_ID, 0):
        gdi32.DeleteObject(font)
        user32.DestroyWindow(window)
        return False
    _ACTIVE[int(window)] = callback

    if owner:
        user32.EnableWindow(owner, False)
    try:
        user32.ShowWindow(window, _SW_SHOW)
        user32.SetForegroundWindow(window)
        user32.SetFocus(ok_button)
        message_struct = wintypes.MSG()
        while user32.IsWindow(window):
            result = user32.GetMessageW(ctypes.byref(message_struct), None, 0, 0)
            if result in (0, -1):
                break
            if not user32.IsDialogMessageW(window, ctypes.byref(message_struct)):
                user32.TranslateMessage(ctypes.byref(message_struct))
                user32.DispatchMessageW(ctypes.byref(message_struct))
    finally:
        if user32.IsWindow(window):
            user32.DestroyWindow(window)
        if owner:
            user32.EnableWindow(owner, True)
            user32.SetForegroundWindow(owner)
        gdi32.DeleteObject(font)
    return True


def _copy_text(text: str) -> bool:
    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    cf_unicode_text = 13
    gmem_moveable = 0x0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    data = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
    if not handle:
        return False
    buffer = kernel32.GlobalLock(handle)
    if not buffer:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(buffer, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(cf_unicode_text, handle):
            kernel32.GlobalFree(handle)
            return False
    finally:
        user32.CloseClipboard()
    return True
