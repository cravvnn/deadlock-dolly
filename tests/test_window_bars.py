"""Dolly's own title bar and link bar, and the frameless-window chrome (USER-ADDITIONS UPDATE 20)."""
import ctypes
import os
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from dolly import ui_theme
from dolly.ui import fonts, window_bars, window_chrome
from dolly.ui.window_bars import BUTTONS, GLYPHS, LinkBar, TitleBar

TOKENS = ui_theme.TOKENS
WIDTH = 800


def _rgb(value):
    """A PhotoImage pixel (tuple or "r g b" string) as an (r, g, b) tuple."""
    if isinstance(value, str):
        value = value.split()
    return tuple(int(v) for v in value)


def _over(base, ink, alpha):
    """``ink`` at ``alpha`` over ``base`` (both "#rrggbb")."""
    mixed = (b + (i - b) * alpha for b, i in zip(ui_theme.rgb(base), ui_theme.rgb(ink)))
    return "#" + "".join(f"{round(c):02x}" for c in mixed)


class BarCase(unittest.TestCase):
    """A real (test-harness hidden) window at 100 % with one bar packed across it."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.scaling = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", 96 / 72)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        self.addCleanup(self.close)
        patch.dict(os.environ, {"DOLLY_UI_MOTION": "0"}).start()
        self.addCleanup(patch.stopall)

    def close(self):
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()

    def show(self, bar):
        bar.pack(fill="x", side="top")
        self.root.geometry(f"{WIDTH}x100")
        self.root.deiconify()
        self.root.update()
        self.root.update_idletasks()

    def press(self, widget, x, y, *, release=True, release_at=None):
        widget.event_generate("<Motion>", x=x, y=y)
        widget.event_generate("<ButtonPress-1>", x=x, y=y)
        if release:
            rx, ry = release_at or (x, y)
            widget.event_generate("<ButtonRelease-1>", x=rx, y=ry)
        self.root.update()


class TitleBarTests(BarCase):
    def setUp(self):
        super().setUp()
        self.chrome = Mock()
        self.chrome.zoomed.return_value = False
        self.on_close = Mock()
        self.file_menu = tk.Menu(self.root, tearoff=False)
        self.file_menu.add_command(label="New shot")
        self.edit_menu = tk.Menu(self.root, tearoff=False)
        self.edit_menu.add_command(label="Undo shot edit")
        self.posted = []
        for name, menu in (("File", self.file_menu), ("Edit", self.edit_menu)):
            menu.tk_popup = Mock(side_effect=lambda x, y, name=name: self.posted.append(
                (name, x, y, self.bar.opened, self.plate_state(name))))
        self.bar = TitleBar(self.root, scale=1.0, chrome=self.chrome,
                            menus=(("File", self.file_menu), ("Edit", self.edit_menu)), on_close=self.on_close)
        self.show(self.bar)

    def plate_state(self, name):
        return self.bar.itemcget(self.bar._menu_plates[name].item, "state")

    def button_centre(self, name):
        left, right = self.bar.button_boxes[name]
        return (left + right) // 2, 12

    def test_bar_is_29_px_of_figma_gradient_with_its_fading_bottom_line_and_takes_no_focus(self):
        bar = self.bar
        self.assertEqual(bar.winfo_height(), 29)
        self.assertEqual(str(bar.cget("takefocus")), "0")
        image = bar.background
        self.assertEqual((image.width(), image.height()), (WIDTH, 29))
        left, right = ui_theme.rgb(TOKENS["titlebar_left"]), ui_theme.rgb(TOKENS["titlebar_right"])
        for got, want in ((_rgb(image.get(0, 0)), left), (_rgb(image.get(WIDTH - 1, 12)), right)):
            for a, b in zip(got, want):
                self.assertLessEqual(abs(a - b), 1)
        # The 1 px line is the bottom row: clear at the left end, #252525 at 15.38 %, clear again from 95.67 %.
        line = ui_theme.rgb(TOKENS["titlebar_line"])
        x = int(.1538 * WIDTH)
        for a, b in zip(_rgb(image.get(x, 28)), line):
            self.assertLessEqual(abs(a - b), 2)
        self.assertEqual(_rgb(image.get(0, 28)), _rgb(image.get(0, 27)))
        self.assertEqual(_rgb(image.get(WIDTH - 1, 28)), _rgb(image.get(WIDTH - 1, 27)))
        self.assertEqual(_rgb(image.get(x, 24)), _rgb(image.get(x, 0)), "the line is one row")

    def test_file_and_edit_sit_at_the_left_and_open_their_menus_under_their_names(self):
        bar = self.bar
        file_left, file_right = bar.menu_boxes["File"]
        edit_left, edit_right = bar.menu_boxes["Edit"]
        self.assertEqual(bar.coords(bar._labels["File"])[0], 15, "File lines up with the link bar's first icon")
        self.assertEqual(file_left, 15 - window_bars.MENU_PAD)
        self.assertEqual(edit_left, file_right, "the hover plates touch, as in a menu bar")
        self.assertEqual(bar.itemcget(bar._labels["File"], "font"), str(fonts.font(bar, "body")))
        self.assertEqual(bar.itemcget(bar._labels["File"], "fill"), TOKENS["muted"])
        self.press(bar, file_left + 10, 12)
        self.press(bar, edit_left + 10, 12)
        x0, y0 = bar.winfo_rootx(), bar.winfo_rooty()
        # Each menu is posted at its plate's left edge, under the bar, with the plate shown open.
        self.assertEqual(self.posted, [("File", x0 + file_left, y0 + 29, "File", "normal"),
                                       ("Edit", x0 + edit_left, y0 + 29, "Edit", "normal")])
        self.assertIsNone(bar.opened)
        self.chrome.start_move.assert_not_called()
        # Hovering a name lights it and tints the bar beneath it (white 6 % over the bar's blue
        # left end, not the grey the right end's tint would be); leaving the bar puts it back.
        bar.event_generate("<Motion>", x=file_left + 5, y=12)
        self.root.update()
        self.assertEqual(bar.itemcget(bar._labels["File"], "fill"), TOKENS["text"])
        plate = bar.itemcget(bar._menu_plates["File"].item, "fill")
        self.assertEqual(plate, _over(bar.fill_at((file_left + file_right) / 2), "#ffffff", .06))
        for got, want in zip(ui_theme.rgb(plate), (0x1e, 0x26, 0x34)):
            self.assertLessEqual(abs(got - want), 1, plate)
        bar.event_generate("<Leave>")
        self.root.update()
        self.assertEqual(bar.itemcget(bar._labels["File"], "fill"), TOKENS["muted"])
        self.assertEqual(self.plate_state("File"), "hidden")
        self.assertEqual(self.plate_state("Edit"), "hidden")

    def test_caption_buttons_sit_at_the_right_and_run_minimize_maximize_and_close(self):
        bar = self.bar
        width = window_bars.BUTTON_WIDTH
        self.assertEqual([bar.button_boxes[name] for name in BUTTONS],
                         [(WIDTH - 3 * width, WIDTH - 2 * width), (WIDTH - 2 * width, WIDTH - width), (WIDTH - width, WIDTH)])
        self.press(bar, *self.button_centre("minimize"))
        self.chrome.minimize.assert_called_once_with()
        self.press(bar, *self.button_centre("maximize"))
        self.chrome.toggle_maximize.assert_called_once_with()
        self.on_close.assert_not_called()
        self.press(bar, *self.button_centre("close"))
        self.on_close.assert_called_once_with()
        # Released somewhere else: nothing happens (as with Windows' own buttons).
        self.press(bar, *self.button_centre("close"), release_at=(400, 12))
        self.press(bar, *self.button_centre("minimize"), release_at=self.button_centre("close"))
        self.on_close.assert_called_once_with()
        self.chrome.minimize.assert_called_once_with()
        self.chrome.start_move.assert_not_called()

    def test_hover_tints_the_buttons_and_close_turns_red(self):
        bar = self.bar
        plates = bar._button_plates
        for name in BUTTONS:
            self.assertEqual(bar.itemcget(plates[name].item, "state"), "hidden")
        under = {name: bar.fill_at(self.button_centre(name)[0]) for name in BUTTONS}
        bar.event_generate("<Motion>", x=self.button_centre("maximize")[0], y=12)
        self.root.update()
        self.assertEqual(bar.itemcget(plates["maximize"].item, "fill"), _over(under["maximize"], "#ffffff", .06))
        self.assertEqual(bar.itemcget(plates["maximize"].item, "state"), "normal")
        self.assertEqual(bar.itemcget("glyph_maximize", "fill"), TOKENS["text"])
        bar.event_generate("<ButtonPress-1>", x=self.button_centre("maximize")[0], y=12)
        self.root.update()
        self.assertEqual(bar.itemcget(plates["maximize"].item, "fill"), _over(under["maximize"], "#ffffff", .04))
        bar.event_generate("<ButtonRelease-1>", x=WIDTH // 2, y=12)   # released elsewhere: nothing runs
        bar.event_generate("<Motion>", x=self.button_centre("close")[0], y=12)
        self.root.update()
        self.assertEqual(bar.itemcget(plates["maximize"].item, "state"), "hidden")
        self.assertEqual(bar.itemcget(plates["close"].item, "fill"), TOKENS["caption_close"])
        self.assertEqual(bar.itemcget("glyph_close", "fill"), TOKENS["text"])
        bar.event_generate("<ButtonPress-1>", x=self.button_centre("close")[0], y=12)
        self.root.update()
        self.assertEqual(bar.itemcget(plates["close"].item, "fill"), _over(under["close"], TOKENS["caption_close"], .9))
        bar.event_generate("<Leave>")
        self.root.update()
        self.assertEqual(bar.itemcget(plates["close"].item, "state"), "hidden")
        self.assertEqual(bar.itemcget("glyph_close", "fill"), TOKENS["muted"])
        # Another window active: the glyphs dim, as Windows draws an inactive caption.
        bar.set_window_active(False)
        self.assertEqual(bar.itemcget("glyph_minimize", "fill"), TOKENS["dim"])
        bar.set_window_active(True)
        self.assertEqual(bar.itemcget("glyph_minimize", "fill"), TOKENS["muted"])

    def test_maximize_glyph_turns_into_restore_while_the_window_is_maximized(self):
        bar = self.bar
        self.assertEqual(bar.glyphs["maximize"][0], "maximize")
        self.chrome.zoomed.return_value = True
        self.root.geometry(f"{WIDTH + 10}x100")   # a state change reaches the bar as the window's <Configure>
        self.root.update()
        self.assertEqual(bar.glyphs["maximize"][0], "restore")
        if bar._glyph_font is not None:
            self.assertEqual(bar.itemcget("glyph_maximize", "text"), GLYPHS["restore"])
            self.assertEqual(bar.itemcget("glyph_close", "text"), GLYPHS["close"])
        self.chrome.zoomed.return_value = False
        bar.update_glyphs()
        self.assertEqual(bar.glyphs["maximize"][0], "maximize")
        self.assertEqual(bar.button_boxes["close"], (WIDTH + 10 - window_bars.BUTTON_WIDTH, WIDTH + 10))
        # A maximize that leaves the window's rect as it was (a window already filling the work
        # area) sends no <Configure>: the bar checks again once Windows has run the command.
        self.chrome.toggle_maximize.side_effect = lambda: setattr(self.chrome.zoomed, "return_value", True)
        bar.invoke("maximize")
        self.assertEqual(bar.glyphs["maximize"][0], "maximize")
        self.root.after(window_bars.GLYPH_RECHECK_MS + 30)
        self.root.update()
        self.assertEqual(bar.glyphs["maximize"][0], "restore")
        self.chrome.toggle_maximize.side_effect = lambda: setattr(self.chrome.zoomed, "return_value", False)
        self.double_click(400, 12, 30000)
        self.root.after(window_bars.GLYPH_RECHECK_MS + 30)
        self.root.update()
        self.assertEqual(bar.glyphs["maximize"][0], "maximize")

    def test_window_state_bindings_run_for_the_window_only_not_for_every_child(self):
        bar = self.bar
        tag = window_chrome.window_tag(self.root)
        self.assertEqual(self.root.bindtags()[0], tag)
        self.assertEqual(self.root.bind("<Configure>"), "", "nothing on the toplevel's path tag")
        for sequence in ("<Configure>", "<Map>", "<Activate>", "<Deactivate>"):
            self.assertIn(sequence, self.root.bind_class(tag))
        child = tk.Frame(self.root, width=40, height=10)
        with patch.object(bar, "update_glyphs") as glyphs, patch.object(bar, "set_window_active") as active:
            child.pack(side="top")
            self.root.update()
            child.configure(width=60)
            self.root.update()
            child.event_generate("<Activate>")
            glyphs.assert_not_called()
            active.assert_not_called()
            self.root.geometry(f"{WIDTH + 4}x100")
            self.root.update()
            glyphs.assert_called()
            self.root.event_generate("<Deactivate>")
            active.assert_called_with(False)

    def double_click(self, x, y, start):
        """Two presses 100 ms apart: Tk runs <Double-Button-1> for the second one."""
        self.bar.event_generate("<ButtonPress-1>", x=x, y=y, time=start)
        self.bar.event_generate("<ButtonRelease-1>", x=x, y=y, time=start + 50)
        self.bar.event_generate("<ButtonPress-1>", x=x, y=y, time=start + 100)
        self.bar.event_generate("<ButtonRelease-1>", x=x, y=y, time=start + 150)
        self.root.update()

    def test_the_rest_of_the_bar_drags_the_window_and_double_click_maximizes(self):
        bar = self.bar
        self.press(bar, 400, 12)
        self.chrome.start_move.assert_called_once_with()
        self.double_click(400, 12, 10000)
        self.assertEqual(self.chrome.start_move.call_count, 2, "the first press of a double click drags")
        self.chrome.toggle_maximize.assert_called_once_with()
        bar.event_generate("<ButtonRelease-3>", x=400, y=12)
        self.root.update()
        self.chrome.system_menu.assert_called_once()
        x_root, y_root = self.chrome.system_menu.call_args.args
        self.assertEqual((x_root, y_root), (bar.winfo_rootx() + 400, bar.winfo_rooty() + 12))
        # Over a caption button: no drag, and a fast second click is another click on it.
        self.double_click(self.button_centre("minimize")[0], 12, 20000)
        self.assertEqual(self.chrome.minimize.call_count, 2)
        bar.event_generate("<ButtonRelease-3>", x=self.button_centre("close")[0], y=12)
        self.root.update()
        self.assertEqual(self.chrome.start_move.call_count, 2)
        self.chrome.toggle_maximize.assert_called_once_with()
        self.chrome.system_menu.assert_called_once()

    def test_beside_a_native_caption_the_bar_drops_its_own_buttons(self):
        bar = self.bar
        bar.set_caption_buttons(False)
        self.assertEqual(bar.button_boxes, {})
        self.assertEqual(bar.find_withtag("glyph"), ())
        self.press(bar, WIDTH - 10, 12)
        self.on_close.assert_not_called()
        self.chrome.start_move.assert_called_once_with()
        bar.set_caption_buttons(True)
        self.assertEqual(len(bar.button_boxes), 3)
        self.assertEqual(len(bar.find_withtag("glyph_close")) >= 1, True)

    def test_a_menu_that_fails_to_post_still_closes_its_open_look(self):
        self.file_menu.tk_popup = Mock(side_effect=tk.TclError("no menu"))
        with self.assertRaises(tk.TclError):
            self.bar.open_menu("File")
        self.assertIsNone(self.bar.opened)


class LinkBarTests(BarCase):
    def setUp(self):
        super().setUp()
        self.bar = LinkBar(self.root, scale=1.0)
        self.show(self.bar)
        self.browser = patch("dolly.ui.window_bars.webbrowser.open").start()

    def click(self, name):
        left, right = self.bar.boxes[name]
        self.press(self.bar, (left + right) // 2, 15)

    def test_bar_is_29_px_with_its_line_on_top_icons_divider_and_credit_where_figma_has_them(self):
        bar = self.bar
        self.assertEqual(bar.winfo_height(), 29)
        self.assertEqual(str(bar.cget("takefocus")), "0")
        image = bar.background
        line = ui_theme.rgb(TOKENS["linkbar_line"])
        x = int(.7115 * WIDTH)
        for a, b in zip(_rgb(image.get(x, 0)), line):
            self.assertLessEqual(abs(a - b), 2)
        for a, b in zip(_rgb(image.get(0, 14)), ui_theme.rgb(TOKENS["linkbar_left"])):
            self.assertLessEqual(abs(a - b), 1)
        self.assertEqual(bar.coords(bar.icons["youtube"]), [15.0, 6.0])
        self.assertEqual(bar.coords(bar.icons["discord"]), [43.0, 6.0])
        self.assertEqual(bar.coords(bar.divider), [71.0, 8.0, 72.0, 22.0])
        self.assertEqual(bar.itemcget(bar.divider, "fill"), TOKENS["linkbar_ink"])
        self.assertEqual("".join(bar.itemcget(item, "text") for item in bar.credit_items),
                         "Dolly by Cravvnn · UX/UI by Kara")
        # Inter's line on Figma's middle (15); the Segoe UI fallback a pixel up, its glyphs where Inter's sit.
        inter = fonts.family_available(bar, "small") == ui_theme.UI_FACE
        self.assertEqual(bar.coords(bar.credit), [81.0, 15.0 if inter else 14.0])
        self.assertEqual(bar.itemcget(bar.credit, "anchor"), "w")
        self.assertEqual(bar.itemcget(bar.credit, "fill"), TOKENS["linkbar_ink"])
        self.assertEqual(bar.itemcget(bar.credit, "font"), str(fonts.font(bar, "small")))

    def test_discord_opens_the_server_in_the_browser(self):
        self.click("discord")
        self.browser.assert_called_once_with("https://discord.gg/XTefeBUqX5", new=2)

    def test_youtube_opens_cravvnns_channel_and_an_empty_url_does_nothing(self):
        self.click("youtube")
        self.browser.assert_called_once_with("https://www.youtube.com/@Cravvnn", new=2)
        with patch.object(window_bars, "YOUTUBE_URL", ""):
            self.assertFalse(self.bar.open_link("youtube"))
        self.browser.assert_called_once()

    def test_the_credit_names_are_links(self):
        self.click("cravvnn")
        self.click("kara")
        self.assertEqual([call.args[0] for call in self.browser.call_args_list],
                         ["https://github.com/cravvnn", "https://www.youtube.com/@KaraMongus"])
        name = self.bar.credit_items[1]
        self.assertEqual(self.bar.itemcget(name, "text"), "Cravvnn")
        left, right = self.bar.boxes["cravvnn"]
        self.assertEqual(self.bar.link_at((left + right) / 2, 14), "cravvnn")
        self.assertIsNone(self.bar.link_at(self.bar.coords(self.bar.credit)[0] + 2, 14), "'Dolly by' is plain text")

    def test_a_browser_failure_is_not_an_error(self):
        import webbrowser
        self.browser.side_effect = webbrowser.Error("no browser")
        self.click("discord")
        self.browser.assert_called_once()

    def test_icons_brighten_on_hover_like_the_footer_links(self):
        bar = self.bar
        ramp = bar.ramps["discord"]
        rest = bar.itemcget(bar.icons["discord"], "image")
        left, right = bar.boxes["discord"]
        bar.event_generate("<Motion>", x=(left + right) // 2, y=15)
        self.root.update()
        self.assertEqual(ramp.colour, TOKENS["muted"])
        self.assertNotEqual(bar.itemcget(bar.icons["discord"], "image"), rest)
        self.assertEqual(str(bar.cget("cursor")), "hand2")
        bar.event_generate("<ButtonPress-1>", x=(left + right) // 2, y=15)
        self.root.update()
        self.assertEqual(ramp.colour, TOKENS["text"])
        bar.event_generate("<Leave>")
        self.root.update()
        self.assertEqual(ramp.colour, TOKENS["linkbar_ink"])
        self.assertEqual(bar.itemcget(bar.icons["discord"], "image"), rest)
        self.assertEqual(str(bar.cget("cursor")), "")
        # A press beside the icons (the divider, the credit) does nothing.
        self.press(bar, 200, 15)
        self.browser.assert_not_called()

    def test_icons_are_figma_vectors_with_even_odd_holes(self):
        youtube = window_bars.icon_coverage("youtube", 18)
        discord = window_bars.icon_coverage("discord", 18)
        # YouTube: the play triangle is a hole in the rounded body.
        self.assertGreater(youtube[8][3], .95)
        self.assertLess(youtube[8][8], .05)
        self.assertLess(youtube[1][9], .05)   # above the body (it spans y 2.7 .. 15.3)
        # Discord: the eyes are holes in the head.
        self.assertGreater(discord[6][9], .95)
        self.assertLess(discord[9][6], .05)
        self.assertLess(discord[9][12], .05)
        image = window_bars.icon_image(self.root, "discord", 18, TOKENS["linkbar_ink"])
        self.assertEqual((image.width(), image.height()), (18, 18))


class ScaledBarsTests(BarCase):
    def test_bars_scale_per_bar_and_add_up_to_bars_px(self):
        self.root.tk.call("tk", "scaling", 1.5 * 96 / 72)
        self.root._dolly_ui_scale = 1.5
        top = TitleBar(self.root, scale=1.5, chrome=Mock(**{"zoomed.return_value": False}), menus=(), on_close=Mock())
        bottom = LinkBar(self.root, scale=1.5)
        self.assertEqual((int(top.cget("height")), int(bottom.cget("height"))), (44, 44))
        self.assertEqual(window_bars.bars_px(1.5), 88)
        self.assertEqual(window_bars.bars_px(1.25), 36 + 36)
        self.assertEqual(window_bars.bars_px(1.0), 58)
        self.assertEqual(top.button_width, 63)
        self.assertEqual(bottom.coords(bottom.icons["discord"]), [65.0, 9.0])
        # Figma's 1 px stroke centred on 71 covers 105.75 .. 107.25 at 150 %: column 106 is the
        # one it covers fully (column 71 at 100 %).
        self.assertEqual(bottom.coords(bottom.divider), [106.0, 12.0, 107.0, 33.0])


# -- the frameless window ------------------------------------------------------------------------

_STYLE_BITS = (window_chrome._WS_CAPTION | window_chrome._WS_SYSMENU | window_chrome._WS_MINIMIZEBOX
               | window_chrome._WS_THICKFRAME | window_chrome._WS_MAXIMIZEBOX)


class FakeUser32:
    def __init__(self):
        self.calls = []
        self.longs = {window_chrome._GWL_STYLE: 0x96000000,      # override-redirect: WS_POPUP | visible
                      window_chrome._GWL_EXSTYLE: 0x00080080,    # layered | toolwindow
                      window_chrome._GWLP_WNDPROC: 0x1234}
        self.zoomed = False
        self.fail_on = None

    def _record(self, *entry):
        if self.fail_on == entry[0]:
            raise OSError(entry[0])
        self.calls.append(entry)

    def GetWindowLongPtrW(self, hwnd, index):
        return self.longs[index]

    def SetWindowLongPtrW(self, hwnd, index, value):
        self._record("SetWindowLongPtrW", index, value)
        self.longs[index] = value
        return 0

    def ShowWindow(self, hwnd, command):
        self._record("ShowWindow", command)

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self._record("SetWindowPos", flags)

    def IsZoomed(self, hwnd):
        return self.zoomed

    def ReleaseCapture(self):
        self._record("ReleaseCapture")

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self._record("PostMessageW", message, wparam, lparam)
        return True

    def SendMessageW(self, *args):   # never used: modal loops are posted, not sent
        self._record("SendMessageW", *args)

    def CallWindowProcW(self, proc, hwnd, message, wparam, lparam):
        self._record("CallWindowProcW", proc, message)
        return 7


class FakeApi:
    WINDOWPOS = window_chrome._Win32.__init__ and None   # replaced per test when needed

    def __init__(self):
        self.user32 = FakeUser32()
        self.dwm = []
        self.areas = []
        self.dwm_error = None

    def hwnd(self, value):
        return value

    def set_dwm(self, hwnd, attribute, value):
        if self.dwm_error is not None:
            raise self.dwm_error
        self.dwm.append((attribute, value))
        return True

    def cursor(self):
        return 300, 40

    def window_proc(self, function):
        return function, 0xABC0

    # Two monitors side by side: 1920 x 1080 (taskbar 40 px) and, to its right, 2560 x 1440.
    def maximized_area(self, hwnd, rect=None):
        self.areas.append(rect)
        if rect is not None and rect[0] + rect[2] // 2 >= 1920:
            return 1920, 0, 2560, 1440
        return 0, 0, 1920, 1040


def fake_root(*, toolwindow=False, state="normal", mapped=False, frame=0x100, ident=0x100):
    root = Mock()
    root._w = "."
    root.tk.call.return_value = int(toolwindow)
    root.tk.getboolean.side_effect = bool
    root.wm_state.return_value = state
    root.winfo_ismapped.return_value = mapped
    root.wm_frame.return_value = hex(frame)
    root.winfo_id.return_value = ident
    return root


class WindowChromeTests(unittest.TestCase):
    def setUp(self):
        patch.dict(os.environ, {window_chrome.ENV: "1"}).start()
        self.addCleanup(patch.stopall)

    def prepared(self, **root):
        api = FakeApi()
        chrome = window_chrome.WindowChrome(fake_root(**root), api=api)
        return chrome, api

    def test_prepare_takes_the_frame_away_before_the_first_map(self):
        chrome, _api = self.prepared()
        self.assertTrue(chrome.prepare())
        self.assertTrue(chrome.frameless)
        self.assertIsNone(chrome.skipped)
        chrome.root.overrideredirect.assert_called_once_with(True)
        chrome.root.attributes.assert_called_once_with("-alpha", 0.0)
        self.assertFalse(chrome.active)

    def test_hidden_test_windows_and_mapped_windows_keep_their_native_frame(self):
        for options, reason in (({"toolwindow": True}, "quiet"), ({"state": "withdrawn"}, "withdrawn"),
                                ({"mapped": True}, "mapped"), ({"frame": 0x200}, "mapped")):
            with self.subTest(reason=reason):
                chrome, api = self.prepared(**options)
                self.assertFalse(chrome.prepare())
                self.assertEqual(chrome.skipped, reason)
                self.assertFalse(chrome.frameless)
                chrome.root.overrideredirect.assert_not_called()
                chrome.root.attributes.assert_not_called()
                self.assertFalse(chrome.finish())
                self.assertEqual(api.user32.calls, [])
        with patch.dict(os.environ, {window_chrome.ENV: "0"}):
            chrome, _api = self.prepared()
            self.assertFalse(chrome.prepare())
            self.assertEqual(chrome.skipped, "disabled")
        with patch.object(window_chrome.sys, "platform", "linux"):
            chrome = window_chrome.WindowChrome(fake_root())
            self.assertFalse(chrome.prepare())
            self.assertEqual(chrome.skipped, "platform")
            self.assertFalse(chrome.start_move())

    def test_the_app_prepares_the_frame_before_the_window_icon_creates_the_windows_frame(self):
        """Tk's ``wm iconbitmap`` creates the window's Windows frame, after which prepare() sees a
        mapped window and keeps the native caption: Kara got two title bars (UPDATE 20 fix)."""
        from dolly import gui

        class Stop(Exception):
            pass

        calls = []

        class Chrome:
            def __init__(self, root):
                pass

            def prepare(self):
                calls.append("prepare")

        with patch.object(gui.window_chrome, "WindowChrome", Chrome), \
                patch.object(gui, "apply_window_icon", lambda root: calls.append("icon")), \
                patch.object(gui, "ui_scale", side_effect=Stop):
            with self.assertRaises(Stop):
                gui.DollyApp(Mock())
        self.assertEqual(calls, ["prepare", "icon"])

    def test_a_real_hidden_test_window_is_never_restyled(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        try:
            root.withdraw()
            chrome = window_chrome.WindowChrome(root)
            self.assertFalse(chrome.prepare())
            self.assertIn(chrome.skipped, ("quiet", "withdrawn"))
            self.assertFalse(root.tk.getboolean(root.tk.call("wm", "overrideredirect", root._w)))
        finally:
            root.destroy()

    def test_finish_restores_the_native_styles_hooks_the_frame_and_shows_the_window(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.root.reset_mock()
        self.assertTrue(chrome.finish())
        self.assertTrue(chrome.active)
        user32 = api.user32
        style = user32.longs[window_chrome._GWL_STYLE]
        ex_style = user32.longs[window_chrome._GWL_EXSTYLE]
        self.assertEqual(style & _STYLE_BITS, _STYLE_BITS, "caption, system menu, min/max boxes and sizing frame")
        self.assertTrue(ex_style & window_chrome._WS_EX_APPWINDOW, "a taskbar button")
        self.assertFalse(ex_style & window_chrome._WS_EX_TOOLWINDOW)
        self.assertEqual(user32.longs[window_chrome._GWLP_WNDPROC], 0xABC0)
        # Hidden while restyled, then shown again so the taskbar notices: through Tk, which runs
        # the <Unmap>/<Map> bindings safely. A ctypes ShowWindow would run them inside the
        # foreign call and abort Python (found by the hidden probe).
        self.assertNotIn("ShowWindow", [entry[0] for entry in user32.calls])
        calls = [call[0] for call in chrome.root.method_calls
                 if call[0] in ("withdraw", "attributes", "deiconify", "focus_force")]
        self.assertEqual(calls, ["withdraw", "attributes", "deiconify", "focus_force"])
        chrome.root.attributes.assert_called_once_with("-alpha", 1.0)
        attributes = [attribute for attribute, _value in api.dwm]
        self.assertEqual(attributes, [20, 33, 34])
        self.assertNotIn(3, attributes, "the minimize and maximize animations stay on")
        self.assertEqual(dict(api.dwm)[34], window_chrome._colorref(TOKENS[window_chrome.BORDER_TOKEN]))
        self.assertTrue(chrome.finish(), "once only")
        chrome.root.deiconify.assert_called_once_with()

    def test_a_failed_finish_gives_the_window_its_native_frame_back(self):
        chrome, api = self.prepared()
        chrome.prepare()
        api.user32.fail_on = "SetWindowPos"
        with self.assertLogs("dolly.ui.window_chrome", "ERROR"):
            self.assertFalse(chrome.finish())
        self.assertFalse(chrome.active)
        self.assertFalse(chrome.frameless)
        chrome.root.overrideredirect.assert_called_with(False)
        chrome.root.deiconify.assert_called_once_with()
        self.assertEqual(api.user32.longs[window_chrome._GWLP_WNDPROC], 0x1234, "the window procedure is unhooked")

    def test_any_error_while_the_window_is_withdrawn_still_shows_it_with_its_native_frame(self):
        # Not only the errors anyone foresaw: Dolly must never keep running withdrawn and
        # transparent, with no window and no taskbar button.
        for error in (ctypes.ArgumentError("argument 2"), RuntimeError("boom"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                chrome, api = self.prepared()
                chrome.prepare()
                chrome.root.reset_mock()
                api.dwm_error = error
                if isinstance(error, Exception):
                    with self.assertLogs("dolly.ui.window_chrome", "ERROR"):
                        self.assertFalse(chrome.finish())
                else:
                    with self.assertRaises(KeyboardInterrupt):
                        chrome.finish()
                self.assertFalse(chrome.active)
                self.assertFalse(chrome.frameless)
                calls = [call[0] for call in chrome.root.method_calls if call[0] in ("withdraw", "deiconify")]
                self.assertEqual(calls, ["withdraw", "deiconify"])
                chrome.root.overrideredirect.assert_called_with(False)
                chrome.root.attributes.assert_called_with("-alpha", 1.0)
                self.assertEqual(api.user32.longs[window_chrome._GWLP_WNDPROC], 0x1234)

    def test_release_unhooks_and_brings_back_a_window_the_app_failed_to_build(self):
        # The app failed after prepare(), before finish(): the window is transparent and override-redirect.
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.root.winfo_exists.return_value = 1
        chrome.root.reset_mock()
        window_chrome.release(chrome.root)
        self.assertFalse(chrome.frameless)
        chrome.root.overrideredirect.assert_called_once_with(False)
        chrome.root.attributes.assert_called_once_with("-alpha", 1.0)
        chrome.root.deiconify.assert_called_once_with()
        # A hooked window whose app has stopped: Windows gets its own procedure back.
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()
        chrome.root.winfo_exists.side_effect = tk.TclError("application has been destroyed")
        window_chrome.release(chrome.root)
        self.assertFalse(chrome.active)
        self.assertEqual(api.user32.longs[window_chrome._GWLP_WNDPROC], 0x1234)
        # Anything else (a plain test object) is left alone.
        window_chrome.release(Mock())

    def test_the_window_procedure_unhooks_itself_when_the_window_is_destroyed(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()
        chrome.root.reset_mock()
        self.assertEqual(chrome._window_proc(1, window_chrome._WM_NCDESTROY, 0, 0), 7, "still forwarded")
        self.assertEqual(api.user32.longs[window_chrome._GWLP_WNDPROC], 0x1234, "its own procedure first")
        self.assertEqual(api.user32.calls[-1], ("CallWindowProcW", 0x1234, window_chrome._WM_NCDESTROY))
        self.assertFalse(chrome.active)
        self.assertEqual(chrome.root.method_calls, [], "Win32 only, never Tk")

    def test_restyle_repairs_styles_in_place_but_not_a_recreated_wrapper(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()
        api.user32.longs[window_chrome._GWL_STYLE] = 0x96000000
        self.assertTrue(chrome.restyle())
        self.assertEqual(api.user32.longs[window_chrome._GWL_STYLE] & _STYLE_BITS, _STYLE_BITS)
        chrome.root.wm_frame.return_value = hex(0x300)   # Tk built a new wrapper window
        api.user32.longs[window_chrome._GWL_STYLE] = 0x96000000
        self.assertFalse(chrome.restyle())
        self.assertEqual(api.user32.longs[window_chrome._GWL_STYLE], 0x96000000)

    def test_window_procedure_removes_the_frame_and_swallows_menu_keys_without_calling_tk(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()
        chrome.root.reset_mock()
        proc = chrome._window_proc
        self.assertEqual(proc(1, window_chrome._WM_NCCALCSIZE, 1, 0), 0)
        for key in (ord("f"), ord("E"), 0):
            self.assertEqual(proc(1, window_chrome._WM_SYSCOMMAND, window_chrome._SC_KEYMENU, key), 0)
        self.assertEqual(proc(1, window_chrome._WM_SYSCOMMAND, window_chrome._SC_KEYMENU, ord(" ")), 7,
                         "Alt+Space still opens the window menu")
        self.assertEqual(proc(1, window_chrome._WM_ENTERSIZEMOVE, 0, 0), 7)
        self.assertTrue(chrome.sizing)
        proc(1, window_chrome._WM_EXITSIZEMOVE, 0, 0)
        self.assertFalse(chrome.sizing)
        self.assertEqual(chrome.root.method_calls, [], "the window procedure never calls Tk")

    def test_a_maximized_window_is_kept_on_the_work_area(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()

        class WINDOWPOS(ctypes.Structure):
            _fields_ = [("hwnd", ctypes.c_void_p), ("hwndInsertAfter", ctypes.c_void_p), ("x", ctypes.c_int),
                        ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", ctypes.c_uint)]
        api.WINDOWPOS = WINDOWPOS
        pos = WINDOWPOS(None, None, -8, -8, 1936, 1056, 0)
        api.user32.zoomed = True
        chrome._window_proc(1, window_chrome._WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos))
        self.assertEqual((pos.x, pos.y, pos.cx, pos.cy), (0, 0, 1920, 1040))
        self.assertEqual(api.areas, [(-8, -8, 1936, 1056)])
        # Win+Shift+Right (or maximizing onto the other monitor): the proposed rect is on the second
        # monitor, so its work area is used, not the one of the monitor the window is leaving.
        pos = WINDOWPOS(None, None, 1912, -8, 2576, 1456, 0)
        chrome._window_proc(1, window_chrome._WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos))
        self.assertEqual((pos.x, pos.y, pos.cx, pos.cy), (1920, 0, 2560, 1440))
        # A resize in place (no move) keeps the monitor the window is on.
        pos = WINDOWPOS(None, None, 0, 0, 1900, 1000, window_chrome._SWP_NOMOVE)
        chrome._window_proc(1, window_chrome._WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos))
        self.assertEqual(api.areas[-1], None)
        self.assertEqual((pos.x, pos.y, pos.cx, pos.cy, pos.flags & window_chrome._SWP_NOMOVE), (0, 0, 1920, 1040, 0))
        api.user32.zoomed = False
        pos = WINDOWPOS(None, None, 10, 10, 800, 600, 0)
        chrome._window_proc(1, window_chrome._WM_WINDOWPOSCHANGING, 0, ctypes.addressof(pos))
        self.assertEqual((pos.x, pos.y, pos.cx, pos.cy), (10, 10, 800, 600))

    def test_an_auto_hidden_taskbar_takes_one_pixel_only_on_its_own_monitor_and_edge(self):
        screen = (0, 0, 1920, 1080)
        none = lambda edge: False   # noqa: E731
        self.assertEqual(window_chrome.maximized_rect((0, 0, 1920, 1040), screen, none), (0, 0, 1920, 1040))
        self.assertEqual(window_chrome.maximized_rect(screen, screen, lambda edge: edge == 3), (0, 0, 1920, 1079))
        self.assertEqual(window_chrome.maximized_rect(screen, screen, lambda edge: edge == 0), (1, 0, 1919, 1080))
        # A monitor without a taskbar keeps its whole area (no other monitor's auto-hide applies).
        self.assertEqual(window_chrome.maximized_rect((1920, 0, 4480, 1440), (1920, 0, 4480, 1440), none),
                         (1920, 0, 2560, 1440))
        # A normal taskbar at the bottom and an auto-hidden bar at the top of the same monitor.
        self.assertEqual(window_chrome.maximized_rect((0, 0, 1920, 1040), screen, lambda edge: edge in (1, 3)),
                         (0, 1, 1920, 1039))

    @unittest.skipUnless(sys.platform == "win32", "Win32 structures")
    def test_the_maximized_area_asks_the_target_monitor_and_its_own_edges(self):
        api = window_chrome._Win32()
        monitors = {1: ((0, 0, 1920, 1080), (0, 0, 1920, 1080)),          # auto-hidden taskbar at the bottom
                    2: ((1920, 0, 4480, 1440), (1920, 0, 4480, 1440))}    # no taskbar
        asked = []

        class User32:
            def MonitorFromRect(self, rect, flags):
                asked.append(("rect", (rect.left, rect.top, rect.right, rect.bottom)))
                return 2 if rect.left + (rect.right - rect.left) // 2 >= 1920 else 1

            def MonitorFromWindow(self, hwnd, flags):
                asked.append(("window", hwnd))
                return 1

            def GetMonitorInfoW(self, monitor, address):
                info = api.MONITORINFO.from_address(address)
                screen, work = monitors[monitor]
                (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom) = screen
                (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom) = work
                return True

        class Shell32:
            def SHAppBarMessage(self, message, address):
                bar = api.APPBARDATA.from_address(address)
                rect = (bar.rc.left, bar.rc.top, bar.rc.right, bar.rc.bottom)
                asked.append(("appbar", message, rect, bar.uEdge))
                return 0x500 if (rect, bar.uEdge) == ((0, 0, 1920, 1080), 3) else 0

        api.user32, api.shell32 = User32(), Shell32()
        self.assertEqual(api.maximized_area(7, (1912, -8, 2576, 1456)), (1920, 0, 2560, 1440))
        self.assertEqual(asked[0], ("rect", (1912, -8, 4488, 1448)))
        self.assertEqual({entry[1] for entry in asked[1:]}, {window_chrome._ABM_GETAUTOHIDEBAREX})
        self.assertEqual({entry[2] for entry in asked[1:]}, {(1920, 0, 4480, 1440)}, "that monitor's own bars")
        asked.clear()
        self.assertEqual(api.maximized_area(7), (0, 0, 1920, 1079))
        self.assertEqual(asked[0], ("window", 7))

    def test_drag_and_caption_commands_are_posted_to_windows(self):
        chrome, api = self.prepared()
        chrome.prepare()
        chrome.finish()
        api.user32.calls.clear()
        self.assertTrue(chrome.start_move())
        self.assertEqual(api.user32.calls, [("ReleaseCapture",),
                                            ("PostMessageW", window_chrome._WM_NCLBUTTONDOWN, 2, 300 | 40 << 16)])
        api.user32.calls.clear()
        chrome.start_resize("se")
        chrome.minimize()
        chrome.toggle_maximize()
        api.user32.zoomed = True
        self.assertTrue(chrome.zoomed())
        chrome.toggle_maximize()
        chrome.system_menu(120, 7)
        posted = [entry[1:] for entry in api.user32.calls if entry[0] == "PostMessageW"]
        self.assertEqual(posted, [(window_chrome._WM_NCLBUTTONDOWN, 17, 300 | 40 << 16),
                                  (window_chrome._WM_SYSCOMMAND, window_chrome._SC_MINIMIZE, 0),
                                  (window_chrome._WM_SYSCOMMAND, window_chrome._SC_MAXIMIZE, 0),
                                  (window_chrome._WM_SYSCOMMAND, window_chrome._SC_RESTORE, 0),
                                  (window_chrome._WM_POPUPSYSTEMMENU, 0, 120 | 7 << 16)])
        self.assertNotIn("SendMessageW", [entry[0] for entry in api.user32.calls])
        chrome.root.iconify.assert_not_called()
        # Back from the taskbar: Windows restores (and activates) it, as a click on its button does.
        api.user32.calls.clear()
        chrome.restore()
        self.assertEqual(api.user32.calls, [("PostMessageW", window_chrome._WM_SYSCOMMAND, window_chrome._SC_RESTORE, 0)])
        chrome.root.deiconify.assert_called_once_with()   # only finish()'s own

    def test_without_the_custom_frame_drag_is_a_no_op_and_the_buttons_use_tk(self):
        chrome, api = self.prepared(state="withdrawn")
        chrome.prepare()
        self.assertFalse(chrome.start_move())
        self.assertFalse(chrome.start_resize("n"))
        chrome.system_menu(1, 1)
        chrome.minimize()
        chrome.root.iconify.assert_called_once_with()
        chrome.root.wm_state.return_value = "normal"
        chrome.toggle_maximize()
        chrome.root.state.assert_called_with("zoomed")
        chrome.root.wm_state.return_value = "zoomed"
        self.assertTrue(chrome.zoomed())
        chrome.toggle_maximize()
        chrome.root.state.assert_called_with("normal")
        chrome.restore()
        chrome.root.deiconify.assert_called_once_with()
        self.assertEqual(api.user32.calls, [])


class EdgeResizerTests(unittest.TestCase):
    def test_zones_follow_the_window_edges_and_corners_while_not_maximized(self):
        root = Mock()
        root.winfo_width.return_value, root.winfo_height.return_value = 1000, 700
        root.winfo_rootx.return_value, root.winfo_rooty.return_value = 0, 0
        root.bindtags.return_value = (".", "Tk", "all")
        root._dolly_ui_scale = 1.0
        root.winfo_toplevel.return_value = root
        chrome = Mock(active=True)
        chrome.zoomed.return_value = False
        resizer = window_chrome.EdgeResizer(root, chrome)
        cases = {(2, 300): "w", (998, 300): "e", (500, 1): "n", (500, 698): "s", (2, 2): "nw", (10, 2): "nw",
                 (2, 10): "nw", (990, 3): "ne", (3, 690): "sw", (996, 696): "se", (500, 300): None, (6, 300): None,
                 # outside the window (a drag that left it): never a zone
                 (-1, 300): None, (1000, 300): None, (1400, 300): None, (500, -3): None, (500, 700): None}
        for (x, y), edge in cases.items():
            with self.subTest(x=x, y=y):
                self.assertEqual(resizer.edge(x, y), edge)
        chrome.zoomed.return_value = True
        self.assertIsNone(resizer.edge(2, 300))
        chrome.zoomed.return_value, chrome.active = False, False
        self.assertIsNone(resizer.edge(2, 300))


class EdgeResizerDragTests(BarCase):
    """The resize zones on a real window: a drag that reaches or leaves the window edge stays the widget's."""

    def setUp(self):
        super().setUp()
        self.chrome = Mock(active=True)
        self.chrome.zoomed.return_value = False
        self.frame = tk.Frame(self.root, width=400, height=100)
        self.frame.pack(fill="both", expand=True)
        self.root.geometry("400x100")
        self.root.deiconify()
        self.root.update()
        self.resizer = window_chrome.EdgeResizer(self.root, self.chrome)
        self.resizer.attach()

    def test_motion_with_a_button_held_never_turns_into_a_resize(self):
        frame, moves, releases = self.frame, [], []
        frame.bind("<B1-Motion>", lambda event: moves.append(event.x))
        frame.bind("<ButtonRelease-1>", lambda event: releases.append(event.x))
        # Hovering the zone shows the resize cursor and stops there.
        frame.event_generate("<Motion>", x=398, y=50)
        self.assertEqual(str(frame.cget("cursor")), "size_we")
        frame.event_generate("<Motion>", x=200, y=50)
        self.assertEqual(str(frame.cget("cursor")), "")
        # A drag through the zone and past the window edge reaches the widget at every step.
        frame.event_generate("<ButtonPress-1>", x=200, y=50)
        for x in (300, 397, 399, 450, 600):
            frame.event_generate("<Motion>", x=x, y=50, state=0x100)
        self.assertEqual(moves, [300, 397, 399, 450, 600])
        self.assertEqual(str(frame.cget("cursor")), "", "no resize arrow mid-drag")
        frame.event_generate("<ButtonRelease-1>", x=600, y=50, state=0x100)
        self.assertEqual(releases, [600])
        self.chrome.start_resize.assert_not_called()
        # A press in the zone starts Windows' resize; the release Tk sends once that loop ends is
        # not the widget's (a control acting on release must not fire).
        frame.event_generate("<Motion>", x=398, y=50)
        frame.event_generate("<ButtonPress-1>", x=398, y=50)
        self.chrome.start_resize.assert_called_once_with("e")
        frame.event_generate("<ButtonRelease-1>", x=398, y=50, state=0x100)
        self.assertEqual(releases, [600])
        # The next click is the widget's again.
        frame.event_generate("<ButtonPress-1>", x=200, y=50)
        frame.event_generate("<ButtonRelease-1>", x=200, y=50, state=0x100)
        self.assertEqual(releases, [600, 200])

    def test_a_slider_dragged_past_the_window_edge_reaches_its_end(self):
        scale = ttk.Scale(self.frame, from_=0, to=100, orient="horizontal")
        scale.place(x=0, y=40, width=400)
        scale.set(25)
        self.root.update()
        self.resizer.attach(scale)
        x, y = (int(v) for v in scale.coords(25))
        scale.event_generate("<ButtonPress-1>", x=x, y=y)
        for x in (300, 398, 450, 600):
            scale.event_generate("<Motion>", x=x, y=y, state=0x100)
        scale.event_generate("<ButtonRelease-1>", x=600, y=y, state=0x100)
        self.assertEqual(round(scale.get()), 100)
        self.chrome.start_resize.assert_not_called()

    def test_widgets_built_later_get_the_zones_when_they_first_map(self):
        late = tk.Frame(self.frame, width=50, height=20)
        self.assertNotIn(window_chrome.EdgeResizer.TAG, late.bindtags())
        late.place(x=0, y=0)
        self.root.update()
        self.assertEqual(late.bindtags()[0], window_chrome.EdgeResizer.TAG)
        menu = tk.Menu(self.root, tearoff=False)
        menu.event_generate("<Map>")
        self.assertNotIn(window_chrome.EdgeResizer.TAG, menu.bindtags(), "menus are windows of their own")

    def test_the_window_box_is_measured_once_until_the_window_changes(self):
        resizer = self.resizer
        self.frame.event_generate("<Motion>", x=200, y=50)
        self.assertEqual(resizer._box[2:], (400, 100))
        with patch.object(self.root, "winfo_width", side_effect=AssertionError("measured again")):
            for x in (100, 200, 398):
                self.frame.event_generate("<Motion>", x=x, y=50)
        self.root.geometry("420x100")
        self.root.update()
        self.assertIsNone(resizer._box)
        self.frame.event_generate("<Motion>", x=417, y=50)
        self.assertEqual(resizer._box[2:], (420, 100))
        self.assertEqual(str(self.frame.cget("cursor")), "size_we")


if __name__ == "__main__":
    unittest.main()
