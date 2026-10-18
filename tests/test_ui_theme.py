"""The desktop and in-game UI must share exactly one palette (dolly/ui_theme.py)."""
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from dolly import ui_theme

ROOT = Path(__file__).resolve().parents[1]

class UiThemeTests(unittest.TestCase):
    def test_tokens_are_valid_hex(self):
        self.assertTrue(ui_theme.TOKENS)
        for name, value in ui_theme.TOKENS.items():
            with self.subTest(token=name):
                self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
                self.assertRegex(value, r"^#[0-9a-f]{6}$")
                ui_theme.rgb(value)

    def test_native_header_is_current(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "generate_ui_tokens.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(
            result.returncode, 0,
            "native/src/dolly_ui_tokens_generated.hpp is stale; run tools/generate_ui_tokens.py\n"
            + result.stdout + result.stderr)

    def test_missing_windows_fonts_fall_back_to_available_families(self):
        with patch.dict(os.environ, {"DOLLY_FONT_FAMILY": "", "DOLLY_FONT_MONO": ""}), \
                patch("tkinter.font.families", return_value=["Noto Sans", "Noto Sans Mono"]), \
                patch.object(ui_theme, "FONT_FAMILY", "Segoe UI"), \
                patch.object(ui_theme, "FONT_MONO", "Consolas"):
            ui_theme.prefer_available_fonts(object())
            self.assertEqual(ui_theme.FONT_FAMILY, "Noto Sans")
            self.assertEqual(ui_theme.FONT_MONO, "Noto Sans Mono")

    def test_explicit_font_variables_are_never_overridden(self):
        with patch.dict(os.environ, {"DOLLY_FONT_FAMILY": "Custom Sans", "DOLLY_FONT_MONO": ""}), \
                patch("tkinter.font.families", return_value=["Noto Sans", "Noto Sans Mono"]), \
                patch.object(ui_theme, "FONT_FAMILY", "Custom Sans"), \
                patch.object(ui_theme, "FONT_MONO", "Consolas"):
            ui_theme.prefer_available_fonts(object())
            self.assertEqual(ui_theme.FONT_FAMILY, "Custom Sans")
            self.assertEqual(ui_theme.FONT_MONO, "Noto Sans Mono")

    def test_desktop_has_no_hardcoded_colours(self):
        for name in ("dolly/gui.py", "dolly/gui_theme.py", "dolly/gui_layout.py",
                     "dolly/curve.py", "dolly/release_launcher.py", "dolly/ui/widgets.py", "dolly/ui/library_page.py",
                     "dolly/ui/settings_page.py", "dolly/ui/export_page.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            found = re.findall(r"#[0-9a-fA-F]{6}", text)
            self.assertEqual(found, [], f"{name} must take colours from dolly/ui_theme.py: {found}")

    def test_overlay_takes_colours_from_the_generated_header(self):
        text = (ROOT / "native/src/dolly_overlay_panel.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "dolly_ui_tokens_generated.hpp"', text)
        found = re.findall(r"panel_color\(0x[0-9a-fA-F]", text)
        self.assertEqual(found, [], f"dolly_overlay_panel.cpp has raw colours: {found}")

class DesktopThemeInitializationTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from dolly import gui_theme
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        gui_theme.initialize(self.root)

    def tearDown(self):
        if hasattr(self, "errors"):
            self.assertEqual(self.errors, [], "Tk callback failed")

    def test_initialized_styles_preserve_typography_surfaces_and_native_elements(self):
        from tkinter import ttk
        from dolly import gui_theme
        with self.subTest(contract='typography and base styles'):
            style = ttk.Style(self.root)
            self.assertEqual(style.theme_use(), "clam")
            self.assertEqual(self.root.cget("bg"), ui_theme.TOKENS["bg"])
            self.assertEqual(self.root.tk.splitlist(style.lookup("Title.TLabel", "font")),
                             (ui_theme.FONT_FAMILY, str(ui_theme.FONT_SIZES["title"]), "bold"))
            self.assertEqual(tuple(map(int, self.root.tk.splitlist(style.lookup("TButton", "padding")))), (10, 5))
            self.assertEqual(style.layout("TButton")[0][0], "Dolly.button")
            self.assertEqual(style.lookup("TCombobox", "fieldbackground", ("readonly",)), ui_theme.TOKENS["field"])
            self.assertEqual(style.lookup("TCombobox", "foreground", ("disabled",)), ui_theme.TOKENS["disabled_fg"])

        with self.subTest(contract='corner stamps and content padding'):
            style = ttk.Style(self.root)
            for name, content in (("Rounded.Card.TFrame", None), ("TButton", "Button.padding"),
                                  ("Card.TEntry", "Entry.padding"), ("TCombobox", "Combobox.padding"),
                                  ("TMenubutton", "Menubutton.padding")):
                with self.subTest(style=name):
                    element, options = style.layout(name)[0]
                    self.assertIn("background", style.element_options(element))
                    children = options["children"]
                    self.assertEqual(children[0][0], element + ".fill")
                    self.assertIn("fieldbackground", style.element_options(children[0][0]))
                    self.assertEqual([item[0] for item in children[1:5]],
                                     [element + "." + corner for corner in ("nw", "ne", "sw", "se")])
                    for (_, corner), direction in zip(children[1:5], ("nw", "ne", "sw", "se")):
                        self.assertEqual(set(corner["sticky"]), set(direction))
                    self.assertEqual(children[5][0], element + ".minimum")
                    self.assertEqual(children[5][1]["sticky"], "",
                                     "The minimum-size shim must never tile across a growing widget")
                    if content:
                        self.assertEqual(children[6][0], content,
                                         "Content must remain a sibling of the field, outside its 2px inset")

        with self.subTest(contract='field surfaces across states'):
            style = ttk.Style(self.root)
            # A transparent rounded asset still has a rectangular widget behind
            # it. Clam's active/focus maps must not expose that rectangle.
            for prefix, surface in (("", "bg"), ("Card.", "panel")):
                for control in ("TEntry", "TCombobox", "TMenubutton"):
                    for state in ((), ("active",), ("focus",), ("readonly", "focus"),
                                  ("readonly", "active", "focus"), ("pressed",), ("disabled",)):
                        with self.subTest(style=prefix + control, state=state):
                            self.assertEqual(style.lookup(prefix + control, "background", state),
                                             ui_theme.TOKENS[surface])
            for state in ((), ("active",), ("focus",), ("readonly", "focus"), ("disabled",)):
                self.assertEqual(style.lookup("Numeric.TEntry", "background", state), ui_theme.TOKENS["panel"])

        with self.subTest(contract='page card and notice assets'):
            style = ttk.Style(self.root)
            for control, element in (("TButton", "Dolly.button"), ("TEntry", "Dolly.field"),
                                     ("TCombobox", "Dolly.field"), ("TMenubutton", "Dolly.menu")):
                self.assertEqual(style.layout(control)[0][0], element)
                self.assertEqual(style.layout("Card." + control)[0][0], element + ".card")
            self.assertEqual(style.layout("Numeric.TEntry")[0][0], "Dolly.field.card")
            self.assertEqual(style.layout("Card.Quiet.TButton")[0][0], "Dolly.button.card")
            self.assertEqual(style.layout("Card.Primary.TButton")[0][0], "Dolly.primary.card")
            self.assertEqual(style.layout("UpdateNotice.TButton")[0][0], "Dolly.button.notice")
            self.assertEqual(style.layout("UpdateNotice.Primary.TButton")[0][0], "Dolly.primary.notice")
            for surface in ("bg", "panel", "selected"):
                image = gui_theme.rounded_image(self.root, ui_theme.TOKENS["field"], ui_theme.TOKENS["edge"],
                                                size=16, radius=5, surface=ui_theme.TOKENS[surface])
                self.assertFalse(image.transparency_get(0, 0))
                self.assertEqual(image.get(0, 0), ui_theme.rgb(ui_theme.TOKENS[surface]))
                self.assertEqual(image.get(8, 8), ui_theme.rgb(ui_theme.TOKENS["field"]))

        with self.subTest(contract='menu and scrollbar elements'):
            style = ttk.Style(self.root)
            self.assertEqual(style.layout("TMenubutton")[0][0], "Dolly.menu")
            for direction in ("Vertical", "Horizontal"):
                layout = style.layout(direction + ".TScrollbar")
                self.assertEqual(layout[0][0], "Dolly." + direction + ".Scrollbar.trough")
                self.assertEqual(layout[0][1]["children"][0][0],
                                 "Dolly." + direction + ".Scrollbar.thumb")

    def test_generated_images_remain_alive_after_collection(self):
        import gc
        gc.collect()
        images = self.root._dolly_theme_images
        self.assertGreater(len(images), 0)
        live = set(self.root.tk.splitlist(self.root.tk.call("image", "names")))
        for image in images:
            self.assertIn(str(image), live)
            self.assertGreater(image.width(), 0)
            self.assertGreater(image.height(), 0)

    def test_update_notes_keep_their_dedicated_selected_surface(self):
        from tkinter import ttk
        from dolly.ui.update_notice import UpdateNotice
        style = ttk.Style(self.root)
        before = style.layout("UpdateNotice.Notes.TFrame")
        self.assertEqual(before[0][0], "Dolly.card.notice")
        view = UpdateNotice(self.root, update=lambda: None, notes=lambda: None, full_notes=lambda: None,
                            later=lambda: None, reopen=lambda: None)
        self.root.update_idletasks()
        self.assertEqual(style.layout("UpdateNotice.Notes.TFrame"), before)
        self.assertEqual(style.lookup("UpdateNotice.Notes.TFrame", "background"), ui_theme.TOKENS["selected"])
        self.assertEqual(style.lookup("UpdateNotice.Notes.TFrame", "fieldbackground"), ui_theme.TOKENS["panel"])
        view.frame.destroy()

    def test_controls_and_both_rails_match_a_card_on_map(self):
        from tkinter import ttk
        panel = ttk.Frame(self.root, style="Card.TFrame")
        panel.pack()
        controls = [ttk.Combobox(panel, state="readonly"), ttk.Menubutton(panel, text="Graphs"),
                    ttk.Scrollbar(panel, orient="vertical"), ttk.Scrollbar(panel, orient="horizontal")]
        for control in controls:
            control.pack()
        self.root.deiconify(); self.root.update()
        style = ttk.Style(self.root)
        scale = max(1, self.root.winfo_fpixels("1i") / 96)
        for control, name in zip(controls, ("Card.TCombobox", "Card.TMenubutton",
                                           "Card.Vertical.TScrollbar", "Card.Horizontal.TScrollbar")):
            with self.subTest(control=name):
                self.assertEqual(str(control.cget("style")), name)
                self.assertEqual(style.lookup(name, "background", ("active",)), ui_theme.TOKENS["panel"])
        self.assertEqual(controls[2].winfo_reqwidth(), round(10 * scale))
        self.assertEqual(controls[3].winfo_reqheight(), round(10 * scale))

    def test_keyboard_focus_dropdown_selection_and_escape_still_work(self):
        import tkinter as tk
        from tkinter import ttk
        entry = ttk.Entry(self.root)
        combo = ttk.Combobox(self.root, values=tuple(str(i) for i in range(20)), state="readonly", height=4)
        following = ttk.Button(self.root, text="Next")
        for control in (entry, combo, following):
            control.pack()
        self.root.deiconify(); self.root.update()
        entry.focus_force(); self.root.update()
        entry.event_generate("<Tab>"); self.root.update()
        self.assertEqual(self.root.focus_get(), combo)
        self.assertTrue(combo.instate(("focus",)), "Focus still selects the rounded focus asset")
        combo.event_generate("<Down>"); self.root.update()
        popdown = str(combo) + ".popdown"
        listing = popdown + ".f.l"
        self.assertTrue(self.root.tk.call("winfo", "ismapped", listing))
        self.root.tk.call(listing, "selection", "clear", 0, "end")
        self.root.tk.call(listing, "selection", "set", 2)
        self.root.tk.call(listing, "activate", 2)
        self.root.tk.call("event", "generate", listing, "<Return>")
        self.root.update()
        self.assertEqual(combo.get(), "2")
        self.assertFalse(self.root.tk.call("winfo", "ismapped", popdown))
        combo.event_generate("<Down>"); self.root.update()
        self.root.tk.call("event", "generate", listing, "<Escape>")
        self.root.update()
        self.assertEqual(combo.get(), "2")
        self.assertFalse(self.root.tk.call("winfo", "ismapped", popdown))
        combo.focus_force(); combo.event_generate("<Tab>"); self.root.update()
        self.assertEqual(self.root.focus_get(), following)

    def test_small_view_fraction_keeps_a_single_usable_minimum_thumb(self):
        from tkinter import ttk
        self.root.geometry("360x260")
        scale = max(1, self.root.winfo_fpixels("1i") / 96)
        minimum = round(16 * scale)
        for direction in ("vertical", "horizontal"):
            with self.subTest(direction=direction):
                bar = ttk.Scrollbar(self.root, orient=direction)
                bar.pack(side="right" if direction == "vertical" else "bottom",
                         fill="y" if direction == "vertical" else "x")
                bar.set(.5, .500001)  # A tiny viewport on a very long document.
                self.root.deiconify(); self.root.update()
                vertical = direction == "vertical"
                cross = (bar.winfo_width() if vertical else bar.winfo_height()) // 2
                length = bar.winfo_height() if vertical else bar.winfo_width()
                coords = lambda along: (cross, along) if vertical else (along, cross)
                points = [i for i in range(length)
                          if bar.identify(*coords(i)).endswith("thumb")]
                self.assertTrue(points)
                self.assertEqual(points, list(range(points[0], points[-1] + 1)),
                                 "One continuous thumb must remain a single native drag target")
                self.assertGreaterEqual(len(points), minimum)
                self.assertLessEqual(len(points), minimum + 1,
                                     "Long image strips must not enlarge the actual minimum thumb")
                bar.destroy()

    def test_scales_use_native_hit_targets_on_the_correct_card_surface(self):
        from tkinter import ttk
        style = ttk.Style(self.root)
        panel = ttk.Frame(self.root, style="Card.TFrame")
        panel.pack()
        controls = [ttk.Scale(panel, orient=direction) for direction in ("horizontal", "vertical")]
        for control in controls:
            control.pack()
        self.root.deiconify(); self.root.update()
        for direction, control in zip(("Horizontal", "Vertical"), controls):
            with self.subTest(direction=direction):
                name = "Card." + direction + ".TScale"
                self.assertEqual(str(control.cget("style")), name)
                layout = style.layout(name)
                self.assertTrue(layout[0][0].endswith("Scale.trough"))
                self.assertTrue(layout[0][1]["children"][0][0].endswith("Scale.slider"))
                for state in ((), ("active",), ("focus",), ("pressed",), ("disabled",)):
                    self.assertEqual(style.lookup(name, "background", state), ui_theme.TOKENS["panel"])

    def test_scale_pointer_keyboard_values_and_handle_bounds_remain_native(self):
        import tkinter as tk
        from tkinter import ttk
        self.root.geometry("360x260")
        for direction in ("horizontal", "vertical"):
            with self.subTest(direction=direction):
                value, callbacks = tk.DoubleVar(self.root, 50), []
                control = ttk.Scale(self.root, orient=direction, from_=0, to=100, variable=value,
                                    command=lambda result: callbacks.append(float(result)), takefocus=True)
                control.pack(fill="x" if direction == "horizontal" else "y", expand=True)
                self.root.deiconify(); self.root.update()
                horizontal = direction == "horizontal"
                cross = (control.winfo_height() if horizontal else control.winfo_width()) // 2
                length = control.winfo_width() if horizontal else control.winfo_height()
                coords = lambda along: (along, cross) if horizontal else (cross, along)
                def slider_points():
                    return [i for i in range(length) if control.identify(*coords(i)).endswith("slider")]
                points = slider_points()
                self.assertTrue(points)
                handle_span = len(points)
                middle = (points[0] + points[-1]) // 2
                point = coords(middle)
                control.event_generate("<ButtonPress-1>", x=point[0], y=point[1])
                moved = coords(min(length - 1, middle + length // 5))
                control.event_generate("<B1-Motion>", x=moved[0], y=moved[1])
                control.event_generate("<ButtonRelease-1>", x=moved[0], y=moved[1])
                self.root.update()
                self.assertGreater(control.get(), 60)
                self.assertEqual(control.get(), value.get())
                point = coords(length - 2)
                self.assertTrue(control.identify(*point).endswith("trough"))
                before = value.get()
                control.event_generate("<ButtonPress-1>", x=point[0], y=point[1])
                control.event_generate("<ButtonRelease-1>", x=point[0], y=point[1])
                self.root.update()
                self.assertGreater(value.get(), before)
                control.focus_force(); self.root.update()
                self.assertTrue(control.instate(("focus",)))
                before = value.get()
                control.event_generate("<Right>" if horizontal else "<Down>"); self.root.update()
                self.assertGreater(value.get(), before)
                for key, endpoint in (("<Home>", 0), ("<End>", 100)):
                    control.event_generate(key); self.root.update()
                    self.assertEqual(value.get(), endpoint)
                    points = slider_points()
                    self.assertTrue(points)
                    self.assertLessEqual(abs(len(points) - handle_span), 1,
                                         "Endpoint handles must keep their full drag area without clipping")
                self.assertTrue(callbacks)
                control.configure(state="disabled")
                point = coords(length // 4)
                control.event_generate("<ButtonPress-1>", x=point[0], y=point[1])
                control.event_generate("<ButtonRelease-1>", x=point[0], y=point[1])
                control.event_generate("<Home>"); self.root.update()
                self.assertEqual(value.get(), 100, "Disabled scales must not change through pointer or keyboard")
                control.destroy()

    def test_scrollbar_thumb_drag_and_trough_click_keep_native_bindings(self):
        import tkinter as tk
        from tkinter import ttk
        self.root.geometry("360x260")
        canvas = tk.Canvas(self.root, width=240, height=200, scrollregion=(0, 0, 1000, 1000))
        canvas.grid(row=0, column=0)
        for direction in ("vertical", "horizontal"):
            with self.subTest(direction=direction):
                view = canvas.yview if direction == "vertical" else canvas.xview
                bar = ttk.Scrollbar(self.root, orient=direction, command=view)
                bar.grid(row=0 if direction == "vertical" else 1,
                         column=1 if direction == "vertical" else 0, sticky="ns" if direction == "vertical" else "ew")
                canvas.configure(**{("yscrollcommand" if direction == "vertical" else "xscrollcommand"): bar.set})
                view("moveto", .2)
                self.root.deiconify(); self.root.update()
                vertical = direction == "vertical"
                cross = (bar.winfo_width() if vertical else bar.winfo_height()) // 2
                length = bar.winfo_height() if vertical else bar.winfo_width()
                coords = lambda along: (cross, along) if vertical else (along, cross)
                thumb = [along for along in range(length)
                         if bar.identify(*coords(along)).endswith("thumb")]
                self.assertTrue(thumb, "Rounded thumb must remain an identifiable ttk drag target")
                point = coords((thumb[0] + thumb[-1]) // 2)
                before = view()[0]
                bar.event_generate("<ButtonPress-1>", x=point[0], y=point[1])
                moved = coords(min(length - 1, (thumb[0] + thumb[-1]) // 2 + 20))
                bar.event_generate("<B1-Motion>", x=moved[0], y=moved[1])
                bar.event_generate("<ButtonRelease-1>", x=moved[0], y=moved[1])
                self.root.update()
                self.assertGreater(view()[0], before)
                trough = [along for along in range(length)
                          if bar.identify(*coords(along)).endswith("trough")]
                self.assertTrue(trough, "Rail must remain an identifiable ttk page-click target")
                point = coords(trough[-1])
                before = view()[0]
                bar.event_generate("<ButtonPress-1>", x=point[0], y=point[1])
                bar.event_generate("<ButtonRelease-1>", x=point[0], y=point[1])
                self.root.update()
                self.assertGreater(view()[0], before)
                canvas.configure(**{("yscrollcommand" if direction == "vertical" else "xscrollcommand"): ""})
                bar.destroy()

    def test_committed_selection_clears_at_idle_without_changing_value_or_cursor(self):
        import tkinter as tk
        from tkinter import ttk
        value = tk.StringVar(self.root, "second")
        combo = ttk.Combobox(self.root, textvariable=value, values=("first", "second"), state="readonly")
        combo.selection_range(0, "end"); combo.icursor(2)
        combo.event_generate("<<ComboboxSelected>>")
        self.assertTrue(combo.selection_present())
        self.root.update_idletasks()
        self.assertFalse(combo.selection_present())
        self.assertEqual(value.get(), "second")
        self.assertEqual(combo.index("insert"), 2)
        combo.selection_range(0, "end")
        self.root.update_idletasks()
        self.assertTrue(combo.selection_present(), "Ordinary text selection must remain available")

    def test_selection_callback_tolerates_destroyed_widget(self):
        from tkinter import ttk
        combo = ttk.Combobox(self.root, values=("first",))
        combo.event_generate("<<ComboboxSelected>>")
        combo.destroy()
        self.root.update_idletasks()

if __name__ == "__main__":
    unittest.main()
