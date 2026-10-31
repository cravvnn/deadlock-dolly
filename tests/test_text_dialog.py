"""The redesigned name prompt retains modal result and cancellation behavior."""
import tkinter as tk
from tkinter import ttk
import unittest

from dolly import gui_theme
from dolly.ui import text_dialog


class TextDialogTests(unittest.TestCase):
    SCALE = 1.0

    def setUp(self):
        self.root = tk.Tk()
        self.old_scale = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", self.SCALE * 96 / 72)
        gui_theme.initialize(self.root)
        self.root.update()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.old_scale)
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def prompt(self, action):
        def interact():
            dialog = next((w for w in self.root.winfo_children() if isinstance(w, text_dialog._TextDialog)), None)
            if dialog is None:
                self.root.after(20, interact)
                return
            try:
                self.assertIs(dialog.grab_current(), dialog)
                def walk(widget):
                    yield widget
                    for child in widget.winfo_children():
                        yield from walk(child)
                for control in (w for w in walk(dialog) if isinstance(w, (ttk.Entry, ttk.Button))):
                    x = control.winfo_rootx() - dialog.winfo_rootx()
                    y = control.winfo_rooty() - dialog.winfo_rooty()
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width())
                    self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height())
                if action == "ok":
                    self.assertEqual(dialog.entry.get(), "Existing")
                    self.assertEqual(dialog.entry.index("sel.first"), 0)
                    dialog.entry.delete(0, "end")
                    dialog.entry.insert(0, "New name")
                    dialog.ok()
                elif action == "close":
                    dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                else:
                    dialog.cancel()
            except Exception as error:
                self.errors.append(error)
                dialog.cancel()
        self.root.after(100, interact)
        return text_dialog.askstring("Rename shot", "Shot name", initialvalue="Existing", parent=self.root)

    def test_ok_and_cancel(self):
        self.assertEqual(self.prompt("ok"), "New name")
        self.assertIsNone(self.prompt("cancel"))
        self.assertIsNone(self.prompt("close"))
        self.assertIsNone(self.root.grab_current())
        self.root.grab_set()
        self.assertIsNone(self.prompt("cancel"))
        self.assertIs(self.root.grab_current(), self.root)
        self.root.grab_release()


class TextDialog150Tests(TextDialogTests):
    SCALE = 1.5


class TextDialog200Tests(TextDialogTests):
    SCALE = 2.0
