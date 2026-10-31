"""Canvas-drawn kit controls and kit styles (PLAN-5 2.3, 2.7, 2.8, 5.2; USER-ADDITIONS UPDATE 4 and 6)."""
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.ui import art, kit_controls, kit_fields, kit_styles, library_page, tween, widgets
from dolly.ui.kit_controls import (
    CanvasButton, CanvasInfoIcon, CardDisclosure, FocusGroup, KitButton, KitSlider, KitStrip, boil_while,
)
from dolly.ui.kit_page import ColumnSpec, KitPage
from dolly.ui.scale import px
from dolly.ui.tween import Animator

T = ui_theme.TOKENS


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


class ControlCase(unittest.TestCase):
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
        return lambda *args, **kwargs: self.calls.append(name)

    def shown(self, size="600x300"):
        gui_theme.initialize(self.root)
        kit_styles.install(self.root)   # the shell theme will call it last (integration, PLAN-5 4.4)
        self.root.deiconify()
        self.root.geometry(size)
        self.root.update()

    def card_canvas(self):
        canvas = tk.Canvas(self.root, background=T["panel"], highlightthickness=0, width=500, height=200)
        canvas.pack()
        return canvas

    def fill(self, button):
        return button.canvas.itemcget(button._fills[0], "fill")


class CanvasButtonTests(ControlCase):
    def test_faces_ramp_from_the_event_and_end_on_the_kit_colours(self):
        self.shown()
        canvas = self.card_canvas()
        secondary = CanvasButton(canvas, text="Browse...", command=self.record("browse"))
        primary = CanvasButton(canvas, text="Record video", command=self.record("record"), kind="primary")
        quiet = CanvasButton(canvas, text="Clear", command=self.record("clear"), kind="quiet")
        for index, button in enumerate((secondary, primary, quiet)):
            button.place(10, 10 + 40 * index)
        self.root.update()
        animator = Animator.of(self.root)
        self.assertEqual(self.fill(secondary), T["button"])
        self.assertEqual(self.fill(primary), T["accent"])
        self.assertEqual(canvas.itemcget(quiet.item, "fill"), T["muted"])
        with patch("dolly.ui.boil.motion_enabled", return_value=True):
            secondary._on_enter()
            first = self.fill(secondary)
            self.assertNotIn(first, (T["button"], T["button_hover"]))   # the first step, drawn in the handler
            self.assertTrue(animator.running)
            animator.finish()
            self.assertEqual(self.fill(secondary), T["button_hover"])
            secondary._on_press(SimpleNamespace(x=15, y=15))
            self.assertEqual(secondary.box()[1] + max(1, px(secondary.scale, 1)),
                             int(canvas.coords(secondary._fills[0])[1]))   # the dip is immediate
            animator.finish()
            self.assertEqual(self.fill(secondary), T["button_active"])
            secondary._on_release(SimpleNamespace(x=15, y=15))
            animator.finish()
            self.assertEqual(self.calls, ["browse"])
            self.assertEqual(self.fill(secondary), T["button_hover"])
            self.assertEqual(int(canvas.coords(secondary._fills[0])[1]), secondary.box()[1])
            # Released outside: no command, back to rest.
            secondary._on_press(SimpleNamespace(x=15, y=15))
            secondary._on_release(SimpleNamespace(x=400, y=150))
            animator.finish()
            self.assertEqual(self.calls, ["browse"])
            self.assertEqual(self.fill(secondary), T["button"])
            primary._on_enter()
            quiet._on_enter()
            animator.finish()
            self.assertEqual(self.fill(primary), T["accent_hover"])
            self.assertEqual(canvas.itemcget(quiet.item, "fill"), T["text"])
            quiet._on_press(SimpleNamespace(x=12, y=95))
            animator.finish()
            self.assertEqual(canvas.itemcget(quiet.item, "fill"), T["text"])
            # Disabled: the kit Disabled face at once, no hover, no invoke.
            primary.configure(state="disabled")
            self.assertEqual(self.fill(primary), T["field"])
            self.assertEqual(canvas.itemcget(primary.item, "fill"), T["dim"])
            self.assertFalse(animator.active(primary.ramp))
            primary._on_enter()
            primary.invoke()
            self.assertFalse(animator.running and animator.active(primary.ramp))
            self.assertEqual(self.calls, ["browse"])
            quiet.configure(state="disabled")
            self.assertEqual(canvas.itemcget(quiet.item, "fill"), T["dim"])
            primary.configure(state="normal")
            animator.finish()
            self.assertEqual(self.fill(primary), T["accent"])
        animator.finish()

    def test_motion_off_lands_at_once_and_nothing_runs_at_rest(self):
        self.shown()
        canvas = self.card_canvas()
        button = CanvasButton(canvas, text="Save paths", command=self.record("save"))
        button.place(0, 0)
        animator = Animator.of(self.root)
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            button._on_enter()
            self.assertEqual(self.fill(button), T["button_hover"])
            self.assertFalse(animator.running)
        self.assertFalse(animator.active())

    def test_the_ttk_button_surface(self):
        canvas = self.card_canvas()
        button = CanvasButton(canvas, text="Record video", command=self.record("record"), kind="primary")
        button.place(20, 30)
        self.root.update_idletasks()
        self.assertEqual(button.invoke(), None)
        self.assertEqual(self.calls, ["record"])
        self.assertEqual(button.cget("text"), "Record video")
        self.assertEqual(button["state"], "normal")
        button.configure(text="Record POV")
        self.assertEqual(canvas.itemcget(button.item, "text"), "Record POV")
        button.configure(command=self.record("pov"))
        button.invoke()
        self.assertEqual(self.calls, ["record", "pov"])
        self.assertEqual(button.state(), ())
        button.state(["disabled"])
        self.assertTrue(button.instate(["disabled"]))
        self.assertFalse(button.instate(["!disabled"]))
        self.assertEqual(button.cget("state"), "disabled")
        self.assertEqual(button.invoke(), "")
        button.state(["!disabled"])
        self.assertTrue(button.instate(["!disabled"]))
        button.place(20, 30)                      # its owner lays it out again after a text change
        presses = []
        button.bind("<ButtonPress-1>", lambda e: presses.append("press"), add="+")
        button.bind("<ButtonRelease-1>", lambda e: presses.append("release"), add="+")
        button._on_press(SimpleNamespace(x=25, y=35))
        button._on_release(SimpleNamespace(x=25, y=35))
        self.assertEqual(presses, ["press", "release"])
        # Geometry from the box and its canvas, also in a withdrawn window.
        box = button.box()
        self.assertEqual((button.winfo_width(), button.winfo_height()), (box[2] - box[0], px(button.scale, 30)))
        self.assertGreater(button.winfo_width(), 1)
        self.assertEqual(button.winfo_reqwidth(), box[2] - box[0])
        self.assertEqual(button.winfo_rootx(), canvas.winfo_rootx() + 20)
        self.assertEqual(button.winfo_rooty(), canvas.winfo_rooty() + 30)
        self.assertTrue(button.winfo_exists())
        self.assertIs(button.winfo_toplevel(), self.root)
        button.set_visible(False)
        self.assertFalse(button.winfo_ismapped())
        self.assertEqual(canvas.itemcget(button.item, "state"), "hidden")

    def test_hug_and_fixed_widths_and_a_text_change_re_hugs(self):
        canvas = self.card_canvas()
        label = lambda text: kit_controls.fonts.hug_width(canvas, "body", text)   # noqa: E731  (Figma's text box)
        s = kit_controls.ui_scale(canvas)
        hug = CanvasButton(canvas, text="Save paths")
        quiet = CanvasButton(canvas, text="Reset", kind="quiet")
        fixed = CanvasButton(canvas, text="Browse game...", width=125)
        self.assertEqual(hug.winfo_reqwidth(), label("Save paths") + 2 * px(s, 13))
        self.assertEqual(quiet.winfo_reqwidth(), label("Reset"))
        self.assertEqual(fixed.winfo_reqwidth(), px(s, 125))
        fixed.place(0, 0)
        x, _y = canvas.coords(fixed.item)
        self.assertLessEqual(abs(x - px(s, 125) / 2), 1)
        resized = []
        hug.on_resize = lambda: resized.append(True)
        hug.configure(text="View update")
        self.assertEqual(resized, [True])
        hug.configure(text="View update")
        self.assertEqual(resized, [True])

    def test_corners_on_a_card_canvas_are_opaque(self):
        canvas = self.card_canvas()
        button = CanvasButton(canvas, text="Browse")
        button.place(0, 0)
        for item in button._corners:
            name = str(canvas.itemcget(item, "image"))
            width, height = int(self.root.tk.call("image", "width", name)), int(self.root.tk.call("image", "height", name))
            for y in range(height):
                for x in range(width):
                    self.assertFalse(int(self.root.tk.call(name, "transparency", "get", x, y)))

    def test_focus_groups_reach_the_buttons_without_drawing_anything(self):
        canvas = self.card_canvas()
        page = SimpleNamespace(scale=1.0)
        first = CanvasButton(canvas, text="One", command=self.record("one"))
        second = CanvasButton(canvas, text="Two", command=self.record("two"))
        group = FocusGroup(page, canvas=canvas)
        group.add(first)
        group.add(second)
        group.place(0, 1)
        first.place(0, 0)
        second.place(100, 0)
        before = {item: canvas.itemconfigure(item) for item in canvas.find_all()}
        second.focus_set()
        self.assertIs(group.focused(), second)
        self.assertEqual(group._activate(), "break")
        self.assertEqual(self.calls, ["two"])
        group._move(-1)
        self.assertIs(group.focused(), first)
        group._activate()
        self.assertEqual(self.calls, ["two", "one"])
        self.assertEqual({item: canvas.itemconfigure(item) for item in canvas.find_all()}, before)
        self.assertEqual(group.proxy.winfo_manager(), "place")
        self.assertEqual(self.root.tk.call("winfo", "class", group.proxy), "Frame")
        for key in ("<Return>", "<space>", "<KP_Enter>", "<Tab>", "<Shift-Tab>", "<Escape>"):
            self.assertTrue(group.proxy.bind(key), key)

    def test_buttons_finds_canvas_buttons_and_kit_buttons_in_creation_order(self):
        canvas = self.card_canvas()
        strip = KitStrip(self.root)
        strip.pack()
        a = CanvasButton(canvas, text="a")
        b = strip.button("b")
        c = KitButton(self.root, text="c")
        d = CanvasButton(canvas, text="d")
        self.assertEqual(kit_controls.buttons(self.root), [a, b, c, d])
        d.destroy()
        self.assertEqual(kit_controls.buttons(self.root), [a, b, c])


class KitButtonTests(ControlCase):
    def test_a_one_button_widget_with_the_same_surface(self):
        self.shown()
        frame = tk.Frame(self.root, background=T["panel"])
        frame.pack()
        button = KitButton(frame, text="Browse", command=self.record("browse"), width=70)
        button.grid(row=0, column=0)
        reset = KitButton(frame, text="Reset", command=self.record("reset"), kind="quiet")
        reset.grid(row=0, column=1)
        self.root.update()
        s = button.button.scale
        self.assertEqual(button.winfo_height(), px(s, 30))
        self.assertEqual(button.winfo_reqwidth(), px(s, 70))
        self.assertEqual(str(button.cget("takefocus")), "1")
        self.assertTrue(button.bind("<space>") and button.bind("<Return>"))
        button._key()
        self.assertEqual(self.calls, ["browse"])
        button.configure(state="disabled")
        self.assertEqual(button.cget("state"), "disabled")
        self.assertTrue(button.instate(["disabled"]))
        button.invoke()
        self.assertEqual(self.calls, ["browse"])
        button.state(["!disabled"])
        button.configure(text="Refresh", background=T["panel"])
        self.assertEqual(button.cget("text"), "Refresh")
        self.assertEqual(button.cget("width"), 70)
        reset.configure(width=0)
        self.assertEqual(reset.winfo_reqwidth(), reset.button.winfo_reqwidth())
        self.assertEqual(reset.button.kind, "quiet")
        self.assertEqual(int(button.cget("highlightthickness")), 0)


class StripAndRowTests(ControlCase):
    def test_a_strip_lays_out_its_row_and_places_its_widgets(self):
        self.shown("700x200")
        frame = tk.Frame(self.root, background=T["panel"])
        frame.pack(fill="x")
        strip = KitStrip(frame)
        strip.pack(fill="x")
        play = strip.button("Play shot", self.record("play"))
        strip.gap(10)
        pause = strip.button("Pause", self.record("pause"))
        strip.push()
        strip.label("Playback speed")
        strip.gap(10)
        combo = strip.widget(ttk.Combobox(strip, values=("1",), style="Kit.TCombobox", width=4))
        self.root.update()
        s = strip.scale
        self.assertEqual(play.box()[0], 0)
        self.assertEqual(pause.box()[0], play.box()[2] + px(s, 10))
        self.assertEqual(combo.winfo_manager(), "place")
        self.assertEqual(combo.winfo_x() + combo.winfo_width(), strip.winfo_width())
        self.assertEqual(strip.winfo_height(), px(s, 30))
        self.assertEqual(len(strip.row.groups), 1)                     # one run of buttons, one group
        with self.assertRaises(ValueError):
            strip.widget(ttk.Entry(frame))


class InfoIconTests(ControlCase):
    def test_hover_opens_the_bubble_after_150_ms_and_keyboard_focus_at_once(self):
        self.shown("700x400")
        page = KitPage(self.root, columns=ColumnSpec())
        page.pack(fill="both", expand=True)
        main, _side = page.columns()
        card = main.add_card("Replay Graphics Profiles", info="Profiles apply when Dolly starts a replay.")
        self.root.update()
        page.layout_now()
        icon = card.info
        scheduled = []
        with patch.object(icon.canvas, "after", lambda ms, func: scheduled.append(ms) or "job"), \
                patch("dolly.ui.boil.motion_enabled", return_value=True):
            icon._on_enter()
            self.assertEqual(scheduled, [150])
            self.assertFalse(icon.shown)
            Animator.of(self.root).finish()
            self.assertIs(icon.ramp.image(), icon.ramp.image(T["accent"]))
            icon._show_bubble()
            self.assertTrue(icon.shown)
            self.assertEqual(len(page.canvas.find_withtag("bubble")), 1)
            with patch.object(icon.canvas, "after_cancel", lambda job: None):
                icon._on_leave()
            self.assertFalse(icon.shown)
            Animator.of(self.root).finish()
            self.assertEqual(page.canvas.find_withtag("bubble"), ())
            icon.group.focus(icon)                # keyboard: at once, and the icon stays dim (N4)
            self.assertTrue(icon.shown)
            self.assertIs(icon.ramp.image(), icon.ramp.image(T["dim"]))
            icon.group._escape()
            self.assertFalse(icon.shown)

    def test_ramp_faces_are_pre_warmed_and_no_frame_builds_an_image(self):
        self.shown()
        canvas = self.card_canvas()
        built = []
        original = art.info_icon_rgba
        with patch.object(art, "info_icon_rgba", lambda *a, **k: built.append(a[2]) or original(*a, **k)):
            icon = CanvasInfoIcon(canvas, "text", surface=T["panel"])
            icon.place(0, 0)
            self.assertEqual(sorted(built), sorted({T["dim"], T["accent"]}))   # both ends at once
            for _ in range(20):
                self.root.update()                                              # idle slices warm the steps
            warmed = set(built)
            self.assertEqual(warmed, set(tween.steps(T["dim"], T["accent"])))
            built.clear()
            with patch("dolly.ui.boil.motion_enabled", return_value=True):
                icon._on_enter()
                animator = Animator.of(self.root)
                for _ in range(10):
                    animator.tick(animator.clock() + .02)
                animator.finish()
            self.assertEqual(built, [])


class DisclosureTests(ControlCase):
    def test_card_disclosure_matches_the_widgets_disclosure_api(self):
        self.shown()
        legacy_holder = ttk.Frame(self.root, style="Card.TFrame")
        legacy_holder.pack(fill="x")
        holder = tk.Frame(self.root, background=T["panel"])
        holder.pack(fill="both", expand=True)
        legacy = widgets.Disclosure(legacy_holder, "Playback options")
        kit = CardDisclosure(holder, "Playback options")
        kit.pack(fill="x")
        for name in ("title", "opened", "toggle", "body", "flip", "set_open"):
            self.assertTrue(hasattr(legacy, name) and hasattr(kit, name), name)
        self.assertIsInstance(kit, tk.Frame)
        self.assertIsInstance(kit.body, tk.Frame)
        self.assertEqual(str(kit.cget("background")), T["panel"])
        tk.Label(kit.body, text="body")
        self.assertFalse(kit.opened)
        kit.flip()
        self.root.update()
        self.assertTrue(kit.opened)
        self.assertEqual(kit.body.winfo_manager(), "pack")
        self.assertEqual(kit.body.pack_info()["pady"], (px(kit.scale, 15), 0))
        kit.set_open(False)
        self.assertEqual(kit.body.winfo_manager(), "")
        self.assertEqual(str(kit.toggle.cget("takefocus")), "1")
        self.assertTrue(kit.toggle.bind("<Return>") and kit.toggle.bind("<space>"))
        with patch("dolly.ui.boil.motion_enabled", return_value=True):
            kit.toggle.event_generate("<Enter>")
            Animator.of(self.root).finish()
        self.assertEqual(kit.toggle.itemcget(kit.row.label, "fill"), T["text"])

    def test_a_card_disclosure_row_ramps_and_toggles(self):
        self.shown("700x400")
        page = KitPage(self.root)
        page.pack(fill="both", expand=True)
        card = page.add_card("Attach Camera")
        disclosure = card.disclosure("Rotation & transition")
        disclosure.body.row().label("Yaw")
        self.root.update()
        page.layout_now()
        toggle = disclosure.toggle
        with patch("dolly.ui.boil.motion_enabled", return_value=True):
            toggle._on_enter()
            self.assertNotIn(card.host.itemcget(toggle.row.label, "fill"), (T["muted"],))
            Animator.of(self.root).finish()
        self.assertEqual(card.host.itemcget(toggle.row.label, "fill"), T["text"])
        toggle.invoke()
        self.assertTrue(disclosure.opened)
        disclosure.group.focus(toggle)
        disclosure.group._activate()
        self.assertFalse(disclosure.opened)


class SliderTests(ControlCase):
    def build(self, **options):
        self.shown("400x100")
        frame = tk.Frame(self.root, background=T["panel"])
        frame.pack(fill="x")
        slider = KitSlider(frame, width=200, **options)
        slider.pack()
        self.root.update()
        return slider

    def test_value_maps_to_the_thumb_and_items_only_move(self):
        variable = tk.DoubleVar(self.root, 1.0)
        slider = self.build(from_=.5, to=3.0, variable=variable)
        s = slider.scale
        items = slider.find_all()
        w = slider.winfo_width()
        expected = px(s, 12) / 2 + (1.0 - .5) / 2.5 * (w - px(s, 12))
        self.assertLessEqual(abs(slider.coords(slider._thumb)[0] - expected), 1)
        variable.set(3.0)
        self.assertLessEqual(abs(slider.coords(slider._thumb)[0] - (w - px(s, 12) / 2)), 1)
        self.assertEqual(slider.find_all(), items)       # moved, never recreated
        fill = slider.coords(slider._fill[0])
        self.assertLessEqual(abs(fill[2] - (w - px(s, 12) / 2)), 1)
        self.assertEqual(int(slider.cget("height")), px(s, 12))

    def test_clicks_drags_and_keys_command_only_on_user_changes(self):
        changes = []
        variable = tk.StringVar(self.root, "0")
        slider = self.build(from_=0, to=10, variable=variable, command=changes.append, format="compact")
        app = []
        slider.bind("<ButtonPress-1>", lambda e: app.append("press"))
        slider.bind("<ButtonRelease-1>", lambda e: app.append("release"))
        w = slider.winfo_width()
        slider.event_generate("<ButtonPress-1>", x=w // 2, y=6)
        slider.event_generate("<ButtonRelease-1>", x=w // 2, y=6)
        self.assertEqual(app, ["press", "release"])            # the app's bindings do not replace the slider's
        self.assertLessEqual(abs(slider.get() - 5.0), 0.1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(variable.get(), f"{slider.get():.2f}".rstrip("0").rstrip("."))
        slider._dragged(SimpleNamespace(x=w))
        self.assertEqual(slider.get(), 10.0)
        self.assertEqual(variable.get(), "10")
        key = lambda name, state=0: slider._key(SimpleNamespace(keysym=name, state=state))  # noqa: E731
        key("Home")
        self.assertEqual(slider.get(), 0.0)
        key("Right")
        self.assertAlmostEqual(slider.get(), 0.1)
        key("Right", 1)
        self.assertAlmostEqual(slider.get(), 0.11)
        key("Prior")
        self.assertAlmostEqual(slider.get(), 1.11)
        key("End")
        self.assertEqual(slider.get(), 10.0)
        key("Down")
        self.assertAlmostEqual(slider.get(), 9.9)
        count = len(changes)
        slider.set(2.5)                                         # from code: no command
        variable.set("4")
        self.assertEqual(len(changes), count)
        self.assertEqual(slider.get(), 4.0)
        self.assertNotIn("<MouseWheel>", slider.bind())
        self.assertEqual(slider.bindtags()[1], "DollyKitSlider")

    def test_disabled_colours_and_input(self):
        slider = self.build(from_=0, to=1)
        slider.configure(state="disabled")
        self.assertTrue(slider.instate(["disabled"]))
        self.assertEqual(slider.cget("state"), "disabled")
        self.assertEqual(slider.itemcget(slider._track[0], "fill"), T["edge_disabled"])
        self.assertEqual(slider.itemcget(slider._fill[0], "fill"), T["accent_disabled"])
        self.assertEqual(str(slider.cget("takefocus")), "0")
        slider._pressed(SimpleNamespace(x=150))
        self.assertEqual(slider.get(), 0.0)
        self.assertIsNone(slider._key(SimpleNamespace(keysym="End", state=0)))
        slider.state(["!disabled"])
        self.assertEqual(slider.itemcget(slider._fill[0], "fill"), T["accent"])
        self.assertEqual(slider.cget("from"), 0.0)
        slider.configure(to=5)
        self.assertEqual(slider.cget("to"), 5.0)


class FieldHelperTests(ControlCase):
    def test_elide_at_rest_placeholder_and_idle_scrollbar_match_the_replay_helpers(self):
        self.shown("400x200")
        value = "C:/Program Files (x86)/Steam/steamapps/common/Deadlock/game/bin/win64/deadlock.exe"
        covers = []
        for helper in (library_page._elide_at_rest, kit_fields.elide_at_rest):
            variable = tk.StringVar(self.root, value)
            entry = ttk.Entry(self.root, textvariable=variable, style="Stage.Card.TEntry", width=20)
            entry.pack()
            cover = helper(entry, variable)
            self.root.update()
            covers.append((cover, entry))
        (old, _), (new, entry) = covers
        self.assertEqual(new.cget("text"), old.cget("text"))
        self.assertTrue(new.cget("text").endswith("…"))
        self.assertEqual(new.winfo_manager(), "place")
        entry.event_generate("<FocusIn>")
        self.assertEqual(new.winfo_manager(), "")
        variable = tk.StringVar(self.root, "")
        writes = []
        variable.trace_add("write", lambda *a: writes.append(variable.get()))
        entry = ttk.Entry(self.root, textvariable=variable, style="Kit.TEntry")
        entry.pack()
        label = kit_fields.placeholder(entry, variable, "Find an action or shortcut")
        self.root.update()
        self.assertEqual(label.winfo_manager(), "place")
        self.assertIs(label.master, entry)
        entry.event_generate("<FocusIn>")
        self.assertEqual(label.winfo_manager(), "")
        entry.event_generate("<FocusOut>")
        self.assertEqual(label.winfo_manager(), "place")
        variable.set("reshade")
        self.assertEqual(label.winfo_manager(), "")
        self.assertEqual(writes, ["reshade"])
        table = ttk.Frame(self.root)
        table.pack()
        tree = ttk.Treeview(table, height=3)
        tree.grid(row=0, column=0)
        bar = ttk.Scrollbar(table, command=tree.yview)
        bar.grid(row=0, column=1)
        kit_fields.hide_idle_scrollbar(table, tree)
        report = str(tree.cget("yscrollcommand"))           # what the table calls when its view changes
        self.root.tk.call(report, 0.0, 1.0)
        self.assertEqual(bar.winfo_manager(), "")
        self.root.tk.call(report, 0.0, 0.5)
        self.assertEqual(bar.winfo_manager(), "grid")
        self.assertEqual(bar.get(), (0.0, 0.5))
        self.assertEqual(kit_fields.number_width(entry, "0", 34), px(kit_controls.ui_scale(entry), 34))
        self.assertGreater(kit_fields.number_width(entry, "000000.000000", 34), px(kit_controls.ui_scale(entry), 34))

    def test_the_search_field_never_writes_its_variable(self):
        self.shown("948x300")
        page = KitPage(self.root, columns=ColumnSpec())
        page.pack(fill="both", expand=True)
        search = tk.StringVar(self.root, "")
        writes = []
        search.trace_add("write", lambda *a: writes.append(search.get()))
        field = page.band().search(search, placeholder="Find an action or shortcut")
        self.root.update()
        page.layout_now()
        self.assertEqual(str(field.entry.cget("style")), "Kit.Flat.TEntry")
        self.assertEqual(field.placeholder.winfo_manager(), "place")
        self.assertEqual(field.rect[2] - field.rect[0], page.main_width)
        field.icon.event_generate("<Button-1>")
        self.assertEqual(writes, [])


class StyleTests(ControlCase):
    def test_no_tiled_image_elements(self):
        gui_theme.initialize(self.root)
        kit_styles.install(self.root)
        self.assertTrue(kit_styles.ELEMENTS)
        for name, ((width, height), sticky) in kit_styles.ELEMENTS.items():
            with self.subTest(element=name):
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                kind = name.rsplit(".", 1)[-1]
                if kind in kit_styles.CORNERS:
                    self.assertEqual(sticky, kind)
                    self.assertEqual(width, height)
                elif kind == "indicator":
                    self.assertEqual(sticky, "w")
                else:
                    self.assertIn(kind, ("downarrow", "minimum"))
                    self.assertEqual(sticky, "")

    def test_no_focus_face_and_no_kit_button_styles(self):
        gui_theme.initialize(self.root)
        kit_styles.install(self.root)
        style = ttk.Style(self.root)
        for name in kit_styles.STYLES:
            with self.subTest(style=name):
                for option, entries in (style.map(name) or {}).items():
                    for entry in entries:
                        self.assertNotIn("focus", " ".join(map(str, entry[:-1])), (option, entry))
        for name in ("Kit.TEntry", "Kit.TCombobox", "Kit.TMenubutton", "Kit.Keycap.TButton"):
            for option in ("fieldbackground", "bordercolor", "lightcolor", "darkcolor", "foreground"):
                self.assertEqual(style.lookup(name, option, ["focus"]), style.lookup(name, option), (name, option))
        for name in ("Kit.TButton", "Kit.Primary.TButton", "Kit.Quiet.TButton"):
            self.assertFalse(style.configure(name))
            self.assertFalse(style.map(name))

    def test_disabled_lookups_are_the_pre_mixed_tokens(self):
        gui_theme.initialize(self.root)
        kit_styles.install(self.root)
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("Kit.TEntry", "fieldbackground", ["disabled"]), T["field_disabled"])
        self.assertEqual(style.lookup("Kit.TEntry", "foreground", ["disabled"]), T["muted_disabled"])
        self.assertEqual(style.lookup("Kit.TCombobox", "fieldbackground", ["disabled"]), T["field_disabled"])
        self.assertEqual(style.lookup("Kit.TCheckbutton", "foreground", ["disabled"]), T["text_disabled"])
        self.assertEqual(style.lookup("Kit.Muted.TCheckbutton", "foreground", ["disabled"]), T["muted_disabled"])
        self.assertEqual(style.lookup("Kit.Keycap.TButton", "bordercolor", ["alternate"]), T["accent"])
        self.assertEqual(style.lookup("Kit.Tab.TButton", "foreground", ["active"]), T["text"])
        self.assertEqual(style.lookup("Kit.Tab.TButton", "foreground"), T["muted"])

    def test_kit_controls_are_30_design_px_tall(self):
        self.shown()
        holder = tk.Frame(self.root, background=T["panel"])
        holder.pack()
        s = kit_controls.ui_scale(holder)
        for widget in (ttk.Entry(holder, style="Kit.TEntry"), ttk.Combobox(holder, style="Kit.TCombobox"),
                       ttk.Button(holder, text="Tab", style="Kit.Tab.TButton"),
                       ttk.Button(holder, text="Ctrl+K", style="Kit.Keycap.TButton")):
            widget.pack()
            self.assertEqual(widget.winfo_reqheight(), px(s, 30), widget)


class BoilTests(ControlCase):
    def test_boil_while_joins_the_clock_until_stopped(self):
        from dolly.ui.boil import BoilClock
        ticks = []
        stop = boil_while(self.root, lambda: ticks.append(1))
        clock = BoilClock.of(self.root)
        self.assertEqual(len(clock.members), 1)
        clock.tick()
        self.assertEqual(ticks, [1])
        stop()
        self.assertEqual(clock.members, ())


if __name__ == "__main__":
    unittest.main()
