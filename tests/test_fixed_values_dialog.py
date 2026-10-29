"""Fixed-values editing stays usable at the supported desktop scales."""
from tkinter import ttk

from test_ui_shell import ShellCase


class FixedValuesDialogTests(ShellCase):
    SCALE = 1.0

    def test_controls_fit_and_set_and_remove_still_work(self):
        self.show()
        app = self.app
        app._show_fixed_values()
        self.root.update()
        dialog = app.fixed_dialog

        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)

        controls = [w for w in descendants(dialog) if isinstance(w, (ttk.Entry, ttk.Button))]
        self.assertEqual(len(controls), 4)
        for control in controls:
            x = control.winfo_rootx() - dialog.winfo_rootx()
            y = control.winfo_rooty() - dialog.winfo_rooty()
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + control.winfo_width(), dialog.winfo_width())
            self.assertLessEqual(y + control.winfo_height(), dialog.winfo_height())

        app.setup_name.set("r_depth_of_field")
        app.setup_value.set("1")
        next(w for w in controls if isinstance(w, ttk.Button) and w.cget("text") == "Set value").invoke()
        self.assertIn("r_depth_of_field", app.project.setup_values)
        app.setup_tree.selection_set(app.setup_tree.get_children()[0])
        next(w for w in controls if isinstance(w, ttk.Button) and w.cget("text") == "Remove").invoke()
        self.assertNotIn("r_depth_of_field", app.project.setup_values)


class FixedValuesDialog150Tests(FixedValuesDialogTests):
    SCALE = 1.5


class FixedValuesDialog200Tests(FixedValuesDialogTests):
    SCALE = 2.0
