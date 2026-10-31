"""The main window without the Windows caption, still behaving like a Windows window.

USER-ADDITIONS UPDATE 20 (Figma Frame 79, [design reference]): Dolly draws its own title bar
(:mod:`.window_bars`). Windows keeps the native behaviour: dragging the bar runs Windows' own
move loop (Aero snap, double-click maximize, dragging a maximized window restores it), the
edges resize, and the taskbar button, Alt+Tab, Alt+F4, Win+arrow keys, the minimize and
maximize animations and the window shadow keep working.

How (the recipe RenderLock ships): Tk is told the window has no frame (override-redirect,
:meth:`WindowChrome.prepare`, before the window first maps), so Tk's own idea of the client
area is exactly the window and every ``winfo_rootx``/``rooty``, ``wm geometry``, menu post
and popup position stays right. Once mapped (:meth:`WindowChrome.finish`) the real frame
*styles* are put back with ctypes (caption, system menu, minimize/maximize boxes, sizing
frame, a taskbar button) and a small window procedure tells Windows the frame has zero size.
Restyling a framed Tk window from outside instead would leave Tk computing its geometry from
its own cached frame, off by the caption height.

Never on a window that is withdrawn, already mapped, or marked ``-toolwindow`` (the hidden
test and capture harnesses): those keep the native frame and nothing here touches them.
``DOLLY_UI_CUSTOM_CHROME=0`` keeps the native caption. Off Windows nothing changes; the
bars' buttons then fall back to Tk's own iconify/zoom.

Rules for code that runs later on this window:
- never call a Win32 function from Python that makes Tk handle the window's state change on
  the spot (``ShowWindow``, a moving/resizing/showing ``SetWindowPos``, ``SendMessage`` of a
  move loop): Tk then runs Python bindings (``<Map>``, ``<Unmap>``, ``<Configure>``) inside the
  ctypes call, where Python has no thread state, and the process aborts. Hide, show and resize
  through Tk (``withdraw``, ``deiconify``, ``geometry``); post everything else to Windows;
- minimize through :meth:`WindowChrome.minimize` and bring the window back from the taskbar
  through :meth:`WindowChrome.restore` (``root.iconify()`` raises on an override-redirect
  window, and ``root.deiconify()`` shows it without activating it);
- never call ``root.overrideredirect`` (or anything else that makes Tk re-create the wrapper
  window) on the main window after :meth:`WindowChrome.finish`: Tk builds a new wrapper and
  destroys the old one, and with it the window procedure, the restored styles and the taskbar
  button. :meth:`WindowChrome.restyle` cannot repair that (it returns False);
- ``root.attributes(-alpha/-topmost/-toolwindow)`` and ``wm resizable`` make Tk rewrite the
  window's styles (the taskbar button goes): avoid them on the main window; if one is ever
  unavoidable, call :meth:`WindowChrome.restyle` right after it.

Portions adapted from RenderLock 0.3.0b16 (agr/gui_chrome.py), Copyright (c) 2026 Cravvnn, MIT License
"""
from __future__ import annotations

import logging
import os
import sys
import tkinter as tk

from .. import ui_theme
from .scale import px, ui_scale

LOG = logging.getLogger(__name__)
ENV = "DOLLY_UI_CUSTOM_CHROME"
EDGE = 5                  # design px: resize zone inside each window edge
CORNER = 14               # corner zones are longer, so diagonal resizing is easy to hit
BORDER_TOKEN = "card_edge"   # Windows 11 draws a 1 px outline round the window in this colour

_GWL_STYLE, _GWL_EXSTYLE, _GWLP_WNDPROC = -16, -20, -4
_WS_CAPTION, _WS_THICKFRAME, _WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
_WS_MINIMIZEBOX, _WS_MAXIMIZEBOX = 0x00020000, 0x00010000
_WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW = 0x00000080, 0x00040000
_WM_CLOSE, _WM_WINDOWPOSCHANGING, _WM_NCCALCSIZE = 0x0010, 0x0046, 0x0083
_WM_NCDESTROY = 0x0082
_WM_NCLBUTTONDOWN, _WM_SYSCOMMAND, _WM_POPUPSYSTEMMENU = 0x00A1, 0x0112, 0x0313
_WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE = 0x0231, 0x0232
_SC_RESTORE, _SC_MAXIMIZE, _SC_MINIMIZE, _SC_KEYMENU = 0xF120, 0xF030, 0xF020, 0xF100
_SWP_NOSIZE, _SWP_NOMOVE = 0x0001, 0x0002
_ABM_GETAUTOHIDEBAREX = 0x0000000B
_ABE_LEFT, _ABE_TOP, _ABE_RIGHT, _ABE_BOTTOM = 0, 1, 2, 3
_HT = {"caption": 2, "w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14, "s": 15, "sw": 16, "se": 17}
_CURSORS = {"w": "size_we", "e": "size_we", "n": "size_ns", "s": "size_ns",
            "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}
_BUTTONS_HELD = 0x100 | 0x200 | 0x400   # Button1Mask | Button2Mask | Button3Mask in an event's state
# Alt+F, Alt+E (the title bar's menus) and Alt or F10 alone: without a menu bar Windows would
# beep, or enter an invisible menu mode that swallows the next key. Alt+Space still opens the
# window menu.
_SWALLOWED_MENU_KEYS = frozenset((0, ord("f"), ord("F"), ord("e"), ord("E")))


def _colorref(color: str) -> int:
    red, green, blue = ui_theme.rgb(color)
    return red | green << 8 | blue << 16


def window_tag(top) -> str:
    """A bind tag that only the toplevel ``top`` itself carries (first in its bind tags).

    A binding on the toplevel's own path tag also runs for every child widget's event (each
    child carries that tag), so ``<Configure>``, ``<Map>`` or ``<Activate>`` there runs Python
    for every widget in the window. A binding on this tag runs for the window's own events only.
    """
    tag = "DollyWindow" + str(top)
    tags = top.bindtags()
    if tag not in tags:
        top.bindtags((tag,) + tuple(tags))
    return tag


def unbind_tag(widget, tag: str, sequence: str, funcid: str) -> None:
    """Remove one handler (``funcid``, from ``bind_class``) from ``tag``'s ``sequence``, keeping the others.

    (Python 3.12's ``unbind`` with a funcid drops every handler of the sequence, and
    ``unbind_class`` takes no funcid.)
    """
    try:
        script = str(widget.tk.call("bind", tag, sequence))
        keep = "\n".join(line for line in script.split("\n") if funcid not in line)
        widget.tk.call("bind", tag, sequence, keep if keep.strip() else "")
        widget.deletecommand(funcid)
    except tk.TclError:
        pass


def maximized_rect(work, screen, autohide) -> tuple[int, int, int, int]:
    """(x, y, width, height) a maximized frameless window covers on one monitor.

    ``work`` and ``screen``: that monitor's work area and whole rect as (left, top, right,
    bottom); ``autohide(edge)``: whether an auto-hidden taskbar sits on that edge of *this*
    monitor (0 left, 1 top, 2 right, 3 bottom). Such a taskbar only slides out if the window
    leaves it a pixel to sense the pointer, so only that edge gives up one pixel.
    """
    left, top, right, bottom = work
    screen_left, screen_top, screen_right, screen_bottom = screen
    if left == screen_left and autohide(_ABE_LEFT):
        left += 1
    if top == screen_top and autohide(_ABE_TOP):
        top += 1
    if right == screen_right and autohide(_ABE_RIGHT):
        right -= 1
    if bottom == screen_bottom and autohide(_ABE_BOTTOM):
        bottom -= 1
    return left, top, right - left, bottom - top


class _Win32:
    """The handful of user32/dwmapi calls the chrome needs, with 64-bit-safe signatures."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32 = user32
        self.LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(self.LRESULT, wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM)
        for name, args, res in (
                ("GetWindowLongPtrW", (wintypes.HWND, ctypes.c_int), ctypes.c_ssize_t),
                ("SetWindowLongPtrW", (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t), ctypes.c_ssize_t),
                ("CallWindowProcW", (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM), self.LRESULT),
                ("SetWindowPos", (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT), wintypes.BOOL),
                ("ShowWindow", (wintypes.HWND, ctypes.c_int), wintypes.BOOL),
                ("IsZoomed", (wintypes.HWND,), wintypes.BOOL),
                ("ReleaseCapture", (), wintypes.BOOL),
                ("PostMessageW", (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM), wintypes.BOOL),
                ("MonitorFromWindow", (wintypes.HWND, wintypes.DWORD), wintypes.HMONITOR),
                ("MonitorFromRect", (ctypes.POINTER(wintypes.RECT), wintypes.DWORD), wintypes.HMONITOR),
                ("GetMonitorInfoW", (wintypes.HMONITOR, ctypes.c_void_p), wintypes.BOOL),
                ("GetCursorPos", (ctypes.POINTER(wintypes.POINT),), wintypes.BOOL)):
            function = getattr(user32, name)
            function.argtypes, function.restype = args, res
        self.dwm = ctypes.WinDLL("dwmapi")
        self.dwm.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        self.dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        class WINDOWPOS(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                        ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]

        class APPBARDATA(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                        ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

        self.MONITORINFO, self.WINDOWPOS, self.APPBARDATA = MONITORINFO, WINDOWPOS, APPBARDATA
        self.shell32 = ctypes.WinDLL("shell32")
        self.shell32.SHAppBarMessage.argtypes = (wintypes.DWORD, ctypes.c_void_p)
        self.shell32.SHAppBarMessage.restype = ctypes.c_size_t

    def hwnd(self, value: int):
        return self.wintypes.HWND(value)

    def maximized_area(self, hwnd, rect=None):
        """(x, y, width, height) a maximized window should cover: the work area of its monitor.

        ``rect``: the proposed (x, y, width, height) when the window is moving (Win+Shift+arrow,
        maximizing onto another monitor): the monitor is the one that rect is on, not the one the
        window is leaving. None: the window's current monitor.
        """
        info = self.MONITORINFO()
        info.cbSize = self.ctypes.sizeof(info)
        if rect is not None:
            x, y, width, height = rect
            monitor = self.user32.MonitorFromRect(self.wintypes.RECT(x, y, x + width, y + height), 2)
        else:
            monitor = self.user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor or not self.user32.GetMonitorInfoW(monitor, self.ctypes.addressof(info)):
            return None
        work, screen = info.rcWork, info.rcMonitor
        screen = (screen.left, screen.top, screen.right, screen.bottom)
        return maximized_rect((work.left, work.top, work.right, work.bottom), screen,
                              lambda edge: self.autohide_bar(edge, screen))

    def autohide_bar(self, edge: int, screen) -> bool:
        """Whether an auto-hidden taskbar sits on ``edge`` of the monitor whose rect is ``screen``."""
        bar = self.APPBARDATA()
        bar.cbSize = self.ctypes.sizeof(bar)
        bar.uEdge = edge
        bar.rc.left, bar.rc.top, bar.rc.right, bar.rc.bottom = screen
        return bool(self.shell32.SHAppBarMessage(_ABM_GETAUTOHIDEBAREX, self.ctypes.addressof(bar)))

    def set_dwm(self, hwnd, attribute: int, value: int) -> bool:
        data = self.ctypes.c_int(value)
        return self.dwm.DwmSetWindowAttribute(hwnd, attribute, self.ctypes.byref(data),
                                              self.ctypes.sizeof(data)) == 0

    def cursor(self):
        point = self.wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(point))
        return point.x, point.y

    def window_proc(self, function):
        """A WNDPROC callback for ``function`` and its address."""
        proc = self.WNDPROC(function)
        return proc, self.ctypes.cast(proc, self.ctypes.c_void_p).value


class WindowChrome:
    """Frameless-but-native handling of one Tk root.

    ``prepare()`` before the window first maps, ``finish()`` on its first ``<Map>``. ``api``
    replaces the Win32 calls (tests). ``frameless``: Tk sizes the window without a frame (the
    client area is the whole window); ``active``: the native styles are back and the window
    procedure is hooked, so moves, resizes and caption commands go through Windows.
    """

    def __init__(self, root, *, api=None):
        self.root = root
        self.api = api
        self.hwnd = None
        self.frameless = False
        self.sizing = False          # True while Windows runs its move/size loop (a flag only)
        self.native_border = False
        self.skipped = None          # why prepare() left the native frame, if it did
        self._proc = None            # keep the callback alive for as long as the window exists
        self._old_proc = None
        # :func:`release` finds it here when the app fails or stops before it could clean up.
        root._dolly_window_chrome = self

    # -- setup ---------------------------------------------------------------------------------

    def skip_reason(self) -> str | None:
        """Why this window keeps its native frame, or None when the custom chrome may go on."""
        if os.environ.get(ENV, "1").strip() == "0":
            return "disabled"
        if sys.platform != "win32" and self.api is None:
            return "platform"
        root = self.root
        try:
            if root.tk.getboolean(root.tk.call("wm", "attributes", root._w, "-toolwindow")):
                return "quiet"              # a hidden test or capture window: never restyled
            if str(root.wm_state()) == "withdrawn":
                return "withdrawn"          # tests and the hidden bundle self-test
            if root.winfo_ismapped() or int(root.wm_frame(), 16) != root.winfo_id():
                return "mapped"             # override-redirect now would re-create the window
        except (tk.TclError, ValueError):
            return "unavailable"
        return None

    def prepare(self) -> bool:
        """Call before the window first maps. False (nothing changed) when it keeps its frame."""
        self.skipped = self.skip_reason()
        if self.skipped is not None:
            return False
        if self.api is None:
            try:
                self.api = _Win32()
            except (OSError, AttributeError, ImportError):
                self.skipped = "unavailable"
                return False
        try:
            self.root.overrideredirect(True)
            self.root.attributes("-alpha", 0.0)   # unseen until the frame styles are back
        except tk.TclError:
            self.skipped = "unavailable"
            self._release()
            return False
        self.frameless = True
        return True

    def finish(self) -> bool:
        """Once mapped: restore the frame styles, hook the window procedure, show the window.

        False when the window keeps (or got back) the native frame; ``frameless`` is then False.
        """
        if self.active:
            return True
        if not self.frameless:
            return False
        api, root = self.api, self.root
        user32 = api.user32
        hidden = done = False
        try:
            hwnd = api.hwnd(int(root.wm_frame(), 16))
            # Hidden while restyled: the taskbar only notices the new style when the window shows
            # again. Through Tk, never ShowWindow from here: Tk delivers the toplevel's <Unmap> and
            # <Map> while the window hides and shows, and Python bindings (the boil clock's) run
            # then. Inside a ctypes call (the GIL released, outside Tcl) that aborts Python;
            # inside Tk's own command it is an ordinary event.
            root.withdraw()
            hidden = True
            # Tk rewrites the extended style when alpha changes, so finish with alpha before restyling.
            root.attributes("-alpha", 1.0)
            style = user32.GetWindowLongPtrW(hwnd, _GWL_STYLE)
            style |= _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_THICKFRAME | _WS_MAXIMIZEBOX
            ex_style = user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
            ex_style = (ex_style & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            old = user32.GetWindowLongPtrW(hwnd, _GWLP_WNDPROC)
            if not old:
                raise OSError("no window procedure")
            self._old_proc = old & 0xFFFFFFFFFFFFFFFF
            self._proc, address = api.window_proc(self._window_proc)
            user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, style)
            user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex_style)
            user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, address)
            self.hwnd = hwnd
            # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED (hidden: Tk reports nothing)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            api.set_dwm(hwnd, 20, 1)          # dark system menu and shadow
            api.set_dwm(hwnd, 33, 2)          # Windows 11: rounded corners
            # Windows 11: a 1 px outline round the window (Windows 10 has none; the bars' own
            # lines still separate it from the desktop). The minimize/maximize animations stay on.
            self.native_border = api.set_dwm(hwnd, 34, _colorref(ui_theme.TOKENS[BORDER_TOKEN]))
            root.deiconify()
            hidden = False
            done = True
        except Exception:  # noqa: BLE001 - whatever failed, the window gets its native frame back (below)
            LOG.exception("Dolly's own window frame failed; the window keeps the Windows frame")
        finally:
            # Also on an error nobody foresaw (even KeyboardInterrupt): never leave Dolly running
            # withdrawn, transparent or half hooked, with no window and no taskbar button.
            if not done:
                self.fall_back(show=hidden)
        if not done:
            return False
        try:
            root.focus_force()   # Tk shows an override-redirect window inactive; the app was just opened
        except tk.TclError:
            pass
        return True

    def fall_back(self, *, show: bool = False) -> None:
        """Give the window the native Windows frame back, opaque (``show``: also deiconify it).

        For a failed :meth:`finish`, and for an app that failed to build after :meth:`prepare`
        (:func:`release`).
        """
        self._undo()
        try:
            self.root.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        if show:
            try:
                self.root.deiconify()
            except tk.TclError:
                pass

    def detach(self) -> None:
        """Hand the window its own window procedure back (Win32 only, never Tk).

        After this Windows never calls into Python for the window again: for when Python stops
        running the window (the app has closed, failed, or the interpreter is shutting down).
        """
        hwnd, self.hwnd = self.hwnd, None
        if hwnd is not None and self._old_proc:
            try:
                self.api.user32.SetWindowLongPtrW(hwnd, _GWLP_WNDPROC, self._old_proc)
            except Exception:  # noqa: BLE001 - shutting down: nothing more to do
                pass

    def restyle(self) -> bool:
        """Put the native styles back after Tk rewrote them in place (see the module rules).

        False when there is nothing to restyle, or when Tk re-created the wrapper window (its new
        HWND has neither the styles nor the window procedure; this cannot repair that).
        """
        if not self.active:
            return False
        user32 = self.api.user32
        try:
            if int(self.root.wm_frame(), 16) != getattr(self.hwnd, "value", self.hwnd):
                return False
            style = user32.GetWindowLongPtrW(self.hwnd, _GWL_STYLE)
            style |= _WS_CAPTION | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_THICKFRAME | _WS_MAXIMIZEBOX
            ex_style = (user32.GetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE) & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            user32.SetWindowLongPtrW(self.hwnd, _GWL_STYLE, style)
            user32.SetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE, ex_style)
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        except (OSError, AttributeError, ValueError, TypeError, tk.TclError):
            return False
        return True

    def _undo(self) -> None:
        """Fall back to the normal Windows frame."""
        self.detach()
        self._release()

    def _release(self) -> None:
        self.frameless = False
        try:
            self.root.overrideredirect(False)
        except tk.TclError:
            pass

    def _window_proc(self, hwnd, msg, wparam, lparam):
        # Runs inside Windows' message dispatch, possibly while Tk itself is mid-call: it must not
        # call tkinter (that corrupts tkinter's interpreter-lock bookkeeping and aborts Python).
        api = self.api
        try:
            if msg == _WM_NCCALCSIZE:
                return 0   # no frame: the client area is the whole window (as Tk already assumes)
            if msg in (_WM_ENTERSIZEMOVE, _WM_EXITSIZEMOVE):
                self.sizing = msg == _WM_ENTERSIZEMOVE
            if msg == _WM_SYSCOMMAND and (wparam & 0xFFF0) == _SC_KEYMENU and lparam in _SWALLOWED_MENU_KEYS:
                return 0   # Tk's own Alt+F / Alt+E bindings open the title bar's menus
            # Not ported (yet) from RenderLock: its size-jump smear guard (WM_SETREDRAW off on the
            # content plus SWP_NOCOPYBITS here on maximize/restore/snap, a flat WM_ERASEBKGND, and
            # repaint() from the root <Configure> once Tk has laid out; agr/gui_chrome.py
            # _hide_content/repaint/check_hidden). Kara's one-time visual check (USER-ADDITIONS
            # UPDATE 20) decides: port it if maximize, restore or snap smear the bars.
            if msg == _WM_WINDOWPOSCHANGING and api.user32.IsZoomed(hwnd):
                pos = api.WINDOWPOS.from_address(lparam)
                if not pos.flags & _SWP_NOSIZE:
                    # A maximized framed window hangs over the screen edges by its (here invisible)
                    # frame; keep it on the work area instead, so the bars are not cut off. The
                    # monitor is the one the window is going to (Win+Shift+arrow, maximizing onto
                    # another monitor); only a resize in place keeps the one it is on.
                    moving = not pos.flags & _SWP_NOMOVE
                    area = api.maximized_area(hwnd, (pos.x, pos.y, pos.cx, pos.cy) if moving else None)
                    if area:
                        pos.x, pos.y, pos.cx, pos.cy = area
                        pos.flags &= ~_SWP_NOMOVE
            if msg == _WM_NCDESTROY:
                # The window's last message: give it its own procedure back first, so Windows never
                # calls this callback again (or calls it into a Python that is shutting down).
                self.detach()
        except Exception:  # noqa: BLE001 - never let a Python error break the window's messages
            pass
        return api.user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    # -- actions -------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.hwnd is not None

    def zoomed(self) -> bool:
        """Whether the window is maximized."""
        if self.active:
            try:
                return bool(self.api.user32.IsZoomed(self.hwnd))
            except (OSError, AttributeError):
                return False
        try:
            return str(self.root.wm_state()) == "zoomed"
        except tk.TclError:
            return False

    def _native(self, hit: str) -> None:
        """Hand the current mouse press to Windows as a press on the frame (move or resize loop).

        Posted, not sent: Windows' drag loop must start from Tk's own event loop. Started from
        inside a Tk event handler, any Tk callback during the drag would abort Python.
        """
        api = self.api
        x, y = api.cursor()
        api.user32.ReleaseCapture()
        api.user32.PostMessageW(self.hwnd, _WM_NCLBUTTONDOWN, _HT[hit], (x & 0xFFFF) | ((y & 0xFFFF) << 16))

    def start_move(self) -> bool:
        """Start Windows' move loop for the press under way (snap, drag-from-maximized). False off Windows."""
        if not self.active:
            return False
        self._native("caption")
        return True

    def start_resize(self, edge: str) -> bool:
        if not self.active:
            return False
        self._native(edge)
        return True

    def minimize(self) -> None:
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, _WM_SYSCOMMAND, _SC_MINIMIZE, 0)
            return
        try:
            self.root.iconify()
        except tk.TclError:
            pass

    def toggle_maximize(self) -> None:
        if self.active:
            command = _SC_RESTORE if self.zoomed() else _SC_MAXIMIZE
            self.api.user32.PostMessageW(self.hwnd, _WM_SYSCOMMAND, command, 0)
            return
        try:
            self.root.state("normal" if self.zoomed() else "zoomed")
        except tk.TclError:
            pass

    def restore(self) -> None:
        """Bring the window back from the taskbar, activated as a click on its taskbar button
        does, and maximized again if it was maximized.

        (``root.deiconify()`` would show the frameless window without activating it or giving it
        the keyboard focus: Tk still treats it as override-redirect.)
        """
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, _WM_SYSCOMMAND, _SC_RESTORE, 0)
            return
        try:
            self.root.deiconify()
        except tk.TclError:
            pass

    def system_menu(self, x_root: int, y_root: int) -> None:
        """The window menu (Restore, Move, Size, Minimize, Maximize, Close) at the pointer.

        Posted to Windows (WM_POPUPSYSTEMMENU), which enables the right items; its modal loop,
        like the drag loop, must not start inside a Tk event handler.
        """
        if self.active:
            self.api.user32.PostMessageW(self.hwnd, _WM_POPUPSYSTEMMENU, 0,
                                         (x_root & 0xFFFF) | ((y_root & 0xFFFF) << 16))


def release(root) -> None:
    """Dolly has stopped running ``root``: it closed, failed to start, or was interrupted.

    The window procedure is handed back, so Windows never calls into a Python that is shutting
    down. A window that is still there (the app failed to build after
    :meth:`WindowChrome.prepare`, perhaps before :meth:`WindowChrome.finish` showed it) gets its
    native frame back, opaque and shown, instead of staying invisible behind the error message.
    """
    chrome = getattr(root, "_dolly_window_chrome", None)
    if not isinstance(chrome, WindowChrome):
        return
    try:
        alive = bool(root.winfo_exists())
    except tk.TclError:
        alive = False
    if alive and chrome.frameless:
        chrome.fall_back(show=True)
    chrome.detach()


class EdgeResizer:
    """Resize zones along the inside of the window edges (the frameless window has no others).

    Every widget of the window gets a bind tag in front of its own (those built later when they
    first map), so a press in a zone starts Windows' resize loop and stops there, before the
    widget under the pointer sees it; the release Tk sends once that loop ends is swallowed too.
    Only while the chrome is active and the window is not maximized, and never while a mouse
    button is held: a drag that reaches or leaves the window edge (a slider, a scrollbar, a curve
    handle) stays the widget's.
    """

    TAG = "DollyWindowEdge"

    def __init__(self, root, chrome: WindowChrome):
        self.root = root
        self.chrome = chrome
        self.cursors: dict = {}
        self.scale = ui_scale(root)
        self._box = None                 # the window's (root x, root y, width, height), until it changes
        self._swallow_release = False    # a press started Windows' resize loop: its release is not the widget's
        root.bind_class(self.TAG, "<Motion>", self._on_motion)
        root.bind_class(self.TAG, "<Leave>", self._on_leave)
        root.bind_class(self.TAG, "<ButtonPress-1>", self._on_press)
        root.bind_class(self.TAG, "<ButtonRelease-1>", self._on_release)
        root.bind_class(window_tag(root), "<Configure>", self._forget_box, add="+")
        # Widgets built later (the update notice, rebuilt rows) join when they first map. Plain Tcl
        # on the toplevel's tag, which every child's <Map> passes: no Python per event.
        tag, path = self.TAG, str(root)
        root.bind("<Map>", "+if {[winfo exists %W] && [winfo toplevel %W] eq {" + path + "}"
                  " && [lsearch -exact [bindtags %W] " + tag + "] < 0} {bindtags %W [linsert [bindtags %W] 0 " + tag + "]}")

    def attach(self, widget=None) -> None:
        widget = widget or self.root
        tags = widget.bindtags()
        if self.TAG not in tags:
            widget.bindtags((self.TAG,) + tuple(tags))
        for child in widget.winfo_children():
            if child.winfo_toplevel() is self.root:
                self.attach(child)

    def _forget_box(self, _event=None) -> None:
        self._box = None

    def _window_box(self):
        box = self._box
        if box is None:
            root = self.root
            box = self._box = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height())
        return box

    def edge(self, x: int, y: int) -> str | None:
        """The zone at window-relative (x, y), or None (also for a point outside the window)."""
        if not self.chrome.active:
            return None
        _x0, _y0, w, h = self._window_box()
        if not (0 <= x < w and 0 <= y < h):
            return None
        scale = self.scale
        edge, corner = max(3, px(scale, EDGE)), px(scale, CORNER)

        def near(value, size, reach):
            return value < reach, value >= size - reach
        left, right = near(x, w, edge)
        top, bottom = near(y, h, edge)
        if not (left or right or top or bottom) or self.chrome.zoomed():
            return None
        left_c, right_c = near(x, w, corner)
        top_c, bottom_c = near(y, h, corner)
        if (top and left_c) or (left and top_c):
            return "nw"
        if (top and right_c) or (right and top_c):
            return "ne"
        if (bottom and left_c) or (left and bottom_c):
            return "sw"
        if (bottom and right_c) or (right and bottom_c):
            return "se"
        return "n" if top else "s" if bottom else "w" if left else "e" if right else None

    def _event_edge(self, event):
        try:
            x0, y0, _w, _h = self._window_box()
            return self.edge(event.x_root - x0, event.y_root - y0)
        except tk.TclError:
            return None

    def _on_motion(self, event):
        if event.state & _BUTTONS_HELD:
            return None   # a drag under way: the widget's own <B1-Motion>, never a resize cursor
        widget, edge = event.widget, self._event_edge(event)
        try:
            if edge:
                if widget not in self.cursors:
                    drop = getattr(widget, "drop_hover", None)
                    if drop is not None:
                        drop()   # e.g. a title bar button: no hover look under a resize cursor
                    self.cursors[widget] = widget.cget("cursor")
                widget.configure(cursor=_CURSORS[edge])
                return "break"   # the widget's own hover handling must not replace the resize cursor
            if widget in self.cursors:
                widget.configure(cursor=self.cursors.pop(widget))
        except (tk.TclError, AttributeError):   # a widget gone, or one only Tcl knows (a path string)
            self.cursors.pop(widget, None)
        return None

    def _on_leave(self, event):
        widget = event.widget
        if widget in self.cursors:
            try:
                widget.configure(cursor=self.cursors.pop(widget))
            except (tk.TclError, AttributeError):
                self.cursors.pop(widget, None)

    def _on_press(self, event):
        edge = self._event_edge(event)
        self._swallow_release = edge is not None
        if edge is None:
            return None
        self._on_leave(event)
        self.chrome.start_resize(edge)
        return "break"

    def _on_release(self, _event):
        if self._swallow_release:
            # Tk sends the release once Windows' resize loop ends; the widget never saw the press,
            # so a control that acts on release must not act on it.
            self._swallow_release = False
            return "break"
        return None
