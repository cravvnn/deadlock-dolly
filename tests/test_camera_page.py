"""Camera page (Figma [design reference]): cards, actions, the bento, the pinned Shot Playback dock and the fade (PLAN-5 5.2)."""
from dataclasses import fields
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.curve_workspace import CameraCurveWorkspace
from dolly.path import Keyframe, Project
from dolly.ui import art, camera_page, fade, kit_controls
from dolly.ui.camera_page import CAMERA_COLUMNS, CameraActions, CameraPage, CameraState
from dolly.ui.kit_controls import CanvasButton, KitSlider
from dolly.ui.playback_card import PlaybackActions, PlaybackCard, PlaybackState
from dolly.ui.scale import px, ui_scale
from dolly.ui.tween import Animator

FIELDS = ("time", "x", "y", "z", "pitch", "yaw", "roll", "aspect_ratio")
FIELD_LABELS = ("Shot seconds", "X", "Y", "Height · Z", "Pitch °", "Yaw °", "Bank °", "Aspect ratio")


class FakeBackend:
    """The fade's window system, recorded (no window is ever made)."""

    def __init__(self):
        self.calls = []

    def create(self, owner):
        self.calls.append(("create",))
        return {"id": len(self.calls)}

    def update(self, handle, x, y, width, height, data, alpha):
        self.calls.append(("update", handle["id"], x, y, width, height))

    def blend(self, handle, alpha):
        self.calls.append(("blend", handle["id"], alpha))

    def move(self, handle, x, y):
        self.calls.append(("move", handle["id"], x, y))

    def show(self, handle):
        self.calls.append(("show", handle["id"]))

    def hide(self, handle):
        self.calls.append(("hide", handle["id"]))

    def uncloak(self, handle):
        self.calls.append(("uncloak", handle["id"]))

    def destroy(self, handle):
        self.calls.append(("destroy", handle["id"]))


def table_factory(parent, columns, labels, widths=None, height=6):
    """``DollyApp._tree`` as the app builds it (a panel frame, a table and its scrollbar)."""
    frame = tk.Frame(parent, background=ui_theme.TOKENS["panel"])
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=height)
    for column, label in zip(columns, labels):
        tree.heading(column, text=label)
        tree.column(column, width=80, minwidth=45, anchor="e", stretch=True)
    tree.grid(row=0, column=0, sticky="nsew")
    bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    bar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=bar.set)
    return frame, tree


class CameraPageCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.calls = []
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback raised an exception")

    def record(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args))

    def names(self):
        return [name for name, _args in self.calls]

    def size(self, width, height):
        """Set the window to ``width`` x ``height`` design px (the page fills it)."""
        s = ui_scale(self.root)
        self.root.geometry(f"{px(s, width)}x{px(s, height)}")
        self.root.update()

    def build(self, shown=(948, 900), *, theme=True, playback=True):
        """The page in a stand-in for the app's workspace (design px), with the Shot Playback frame docked.

        948 = the frame's 930 px content plus the 18 px rail gutter (PLAN-5 N8).
        """
        if theme:
            gui_theme.initialize(self.root)
        if shown:
            self.root.deiconify()
            self.size(*shown)
        var = lambda value="": tk.StringVar(self.root, value)  # noqa: E731
        state = CameraState(
            capture_mode=var("Replay timing"), segment_seconds=var("3"),
            hotkey_label=var("In-game capture · Ctrl+Alt+K"), hotkey_enabled=tk.BooleanVar(self.root, False),
            project_text=var("Example camera path"), path_summary=var("3 cameras · 6.00 s"),
            lens_interpolation=var("smooth"), interpolation=var("smooth"), selected_text=var("No camera selected"),
            standard_aspect=var("16:9"),
            key_vars={field: var("1.77777778" if field == "aspect_ratio" else "0") for field in FIELDS},
            aspect_presets=("16:9", "16:10", "21:9", "4:3"), tree_fields=FIELDS, tree_labels=FIELD_LABELS)
        actions = {field.name: self.record(field.name) for field in fields(CameraActions)}
        actions["tree"] = table_factory
        actions["curve_context"] = lambda: (1, 1)
        actions["apply_attach"] = lambda all_keys: self.calls.append(("apply_attach", (all_keys,)))
        workspace = tk.Frame(self.root, background="")
        workspace.pack(fill="both", expand=True)
        view = self.view = CameraPage(workspace, state, CameraActions(**actions))
        self.state = state
        if playback:
            self.playback = PlaybackCard(workspace, PlaybackState(
                shot_time=tk.DoubleVar(self.root, 0.0), time_text=var("0"), speed=var("1"),
                hide_hud=tk.BooleanVar(self.root, True), frozen=tk.BooleanVar(self.root, False),
                seek_relief=tk.BooleanVar(self.root, True), rate=var("60"), smoothing=var("Light")),
                PlaybackActions(**{field.name: self.record(field.name) for field in fields(PlaybackActions)}))
            view.playback_host.adopt(self.playback.timeline_frame)
        self.settle()
        self.calls.clear()
        return view

    def settle(self):
        """Let the page and the curve workspace finish laying out (the workspace's height arrives at idle)."""
        for _ in range(6):
            self.root.update()
        self.view.page.layout_now()
        self.root.update_idletasks()

    def press(self, widget, key="<Return>"):
        """``key`` in ``widget`` (key events go to the focused widget; without window regions a
        card scrolled out of view is unmapped, so the widget is revealed first)."""
        self.view.page.reveal(widget)
        widget.focus_force()
        self.root.update()
        widget.event_generate(key)
        self.root.update()

    def button(self, view, text):
        """The page's card button labelled ``text`` (the curve lane's own Reset is a widget, not a card button)."""
        return next(button for button in kit_controls.buttons(view.page)
                    if isinstance(button, CanvasButton) and button.cget("text") == text)

    def design(self, view, rect):
        s = view.page.scale
        return tuple(round(value / s) for value in rect)

    def cameras(self, count):
        """``count`` rows in the Cameras table, as the app's refresh leaves it, then the page's fit."""
        tree = self.view.camera_tree
        tree.delete(*tree.get_children())
        for index in range(count):
            tree.insert("", "end", iid=str(index), values=(f"Camera {index + 1:02d}",))
        rows = self.view.fit_camera_rows()
        self.settle()
        return rows

    def assertFlush(self, view):
        """The bento: both columns start on the same line and end on the same line, 20 px gutters."""
        page, s = view.page, view.page.scale
        capture, cameras, curves = (card.rect for card in (view.capture_card, view.cameras_card, view.curves_card))
        selected, attach = view.selected_card.rect, view.attach_card.rect
        self.assertEqual(capture[1], selected[1])
        self.assertEqual(curves[3], attach[3])
        for upper, lower in ((capture, cameras), (cameras, curves), (selected, attach)):
            self.assertEqual(lower[1] - upper[3], px(s, 20))
        self.assertEqual(selected[0] - capture[2], px(s, 20))
        self.assertEqual(page.content_height, attach[3])


class CameraPageTests(CameraPageCase):
    def test_titles_columns_and_card_order_follow_the_frame(self):
        view = self.build()
        page = view.page
        self.assertEqual((camera_page.TITLE, camera_page.SUBTITLE),
                         ("Camera", "Capture cameras from the replay, then edit their timing, curves and attachment."))
        main = [card.title for card in page.cards if card.column == "main"]
        side = [card.title for card in page.cards if card.column == "side"]
        self.assertEqual(main, ["Capture", "Cameras", "Camera Curves"])
        self.assertEqual(side, ["Selected Camera", "Attach Camera"])
        self.assertEqual([host.card.title for host in page.hosts], ["Shot Playback"])
        self.assertEqual(view.playback_host.where, "dock")
        host = view.playback_host     # every card title is 15 px, Shot Playback's too (UPDATE 25)
        self.assertEqual(str(host.page.dock.itemcget(host._dock["title"], "font")), "DollyCardTitle")
        for card in page.cards:
            self.assertEqual(str(card.host.itemcget(card.title_item, "font")), "DollyCardTitle", card.title)
        self.assertIs(view.camera_page, page)
        self.assertEqual(page.spec, CAMERA_COLUMNS)
        self.assertEqual(view.attach_card.info.text, camera_page.ATTACH_INFO)
        self.assertEqual(view.attach_card.title_gap, 20)
        # Only Kara's cards (UPDATE 17): the old Path · Top Down overview and its drawing hook are gone.
        for name in ("path_card", "canvas"):
            self.assertFalse(hasattr(view, name), name)
        self.assertNotIn("schedule_draw_path", {field.name for field in fields(CameraActions)})

    def test_the_cards_are_a_bento_with_flush_columns(self):
        view = self.build()
        page = view.page
        s = page.scale
        self.assertTrue(page.two_columns)
        self.assertEqual(page.CARD_GAP, camera_page.BENTO_GAP)
        self.assertFlush(view)
        rects = {card.title: self.design(view, card.rect) for card in page.cards}
        tolerance = 1 if s == 1 else 2
        # Kara's card sizes; Attach Camera is her 590 plus the live roster line under the player row.
        for title, (x, y, w, h) in (("Capture", (0, 0, 613, 173)), ("Cameras", (0, 193, 613, 199)),
                                    ("Selected Camera", (633, 0, 297, 310)), ("Attach Camera", (633, 330, 297, 640))):
            x0, y0, x1, y1 = rects[title]
            for got, want in ((x0, x), (y0, y), (x1 - x0, w), (y1 - y0, h)):
                self.assertLessEqual(abs(got - want), tolerance, (title, rects[title]))
        # The right column sets the height; the left one hands its spare to the graph
        # (three cameras: no table row is wanted), so nothing in a card is empty space.
        self.assertLessEqual(abs(page.content_height / s - 970), tolerance)
        self.assertEqual(view.attach_card.extra, 0)
        self.assertEqual(view.table_block.extra, 0)
        lane = view.position_curve
        self.assertGreater(view.lanes_block.extra, 0)
        self.assertEqual(lane.canvas.winfo_height(), px(s, 240) + view.lanes_block.extra)
        # The curve modes sit between the graph and the hint.
        modes = view.camera_curve_workspace.modes_row
        self.assertEqual(modes.grid_info()["in"], lane)
        self.assertLess(lane.canvas.winfo_y(), modes.winfo_y())
        self.assertLess(modes.winfo_y(), lane.hint.winfo_y())

    def test_switching_curve_tabs_keeps_the_card_and_gives_the_graph_the_room(self):
        view = self.build()
        page, workspace = view.page, view.camera_curve_workspace
        s = page.scale
        curves = view.curves_card.rect
        graphs = {}
        for group in (1, 2, 0):
            workspace.select_group(group)
            self.settle()
            graphs[group] = workspace.lanes[group].canvas.winfo_height()
            # The slot follows the workspace's own height; the graph takes what the card has left.
            self.assertEqual(view.lanes_block.fixed, workspace.winfo_reqheight() / s)
            self.assertEqual(graphs[group], px(s, 240) + view.lanes_block.extra)
            self.assertEqual(view.curves_card.rect, curves)
            self.assertFlush(view)
        # Rotation and Framing have no tangent row: their graphs are taller than Position's.
        self.assertGreater(graphs[1], graphs[0])
        self.assertGreater(graphs[2], graphs[0])

    def test_stacked_graphs_leave_the_right_column_at_its_own_height(self):
        view = self.build()
        page, workspace = view.page, view.camera_curve_workspace
        s = page.scale
        attach = view.attach_card.rect
        workspace.stack_var.set(True)
        workspace._layout()
        self.settle()
        # Two graphs at their least height: the left column is far taller, and Attach Camera
        # keeps its own height instead of an empty stretch (the page scrolls further).
        self.assertEqual([lane.canvas.winfo_height() for lane in workspace.lanes[:2]], [px(s, 240)] * 2)
        self.assertEqual(view.attach_card.rect, attach)
        self.assertEqual(view.attach_card.extra, 0)
        self.assertGreater(view.curves_card.rect[3] - attach[3], px(s, camera_page.STRETCH_LIMIT))
        self.assertEqual(page.content_height, view.curves_card.rect[3])
        workspace.stack_var.set(False)
        workspace._layout()
        self.settle()
        self.assertFlush(view)
        # A left column only a little taller is met: Attach Camera grows to the same bottom.
        view._share_right(px(s, 12))
        self.assertEqual(view.attach_card.extra, px(s, 12))
        view._share_right(px(s, camera_page.STRETCH_LIMIT) + 1)
        self.assertEqual(view.attach_card.extra, 0)

    def test_page_layouts_never_unmap_the_curves_or_the_table(self):
        # The bento lays the growing cards out twice a layout; an unmapped workspace would
        # cancel a curve drag or a typed lane value in progress (the lanes bind <Unmap>).
        view = self.build(playback=False)
        page = view.page
        unmapped = []
        for widget in (view.camera_curve_workspace, view.position_curve, view.camera_table):
            widget.bind("<Unmap>", lambda event, widget=widget: unmapped.append(widget) if event.widget is widget
                        else None, add="+")
        self.cameras(12)
        before = view.position_curve.canvas.winfo_height()
        for _ in range(3):
            page.layout_now()
            self.root.update()
        self.cameras(12)
        view.rotation_disclosure.set_open(True)    # the left column re-splits its spare
        self.settle()
        self.assertGreater(view.table_block.extra, 0)
        self.assertNotEqual(view.position_curve.canvas.winfo_height(), before)
        view.rotation_disclosure.set_open(False)
        self.settle()
        self.assertEqual(unmapped, [])
        self.assertEqual(view.position_curve.canvas.winfo_height(), before)

    def test_one_column_below_the_main_minimum_and_two_columns_at_930(self):
        view = self.build(shown=(762 + 18, 900))
        page = view.page
        s = page.scale
        page.layout_now()
        self.assertFalse(page.two_columns)
        order = [card.title for card in sorted(page.cards, key=lambda card: card.rect[1])]
        self.assertEqual(order, ["Capture", "Cameras", "Camera Curves", "Selected Camera", "Attach Camera"])
        self.assertEqual({card.rect[0] for card in page.cards}, {0})
        self.assertEqual(page.content_width, px(s, 613))
        # Stacked, every card at its own height: the graph at its least, no stretch.
        self.assertEqual(view.lanes_block.extra, 0)
        self.assertEqual(view.position_curve.canvas.winfo_height(), px(s, 240))
        self.size(930 + 18, 900)
        page.layout_now()
        self.assertTrue(page.two_columns)
        self.assertEqual(view.selected_card.rect[0], px(s, 633))
        self.assertFlush(view)

    def test_every_action_is_reachable_from_a_control(self):
        view = self.build()
        for text in ("Start path here", "Capture camera", "Replace selected", "Controls…", "Reset", "Update camera",
                     "Preview", "Bone Picker...", "Cancel picker", "Apply to camera", "Apply to all"):
            self.button(view, text).invoke()
        self.assertEqual(self.calls, [
            ("start_path_here", ()), ("capture_here", ()), ("replace_camera_here", ()), ("show_keybinds", ()),
            ("reset_aspect", ()), ("update_key", ()), ("apply_selected", ()), ("open_bone_picker", ()),
            ("cancel_bone_picker", ()), ("apply_attach", (False,)), ("apply_attach", (True,))])
        self.calls.clear()
        view.capture_mode_combo.event_generate("<<ComboboxSelected>>")
        view.capture_hotkey_checkbox.invoke()
        view.camera_tree.event_generate("<<TreeviewSelect>>")
        self.press(view.camera_tree, "<Delete>")
        view.more_menu.invoke(0)
        view.more_menu.invoke(2)
        for entry in (*view.key_entries.values(), view.standard_combo):
            self.press(entry)
        view.standard_combo.event_generate("<<ComboboxSelected>>")
        for combo in (view.path_curve_mode, view.lens_curve_mode):
            combo.event_generate("<<ComboboxSelected>>")
        self.root.update()
        names = self.names()
        for name in ("capture_mode_changed", "toggle_capture_hotkey", "select_key", "delete_key", "open_coordinates",
                     "update_key", "apply_options"):
            self.assertIn(name, names)
        self.assertEqual(names.count("update_key"), 3)
        self.assertEqual(names.count("delete_key"), 2)
        self.assertEqual(names.count("apply_options"), 4)
        # The curve callbacks go straight to the workspace.
        workspace = view.camera_curve_workspace
        self.assertIsInstance(workspace, CameraCurveWorkspace)
        commands = {name: getattr(workspace.state, attr) for name, attr in (
            ("change_unified_curve", "on_change"), ("change_camera_tangent", "on_tangent_change"))}
        commands.update(select_curve_key=workspace.on_select, reset_rotation_curve=workspace.on_reset,
                        preview_camera_curve=workspace.on_preview)
        self.calls.clear()
        for name, callback in commands.items():
            callback()
            self.assertEqual(self.calls[-1][0], name)
        self.assertEqual(workspace.get_context(), (1, 1))
        self.assertIs(workspace.state.view, view.timeline_view)

    def test_the_curve_card_hosts_the_legend_without_view_tools(self):
        view = self.build()
        workspace = view.camera_curve_workspace
        # Kara removed "Update original" and "Frame all" from the Camera Curves card.
        self.assertIsNone(workspace.update_original_button)
        self.assertIsNone(workspace.frame_all_button)
        self.assertEqual(view.curves_card.trailing.core.cells, [])
        self.assertIsNone(workspace.tools)
        self.assertTrue(workspace.fill_lanes)   # the graphs take the card's spare height
        self.assertEqual(workspace.footer.winfo_manager(), "")
        self.assertEqual(view.curves_card.info.text, workspace.footer.cget("text"))
        workspace.original_var.set(True)
        workspace._show_original()
        self.assertIn("Solid: current; dashed: original", view.curves_card.info.text)
        self.assertEqual((view.path_curve_mode.cget("textvariable"), view.lens_curve_mode.cget("textvariable")),
                         (str(self.state.interpolation), str(self.state.lens_interpolation)))
        self.assertEqual(tuple(view.path_curve_mode.cget("values")), ("smooth", "spline", "linear"))
        self.assertEqual(tuple(view.lens_curve_mode.cget("values")), ("smooth", "linear", "step"))
        self.assertEqual((view.position_curve, view.rotation_curve, view.aspect_curve), workspace.lanes)
        self.assertIs(view.camera_curve_tabs, workspace.tabs)
        # Stacked graphs: the Graphs dropdown replaces the tabs and the modes go under the last lane.
        workspace.stack_var.set(True)
        workspace._layout()
        self.root.update_idletasks()
        self.assertEqual(workspace.tabs.winfo_manager(), "")
        self.assertEqual(workspace.graphs_button.winfo_manager(), "grid")
        self.assertEqual(workspace.modes_row.grid_info()["in"], workspace.body)
        self.assertEqual(int(workspace.modes_row.grid_info()["row"]), len(workspace.visible_groups()))

    def test_attach_variables_keep_their_names_order_and_defaults(self):
        view = self.build(playback=False)
        self.assertEqual(list(view.attach_vars), ["x", "y", "z", "pitch", "yaw", "roll"])
        self.assertEqual({name: variable.get() for name, variable in view.attach_vars.items()},
                         dict.fromkeys(view.attach_vars, "0"))
        self.assertEqual((view.attach_enabled.get(), view.attach_player.get(), view.attach_point.get(),
                          view.attach_bone.get(), view.attach_smoothing.get(), view.attach_hide.get(),
                          view.attach_clearance.get(), view.source_blend.get()),
                         (False, "", "Eyes", "head", "0", True, "Exact offset", "0"))
        # Sliders and their fields share the variable; a drag writes the compact value.
        slider, entry = view.attach_sliders["x"]
        self.assertIsInstance(slider, KitSlider)
        self.assertEqual((slider.cget("from"), slider.cget("to")), (-10.0, 10.0))
        self.assertEqual(str(entry.cget("textvariable")), str(view.attach_vars["x"]))
        slider._change(2.5, user=True)
        self.assertEqual(view.attach_vars["x"].get(), "2.5")
        self.assertEqual((view.attach_sliders["pitch"][0].cget("to"), view.attach_sliders["smooth"][0].cget("to"),
                          view.attach_sliders["blend"][0].cget("to")), (180.0, 1.0, 10.0))
        self.assertEqual(view.attach_note.cget("text"), "Load a replay to list players.")
        view.attach_note.configure(text="Live roster: 3 player(s).")
        self.assertEqual(view.attach_note.cget("text"), "Live roster: 3 player(s).")
        self.assertEqual(tuple(view.attach_point_combo.cget("values")), ("Eyes", "Weapon", "Bone Picker..."))

    def test_the_bone_row_follows_the_attach_point_and_the_picker_choice_asks_the_app(self):
        view = self.build(playback=False)
        page = view.page
        self.assertFalse(view.bone_row.visible)
        height = view.attach_card.rect[3] - view.attach_card.rect[1]
        view.attach_point.set("Bone")
        page.layout_now()
        self.assertTrue(view.bone_row.visible)
        self.assertEqual(view.attach_card.rect[3] - view.attach_card.rect[1], height + px(page.scale, 40))
        self.assertFlush(view)   # the left column meets the taller right one
        self.assertEqual(view.attach_bone_combo.winfo_manager(), "place")
        view.attach_point.set("Eyes")
        page.layout_now()
        self.assertFalse(view.bone_row.visible)
        self.assertEqual(view.attach_bone_combo.winfo_manager(), "")
        self.calls.clear()
        view.attach_point.set("Bone Picker...")
        self.assertEqual(self.names(), ["attach_point_picker"])

    def test_the_cameras_table_takes_whole_rows_of_the_spare_height_then_scrolls_inside(self):
        view = self.build(playback=False)
        page, tree = view.page, view.camera_tree
        s = page.scale
        row = px(s, 36)
        self.assertEqual(str(tree.cget("style")), "Kit.Treeview")
        self.assertEqual(tuple(tree.cget("displaycolumns")), ("view", "time", "aspect_ratio", "roll"))
        self.assertEqual([tree.heading(column, "text") for column in ("view", "time", "aspect_ratio", "roll")],
                         ["Camera", "Arrive · seconds", "Aspect ratio", "Bank °"])
        attach, content = view.attach_card.rect, page.content_height
        cameras = view.cameras_card.rect
        table = view.camera_table.winfo_height()
        # The design's state has room for no extra row: however many cameras, the table
        # shows three and scrolls inside, and the page keeps its height.
        for count in (1, 5, 12):
            rows = max(3, min(count, 4))  # vertical-aim row adds room for one more camera row
            self.assertEqual(self.cameras(count), rows)
            self.assertEqual(view.cameras_card.rect[:3], cameras[:3])
            self.assertEqual((page.content_height, view.attach_card.rect), (content, attach))
            self.assertFlush(view)
        self.assertEqual(table, px(s, 26) + 3 * row)
        # Rotation & transition opened: the right column grows, the left fills it with as
        # many rows as fit (12 cameras want 8), the rest goes to the graph.
        view.rotation_disclosure.set_open(True)
        self.settle()
        spare = view.table_block.extra + view.lanes_block.extra   # the left column's whole spare
        rows = int(tree.cget("height"))
        self.assertGreater(page.content_height, content)
        self.assertEqual(rows, 3 + min(5, spare // row))
        self.assertGreater(rows, 3)
        self.assertEqual(view.table_block.extra, (rows - 3) * row)
        self.assertLess(view.lanes_block.extra, row)       # whole rows first, only the rest to the graph
        self.assertEqual(view.camera_table.winfo_height(), px(s, 26) + rows * row)
        self.assertFlush(view)
        # Fewer cameras than room: the table shows only the rows the shot fills.
        self.assertEqual(self.cameras(4), 4)
        self.assertGreaterEqual(view.lanes_block.extra, row)
        self.assertFlush(view)
        view.rotation_disclosure.set_open(False)
        self.settle()
        self.assertEqual(int(tree.cget("height")), 4)
        self.assertEqual(page.content_height, content)
        # One column: the table simply grows with the shot, 3 to 8 rows.
        self.size(762 + 18, 900)
        page.layout_now()
        self.assertFalse(page.two_columns)
        for count, rows in ((1, 3), (5, 5), (12, 8)):
            self.assertEqual(self.cameras(count), rows)
            self.assertEqual(view.cameras_card.rect[3] - view.cameras_card.rect[1],
                             cameras[3] - cameras[1] + (rows - 3) * row)
        # A double click puts the keyboard in the aspect ratio field.
        self.assertTrue(tree.bind("<Double-1>"))

    def test_selected_status_is_dim_until_a_camera_is_selected(self):
        view = self.build(playback=False)
        t = ui_theme.TOKENS
        self.assertEqual(view.selected_label.cget("foreground"), t["dim"])
        self.state.selected_text.set("Camera 02")
        self.assertEqual(view.selected_label.cget("foreground"), t["text"])
        self.state.selected_text.set("No camera selected")
        self.assertEqual(view.selected_label.cget("foreground"), t["dim"])
        self.assertEqual(str(view.lens_entry.cget("textvariable")), str(self.state.key_vars["aspect_ratio"]))
        self.assertEqual(str(view.more_button.cget("style")), "Kit.TMenubutton")
        self.assertEqual(view.more_menu.entrycget(0, "label"), "Coordinates / timing…")
        self.assertEqual(view.more_menu.entrycget(2, "label"), "Delete selected camera")
        self.assertNotEqual(str(view.standard_combo.cget("state")), "readonly")   # custom ratios on Return

    def test_capture_controls_keep_their_states_and_kit_faces(self):
        view = self.build(playback=False)
        self.assertEqual(str(view.segment_entry.cget("state")), "disabled")   # Replay timing
        self.assertEqual([button.cget("text") for button in view.capture_buttons],
                         ["Start path here", "Capture camera", "Replace selected"])
        self.assertEqual([button.kind for button in view.capture_buttons], ["primary", "secondary", "secondary"])
        for button in view.capture_buttons:
            button.configure(state="disabled")
            self.assertTrue(button.instate(["disabled"]))
            button.invoke()
        self.assertEqual(self.calls, [])
        self.assertEqual(str(view.capture_hotkey_checkbox.cget("textvariable")), str(self.state.hotkey_label))
        for widget in (view.capture_mode_combo, view.segment_entry, view.lens_entry, view.standard_combo,
                       view.attach_player_combo, view.attach_point_combo, view.attach_checkbox):
            self.assertTrue(str(widget.cget("style")).startswith("Kit."), widget)

    def test_shot_playback_is_pinned_in_the_dock_and_the_cards_scroll_above_it(self):
        view = self.build(shown=(948, 700))
        page, host = view.page, view.playback_host
        frame = self.playback.timeline_frame
        self.assertEqual(host.mode, "dock")
        self.assertEqual(frame.winfo_manager(), "place")
        self.assertEqual(str(frame.place_info()["in"]), str(page.dock))
        self.assertTrue(page._dock_packed)
        s = page.scale
        # 15 above the card, the card, and the page's end gap to the footer links (UPDATE 9: Replay's 29).
        self.assertEqual(page.dock.winfo_height(), px(s, 15) + host.card_height() + page.end_gap())
        self.assertEqual(page.end_gap(), int(29 * s))
        self.assertEqual(host.card_height(), host.card.inset * 2 + px(s, 18) + px(s, 15) + frame.winfo_reqheight())
        # The viewport ends above the dock; the cards overflow it and scroll; the dock does not move.
        self.assertGreater(page.content_height, page.canvas.winfo_height())
        self.assertTrue(self.playback.slider.winfo_ismapped())
        before = frame.winfo_rooty()
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            self.assertEqual(page._wheel(SimpleNamespace(widget=self.playback.slider, delta=-120)), "break")
        self.root.update()
        self.assertGreater(page.canvas.canvasy(0), 0)
        self.assertEqual(frame.winfo_rooty(), before)
        self.assertEqual(frame.scroll_page_wheel, page.scroll_page_wheel)
        # The card's background art (UPDATE 7): the dock shows it inside the plate, the frame gets its share.
        self.assertIs(host.art, art.playback_art)
        self.assertEqual(page.dock.itemcget(host._dock["art"], "state"), "normal")
        self.assertIn("card_backdrop", vars(frame))
        # A curve lane and the table keep their own wheel.
        self.assertIsNone(page._wheel(SimpleNamespace(widget=view.position_curve.canvas, delta=-120)))
        self.assertIsNone(page._wheel(SimpleNamespace(widget=view.camera_tree, delta=-120)))

    def test_the_fade_follows_the_cut_edges_with_a_recorded_backend(self):
        backend = FakeBackend()
        # Own fake handles from construction; a live Win32 handle cannot be
        # handed to the recording backend after the page has already mapped.
        with patch("dolly.ui.fade.Win32Backend", return_value=backend):
            view = self.build(shown=(948, 700))
        page = view.page
        self.assertIsInstance(page.fade, fade.ScrollFade)
        page.fade.backend = backend
        with patch("dolly.ui.fade.available", return_value=True), \
                patch("dolly.ui.boil.motion_enabled", return_value=False):
            page.fade._available = None
            page.scroll_to(0)
            page.fade._remeasure()
            self.assertEqual({edge: band.wanted for edge, band in page.fade.bands.items()},
                             {"top": False, "bottom": True})
            page.scroll_to(px(page.scale, 300))
            Animator.of(self.root).finish()
            self.assertEqual({edge: band.wanted for edge, band in page.fade.bands.items()},
                             {"top": True, "bottom": True})
            page.scroll_to(page.content_height)
            self.root.update()
            if page.fade._move_job is not None:
                self.root.after_cancel(page.fade._move_job)
            page.fade._moved()
            from dolly.ui.boil import BoilClock
            clock = BoilClock.of(self.root)
            clock._cancel_settle()
            clock._settled()
            page.fade._remeasure()
            Animator.of(self.root).finish()
            self.assertEqual({edge: band.wanted for edge, band in page.fade.bands.items()},
                             {"top": True, "bottom": False})
        self.assertTrue(any(call[0] == "update" for call in backend.calls))
        # Band positions are the viewport's edges: the bottom band ends where the dock begins.
        x, y, width, viewport = page.fade._origin
        self.assertEqual((width, viewport), (page.canvas.winfo_width(), page.canvas.winfo_height()))

    def test_window_counts_stay_within_the_page_budgets(self):
        view = self.build()
        counts = view.page.window_counts()
        if view.page.sheet is not None:
            self.assertEqual(counts["page"], 1)    # the sheet
            self.assertLessEqual(counts["moving"], 1)   # the docked frame does not scroll
        self.assertLessEqual(counts["inputs"], 40)     # PR3: Camera ≤ 40
        # No button of the page is a widget (N21): every one is drawn on a card canvas.
        ttk_buttons = [widget for widget in self._descendants(view.page)
                       if widget.winfo_class() == "TButton" and str(widget.cget("style")) not in (
                           "Kit.Tab.TButton", "Kit.Tab.Selected.TButton")]
        self.assertEqual(ttk_buttons, [])
        labels = [widget for widget in self._descendants(view.page) if widget.winfo_class() in ("TLabel", "Label")
                  and not self._inside(widget, view.camera_curve_workspace)]
        self.assertEqual(labels, [])

    def _descendants(self, widget):
        found, pending = [], [widget]
        while pending:
            current = pending.pop()
            for child in current.winfo_children():
                found.append(child)
                pending.append(child)
        return found

    @staticmethod
    def _inside(widget, ancestor):
        while widget is not None:
            if widget is ancestor:
                return True
            widget = widget.master
        return False

    def test_reveal_and_tab_reach_offscreen_controls(self):
        view = self.build(shown=(948, 700))
        page = view.page
        page.scroll_to(0)
        page.reveal(view.apply_all_button)
        self.root.update()
        rect, top = page.rect_of(view.apply_all_button), page.canvas.canvasy(0)
        self.assertTrue(top <= rect[1] and rect[3] <= top + page.canvas.winfo_height())
        page.scroll_to(0)
        page.reveal(view.attach_sliders["yaw"][0])   # inside the closed Rotation & transition disclosure
        self.assertTrue(view.rotation_disclosure.opened)
        # Tab reaches every input through the page's takefocus script (it reveals first).
        script = str(view.lens_entry.cget("takefocus"))
        self.assertNotIn(script, ("", "0", "1", "ttk::takefocus"))
        self.assertEqual(str(self.root.tk.call(script, str(view.apply_all_button.group.proxy))), "1")
        rect, top = page.rect_of(view.apply_all_button), page.canvas.canvasy(0)
        self.assertTrue(top <= rect[1] and rect[3] <= top + page.canvas.winfo_height())


if __name__ == "__main__":
    unittest.main()
