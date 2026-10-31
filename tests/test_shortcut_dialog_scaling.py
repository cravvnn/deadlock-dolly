"""Shortcut recording controls remain within the window at desktop scales."""
from tkinter import ttk

from test_controls_page import ControlsAppFixture, walk


class ShortcutDialogTests(ControlsAppFixture):
    fixture_scale = 1.0

    def test_controls_fit_and_escape_shortcut_can_be_selected(self):
        dialog = self.open_recorder()
        controls = [w for w in walk(dialog) if isinstance(w, ttk.Button)]
        self.assertEqual({w.cget("text") for w in controls},
                         {"Cancel", "Save shortcut", "Record again", "Use Escape as the shortcut"})
        for control in controls:
            x = control.winfo_rootx() - dialog.winfo_rootx()
            y = control.winfo_rooty() - dialog.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width())
            self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height())
        self.button("Use Escape as the shortcut").invoke()
        self.assertTrue(self.app._binding_capture.ready)
        self.assertEqual(self.app._binding_capture.candidate.key, "Escape")
        self.button("Cancel").invoke()
        self.assertIsNone(self.app.binding_dialog)


class ShortcutDialog150Tests(ShortcutDialogTests):
    fixture_scale = 1.5


class ShortcutDialog200Tests(ShortcutDialogTests):
    fixture_scale = 2.0
