"""Yes/No and Yes/No/Cancel dialogs preserve modal return values."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from dolly import gui_theme
from dolly.ui import question_dialog


class QuestionDialogTests(unittest.TestCase):
    SCALE = 1.0

    def setUp(self):
        self.root = tk.Tk()
        self.old_scale = self.root.tk.call("tk", "scaling")
        self.root.tk.call("tk", "scaling", self.SCALE * 96 / 72)
        gui_theme.initialize(self.root)
        self.root.update()
        self.errors = []

    def tearDown(self):
        self.root.tk.call("tk", "scaling", self.old_scale)
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def prompt(self, choice, cancel=False):
        def interact():
            dialog = next((w for w in self.root.winfo_children() if isinstance(w, question_dialog._QuestionDialog)), None)
            if dialog is None:
                self.root.after(20, interact)
                return
            try:
                self.assertIs(dialog.grab_current(), dialog)
                def walk(widget):
                    yield widget
                    for child in widget.winfo_children():
                        yield from walk(child)
                buttons = {w.cget("text"): w for w in walk(dialog) if isinstance(w, ttk.Button)}
                for button in buttons.values():
                    x = button.winfo_rootx() - dialog.winfo_rootx()
                    y = button.winfo_rooty() - dialog.winfo_rooty()
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + button.winfo_width(), dialog.winfo_width())
                    self.assertLessEqual(y + button.winfo_height(), dialog.winfo_height())
                if choice == "close":
                    dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
                else:
                    buttons[choice].invoke()
            except Exception as error:
                self.errors.append(error)
                dialog.cancel()
        self.root.after(100, interact)
        ask = question_dialog.askyesnocancel if cancel else question_dialog.askyesno
        return ask("Save changes?", "Save changes to this shot?", parent=self.root)

    def test_results_and_modal_grab(self):
        self.assertIs(self.prompt("Yes"), True)
        self.assertIs(self.prompt("No"), False)
        self.assertIs(self.prompt("close"), False)
        self.assertIs(self.prompt("Cancel", cancel=True), None)
        self.root.grab_set()
        self.assertIs(self.prompt("No", cancel=True), False)
        self.assertIs(self.root.grab_current(), self.root)
        self.root.grab_release()

    def test_native_fallback_if_custom_dialog_fails(self):
        with patch.object(question_dialog, "_ask", side_effect=tk.TclError("fixture")), \
                patch.object(question_dialog._system, "askyesno", return_value=True) as native:
            self.assertTrue(question_dialog.askyesno("Title", "Message", parent=self.root))
            native.assert_called_once_with("Title", "Message", parent=self.root)

    def test_info_and_warning_keep_long_messages_and_close(self):
        message = "Detailed diagnostic line.\n" * 30
        for kind, show in (("info", question_dialog.showinfo), ("warning", question_dialog.showwarning)):
            def interact():
                dialog = next((w for w in self.root.winfo_children() if isinstance(w, question_dialog._NoticeDialog)), None)
                if dialog is None:
                    self.root.after(20, interact)
                    return
                try:
                    self.assertEqual(dialog.kind, kind)
                    self.assertIs(dialog.grab_current(), dialog)
                    def walk(widget):
                        yield widget
                        for child in widget.winfo_children():
                            yield from walk(child)
                    widgets = list(walk(dialog))
                    view = next(w for w in widgets if isinstance(w, tk.Text))
                    self.assertEqual(view.get("1.0", "end-1c"), message)
                    button = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "OK")
                    y = button.winfo_rooty() - dialog.winfo_rooty()
                    self.assertLessEqual(y + button.winfo_height(), dialog.winfo_height())
                    button.invoke()
                except Exception as error:
                    self.errors.append(error)
                    dialog.cancel()
            self.root.after(100, interact)
            self.assertEqual(show("Diagnostic", message, parent=self.root), "ok")
        self.assertIsNone(self.root.grab_current())


class QuestionDialog150Tests(QuestionDialogTests):
    SCALE = 1.5


class QuestionDialog200Tests(QuestionDialogTests):
    SCALE = 2.0
