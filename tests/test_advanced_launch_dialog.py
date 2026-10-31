"""Advanced launch controls remain available at desktop DPI scales."""
from tkinter import ttk
from test_ui_shell import ShellCase


class AdvancedLaunchDialogTests(ShellCase):
    SCALE = 1.0

    def test_controls_fit_and_keep_their_initial_states(self):
        self.show()
        app = self.app
        dialog = app.advanced_startup_dialog
        dialog.deiconify()
        self.root.update()
        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)
        controls = [w for w in walk(dialog) if isinstance(w, (ttk.Entry, ttk.Button, ttk.Combobox))]
        for control in controls:
            x = control.winfo_rootx() - dialog.winfo_rootx()
            y = control.winfo_rooty() - dialog.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width(), str(control))
            self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height(), str(control))
            self.assertLessEqual(control.winfo_x() + control.winfo_width(), control.master.winfo_width(), str(control))
            self.assertLessEqual(control.winfo_y() + control.winfo_height(), control.master.winfo_height(), str(control))
        self.assertFalse(app.launch_button.instate(["disabled"]))
        for button in (app.connect_button, app.initialize_button, app.load_replay_button,
                       app.probe_button, app.disconnect_button):
            self.assertTrue(button.instate(["disabled"]))
        dialog.withdraw()


class AdvancedLaunch150Tests(AdvancedLaunchDialogTests):
    SCALE = 1.5


class AdvancedLaunch200Tests(AdvancedLaunchDialogTests):
    SCALE = 2.0
