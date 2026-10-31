"""Replay sorting preserves the chosen file and remains usable at desktop scales."""
from pathlib import Path

from dolly.replays import ReplayEntry
from dolly.ui.kit_controls import buttons
from test_ui_shell import ShellCase


def fixtures():
    return [ReplayEntry(Path("C:/replays/gamma.dem"), "gamma.dem", 20, 300),
            ReplayEntry(Path("C:/replays/Alpha.dem"), "Alpha.dem", 100, 100),
            ReplayEntry(Path("C:/replays/beta.dem"), "beta.dem", 5, 200)]


class ReplaySortTests(ShellCase):
    SCALE = 1.0

    def test_sorting_uses_real_numeric_values_and_preserves_selection(self):
        self.show(tab=self.app.setup_tab)
        app = self.app
        app.replay_entries = fixtures()
        app.demo_path.set(str(app.replay_entries[2].path))
        app._filter_replays()
        def names():
            return [app.replay_tree.item(row, "values")[0] for row in app.replay_tree.get_children()]
        for choice, expected in (("Name ↑", ["Alpha.dem", "beta.dem", "gamma.dem"]),
                                 ("Name ↓", ["gamma.dem", "beta.dem", "Alpha.dem"]),
                                 ("Size ↑", ["beta.dem", "gamma.dem", "Alpha.dem"]),
                                 ("Size ↓", ["Alpha.dem", "gamma.dem", "beta.dem"]),
                                 ("Modified ↑", ["Alpha.dem", "beta.dem", "gamma.dem"]),
                                 ("Modified ↓", ["gamma.dem", "beta.dem", "Alpha.dem"])):
            app.replay_sort.set(choice)
            self.root.update()
            self.assertEqual(names(), expected)
            self.assertEqual(app.replay_tree.selection(), ("2",))
            self.assertEqual(app.demo_path.get(), str(app.replay_entries[2].path))
        app.replay_search.set("beta")
        self.root.update()
        self.assertEqual(names(), ["beta.dem"])
        app._select_replay()
        self.assertEqual(app.demo_path.get(), str(app.replay_entries[2].path))
        app.replay_search.set("")
        self.root.update()
        self.assertEqual(app.replay_tree.selection(), ("2",))

    def test_toolbar_fits_without_overlap(self):
        self.show(tab=self.app.setup_tab)
        view = self.app.library_view
        sort_group = view.sort_combo.master
        toolbar = sort_group.master
        widgets = [view.search_entry, sort_group, *buttons(toolbar)]
        self.assertEqual(len(widgets), 4)
        boxes = [(w.winfo_rootx(), w.winfo_rootx() + w.winfo_width()) for w in widgets]
        for left, right in zip(boxes, boxes[1:]):
            self.assertLessEqual(left[1], right[0])
        self.assertLessEqual(boxes[-1][1], toolbar.winfo_rootx() + toolbar.winfo_width())
        self.assertEqual(tuple(view.sort_combo.cget("values")),
                         ("Name ↑", "Name ↓", "Size ↑", "Size ↓", "Modified ↑", "Modified ↓"))


class ReplaySort150Tests(ReplaySortTests):
    SCALE = 1.5


class ReplaySort200Tests(ReplaySortTests):
    SCALE = 2.0
