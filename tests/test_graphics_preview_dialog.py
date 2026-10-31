"""Graphics profile preview retains its real data and actions at desktop scales."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock

from dolly import gui_theme
from dolly.graphics_profiles_ui import GraphicsProfiles


class GraphicsPreviewTests(unittest.TestCase):
    SCALE = 1.0

    def test_save_preview_displays_all_sections_and_commits(self):
        root = tk.Tk()
        root.withdraw()
        old_scale = root.tk.call("tk", "scaling")
        try:
            root.tk.call("tk", "scaling", self.SCALE * 96 / 72)
            gui_theme.initialize(root)
            root.deiconify()
            root.update()
            panel = GraphicsProfiles.__new__(GraphicsProfiles)
            panel.root = root
            panel.run = lambda operation: operation()
            save = Mock()
            text = ("Source: C:\\game\\video.txt\n\nQuality values to save:\n"
                    "Texture detail bias: 0\nDepth of field: true\n\n"
                    "Excluded fields (kept from the destination):\ndeviceid\nsetting.fullscreen")
            panel.show_preview("Save graphics profile", text, save)
            root.update()
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            def walk(widget):
                yield widget
                for child in widget.winfo_children():
                    yield from walk(child)
            widgets = list(walk(dialog))
            for control in (w for w in widgets if isinstance(w, (ttk.Button, ttk.Treeview, tk.Text))):
                x = control.winfo_rootx() - dialog.winfo_rootx()
                y = control.winfo_rooty() - dialog.winfo_rooty()
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width())
                self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height())
            table = next(w for w in widgets if isinstance(w, ttk.Treeview))
            self.assertEqual([tuple(table.item(row, "values")) for row in table.get_children()],
                             [("Texture detail bias", "0"), ("Depth of field", "true")])
            view = next(w for w in widgets if isinstance(w, tk.Text))
            self.assertIn("setting.fullscreen", view.get("1.0", "end"))
            button = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Save profile for next launch")
            button.invoke()
            save.assert_called_once_with()
            self.assertFalse(dialog.winfo_exists())
        finally:
            root.tk.call("tk", "scaling", old_scale)
            root.destroy()


class GraphicsPreview150Tests(GraphicsPreviewTests):
    SCALE = 1.5


class GraphicsPreview200Tests(GraphicsPreviewTests):
    SCALE = 2.0
