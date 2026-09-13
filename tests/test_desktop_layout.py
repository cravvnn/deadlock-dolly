"""Real Tk layout transitions, without game commands or user preference writes."""
import copy
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
        self.settings = patch.object(DollyApp, "_load_app_settings", return_value=AppSettings()).start()
        self.addCleanup(patch.stopall)
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closed = True
        self.app.jobs.put(None)
        self.root.destroy()

    def test_layout_switch_preserves_shot_and_export_settings(self):
        app = self.app
        app.project.keyframes = [Keyframe(0, 1, 2, 3, 4, 5, 6)]
        app.dirty = True
        app.video_layer_players.set(True)
        before = copy.deepcopy(app.project)
        with patch("dolly.gui.save_settings") as save:
            app.full_editor.set(True)
            app._toggle_full_editor()
            self.assertTrue(save.call_args.args[0].full_editor)
            app.notebook.select(app.camera_tab)
            app.full_editor.set(False)
            app._toggle_full_editor()
        self.assertEqual(app.project, before)
        self.assertTrue(app.dirty)
        self.assertTrue(app.video_layer_players.get())
        self.assertEqual(app.notebook.select(), str(app.setup_tab))
        self.assertEqual(app.notebook.tab(app.camera_tab, "state"), "hidden")
        self.assertEqual(app.notebook.tab(app.cvar_tab, "state"), "hidden")
        self.assertFalse(app.app_settings.full_editor)

    def test_keybind_shortcut_opens_settings_without_full_editor(self):
        self.app._show_keybinds("reshade")
        self.assertEqual(self.app.notebook.select(), str(self.app.settings_tab))
        self.assertTrue(self.app.controls_disclosure.opened)
        self.assertEqual(self.app.bindings_tree.selection(), ("reshade",))
        self.assertFalse(self.app.full_editor.get())

    def test_simple_startup_stays_in_library(self):
        self.app.notebook.select(self.app.setup_tab)
        with patch("dolly.gui.editor_session.configure"):
            self.app._editing_started(None)
        self.assertEqual(self.app.notebook.select(), str(self.app.setup_tab))

    def test_busy_layout_switch_does_not_overwrite_pending_preferences(self):
        self.app.busy = True
        self.app.full_editor.set(True)
        with patch("dolly.gui.save_settings") as save:
            self.app._toggle_full_editor()
        save.assert_not_called()
        self.assertFalse(self.app.full_editor.get())
