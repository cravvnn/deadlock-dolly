"""The redesigned shell in the real app: sidebar, stage header and footer, stickers, layout modes."""
import os
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import ANY, Mock, patch

from dolly import ui_theme
from dolly.gui import DollyApp, _session_tone
from dolly.settings import AppSettings
from dolly.ui import (camera_page, controls_page, export_page, library_page, look_page, settings_page, window_bars,
                      window_chrome)
from dolly.ui.action_proxy import ActionProxy, ProgressProxy
from dolly.ui.backdrop import Backdrop
from dolly.ui.scale import px
from dolly.ui import brand_mark, sidebar as sidebar_module
from dolly.ui.sidebar import NavItem
from dolly.ui.update_notice import UpdateNotice

TOKENS = ui_theme.TOKENS
DESIGN_SUBTITLE = "Choose a replay, open it in Dolly, then frame your cameras inside Deadlock."


def _replay_page_contract():
    """Only what the Replay page package has not delivered yet, stubbed for these tests.

    The shell needs ``library_page.TITLE``/``SUBTITLE`` and button-like proxies for
    START, Cancel startup and the progress bar. With the redesigned page in place
    nothing is replaced.
    """
    if not hasattr(library_page, "TITLE"):
        patch.object(library_page, "TITLE", "Replay & Shots", create=True).start()
        patch.object(library_page, "SUBTITLE", DESIGN_SUBTITLE, create=True).start()
    build = DollyApp._build_setup

    def build_setup(app):
        build(app)
        if not isinstance(app.play_replay_button, ActionProxy):
            app.play_replay_button = ActionProxy("Open replay in Dolly", app._start_editing_session, label="START")
        if not isinstance(app.cancel_startup_button, ActionProxy):
            app.cancel_startup_button = ActionProxy("Cancel startup", app._cancel_startup, state="disabled")
        if not isinstance(app.startup_bar, ProgressProxy):
            app.startup_bar = ProgressProxy()
    patch.object(DollyApp, "_build_setup", build_setup).start()


class ShellCase(unittest.TestCase):
    """The real app, hidden, at ``SCALE`` (100 % unless a subclass says otherwise)."""

    SCALE = 1.0

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        # Tk on Windows keeps one `tk scaling` for every interpreter in the process,
        # so a high-DPI fixture that ran earlier would leave this window at its scale.
        # The layout here is checked at SCALE: pin it; close_app hands the old value back.
        self.scaling = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", 96 * self.SCALE / 72)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        # Runs last, so callbacks that fail while the window closes are caught too.
        self.addCleanup(lambda: self.assertEqual(self.errors, []))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DOLLY_UI_MOTION": "0"}).start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        _replay_page_contract()
        patch.object(DollyApp, "_submit").start()
        patch.object(DollyApp, "_poll").start()
        self.open_folder = patch.object(DollyApp, "_open_output_folder").start()
        self.diagnostics = patch.object(DollyApp, "_diagnostics").start()
        patch.object(DollyApp, "_load_app_settings", return_value=AppSettings(
            auto_updates=False, export_folder=str(Path(directory.name) / "exports"))).start()
        patch("dolly.graphics_profiles.load_library", return_value={"format": 1, "selected": "", "profiles": []}).start()
        self.app = DollyApp(self.root)
        self.stage = self.app.stage
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.tk.call("tk", "scaling", self.scaling)
        self.root.destroy()

    def page_area(self, width, height):
        """The window geometry whose stage (the page area) is ``width`` x ``height`` design px.

        The title bar and the link bar (USER-ADDITIONS UPDATE 20) span the window above and
        below the stage, so the window is that much taller than the page area.
        """
        return f"{self.px(width)}x{self.px(height) + window_bars.bars_px(self.app.ui_scale)}"

    def show(self, size=None, tab=None):
        """Map the (test-harness hidden) window at ``size`` (default: a 1200 x 830 page area) and settle."""
        self.root.deiconify()
        self.root.geometry(size or self.page_area(1200, 830))
        if tab is not None:
            self.app.notebook.select(tab)
        self.root.update()
        self.root.update_idletasks()

    def px(self, value):
        return px(self.app.ui_scale, value)


class ShellTests(ShellCase):
    # ------------------------------------------------------------------ sidebar

    def test_dollys_mark_sits_left_of_the_wordmark_inside_the_column(self):
        """UPDATE 21 (Kara: "add dolly logo near deadlock dolly in sidebar")."""
        sidebar = self.app.sidebar
        self.assertIsNotNone(sidebar.mark_item)
        mark = sidebar.bbox(sidebar.mark_item)
        wordmark = next(item for item in sidebar.find_all()
                        if sidebar.type(item) == "text" and sidebar.itemcget(item, "text") == "Deadlock Dolly")
        text = sidebar.bbox(wordmark)
        self.assertEqual(mark[0], self.px(sidebar_module.BRAND_X))
        self.assertEqual(sidebar.mark_image.width(), self.px(brand_mark.SIZE))
        self.assertGreaterEqual(text[0], mark[2] + self.px(sidebar_module.MARK_GAP) - 2)   # text bbox has a pixel of slack
        self.assertLessEqual(text[2], int(sidebar.cget("width")))
        # Centred on the wordmark's row.
        self.assertLessEqual(abs((mark[1] + mark[3]) / 2 - self.px(43)), 1)
        # The tagline stays on the column's left edge, under the mark.
        tagline = next(item for item in sidebar.find_all()
                       if sidebar.type(item) == "text" and sidebar.itemcget(item, "text") == "Deadlock camera tool")
        self.assertEqual(sidebar.coords(tagline)[0], self.px(sidebar_module.BRAND_X))

    def test_sidebar_rows_follow_the_notebook_pages(self):
        app = self.app
        tabs = (app.setup_tab, app.camera_tab, app.cvar_tab, app.export_tab, app.controls_tab, app.settings_tab)
        self.assertEqual(list(app.navigation), [str(tab) for tab in tabs])
        self.assertEqual(list(app.sidebar.items), [str(tab) for tab in tabs])
        self.assertEqual([item.cget("text") for item in app.navigation.values()],
                         ["Replay", "Camera", "Look", "Export", "Controls", "Settings"])
        for key, item in app.navigation.items():
            self.assertIsInstance(item, NavItem)
            self.assertIs(item, app.sidebar.items[key])
        self.assertEqual(app.sidebar.selected, str(app.setup_tab))
        app.navigation[str(app.camera_tab)].invoke()
        self.root.update()
        self.assertEqual(app.notebook.select(), str(app.camera_tab))
        self.assertEqual(app.sidebar.selected, str(app.camera_tab))
        self.assertEqual(self.stage.title_text.cget("text"), "Camera")
        self.assertEqual(self.stage.subtitle_text.cget("text"), camera_page.SUBTITLE)
        app.navigation[str(app.setup_tab)].invoke()
        self.root.update()
        self.assertEqual(self.stage.title_text.cget("text"), library_page.TITLE)
        self.assertEqual(self.stage.subtitle_text.cget("text"), library_page.SUBTITLE)
        # Every page's header copy comes from its module (PLAN-5 3.0.1).
        for tab, page in ((app.cvar_tab, look_page), (app.export_tab, export_page),
                          (app.controls_tab, controls_page), (app.settings_tab, settings_page)):
            app.navigation[str(tab)].invoke()
            self.root.update()
            self.assertEqual(self.stage.title_text.cget("text"), page.TITLE)
            self.assertEqual(self.stage.subtitle_text.cget("text"), page.SUBTITLE)
        app.navigation[str(app.setup_tab)].invoke()
        self.root.update()
        # A disabled row ignores clicks; the notebook stays where it is.
        look = app.navigation[str(app.cvar_tab)]
        look.configure(state="disabled")
        self.assertEqual(look.cget("state"), "disabled")
        self.assertEqual(look.invoke(), "")
        self.root.update()
        self.assertEqual(app.notebook.select(), str(app.setup_tab))
        look.configure(state="normal")

    def test_page_switches_set_the_timeline_row_before_the_page_shows(self):
        # Placing the timeline after the switch would lay the page out twice. Camera and Look
        # pin it in their docks (PLAN-5 N10, USER-ADDITIONS UPDATE 7); other pages hide it.
        # Shown at the design size: with under 160 px of viewport left a dock unpins.
        app = self.app
        self.show(self.page_area(1200, 810))
        frame = app.timeline_frame
        timeline = []
        select = app.notebook.select
        camera_host = str(app.camera_page.dock)
        look_host = str(app.look_page.dock)

        def placed_in():
            return frame.winfo_manager(), str(frame.place_info().get("in", ""))

        def recording_select(*tab):
            if tab:
                timeline.append(placed_in())
            return select(*tab)
        patch.object(app.notebook, "select", recording_select).start()
        for tab, placed in ((app.camera_tab, ("place", camera_host)), (app.cvar_tab, ("place", look_host)),
                            (app.export_tab, ("", "")), (app.camera_tab, ("place", camera_host)),
                            (app.cvar_tab, ("place", look_host)), (app.setup_tab, ("", ""))):
            with self.subTest(tab=str(tab)):
                timeline.clear()
                app.navigation[str(tab)].invoke()
                self.root.update_idletasks()
                self.assertEqual(timeline, [placed])
                self.assertEqual(select(), str(tab))
                self.assertEqual(placed_in(), placed)
                # One host at a time: the frame and its owner agree.
                hosts = [host for host in app._playback_hosts.values() if host.adopted is frame]
                self.assertEqual(len(hosts), 1 if placed[0] else 0)
        # Direct notebook.select() callers are handled by the tab-change fallback.
        app._show_keybinds()
        self.assertEqual(select(), str(app.controls_tab))
        self.assertEqual(frame.winfo_manager(), "")
        app.notebook.select(app.cvar_tab)
        self.root.update()
        self.assertEqual(placed_in(), ("place", look_host))

    def test_change_shortcut_dialog_shows_its_keycap_listening_until_it_closes(self):
        # PLAN-5 4.4 step 6: the Controls keycap listens while its dialog is open, and every
        # way out of the dialog ends it (Escape needs keyboard focus, which hidden windows lack).
        app = self.app
        patch("dolly.ui.binding_capture.SideMouseCapture.start", return_value=False).start()
        controls = app.controls_view
        keycap = controls.binding_buttons["panel"]

        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        for close in ("cancel", "window", "destroy"):
            with self.subTest(close=close):
                app._record_binding("panel")
                self.root.update_idletasks()
                dialog = app.binding_dialog
                self.assertEqual(controls._listening, "panel")
                self.assertTrue(keycap.instate(["alternate"]))
                if close == "cancel":
                    next(widget for widget in walk(dialog)
                         if isinstance(widget, ttk.Button) and widget.cget("text") == "Cancel").invoke()
                elif close == "window":
                    dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                else:
                    dialog.destroy()
                self.root.update_idletasks()
                self.assertIsNone(controls._listening)
                self.assertFalse(keycap.instate(["alternate"]))

    def test_sidebar_keyboard_ring_starts_on_the_page_and_opens_rows(self):
        app, sidebar = self.app, self.app.sidebar
        sidebar._focus_in(None)
        self.assertEqual(sidebar.itemcget(sidebar._ring, "state"), "normal")
        self.assertEqual(sidebar._focus, str(app.setup_tab))
        sidebar._move(1)
        sidebar._move(1)
        self.assertEqual(sidebar._focus, str(app.cvar_tab))
        self.assertEqual(sidebar.selected, str(app.setup_tab), "moving the ring does not switch pages")
        sidebar._activate(None)
        self.root.update()
        self.assertEqual(app.notebook.select(), str(app.cvar_tab))
        sidebar._move(None, end=-1)
        self.assertEqual(sidebar._focus, str(app.settings_tab))
        sidebar._move(1)
        self.assertEqual(sidebar._focus, str(app.settings_tab), "the ring stops at the last row")
        sidebar._focus_out(None)
        self.assertEqual(sidebar.itemcget(sidebar._ring, "state"), "hidden")

    def test_sidebar_requests_the_design_height_used_by_the_update_notes_budget(self):
        self.assertEqual(self.app.sidebar.winfo_reqheight(), self.px(361))
        self.assertEqual(self.app.sidebar.winfo_reqwidth(), self.px(190))

    def test_version_line_follows_the_live_driver(self):
        """Figma [design reference]: "<version>  ·  " in dim, then the driver in use; only "Console driver"
        takes the warn tone, and "(legacy)" is gone."""
        app, sidebar = self.app, self.app.sidebar
        from dolly import __version__
        from dolly.ui import fonts
        self.root.update_idletasks()

        def line():
            return [(sidebar.itemcget(item, "text"), sidebar.itemcget(item, "fill"))
                    for item in (sidebar.version_item, sidebar.driver_item)]
        prefix = f"{__version__}  ·  "
        self.assertEqual(line(), [(prefix, TOKENS["dim"]), ("Native driver", TOKENS["dim"])])
        app._set_driver_indicator("console", True)
        self.assertEqual(line(), [(prefix, TOKENS["dim"]), ("Console driver", TOKENS["warn"])])
        x, y = sidebar.coords(sidebar.version_item)   # one baseline: the driver starts where the version ends
        self.assertEqual(sidebar.coords(sidebar.driver_item), [x + fonts.font(sidebar, "small").measure(prefix), y])
        app._set_driver_indicator("native", True)
        self.assertEqual(line()[1], ("Native driver", TOKENS["dim"]))

    # -------------------------------------------------------------- footer links

    def test_footer_links_route_to_the_existing_commands(self):
        self.show()
        app = self.app
        links = self.stage.links
        self.assertEqual([link.cget("text") for link in links], ["Open output folder", "Show log", "Export Diagnostics"])
        links[0].invoke()
        self.open_folder.assert_called_once_with()
        links[2].invoke()
        self.diagnostics.assert_called_once_with()
        links[1].invoke()
        self.assertTrue(app.show_log.get())
        self.assertEqual(app.log_dialog.state(), "normal")
        self.assertEqual(links[1].cget("text"), "Hide log")
        app._close_log()   # The log window's own close button keeps the link in step.
        self.assertEqual(links[1].cget("text"), "Show log")
        links[1].invoke()
        links[1].invoke()
        self.assertFalse(app.show_log.get())
        self.assertEqual(app.log_dialog.state(), "withdrawn")

    # ------------------------------------------------------------------ stickers

    def test_stop_sticker_runs_stop_restore_on_every_page(self):
        app = self.app
        app._session_operation = Mock()
        sticker = self.stage.secondary_sticker
        self.assertEqual(sticker.label_text, "STOP/RESTORE")
        for tab in (app.setup_tab, app.camera_tab, app.cvar_tab, app.export_tab, app.controls_tab, app.settings_tab):
            app.notebook.select(tab)
            self.root.update()
            self.assertIn(sticker, self.stage.visible_stickers())
        sticker.invoke()
        app._session_operation.assert_called_once_with("Stopping", app.controller.stop)

    def test_start_sticker_is_replay_only_and_mirrors_the_play_button(self):
        app = self.app
        start = self.stage.primary_sticker
        self.assertIsNotNone(start)
        self.assertEqual(start.label_text, "START")
        self.assertIs(start.action, app.play_replay_button)
        self.assertEqual(self.stage.visible_stickers(), [self.stage.secondary_sticker, start])
        app.play_replay_button.configure(state="disabled")   # what the poll does while busy
        self.assertEqual(start.cget("state"), "disabled")
        self.assertFalse(start.focusable())
        self.assertEqual(start.face()[2], .4)
        app._submit = Mock(return_value=True)
        self.assertEqual(start.invoke(), "")
        app._submit.assert_not_called()
        app.play_replay_button.configure(state="normal")
        app.game_path.set("C:/game/citadel.exe")
        app.demo_path.set("C:/replays/test.dem")
        app.controller = Mock()
        app._disable_external_input = Mock()
        start.invoke()
        app._submit.assert_called_once_with("Starting replay editor", ANY, ANY)
        self.assertIsNotNone(app.startup_cancel)
        self.assertTrue(self.stage.progress.running)
        app.notebook.select(app.camera_tab)
        self.root.update()
        self.assertIsNone(self.stage.primary_sticker)
        self.assertEqual(self.stage.visible_stickers(), [self.stage.secondary_sticker])
        self.assertFalse(start.visible())

    def test_cancel_sticker_shows_only_while_startup_can_be_cancelled(self):
        app = self.app
        cancel = self.stage.cancel_sticker
        self.assertEqual(cancel.label_text, "CANCEL")
        self.assertIs(cancel.action, app.cancel_startup_button)
        self.assertNotIn(cancel, self.stage.visible_stickers())
        self.show()
        stop, start = self.stage.secondary_sticker, self.stage.primary_sticker
        start_right = start.bbox()[2]
        app.startup_cancel = threading.Event()
        app.startup_bar.start(15)
        app.cancel_startup_button.configure(state="normal")   # the poll while starting
        app.play_replay_button.configure(state="disabled")
        self.root.update_idletasks()
        # CANCEL takes START's place for as long as the startup can be cancelled.
        self.assertEqual(self.stage.visible_stickers(), [stop, cancel])
        self.assertFalse(start.visible())
        self.assertLess(stop.bbox()[0], cancel.bbox()[0])
        self.assertAlmostEqual(cancel.bbox()[2], start_right, delta=4)
        self.assertEqual(self.stage._focus.controls[-2:], [stop, cancel])
        cancel.invoke()
        self.assertTrue(app.startup_cancel.is_set())
        self.assertEqual(app.cancel_startup_button.cget("state"), "disabled")
        self.assertIn("Cancelling", app.startup_progress.get())
        self.root.update_idletasks()
        self.assertNotIn(cancel, self.stage.visible_stickers())
        self.assertFalse(cancel.visible())
        self.assertEqual(self.stage.visible_stickers(), [stop, start])

    def test_progress_line_follows_the_startup_bar(self):
        self.assertFalse(self.stage.progress.running)
        self.app.startup_bar.start(15)
        self.assertTrue(self.stage.progress.running)
        self.app.startup_bar.stop()
        self.assertFalse(self.stage.progress.running)

    # ------------------------------------------------------------- status line

    def test_footer_status_moves_between_ready_busy_and_error(self):
        app = self.app
        app.status_text.set("Replay ready.")
        self.assertEqual(app._footer_status(), ("Replay ready.", "ready"))
        app.busy = True
        self.assertEqual(app._footer_status(), ("Replay ready.", "busy"))
        app.busy = False
        app.startup_cancel = threading.Event()
        app.startup_progress.set("Launching Deadlock and preparing the hideout…")
        self.assertEqual(app._footer_status(), ("Launching Deadlock and preparing the hideout…", "busy"))
        self.root.update_idletasks()
        self.assertEqual(self.stage.status_label.cget("text"), "Launching Deadlock and preparing the hideout…")
        self.assertEqual(self.stage.status_dot.cget("state"), "busy")
        app.startup_cancel = None
        with patch("dolly.gui.dialogs.show_error") as show_error:
            app._error("Open shot", RuntimeError("The shot file is damaged."))
        show_error.assert_called_once()
        self.assertEqual(app._footer_status(), ("The shot file is damaged.", "error"))
        self.root.update_idletasks()
        self.assertEqual(self.stage.status_label.cget("text"), "The shot file is damaged.")
        self.assertEqual(self.stage.status_label.cget("fill"), TOKENS["status_error"])
        self.assertEqual(self.stage.status_dot.cget("state"), "error")
        app.status_text.set("Shot saved.")
        self.root.update_idletasks()
        self.assertEqual(app._footer_status(), ("Shot saved.", "ready"))
        self.assertEqual(self.stage.status_label.cget("fill"), TOKENS["muted"])
        self.assertEqual(self.stage.status_dot.cget("state"), "ready")

    def test_long_status_is_elided_and_shown_whole_in_a_bubble(self):
        self.show(self.page_area(1000, 700))
        message = "The replay could not be prepared because Deadlock stopped responding while it loaded. " * 4
        self.app.status_text.set(message.strip())
        self.root.update_idletasks()
        label = self.stage.status_label
        self.assertTrue(label.elided())
        self.assertEqual(label.full_text(), message.strip())
        self.stage._show_bubble()
        self.root.update_idletasks()
        bubble = self.stage._bubble
        self.assertTrue(bubble.winfo_ismapped())
        self.assertEqual(bubble.itemcget(self.stage._bubble_text, "text"), message.strip())
        self.assertLessEqual(bubble.winfo_x() + bubble.winfo_width(), self.stage.winfo_width())
        self.stage._hide_bubble()
        self.root.update_idletasks()
        self.assertFalse(bubble.winfo_ismapped())

    def test_header_game_status_tone_and_context_line(self):
        app = self.app
        self.assertEqual(_session_tone("Game not connected"), "dim")
        self.assertEqual(_session_tone("Game closed"), "dim")
        self.assertEqual(_session_tone("Waiting for hideout"), "muted")
        self.assertEqual(_session_tone("Session needs attention"), "error")
        self.assertEqual(_session_tone("Disconnected — reconnect to continue"), "error")
        for text, token in (("Waiting for hideout", "muted"), ("Session needs attention", "status_error"),
                            ("Game not launched", "dim")):
            app.session_text.set(text)
            self.root.update_idletasks()
            self.assertEqual(self.stage.session_text.cget("text"), text)
            self.assertEqual(self.stage.session_text.cget("fill"), TOKENS[token])
        # The header shows current work only; the shot name stays in the window title.
        app.project_text.set("Mirage push *")
        app.busy_text.set("Replay tick 256")
        self.root.update_idletasks()
        self.assertEqual(self.stage.context_text.cget("text"), "Replay tick 256")
        app.busy_text.set("")
        self.root.update_idletasks()
        self.assertEqual(self.stage.context_text.cget("text"), "")

    # ------------------------------------------------------------------- layout

    def test_layout_modes_follow_the_stage_size(self):
        self.show(self.page_area(1200, 830))
        width, height = self.stage.winfo_width(), self.stage.winfo_height()
        self.assertEqual((width, height), (self.px(1200) - self.px(190), self.px(830)))
        self.assertEqual(self.stage.mode, "design")
        self.assertEqual(self.stage.page_rect(), (self.px(40), self.px(107), width - self.px(40), height - self.px(112)))
        workspace = self.stage.workspace
        self.assertEqual((workspace.winfo_x(), workspace.winfo_y()), (self.px(40), self.px(107)))
        # The kit pages' rail gutter (18) lies inside the right padding (PLAN-5 N8).
        self.assertEqual(workspace.winfo_width(), width - 2 * self.px(40) + self.px(18))
        self.assertEqual(self.stage.workspace_rect(),
                         (self.px(40), self.px(107), width - self.px(40) + self.px(18), height - self.px(112)))
        self.show(self.page_area(1000, 700))
        width, height = self.stage.winfo_width(), self.stage.winfo_height()
        self.assertEqual(height, self.px(700))
        self.assertEqual(self.stage.mode, "compact")
        self.assertEqual(self.stage.page_rect(), (self.px(24), self.px(76), width - self.px(24), height - self.px(98)))
        self.assertEqual(self.stage.workspace.winfo_width(), width - 2 * self.px(24) + self.px(18))
        # Footer rows sit below the page; START's label ends inside the right padding.
        page_bottom = self.stage.page_rect()[3]
        for sticker in self.stage.visible_stickers():
            x0, y0, x1, _y1 = self.stage.bbox(sticker.label)
            self.assertGreater(y0, page_bottom)
            self.assertLessEqual(x1, width - self.px(24))
        for link in self.stage.links:
            self.assertGreater(link.bbox()[1], page_bottom)

    def test_backdrop_paints_the_stage_around_the_page(self):
        self.show()
        backdrop = Backdrop.of(self.stage)
        self.assertIs(backdrop, self.app.backdrop)
        self.assertIn(self.stage, [surface.canvas for surface in backdrop.surfaces])
        # The pages (and their rail gutter) cover the workspace; the backdrop shows around it.
        x0, y0, x1, y1 = self.stage.workspace_rect()
        self.assertEqual((x0, y0, y1), (self.stage.page_rect()[0], self.stage.page_rect()[1], self.stage.page_rect()[3]))
        self.assertEqual(x1, self.stage.page_rect()[2] + self.px(18))
        width, height = self.stage.winfo_width(), self.stage.winfo_height()
        self.assertEqual(self.stage.open_areas(),
                         [(0, 0, width, y0), (0, y0, x0, y1), (x1, y0, width, y1), (0, y1, width, height)])

    def test_update_notice_pushes_the_page_down_and_gives_the_room_back(self):
        self.show()
        top = self.stage.page_rect()[1]
        self.assertEqual(top, self.px(107))
        notice = UpdateNotice(self.app.update_notice_parent, update=Mock(), notes=Mock(), full_notes=Mock(),
                              later=Mock(), reopen=Mock(), height_limit=lambda: 100)
        notice.show("9.9.9", "A new Dolly is ready to install.")
        self.root.update()
        self.root.update_idletasks()
        host = self.app.update_notice_parent
        self.assertGreater(host.winfo_reqheight(), 1)
        self.assertEqual(self.stage.page_rect()[1], self.px(107) + host.winfo_reqheight() + self.px(30))
        self.assertEqual(host.winfo_width(), self.stage.page_rect()[2] - self.stage.page_rect()[0])
        notice.collapse("9.9.9")
        self.root.update()
        self.root.update_idletasks()
        self.assertEqual(self.stage.page_rect()[1], top)
        self.assertTrue(notice.reminder.winfo_ismapped())
        notice.hide()
        self.root.update()
        self.root.update_idletasks()
        self.assertLessEqual(host.winfo_reqheight(), 1)
        self.assertEqual(self.stage.page_rect()[1], top)

    def test_minimum_window_keeps_recording_buttons_and_slider_inside(self):
        app = self.app

        def inside(widget):
            self.root.update_idletasks()
            x = widget.winfo_rootx() - self.root.winfo_rootx()
            y = widget.winfo_rooty() - self.root.winfo_rooty()
            return (widget.winfo_ismapped() and x >= 0 and y >= 0
                    and x + widget.winfo_width() <= self.root.winfo_width()
                    and y + widget.winfo_height() <= self.root.winfo_height())

        self.show(app._main_size(1000, 700), app.export_tab)
        self.assertTrue(inside(app.video_path_entry), "video_path_entry")
        # Recording is the page's last card (PLAN-5 N1): its buttons fit once revealed.
        canvas = app.export_page.canvas
        for name in ("video_start_button", "video_stop_button", "video_cancel_button"):
            app.export_page.reveal(getattr(app, name))
            button = getattr(app, name)
            self.assertTrue(inside(button), name)
            # ... and inside the page's scroll viewport, not just the window.
            self.assertGreaterEqual(button.winfo_rooty(), canvas.winfo_rooty(), name)
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                 canvas.winfo_rooty() + canvas.winfo_height(), name)
        # Camera grows the window (USER-ADDITIONS UPDATE 10); sized back down by hand,
        # the pinned Shot Playback still fits.
        self.show(app._main_size(1000, 700), app.camera_tab)
        self.show(app._main_size(1000, 700))
        self.assertEqual(self.root.winfo_height(), app._main_dimensions(1000, 700)[1])
        self.assertEqual(self.stage.winfo_height(), self.px(700), "the minimum page area under the bars")
        self.assertTrue(inside(app.slider))
        self.assertTrue(app.timeline_frame.winfo_ismapped())
        self.show(app._main_size(1000, 700), app.setup_tab)
        self.assertFalse(app.timeline_frame.winfo_ismapped())
        # Replay never scrolls (USER-ADDITIONS UPDATE 17.5): its table shows fewer rows instead.
        self.assertEqual(app.library_page.canvas.yview(), (0.0, 1.0))
        self.assertFalse(app.library_page.scrollbar.winfo_ismapped())
        self.assertGreaterEqual(int(app.replay_tree.cget("height")), 1)


class TallPageWindowTests(ShellCase):
    """USER-ADDITIONS UPDATE 10, 17.3 and 19: Camera and Look show in a window as tall as Look needs.

    Look shows all its cards with no page scroll and Camera opens at that same height, its
    bento scrolling inside; both only as far as the work area allows. Any other page puts
    back the height the window had. One geometry change per switch, set before the page
    shows (none between Camera and Look); the width and the top edge stay; a height chosen
    by hand is never taken back.
    """

    def setUp(self):
        super().setUp()
        # Room for any height: the pages' own heights show, not the test screen's.
        patch("dolly.gui.chrome.work_area", return_value=(0, 0, 100000, 100000)).start()
        self.root.wm_maxsize(100000, 100000)
        self.calls = []
        geometry, select = self.root.geometry, self.app.notebook.select

        def recording_geometry(*spec):
            if spec:
                self.calls.append(("geometry", spec[0]))
            return geometry(*spec)

        def recording_select(*tab):
            if tab:
                self.calls.append(("select", str(tab[0])))
            return select(*tab)
        patch.object(self.root, "geometry", recording_geometry).start()
        patch.object(self.app.notebook, "select", recording_select).start()
        self.geometry, self.select = geometry, select

    def go(self, tab):
        """Click ``tab``'s sidebar row; the geometry and select calls it made, in order."""
        self.calls.clear()
        self.app.navigation[str(tab)].invoke()
        self.root.update()
        return list(self.calls)

    def size(self):
        return self.root.winfo_width(), self.root.winfo_height()

    def assert_whole_page(self, page):
        """Every card shows: the viewport is exactly as tall as the cards, no rail, no scroll."""
        self.assertEqual(page.canvas.winfo_height(), page.content_height)
        self.assertFalse(page.scrollbar.winfo_ismapped())
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertEqual(self.app._playback_hosts[str(page.master)].mode, "dock")

    def assert_camera_scrolls_at_looks_height(self, look):
        """Camera opens at Look's height; its taller bento scrolls inside it."""
        page = self.app.camera_page
        self.assertEqual(self.root.winfo_height(), look)
        self.assertGreater(page.content_height, page.canvas.winfo_height())
        self.assertEqual(self.app._playback_hosts[str(page.master)].mode, "dock")

    def test_camera_takes_looks_height_and_the_other_pages_give_it_back(self):
        app = self.app
        # The window of a 1200 x 810 page area: the stage plus the title and link bars.
        width, base = self.px(1200), self.px(810) + window_bars.bars_px(self.app.ui_scale)
        self.show(f"{width}x{base}")
        self.assertEqual(self.size(), (width, base))
        look_calls = self.go(app.cvar_tab)
        look = self.root.winfo_height()
        self.assertEqual(look_calls, [("geometry", f"{width}x{look}"), ("select", str(app.cvar_tab))])
        self.assertGreater(look, base)
        self.assert_whole_page(app.look_page)
        # Camera <-> Look: the same height, no geometry change between them.
        self.assertEqual(self.go(app.camera_tab), [("select", str(app.camera_tab))])
        self.assert_camera_scrolls_at_looks_height(look)
        self.assertEqual(self.go(app.export_tab), [("geometry", f"{width}x{base}"), ("select", str(app.export_tab))])
        self.assertEqual(self.size(), (width, base))
        self.assertEqual(self.go(app.settings_tab), [("select", str(app.settings_tab))])
        # From another page Camera grows straight to Look's height, set before it shows.
        self.assertEqual(self.go(app.camera_tab), [("geometry", f"{width}x{look}"), ("select", str(app.camera_tab))])
        self.assert_camera_scrolls_at_looks_height(look)
        self.assertEqual(self.go(app.cvar_tab), [("select", str(app.cvar_tab))])
        self.assert_whole_page(app.look_page)
        self.assertEqual(self.go(app.camera_tab), [("select", str(app.camera_tab))])
        # The page already shown keeps the window, whatever changed on it since.
        app.playback_disclosure.set_open(True)
        self.root.update()
        self.assertEqual(self.go(app.camera_tab), [])
        self.assertEqual(self.size(), (width, look))
        app.playback_disclosure.set_open(False)
        self.root.update()
        self.assertEqual(self.go(app.setup_tab), [("geometry", f"{width}x{base}"), ("select", str(app.setup_tab))])
        # Direct notebook.select() callers get the same window once Tk is idle.
        self.select(app.cvar_tab)
        self.root.update()
        self.assertEqual(self.size(), (width, look))
        self.select(app.camera_tab)
        self.root.update()
        self.assertEqual(self.size(), (width, look))
        camera = look
        self.select(app.controls_tab)
        self.root.update()
        self.assertEqual(self.size(), (width, base))
        # Resized by hand while grown: leaving keeps the user's height.
        mine = base + self.px(90)
        self.go(app.camera_tab)
        self.geometry(f"{width}x{mine}")
        self.root.update()
        self.assertEqual(self.go(app.export_tab), [("select", str(app.export_tab))])
        self.assertEqual(self.size(), (width, mine))
        # Shorter than a page's height: grows again, and gives back exactly that height.
        self.go(app.cvar_tab)
        self.assertEqual(self.size(), (width, look))
        self.go(app.setup_tab)
        self.assertEqual(self.size(), (width, mine))
        # Already taller than Camera's height: no page changes it.
        self.geometry(f"{width}x{camera + self.px(40)}")
        self.root.update()
        for tab in (app.camera_tab, app.export_tab, app.cvar_tab, app.setup_tab):
            self.assertEqual(self.go(tab), [("select", str(tab))])
            self.assertEqual(self.size(), (width, camera + self.px(40)))

    def test_a_page_hidden_while_the_window_changed_width_is_measured_once_it_shows(self):
        # A hidden page keeps the width it last showed at: its height is known only once it
        # shows at the new one, and is then set once. Camera then follows Look's new height.
        app = self.app
        self.show(self.page_area(1200, 810))
        self.go(app.cvar_tab)
        self.go(app.setup_tab)
        width = self.px(1000)
        self.geometry(f"{width}x{self.px(810) + window_bars.bars_px(app.ui_scale)}")
        self.root.update()
        calls = self.go(app.cvar_tab)
        look = self.root.winfo_height()
        self.assertEqual(calls, [("select", str(app.cvar_tab)), ("geometry", f"{width}x{look}")])
        self.assert_whole_page(app.look_page)
        self.assertEqual(self.go(app.camera_tab), [("select", str(app.camera_tab))])
        self.assertEqual(self.size(), (width, look))


class TallPageWindowHighDpiTests(TallPageWindowTests):
    """The same at 150 %."""

    SCALE = 1.5


class WindowBarsShellTests(ShellCase):
    """USER-ADDITIONS UPDATE 20 (Figma Frame 79, [design reference]) in the real app: Dolly's title bar
    with File and Edit and the caption buttons on top, the link bar at the bottom, and every
    page keeping the page area it had under the native caption."""

    def test_the_bars_span_the_window_above_and_below_the_sidebar_and_stage(self):
        app = self.app
        self.show(self.page_area(1200, 810))
        top, bottom = app.title_bar, app.link_bar
        self.assertEqual(top.grid_info()["row"], 0)
        self.assertEqual(bottom.grid_info()["row"], 2)
        for bar in (top, bottom):
            self.assertEqual((bar.grid_info()["column"], bar.grid_info()["columnspan"]), (0, 2))
            self.assertEqual((bar.winfo_x(), bar.winfo_width()), (0, self.root.winfo_width()))
        self.assertEqual((top.winfo_y(), top.winfo_height()), (0, self.px(29)))
        self.assertEqual(bottom.winfo_height(), self.px(29))
        self.assertEqual(bottom.winfo_y() + bottom.winfo_height(), self.root.winfo_height())
        for column in (app.sidebar, self.stage):
            self.assertEqual(column.winfo_y(), self.px(29))
            self.assertEqual(column.winfo_height(), self.px(810), "the page area the window had before")
        self.assertEqual(self.stage.mode, "design")
        # No native menu bar any more: File and Edit live on the title bar.
        self.assertEqual(str(self.root.cget("menu")), "")
        self.assertEqual(list(top.menus), ["File", "Edit"])
        self.assertIs(top.menus["File"], app.file_menu)
        self.assertIs(top.menus["Edit"], app.edit_menu)

    def test_file_and_edit_keep_their_entries_shortcuts_and_history_refresh(self):
        app = self.app
        entries = [(app.file_menu.entrycget(i, "label"), app.file_menu.entrycget(i, "accelerator"))
                   for i in range(app.file_menu.index("end") + 1) if app.file_menu.type(i) == "command"]
        self.assertEqual(entries, [("New shot", "Ctrl+N"), ("Open shot…", "Ctrl+O"), ("Save", "Ctrl+S"),
                                   ("Save as…", ""), ("Rename shot…", ""), ("Controls…", ""),
                                   ("Export diagnostics…", ""), ("Check Native DOF support", ""),
                                   ("Recover game configuration…", ""), ("Exit", "")])
        self.assertEqual([app.edit_menu.entrycget(i, "accelerator") for i in (0, 1)], ["Ctrl+Z", "Ctrl+Y"])
        # Edit's entries follow the shot history each time it opens (Tk runs the postcommand on post).
        self.root.tk.eval(str(app.edit_menu.cget("postcommand")))
        self.assertEqual([app.edit_menu.entrycget(i, "state") for i in (0, 1)], ["disabled", "disabled"])
        for key in ("<Control-s>", "<Control-o>", "<Control-n>", "<Control-z>", "<Control-y>"):
            self.assertTrue(self.root.bind(key), key)

    def test_the_top_bar_shows_the_shot_title_after_a_divider_and_follows_it(self):
        """Kara: "add shot title in the top bar" (the shot name and its unsaved marker)."""
        app = self.app
        self.show()
        bar = app.title_bar
        self.assertEqual(bar.itemcget(bar.title_item, "text"), "Untitled shot")
        # After a divider right of Edit, like the link bar's credit.
        divider = bar.coords(bar.title_divider)
        self.assertGreater(divider[0], bar.bbox(bar._labels["Edit"])[2])
        self.assertGreater(bar.coords(bar.title_item)[0], divider[2])
        self.assertEqual(bar.itemcget(bar.title_item, "fill"), ui_theme.TOKENS["muted"])   # like File and Edit
        app.dirty = True
        app._title()
        self.assertEqual(bar.itemcget(bar.title_item, "text"), app.project.name + " *")

    def test_alt_f_and_alt_e_open_the_menus_from_the_keyboard(self):
        app = self.app
        self.show()
        posted = []
        for name, menu in (("File", app.file_menu), ("Edit", app.edit_menu)):
            patch.object(menu, "tk_popup", side_effect=lambda x, y, name=name: posted.append(name)).start()
        field = app.library_view.search_entry   # a text field: its class bindings let Alt+F through
        for widget in (self.stage, field):
            for sequence in ("<Alt-KeyPress-f>", "<Alt-KeyPress-E>"):
                widget.event_generate(sequence)
                self.root.update()
        self.assertEqual(posted, ["File", "Edit", "File", "Edit"])

    def test_the_close_button_runs_the_windows_close_path(self):
        app = self.app
        self.show()
        info = patch("dolly.gui.messagebox.showinfo").start()
        app.busy = True
        app.title_bar.invoke("close")
        info.assert_called_once()
        self.assertEqual(info.call_args.args[0], "Operation in progress")
        # The same handler Windows' close (Alt+F4, the taskbar) runs.
        self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        self.assertEqual(info.call_count, 2)
        app.busy = False
        patch.object(app, "_allow_discard", return_value=False).start()
        app.title_bar.invoke("close")
        self.assertFalse(app._closing, "unsaved changes kept: the window stays")
        self.assertTrue(self.root.winfo_exists())

    def test_minimize_and_maximize_go_through_the_window_chrome(self):
        app = self.app
        chrome = app.window_chrome
        for name, method in (("minimize", "minimize"), ("maximize", "toggle_maximize")):
            with patch.object(chrome, method) as action:
                app.title_bar.invoke(name)
            action.assert_called_once_with()
        with patch.object(chrome, "start_move", return_value=False) as move:
            self.show()
            bar = app.title_bar
            bar.event_generate("<ButtonPress-1>", x=bar.winfo_width() // 2, y=self.px(12))
            bar.event_generate("<ButtonRelease-1>", x=bar.winfo_width() // 2, y=self.px(12))
            self.root.update()
        move.assert_called_once_with()

    def test_a_hidden_test_window_keeps_its_native_frame_and_still_draws_the_buttons(self):
        app = self.app
        self.assertFalse(app.window_chrome.frameless)
        self.assertIn(app.window_chrome.skipped, ("quiet", "withdrawn"))
        self.assertFalse(self.root.tk.getboolean(self.root.tk.call("wm", "overrideredirect", self.root._w)))
        self.show()
        self.assertEqual(sorted(app.title_bar.button_boxes), ["close", "maximize", "minimize"])
        self.assertFalse(app.window_chrome.start_move(), "dragging is Windows' own, and only once frameless")

    def test_the_first_map_hook_runs_once_on_the_window_itself_and_is_then_dropped(self):
        app = self.app
        tag = window_chrome.window_tag(self.root)
        self.assertNotIn("_finish_window_chrome", self.root.bind("<Map>"), "not on every child's <Map>")
        self.assertIn("_finish_window_chrome", self.root.bind_class(tag, "<Map>"))
        self.show()
        self.assertTrue(app._window_chrome_done)
        binding = self.root.bind_class(tag, "<Map>")
        self.assertNotIn("_finish_window_chrome", binding)
        self.assertTrue(binding.strip(), "the title bar's own <Map> handler stays")


class HighDpiWindowTests(unittest.TestCase):
    """Window sizes are design px at the display scale and always fit the monitor's work area.

    USER-ADDITIONS UPDATE 20: the main window's client is the 1200 x 810 design px page area
    plus the 26 px title bar and the 29 px link bar (39 + 44 = 83 px at 150 %). Dolly's own
    frame adds nothing round it; a window that keeps the native caption (the fallback, and
    every hidden test window) still leaves room for that frame.
    """

    def app(self, scale, screen=(2560, 1600), *, frameless=False):
        app = DollyApp.__new__(DollyApp)
        app.ui_scale = scale
        app.root = Mock(winfo_screenwidth=Mock(return_value=screen[0]),
                        winfo_screenheight=Mock(return_value=screen[1]))
        if frameless:
            app.window_chrome = Mock(frameless=True)
        return app

    def test_default_size_is_the_design_size_at_150_percent_centred_on_the_work_area(self):
        with patch("dolly.gui.chrome.work_area", return_value=(0, 0, 2560, 1552)),                 patch("dolly.gui.chrome.frame_size", return_value=(22, 81)) as frame:
            app = self.app(1.5, frameless=True)
            self.assertEqual(app._bars_px(), 88)
            self.assertEqual(app._main_dimensions(1200, 810), (1800, 1215 + 88))
            self.assertEqual(app._main_dimensions(1000, 700), (1500, 1050 + 88))
            self.assertEqual(app._main_size(1200, 810, centered=True), "1800x1303+380+124")
            frame.assert_not_called()
            # Dialogs keep their native frames.
            self.assertEqual(app._window_size(850, 300), "1275x450")
            # With the native caption (the fallback) its frame still has to fit.
            app = self.app(1.5)
            self.assertEqual(app._main_dimensions(1200, 810), (1800, 1303))
            self.assertEqual(app._main_size(1200, 810, centered=True), "1800x1303+369+84")
            frame.assert_called_with(menu=False)

    def test_a_small_work_area_clamps_size_and_minimum_so_the_window_fits(self):
        # 1920 x 1080 at 150 % with a 48 px taskbar: 1280 x 688 design px of desktop.
        with patch("dolly.gui.chrome.work_area", return_value=(0, 0, 1920, 1032)),                 patch("dolly.gui.chrome.frame_size", return_value=(22, 81)):
            app = self.app(1.5, (1920, 1080), frameless=True)
            self.assertEqual(app._main_dimensions(1200, 810), (1800, 1032))
            self.assertEqual(app._main_dimensions(1000, 700), (1500, 1032))
            self.assertEqual(app._main_size(1200, 810, centered=True), "1800x1032+60+0")
            app = self.app(1.5, (1920, 1080))
            self.assertEqual(app._main_dimensions(1200, 810), (1800, 951))
            self.assertEqual(app._main_dimensions(1000, 700), (1500, 951))
            self.assertEqual(app._main_size(1200, 810, centered=True), "1800x951+49+0")

    def test_the_window_opens_on_a_secondary_work_area_origin(self):
        with patch("dolly.gui.chrome.work_area", return_value=(-1920, 200, 0, 1240)),                 patch("dolly.gui.chrome.frame_size", return_value=(16, 59)):
            app = self.app(1.0, (2560, 1440), frameless=True)
            self.assertEqual(app._main_size(1200, 810, centered=True), "1200x868+-1560+286")
            app = self.app(1.0, (2560, 1440))
            self.assertEqual(app._main_size(1200, 810, centered=True), "1200x868+-1568+256")

    def test_a_larger_secondary_monitor_is_not_capped_by_the_primary_screen_size(self):
        # Tk's winfo_screenwidth/height describe the smaller primary display.
        with patch("dolly.gui.chrome.work_area", return_value=(1920, 0, 5760, 2100)), \
                patch("dolly.gui.chrome.frame_size", return_value=(32, 78)):
            app = self.app(2.0, (1920, 1080))
            self.assertEqual(app._main_dimensions(1200, 810), (2400, 1736))
            self.assertEqual(app._window_dimensions(820, 756), (1640, 1512))

    def check_camera_and_look_growth(self, app, root, frame):
        """Look needs 1440 px (bars included) and Camera takes the same height; the window is
        1298 px (the 1215 px page area and 83 px of bars) at y 300 on a 1552 px work area."""
        top = 1552 - frame - 1440   # as far up as the grown window must move

        def fit(tab, height=None, y=None):
            root.geometry.reset_mock()
            if height is not None:
                root.winfo_height.return_value, root.winfo_y.return_value = height, y
            app._fit_window_to_page(tab)
            return [call.args[0] for call in root.geometry.call_args_list]
        self.assertEqual(fit("camera"), [f"1800x1440+369+{top}"])
        # Camera -> Look: the same height, nothing changes.
        self.assertEqual(fit("look", 1440, top), [])
        self.assertEqual(fit("export", 1440, top), ["1800x1298+369+300"])
        # Moved by the user while grown: only the height goes back.
        self.assertEqual(fit("look", 1298, 300), [f"1800x1440+369+{top}"])
        self.assertEqual(fit("settings", 1440, 100), ["1800x1298"])
        # Room below: the top edge stays.
        self.assertEqual(fit("look", 1298, 20), ["1800x1440"])
        self.assertEqual(fit("camera", 1440, 20), [])
        self.assertEqual(fit("export", 1440, 20), ["1800x1298"])
        # Maximized or hidden windows are Windows' or nobody's to size.
        for state, mapped in (("zoomed", True), ("normal", False), ("withdrawn", False)):
            root.wm_state.return_value, root.winfo_ismapped.return_value = state, mapped
            self.assertEqual(fit("camera", 1298, 20), [])
            self.assertIsNone(getattr(app, "_grown_window", None))

    def test_camera_and_look_grow_upwards_only_as_far_as_the_work_area_needs(self):
        # USER-ADDITIONS UPDATE 10, 17.3 and 19 at 150 %. A window whose bottom would leave the
        # work area moves up just enough, and back down with its old height. Dolly's own frame
        # needs no room round the client; the native one (the fallback) needs 81 px.
        for frameless, frame in ((True, 0), (False, 81)):
            with self.subTest(frameless=frameless),                     patch("dolly.gui.chrome.work_area", return_value=(0, 0, 2560, 1552)),                     patch("dolly.gui.chrome.frame_size", return_value=(22, 81)):
                app = self.app(1.5, frameless=frameless)
                app.camera_tab, app.cvar_tab = "camera", "look"
                app._page_height = {"camera": 1440, "look": 1440}.get
                root = app.root
                root.configure_mock(**{"wm_state.return_value": "normal", "winfo_ismapped.return_value": True,
                                       "winfo_width.return_value": 1800, "winfo_height.return_value": 1298,
                                       "winfo_x.return_value": 369, "winfo_y.return_value": 300})
                self.check_camera_and_look_growth(app, root, frame)

    def test_design_px_helper_and_a_bare_test_object_draw_at_100_percent(self):
        self.assertEqual(self.app(1.5)._px((0, 7, 25)), (0, 10, 38))
        self.assertEqual(DollyApp.__new__(DollyApp)._px((0, 7, 25)), (0, 7, 25))

    def test_main_declares_dpi_awareness_before_the_first_window(self):
        order = []
        with patch("dolly.gui.enable_dpi_awareness", side_effect=lambda: order.append("aware") or True), \
                patch("dolly.gui.tk.Tk", side_effect=lambda: order.append("window") or Mock()), \
                patch("dolly.gui.DollyApp"), patch("dolly.update_ui.UpdateUI"):
            from dolly import gui
            gui.main()
        self.assertEqual(order, ["aware", "window"])

    def test_a_failed_custom_frame_sizes_the_window_again_for_the_native_frame(self):
        # A 1366 x 768 laptop at 100 %: the frameless window filled its 728 px work area exactly.
        # Back with the native caption it must shrink so the framed window still fits.
        with patch("dolly.gui.chrome.work_area", return_value=(0, 0, 1366, 728)), \
                patch("dolly.gui.chrome.frame_size", return_value=(16, 39)), \
                patch("dolly.gui.chrome.style_title_bar") as style:
            app = self.app(1.0, (1366, 768), frameless=True)
            self.assertEqual(app._main_size(1200, 810, centered=True), "1200x728+83+0")
            window = app.window_chrome

            def finish():
                window.frameless = False   # finish() gave the window its native frame back
                return False
            window.finish.side_effect = finish
            app.title_bar = Mock()
            app._finish_window_chrome()
        app.title_bar.set_caption_buttons.assert_called_once_with(False)
        app.root.minsize.assert_called_once_with(1000, 688)
        app.root.geometry.assert_called_once_with("1200x688+75+0")
        style.assert_called_once_with(app.root)


class WindowChromeAppTests(unittest.TestCase):
    """USER-ADDITIONS UPDATE 20: how the app hands its frameless window to Windows and back."""

    def test_after_the_game_exits_a_minimized_window_comes_back_activated(self):
        app = DollyApp.__new__(DollyApp)
        app.root = Mock(**{"state.return_value": "iconic"})
        app.window_chrome = Mock(active=True)
        app._restore_from_taskbar()
        app.window_chrome.restore.assert_called_once_with()   # posted to Windows: activates like a taskbar click
        app.root.deiconify.assert_not_called()
        app.window_chrome = Mock(active=False)   # the native frame: Tk's own deiconify activates it
        app._restore_from_taskbar()
        app.root.deiconify.assert_called_once_with()
        app.window_chrome.restore.assert_not_called()

    def test_main_hands_the_window_back_whether_the_app_closes_or_fails_to_start(self):
        from dolly import gui
        root = Mock()
        with patch("dolly.gui.enable_dpi_awareness"), patch("dolly.gui.tk.Tk", return_value=root), \
                patch("dolly.gui.window_chrome.release") as release:
            with patch("dolly.gui.DollyApp", side_effect=RuntimeError("a page failed to build")):
                with self.assertRaises(RuntimeError):
                    gui.main()
            release.assert_called_once_with(root)
            with patch("dolly.gui.DollyApp"), patch("dolly.update_ui.UpdateUI"):
                gui.main()
            root.mainloop.assert_called_once_with()
            self.assertEqual(release.call_count, 2)


if __name__ == "__main__":
    unittest.main()
