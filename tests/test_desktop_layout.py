"""Real Tk layout transitions, without game commands or user preference writes."""
import copy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from dolly.gui import DollyApp
from dolly.settings import AppSettings
from dolly.path import Keyframe

class DesktopLayoutTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.submit = patch.object(DollyApp, "_submit").start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.export_folder = Path(directory.name) / 'Remembered exports'
        self.settings = patch.object(DollyApp, "_load_app_settings",
            return_value=AppSettings(export_folder=str(self.export_folder))).start()
        patch("dolly.graphics_profiles.load_library", return_value={"format": 1, "selected": "", "profiles": []}).start()
        self.addCleanup(patch.stopall)
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.destroy()

    def test_old_reduced_preference_does_not_hide_pages_or_change_shot(self):
        from tkinter import ttk
        app = self.app
        self.assertFalse(app.app_settings.full_editor)
        app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        app.dirty = True
        app.video_layer_players.set(True)
        before = copy.deepcopy(app.project)
        app.notebook.select(app.camera_tab)
        for busy in (False, True):
            app.busy = busy
            app.full_editor.set(False)  # An older internal caller cannot hide tools.
            with patch("dolly.gui.save_settings") as save:
                app._toggle_full_editor()
            save.assert_not_called()
            self.assertTrue(app.full_editor.get())
            self.assertEqual(app.notebook.select(), str(app.camera_tab))
        self.assertEqual(app.project, before)
        self.assertTrue(app.dirty)
        self.assertTrue(app.video_layer_players.get())
        self.assertEqual(app.notebook.tab(app.camera_tab, "state"), "normal")
        self.assertEqual(app.notebook.tab(app.cvar_tab, "state"), "normal")
        self.assertEqual(len(app.navigation), 6)
        def children(parent):
            for child in parent.winfo_children():
                yield child
                yield from children(child)
        self.assertFalse([widget for widget in children(self.root)
                          if isinstance(widget, ttk.Checkbutton) and widget.cget("text") == "Full editor"])
        self.assertFalse(app.app_settings.full_editor, "Opening the editor must not rewrite preferences.")

    def test_controls_shortcut_keeps_all_pages_available(self):
        self.app._show_keybinds("reshade")
        self.assertEqual(self.app.notebook.select(), str(self.app.controls_tab))
        self.assertEqual(self.app.controls_view.search.get(), "")
        self.assertIn("reshade", self.app.controls_view.binding_buttons)
        self.assertTrue(self.app.full_editor.get())

    def test_editing_start_opens_camera_without_requiring_a_mode_switch(self):
        self.app.notebook.select(self.app.setup_tab)
        with patch("dolly.gui.editor_session.configure"):
            self.app._editing_started(None)
        self.assertEqual(self.app.notebook.select(), str(self.app.camera_tab))
        self.assertEqual(self.app.notebook.tab(self.app.setup_tab, "state"), "normal")

    def test_mouse_wheel_scrolls_page_without_changing_closed_option(self):
        self.root.deiconify(); self.root.geometry("1000x700")
        self.app.notebook.select(self.app.export_tab); self.root.update()
        combo = self.app.video_fps_combo
        value = combo.get(); page = self.app.export_page.canvas
        page.yview_moveto(0); self.root.update()
        before = page.yview()
        combo.event_generate("<MouseWheel>", delta=-120); self.root.update()
        # The page wheel eases (PLAN-5 N5); its end state is what matters here.
        from dolly.ui.tween import Animator
        Animator.of(self.root).finish(); self.root.update()
        self.assertEqual(combo.get(), value)
        self.assertGreater(page.yview()[0], before[0])
        # Explicit dropdown selection remains available.
        combo.current(2); combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.app.video_fps.get(), "120")

    def test_export_cards_align_when_encoder_options_expand(self):
        # One column like Replay's (USER-ADDITIONS UPDATE 13): a growing card (the Player POV
        # row) pushes the cards below it down by exactly its growth and moves none sideways.
        from dolly.gui_layout import Disclosure
        self.root.deiconify(); self.root.geometry("1180x800")
        self.app.notebook.select(self.app.export_tab); self.root.update()
        page = self.app.export_page
        page.layout_now()
        def rects():
            page.layout_now()
            return (page.rect_of(self.app.camera_source_card), page.rect_of(self.app.export_capture_card),
                    page.rect_of(self.app.export_passes_card))
        source, capture, passes = rects()
        self.assertFalse(page.two_columns)
        self.assertEqual(len({source[0], capture[0], passes[0]}), 1)
        self.assertLess(source[3], capture[1])
        self.assertLess(capture[3], passes[1])
        self.app.video_source.set("Player POV"); self.app._video_source_changed(); self.root.update()
        grown, still, below = rects()
        growth = (grown[3] - grown[1]) - (source[3] - source[1])
        self.assertGreater(growth, 0)
        for old, new in ((capture, still), (passes, below)):
            self.assertEqual((new[0], new[2], new[3] - new[1]), (old[0], old[2], old[3] - old[1]))
            self.assertEqual(new[1] - old[1], growth)
        def children(parent):
            for child in parent.winfo_children():
                yield child
                yield from children(child)
        self.assertFalse([child for child in children(page) if isinstance(child, Disclosure)])

    def test_launch_setup_inherits_card_surface_through_nested_fields(self):
        # Launch Setup is a card of its own in the redesign (Figma [design reference]), no
        # longer a disclosure: every frame up to the card body is on the card.
        from tkinter import ttk
        combo = self.app.home_camera_driver_combo
        body = self.app.library_view.launch_card.body
        frame = combo.master
        while True:
            self.assertIsInstance(frame, ttk.Frame)
            self.assertEqual(ttk.Style(frame).lookup(frame.cget("style"), "background"),
                             ttk.Style(frame).lookup("Card.TFrame", "background"))
            if frame is body:
                break
            frame = frame.master
        # The kit's 30 px card dropdown, still drawn on the card surface.
        self.assertEqual(str(combo.cget("style")), "Stage.Card.TCombobox")
        self.assertEqual(ttk.Style(combo).lookup("Stage.Card.TCombobox", "background"),
                         ttk.Style(combo).lookup("Card.TFrame", "background"))

    def test_graphics_profile_selector_is_available_with_current_settings_default(self):
        from dolly.graphics_profiles import CURRENT
        self.assertEqual(self.app.graphics_profiles.selection.get(), CURRENT)
        self.assertEqual(tuple(self.app.graphics_profiles.combo.cget("values")), (CURRENT,))
        self.assertIn("next launch", self.app.graphics_profiles.status.get())


class WindowSizeTests(unittest.TestCase):
    def test_editor_opens_at_the_figma_frame_and_keeps_the_design_minimum(self):
        # A 1200 x 810 design px page area (1800 x 1215 screen pixels at 150 %, clamped to the
        # work area) under the 26 px title bar and above the 29 px link bar (USER-ADDITIONS UPDATE 20).
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        app = None
        try:
            with patch.object(DollyApp, '_submit'), \
                 patch.object(DollyApp, '_load_app_settings', return_value=AppSettings()), \
                 patch('dolly.graphics_profiles.load_library', return_value={"format": 1, "selected": "", "profiles": []}), \
                 patch.object(DollyApp, '_main_size', autospec=True, side_effect=DollyApp._main_size) as size:
                app = DollyApp(root)
            first = size.call_args_list[0]
            self.assertEqual((first.args[1:], first.kwargs), ((1200, 810), {"centered": True}))
            self.assertEqual(root.wm_minsize(), app._main_dimensions(1000, 700))
            # The whole client is Dolly's: no native menu bar comes out of it, and the stage gets
            # the design's page area between the bars.
            root.deiconify()
            root.update()
            self.assertEqual(str(root.cget("menu")), "")
            self.assertEqual((root.winfo_width(), root.winfo_height()), app._main_dimensions(1200, 810))
            self.assertEqual(app.stage.winfo_height(), root.winfo_height() - app._bars_px())
        finally:
            if app is not None:
                app.closed = True
                app.jobs.put(None)
            for timer in root.tk.splitlist(root.tk.call('after', 'info')):
                root.tk.call('after', 'cancel', timer)
            root.destroy()


class GraphicsProfileStartupFailureTests(unittest.TestCase):
    def test_damaged_profile_library_reports_error_after_window_finishes_building(self):
        from dolly.launcher import LaunchError
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        app = None
        try:
            with patch.object(DollyApp, '_submit'), \
                 patch.object(DollyApp, '_load_app_settings', return_value=AppSettings()), \
                 patch('dolly.graphics_profiles.load_library', side_effect=LaunchError('Damaged profile library')), \
                 patch('dolly.gui.dialogs.show_error') as show:
                app = DollyApp(root)
                self.assertTrue(hasattr(app, 'log_widget'))
                root.update_idletasks()
                show.assert_called_once()
                self.assertIn('Damaged profile library', app.graphics_profiles.status.get())
                self.assertIn('Damaged profile library', app.log_widget.get('1.0', 'end'))
        finally:
            if app is not None:
                app.closed = True
                app.jobs.put(None)
            for timer in root.tk.splitlist(root.tk.call('after', 'info')):
                root.tk.call('after', 'cancel', timer)
            root.destroy()
