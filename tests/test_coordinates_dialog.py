"""Coordinate editor layout and actions at the supported desktop scales."""
from tkinter import ttk
from unittest.mock import Mock

from dolly.gui import FIELDS
from test_ui_shell import ShellCase


class CoordinatesDialogTests(ShellCase):
    SCALE = 1.0

    def test_fields_and_actions_fit_and_keep_their_commands(self):
        self.show()
        app = self.app
        actions = {name: Mock() for name in ("_update_key", "_add_key", "_use_current_tick", "_retime_to_replay", "_apply_options")}
        for name, command in actions.items():
            setattr(app, name, command)
        app._open_coordinates()
        self.root.update()
        dialog = app.coordinates_dialog
        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)
        widgets = list(walk(dialog))
        self.assertEqual(set(app.key_entries), set(FIELDS))
        buttons = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Button)}
        for control in (w for w in widgets if isinstance(w, (ttk.Entry, ttk.Combobox, ttk.Button))):
            x = control.winfo_rootx() - dialog.winfo_rootx()
            y = control.winfo_rooty() - dialog.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width())
            self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height())
            self.assertGreaterEqual(control.winfo_x(), 0)
            self.assertGreaterEqual(control.winfo_y(), 0)
            self.assertLessEqual(control.winfo_x() + control.winfo_width(), control.master.winfo_width())
            self.assertLessEqual(control.winfo_y() + control.winfo_height(), control.master.winfo_height())
        for label, name in (("Update selected", "_update_key"), ("Add entered camera", "_add_key"),
                            ("Use current replay tick", "_use_current_tick"), ("Retime to replay", "_retime_to_replay"),
                            ("Apply settings", "_apply_options")):
            buttons[label].invoke()
            actions[name].assert_called_once_with()
        buttons["Done"].invoke()
        self.assertFalse(dialog.winfo_exists())


class CoordinatesDialog150Tests(CoordinatesDialogTests):
    SCALE = 1.5


class CoordinatesDialog200Tests(CoordinatesDialogTests):
    SCALE = 2.0
