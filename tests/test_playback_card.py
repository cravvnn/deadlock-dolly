"""Shot Playback content (Figma [design reference]): exports, one card canvas over the card's art, two docked hosts (PLAN-5 5.2).

USER-ADDITIONS UPDATE 7: the card's background art (halftone dots and the drawing) is drawn once
per card size as the bottom item of the card, and everything on the card sits on it.
"""
from dataclasses import fields
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, ui_theme
from dolly.ui import art, fonts, kit_controls, playback_card
from dolly.ui.kit_controls import CanvasButton, KitSlider
from dolly.ui.kit_page import ColumnSpec, KitPage
from dolly.ui.playback_card import (
    ART_TAG, RATES, SMOOTHING, SPEEDS, CardCheck, CardDisclosure, CardToggle, FieldPlate, PlaybackActions,
    PlaybackCard, PlaybackState,
)
from dolly.ui.scale import hairline, px, ui_scale

EXPORTS = ("timeline_frame", "timeline_body", "slider", "time_entry", "speed_combo", "playback_disclosure",
           "rate_combo", "smoothing_row", "smoothing_combo", "play_button", "pause_button", "stop_button",
           "preview_frame_button", "seek_button")


# tkinter's event fields, in the order its bind scripts pass them.
_FIELDS = ("%#", "%b", "%f", "%h", "%k", "%s", "%t", "%w", "%x", "%y", "%A", "%E", "%K", "%N", "%W", "%T",
           "%X", "%Y", "%D")


def art_item(canvas):
    items = canvas.find_withtag(ART_TAG)
    return items[0] if items else None


def fire(widget, sequence):
    """Run ``widget``'s own binding for a key ``sequence`` (hidden windows never get the keyboard)."""
    script = widget.bind(sequence)
    values = {"%W": str(widget), "%K": "space", "%A": "{}", "%T": "2"}
    for name in _FIELDS:
        script = script.replace(name, values.get(name, "0"))
    widget.tk.call("catch", script)     # (a binding that returns "break" ends the script)


class PlaybackCase(unittest.TestCase):
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
        return lambda *args: self.calls.append((name, args))

    def build(self, parent=None, *, shown=True):
        gui_theme.initialize(self.root)
        if shown:
            self.root.deiconify()
            s = ui_scale(self.root)
            self.root.geometry(f"{px(s, 948)}x{px(s, 700)}")
        var = lambda value="": tk.StringVar(self.root, value)  # noqa: E731
        self.state = PlaybackState(
            shot_time=tk.DoubleVar(self.root, 0.0), time_text=var("0"), speed=var("1"),
            hide_hud=tk.BooleanVar(self.root, True), frozen=tk.BooleanVar(self.root, False),
            seek_relief=tk.BooleanVar(self.root, True), rate=var("60"), smoothing=var("Light"))
        actions = {field.name: self.record(field.name) for field in fields(PlaybackActions)}
        card = PlaybackCard(parent or self.root, self.state, PlaybackActions(**actions))
        if parent is None:
            card.timeline_frame.pack(fill="x")   # the stand-in for a host card
        return card

    def two_pages(self, *, art_fn=art.playback_art):
        """Camera and Look kit pages in one notebook, both docking Shot Playback with its art (UPDATE 7)."""
        workspace = tk.Frame(self.root, background="")
        workspace.pack(fill="both", expand=True)
        notebook = ttk.Notebook(workspace)
        notebook.pack(fill="both", expand=True)
        card = self.build(workspace)
        tk.Misc.lift(card.timeline_frame)
        camera = KitPage(notebook, columns=ColumnSpec(main_min=460), pinned=True)
        look = KitPage(notebook, columns=ColumnSpec(main_min=520), pinned=True)
        notebook.add(camera)
        notebook.add(look)
        for page in (camera, look):
            main, side = page.columns()
            for title in ("One", "Two", "Three"):
                main.add_card(title).row().label("content")
            side.add_card("Side").row().label("content")
        camera_host = camera.host_card("Shot Playback", where="dock", art=art_fn)
        look_host = look.host_card("Shot Playback", where="dock", art=art_fn)
        self.root.update()
        return SimpleNamespace(card=card, notebook=notebook, camera=camera, look=look, camera_host=camera_host,
                               look_host=look_host)


class PlaybackCardTests(PlaybackCase):
    def test_exports_and_a_frame_that_paints_nothing_around_one_card_canvas(self):
        card = self.build()
        for name in EXPORTS:
            self.assertIsNotNone(getattr(card, name), name)
        frame = card.timeline_frame
        self.assertEqual(str(frame.cget("background")), "")
        self.assertEqual(frame.winfo_children(), [card.timeline_body])
        self.assertIsInstance(card.timeline_body, tk.Canvas)
        self.assertEqual(card.timeline_body.cget("background"), ui_theme.TOKENS["panel"])
        # No ttk frame or label anywhere in the composite: every label is canvas text (PR1, PR4).
        pending = [frame]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            self.assertNotIn(widget.winfo_class(), ("TFrame", "TLabel", "Label"), widget)
        self.assertIsInstance(card.slider, KitSlider)
        self.assertIsInstance(card.playback_disclosure, CardDisclosure)
        self.assertIsInstance(card.playback_disclosure.toggle, CardToggle)
        # Figma [design reference] reads "1x": the dropdown lists the speeds with the suffix.
        self.assertEqual(tuple(card.speed_combo.cget("values")), tuple(value + "x" for value in SPEEDS))
        self.assertEqual(tuple(card.rate_combo.cget("values")), RATES)
        self.assertEqual(tuple(card.smoothing_combo.cget("values")), SMOOTHING)
        self.assertEqual((str(card.speed_combo.cget("state")), str(card.rate_combo.cget("state")),
                          str(card.smoothing_combo.cget("state"))), ("normal", "readonly", "readonly"))
        texts = {card.timeline_body.itemcget(i, "text") for i in card.timeline_body.find_all()
                 if card.timeline_body.type(i) == "text"}
        self.assertTrue({"s", "Playback speed", "Updates / s", "Console smoothing",
                         playback_card.SMOOTHING_NOTE} <= texts)

    def test_the_five_buttons_are_canvas_buttons_on_the_card_routing_to_their_actions(self):
        card = self.build()
        buttons = card.buttons()
        self.assertEqual([button.cget("text") for button in buttons],
                         ["Play shot", "Pause", "Stop / restore", "Preview frame", "Seek replay"])
        self.assertEqual(kit_controls.buttons(card.timeline_frame), buttons)
        card.playback_disclosure.set_open(True)        # the option buttons act only while shown
        self.root.update()
        for button in buttons:
            self.assertIsInstance(button, CanvasButton)
            self.assertIs(button.canvas, card.timeline_body)
            self.assertEqual(button.kind, "secondary")
            self.assertEqual(button.plate, "rgba")      # corners blend into the card's art
            button.invoke()
        self.assertEqual([name for name, _ in self.calls], ["play", "pause", "stop", "apply_frame", "seek"])
        # One focus group for the three playback buttons, one for the two option buttons.
        self.assertEqual({button.group for button in buttons[:3]}, {card.play_button.group})
        self.assertEqual({button.group for button in buttons[3:]}, {card.seek_button.group})
        # Disabled like a ttk button (the poll's busy rule).
        card.pause_button.configure(state="disabled")
        self.calls.clear()
        card.pause_button.invoke()
        self.assertEqual(self.calls, [])

    def test_slider_dragging_flag_speed_and_relief_reach_the_app(self):
        card = self.build()
        self.root.update()
        card.slider.configure(to=42.5)
        self.assertEqual(card.slider.cget("to"), 42.5)
        self.state.shot_time.set(10.0)
        self.assertEqual(card.slider.get(), 10.0)
        card.slider.event_generate("<ButtonPress-1>", x=card.slider.winfo_width() // 2, y=5)
        card.slider.event_generate("<ButtonRelease-1>", x=card.slider.winfo_width() // 2, y=5)
        self.root.update()
        names = [name for name, _ in self.calls]
        self.assertEqual(names[0], "set_dragging")
        self.assertEqual(self.calls[0][1], (True,))
        self.assertIn("slide", names)   # the press jumped the playhead: command(str(value)) like ttk.Scale
        self.assertEqual(self.calls[-1], ("set_dragging", (False,)))
        self.calls.clear()
        card.speed_combo.event_generate("<<ComboboxSelected>>")
        card.speed_combo.focus_force()
        self.root.update()
        card.speed_combo.event_generate("<Return>")
        self.root.update()
        self.assertEqual([name for name, _ in self.calls], ["apply_playback_speed", "apply_playback_speed"])
        self.calls.clear()
        card.playback_disclosure.set_open(True)
        self.root.update()
        relief = next(check for check in card.checks_row.winfo_children()
                      if check.cget("text") == "Renderer relief during seeks")
        self.assertIsInstance(relief, CardCheck)
        relief.invoke()
        self.assertEqual([name for name, _ in self.calls], ["set_seek_relief"])
        self.assertFalse(self.state.seek_relief.get())
        self.assertTrue(relief.instate(["!selected"]))
        # A click toggles too, Space as well (its own binding; the hidden window never has the keyboard).
        hide = card.checks_row.winfo_children()[0]
        hide.event_generate("<ButtonRelease-1>", x=2, y=2)
        self.assertFalse(self.state.hide_hud.get())
        fire(hide, "<space>")
        self.assertTrue(self.state.hide_hud.get())

    def test_playback_options_hold_the_rows_and_the_poll_can_pack_the_smoothing_row(self):
        card = self.build()
        disclosure = card.playback_disclosure
        self.assertFalse(disclosure.opened)
        self.root.update()
        closed = card.timeline_frame.winfo_reqheight()
        self.assertEqual(disclosure.body.winfo_manager(), "")
        disclosure.flip()
        self.root.update()
        self.assertTrue(disclosure.opened)
        self.assertEqual(disclosure.body.winfo_manager(), "pack")
        self.assertGreater(card.timeline_frame.winfo_reqheight(), closed)
        self.assertEqual(disclosure.body.winfo_children(), [card.checks_row, card.options_strip, card.smoothing_row])
        self.assertEqual([check.cget("text") for check in card.checks_row.winfo_children()],
                         ["Hide HUD", "Frozen preview", "Renderer relief during seeks"])
        self.assertEqual(card.smoothing_row.winfo_manager(), "pack")
        self.assertTrue(card.smoothing_combo.winfo_ismapped())
        opened = card.timeline_frame.winfo_reqheight()
        card.smoothing_row.pack_forget()       # the native driver: the poll hides the row
        self.root.update()
        self.assertEqual(card.smoothing_row.winfo_manager(), "")
        self.assertFalse(card.smoothing_combo.winfo_ismapped())
        self.assertEqual(card.timeline_frame.winfo_reqheight(), opened - px(card.scale, 10) - px(card.scale, 30))
        card.smoothing_row.pack(fill="x", pady=(px(card.scale, 10), 0))
        self.root.update()
        self.assertEqual(card.smoothing_row.winfo_manager(), "pack")
        self.assertEqual(card.timeline_frame.winfo_reqheight(), opened)
        disclosure.flip()
        self.root.update()
        self.assertEqual(card.timeline_frame.winfo_reqheight(), closed)
        self.assertFalse(card.checks[0].winfo_ismapped())
        self.assertFalse(card.rate_combo.winfo_ismapped())

    def test_closed_card_rows_follow_the_frame(self):
        card = self.build()
        self.root.update()
        s = card.scale
        body = card.timeline_body

        def rect(widget):
            return (widget.winfo_rootx() - body.winfo_rootx(), widget.winfo_rooty() - body.winfo_rooty(),
                    widget.winfo_width(), widget.winfo_height())
        # Shot time 30, 10, controls 30, 15, the disclosure row 18 ([design reference]: rows 54..157).
        self.assertEqual(card.timeline_frame.winfo_reqheight(), px(s, 30) + px(s, 10) + px(s, 30) + px(s, 15)
                         + px(s, 18))
        width = body.winfo_width()
        # The slider fills the row to 10 px before the time field; its track is centred on the row.
        x, y, w, h = rect(card.slider)
        self.assertEqual((x, y, h), (0, (px(s, 30) - px(s, 12)) // 2, px(s, 12)))
        plate = card.time_field.box
        self.assertEqual(x + w + px(s, 10), plate[0])
        self.assertEqual(plate[3] - plate[1], px(s, 30))
        self.assertGreaterEqual(plate[2] - plate[0], px(s, 34))
        unit = card.unit.bbox()
        self.assertEqual(plate[2] + px(s, 5), card.unit.canvas.coords(card.unit.item)[0])
        self.assertLessEqual(unit[2], width + 1)
        # The field fills its plate between the rounded ends (radius 4), the whole height.
        r = card.time_field.radius
        self.assertEqual(rect(card.time_entry), (plate[0] + r, 0, plate[2] - plate[0] - 2 * r, px(s, 30)))
        # Controls 10 below, the speed dropdown at the right edge; the options row 15 below them.
        self.assertEqual(card.play_button.box()[:2], (0, px(s, 30) + px(s, 10)))
        speed = card.speed_field.box
        self.assertEqual((speed[1], speed[2]), (px(s, 40), width))
        self.assertGreaterEqual(speed[2] - speed[0], px(s, 55))
        self.assertEqual(rect(card.playback_disclosure.toggle)[:2], (0, px(s, 40) + px(s, 30) + px(s, 15)))

    def test_the_speed_reads_1x_and_hugs_it_while_the_app_keeps_the_bare_number(self):
        card = self.build()
        self.root.update()
        s = ui_scale(self.root)
        # Figma [design reference]: "1x" in a 55 px dropdown (13 in, the value, 8, the 8 px chevron, 13 out).
        self.assertEqual(card.speed_combo.get(), "1x")
        self.assertEqual(card.speed_field.box[2] - card.speed_field.box[0], px(s, 55))
        # Picking or typing a speed hands the app the number; the app's own writes show with "x".
        card.speed_combo.set("0.25x")
        self.assertEqual(self.state.speed.get(), "0.25")
        card.speed_combo.set("2")
        self.assertEqual(self.state.speed.get(), "2")
        self.state.speed.set("0.5")
        self.assertEqual(card.speed_combo.get(), "0.5x")
        self.assertEqual((playback_card.speed_label(""), playback_card.speed_value("4 x")), ("", "4"))
        # The dropdown hugs a wider value (the speed label moves left with it) and the plate follows.
        self.root.update()
        wide = card.speed_field.box
        self.assertGreater(wide[2] - wide[0], px(s, 55))
        self.assertEqual(wide[2], card.timeline_body.winfo_width())
        self.state.speed.set("1")
        self.root.update()
        self.assertEqual(card.speed_field.box[2] - card.speed_field.box[0], px(s, 55))

    def test_the_docked_title_is_15_px_like_every_card_title(self):
        # Kara, UPDATE 25 ("no make it 15px"): Shot Playback's title went back from 18 px to 15.
        h = self.two_pages()
        s = ui_scale(self.root)
        for host in (h.camera_host, h.look_host):
            dock = host.page.dock
            font = dock.itemcget(host._dock["title"], "font")
            self.assertEqual(int(self.root.tk.call("font", "configure", font, "-size")), -px(s, 15))
        self.assertEqual(int(self.root.tk.call("font", "configure", h.camera.cards[0].host.itemcget(
            h.camera.cards[0].title_item, "font"), "-size")), -px(s, 15))

    def test_the_transport_buttons_hug_their_labels_like_figma(self):
        # Figma [design reference]: Play shot 78, Pause 61, Stop / restore 104, 10 apart: 13 + the label in
        # Inter's own widths (52, 35, 78; GDI draws "Stop / restore" 77 wide) + 13.
        card = self.build()
        self.root.update()
        s = ui_scale(self.root)
        boxes = [button.box() for button in (card.play_button, card.pause_button, card.stop_button)]
        labels = [fonts.hug_width(self.root, "body", text) for text in ("Play shot", "Pause", "Stop / restore")]
        self.assertEqual([box[2] - box[0] for box in boxes], [label + 2 * px(s, 13) for label in labels])
        self.assertEqual([right[0] - left[2] for left, right in zip(boxes, boxes[1:])], [px(s, 10)] * 2)
        if fonts.family_available(self.root, "body") == "Inter" and fonts._advances(fonts._face_file("Inter", "normal")):
            self.assertEqual(labels, [px(s, 52), px(s, 35), px(s, 78)])

    def test_fields_are_flat_inside_plates_drawn_on_the_card(self):
        card = self.build()
        self.root.update()
        style = ttk.Style(self.root)
        for name in ("Playback.TEntry", "Playback.TCombobox"):
            layout = str(style.layout(name))
            self.assertNotIn("Kit.field.nw", layout)          # no corner stamps over the art
            self.assertIn("Kit.combo.fill" if "Combobox" in name else "Kit.field.fill", layout)
        self.assertEqual(str(card.time_entry.cget("style")), "Playback.TEntry")
        for combo in (card.speed_combo, card.rate_combo, card.smoothing_combo):
            self.assertEqual(str(combo.cget("style")), "Playback.TCombobox")
        field = card.speed_field
        t = ui_theme.TOKENS
        self.assertEqual({card.timeline_body.itemcget(i, "fill") for i in field._fills}, {t["field"]})
        # The app's poll disables the dropdowns while busy: the plate's ends follow at once.
        card.speed_combo.configure(state="disabled")
        self.assertEqual({card.timeline_body.itemcget(i, "fill") for i in field._fills}, {t["field_disabled"]})
        card.speed_combo.state(["!disabled"])
        self.assertEqual({card.timeline_body.itemcget(i, "fill") for i in field._fills}, {t["field"]})
        # The corner images are the kit's RGBA corners (transparent outside: the art shows there).
        image = card.timeline_body.itemcget(field._corners[0], "image")
        self.assertEqual(image, str(art.corner_face(self.root, field.radius, t["field"], "nw")))

    def test_a_withdrawn_window_still_sizes_the_docked_slider(self):
        workspace = tk.Frame(self.root, background="")
        workspace.pack(fill="both", expand=True)
        card = self.build(workspace, shown=False)
        page = KitPage(workspace, columns=ColumnSpec(main_min=460), pinned=True)
        page.pack(fill="both", expand=True)
        page.columns()[0].add_card("One").row().label("content")
        host = page.host_card("Shot Playback", where="dock", art=art.playback_art)
        self.root.geometry("948x700")
        host.adopt(card.timeline_frame)
        self.root.update_idletasks()
        self.assertGreater(card.slider.winfo_width(), 1)
        self.assertGreater(card.slider.winfo_height(), 1)


class PlaybackHostTests(PlaybackCase):
    def test_adopted_by_two_docks_one_host_at_a_time(self):
        h = self.two_pages()
        frame = h.card.timeline_frame
        h.camera_host.adopt(frame)
        self.root.update()
        self.assertEqual(str(frame.place_info()["in"]), str(h.camera.dock))
        self.assertTrue(h.card.slider.winfo_ismapped())
        self.assertGreater(h.card.slider.winfo_width(), 1)
        self.assertEqual(frame.scroll_page_wheel, h.camera.scroll_page_wheel)
        # Tab order inside the adopted frame: slider, time, the buttons' group, speed, options toggle.
        order, widget = [], h.card.slider
        for _ in range(5):
            order.append(widget)
            widget = widget.tk_focusNext()
        self.assertEqual(order[:2], [h.card.slider, h.card.time_entry])
        self.assertIs(getattr(order[2], "_kit_group", None), h.card.play_button.group)
        self.assertEqual(order[3:], [h.card.speed_combo, h.card.playback_disclosure.toggle])
        h.notebook.select(h.look)
        h.look_host.adopt(frame)
        self.root.update()
        self.assertIsNone(h.camera_host.adopted)
        self.assertEqual(str(frame.place_info()["in"]), str(h.look.dock))
        self.assertEqual(frame.scroll_page_wheel, h.look.scroll_page_wheel)
        h.look_host.release(frame)
        self.root.update()
        self.assertEqual(frame.winfo_manager(), "")
        self.assertIsNone(h.card._art)

    def test_the_card_art_is_the_bottom_item_under_everything_on_the_card(self):
        h = self.two_pages()
        card, page, host = h.card, h.camera, h.camera_host
        host.adopt(card.timeline_frame)
        self.root.update()
        s, dock = page.scale, page.dock
        block = page.content_width
        height = host.card_height()
        image, x, y = card._art
        # One picture of the whole card at its size (this scale's px), drawn from the shipped art.
        self.assertEqual((image.width(), image.height()), (block, height))
        self.assertIs(image, art.playback_art(self.root, s, block, height))
        inset = host.card.inset
        self.assertEqual((x, y), (inset, inset + px(s, page.TITLE_LINE) + px(s, host.card.title_gap)))
        # The dock shows the plate middle inside the 1 px edge: above the middle fill, under the edges and title.
        r, line = px(s, page.CARD_RADIUS), hairline(s)
        d = host._dock
        dock_art = d["art"]
        self.assertEqual(dock.itemcget(dock_art, "state"), "normal")
        shown = dock.itemcget(dock_art, "image")
        self.assertEqual((int(dock.tk.call("image", "width", shown)), int(dock.tk.call("image", "height", shown))),
                         (block - 2 * r, height - 2 * line))
        self.assertEqual(dock.coords(dock_art), [float(r), float(px(s, page.DOCK_TOP_GAP) + line)])
        order = dock.find_all()
        self.assertLess(order.index(d["middle"]), order.index(dock_art))
        for item in (*d["edges"], d["title"]):
            self.assertLess(order.index(dock_art), order.index(item))
        # The body and the small canvases on it show the same picture at their own offsets, at the bottom.
        body = card.timeline_body
        item = art_item(body)
        self.assertEqual(body.find_all()[0], item)
        self.assertEqual(body.itemcget(item, "image"), str(image))
        self.assertEqual(body.coords(item), [float(-x), float(-y)])
        for widget in (card.slider, card.playback_disclosure.toggle):
            sub = art_item(widget)
            self.assertIsNotNone(sub, widget)
            self.assertEqual(widget.find_all()[0], sub)
            self.assertEqual(widget.coords(sub), [float(-x - widget.winfo_x()), float(-y - widget.winfo_y())])
        # Nothing on the card is a flat box over it: buttons blend (RGBA), fields sit in drawn plates,
        # checkboxes and the disclosure row are canvases showing the art; no ttk label or frame.
        card.playback_disclosure.set_open(True)
        self.root.update()
        for check in card.checks:
            self.assertIsNotNone(art_item(check))
            self.assertEqual(check.coords(art_item(check))[1], float(-y - check.winfo_y()))
        for child in body.winfo_children():
            if getattr(child, "_kit_group", None) is not None:
                self.assertEqual((child.winfo_width(), child.winfo_height()), (1, 1))   # the focus stand-ins
            elif isinstance(child, ttk.Widget):
                self.assertIsInstance(child.plate, FieldPlate)
            else:
                self.assertIsNotNone(art_item(child), child)

    def test_the_art_is_made_once_per_card_size_never_while_scrolling_or_animating(self):
        made = []

        def counting(root, scale, width, height, box=None):
            made.append((width, height, box))
            return art.playback_art(root, scale, width, height, box=box)
        h = self.two_pages(art_fn=counting)
        card, page, host = h.card, h.camera, h.camera_host
        host.adopt(card.timeline_frame)
        self.root.update()
        built = list(made)
        self.assertTrue(built)
        images = {str(image) for image in (card._art[0],)}
        # Scrolling the page (eased or not), a hover ramp and slider moves build nothing.
        with patch("dolly.ui.boil.motion_enabled", return_value=False):
            for y in (40, 0, 400, 0):
                page.scroll_to(y)
            page.scroll_page_wheel(SimpleNamespace(delta=-120, widget=page.canvas))
        card.play_button._on_enter()
        card.play_button._on_leave()
        self.state.shot_time.set(5.0)
        self.root.update()
        page.layout_now()                      # a layout with nothing changed rebuilds nothing either
        self.root.update()
        self.assertEqual({str(card._art[0])}, images)
        self.assertEqual(len({m for m in made}), len({m for m in built}))
        # A new card size (Playback options open) makes the next picture, once.
        card.playback_disclosure.set_open(True)
        self.root.update()
        page.layout_now()
        self.root.update()
        image = card._art[0]
        self.assertEqual(image.height(), host.card_height())
        self.assertNotIn(str(image), images)
        # Each size is a cached crop: asking again returns the same image.
        self.assertIs(art.playback_art(self.root, page.scale, image.width(), image.height()), image)

    def test_without_art_the_card_is_plain_panel(self):
        h = self.two_pages(art_fn=None)
        card = h.card
        h.camera_host.adopt(card.timeline_frame)
        self.root.update()
        self.assertIsNone(card._art)
        self.assertIsNone(art_item(card.timeline_body))
        self.assertEqual(h.camera.dock.itemcget(h.camera_host._dock["art"], "state"), "hidden")


if __name__ == "__main__":
    unittest.main()
