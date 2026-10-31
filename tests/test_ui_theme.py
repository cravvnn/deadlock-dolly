"""Every Dolly UI colour lives in dolly/ui_theme.py: the frozen in-game palette and the desktop one."""
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest

from dolly import ui_theme

ROOT = Path(__file__).resolve().parents[1]

class UiThemeTests(unittest.TestCase):
    def test_tokens_are_valid_hex(self):
        for palette in ("TOKENS", "NATIVE_TOKENS"):
            tokens = getattr(ui_theme, palette)
            self.assertTrue(tokens)
            for name, value in tokens.items():
                with self.subTest(palette=palette, token=name):
                    self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
                    self.assertRegex(value, r"^#[0-9a-f]{6}$")
                    ui_theme.rgb(value)

    def test_native_palette_is_frozen_and_desktop_extends_it(self):
        native, desktop = ui_theme.NATIVE_TOKENS, ui_theme.TOKENS
        # The in-game panel keeps exactly its 36 colours; the desktop carries
        # every native key (overridden or not) plus its own additions.
        self.assertEqual(len(native), 36)
        self.assertLessEqual(set(native), set(desktop))
        self.assertEqual(ui_theme.required_tokens(), tuple(sorted(native)))
        for name in ("tab_dim", "curve_rotation", "warn", "keycap"):
            self.assertEqual(desktop[name], native[name], "Colours outside Dolly Blue stay shared")
        # The desktop Camera curve draws X in the Redesign frame's blue ([design reference]); the overlay keeps its own.
        self.assertEqual(desktop["curve_x"], "#50aaff")
        self.assertNotEqual(desktop["curve_x"], native["curve_x"])
        spec = importlib.util.spec_from_file_location("dolly_generate_ui_tokens", ROOT / "tools" / "generate_ui_tokens.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        rendered = re.findall(r"constexpr unsigned (\w+) = 0x([0-9a-f]{6});", generator.render())
        self.assertEqual([name for name, _ in rendered], [name.upper() for name in sorted(native)])
        self.assertEqual([value for _, value in rendered], [native[name][1:] for name in sorted(native)])
        self.assertFalse({name.lower() for name, _ in rendered} & (set(desktop) - set(native)),
                         "Desktop-only keys never reach the header")
        # Card auto-detection compares backgrounds: the surfaces must differ.
        self.assertNotEqual(desktop["panel"], desktop["bg"])
        self.assertNotIn(desktop["selected"], (desktop["panel"], desktop["bg"]))
        for name in ("dim", "card_edge", "rail", "rail_hover", "status_ready", "status_busy", "status_error",
                     "sidebar_top", "sidebar_edge_top", "sidebar_edge_bottom", "stage_top", "nav_selected_to",
                     "nav_idle_to", "backdrop_shard", "backdrop_rim", "backdrop"):
            self.assertIn(name, desktop)
            self.assertNotIn(name, native)

    def test_display_fonts_name_sizes_and_fallbacks(self):
        self.assertEqual(ui_theme.FONT_SIZES["body"], 9)
        self.assertEqual(ui_theme.FONT_SIZES["title"], 18)
        for key, candidates in ui_theme.DISPLAY_FONTS.items():
            with self.subTest(font=key):
                self.assertTrue(candidates)
                for family, size, style in candidates:
                    self.assertIsInstance(family, str)
                    self.assertGreater(size, 0)
                    self.assertIn(style, ("", "bold"))
                if key != "icon":
                    self.assertTrue(candidates[-1][0].startswith("Segoe UI"),
                                    "Every display face ends in a system fallback")
                    families = [family for family, _size, _style in candidates]
                    self.assertTrue(any(family.startswith("Inter") for family in families),
                                    "Inter, shipped with Dolly, comes before the Segoe UI fallback")
                    first_segoe = min(i for i, family in enumerate(families) if family.startswith("Segoe"))
                    self.assertTrue(all(not family.startswith("Inter") for family in families[first_segoe:]))
        for family in ui_theme.TRUE_DESCENT_EM:
            self.assertTrue(family.startswith("VALVE "))
        # UPDATE 23: text is Inter. UPDATE 25: one card title size, 15 px, on every page (no 18 px role).
        self.assertEqual(ui_theme.DISPLAY_FONTS["body"][0], (ui_theme.UI_FACE, 12, ""))
        self.assertEqual(ui_theme.DISPLAY_FONTS["small"][0], (ui_theme.UI_FACE, 11, ""))
        self.assertEqual(ui_theme.DISPLAY_FONTS["card_title"][0], ("VALVE Oracle SemiBold", 15, ""))
        self.assertEqual(sorted(key for key in ui_theme.DISPLAY_FONTS if "title" in key), ["card_title", "page_title"])
        self.assertEqual((ui_theme.UI_FACE, ui_theme.UI_FACE_VALUE, ui_theme.FONT_FALLBACK),
                         ("Inter", "Inter Medium", "Segoe UI"))

    def test_native_header_is_current(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "generate_ui_tokens.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(
            result.returncode, 0,
            "native/src/dolly_ui_tokens_generated.hpp is stale; run tools/generate_ui_tokens.py\n"
            + result.stdout + result.stderr)

    def test_desktop_has_no_hardcoded_colours(self):
        names = {"dolly/gui.py", "dolly/gui_theme.py", "dolly/gui_layout.py",
                 "dolly/curve.py", "dolly/release_launcher.py", "dolly/ui/widgets.py", "dolly/ui/library_page.py",
                 "dolly/ui/settings_page.py", "dolly/ui/export_page.py", "dolly/curve_workspace.py",
                 "dolly/screenshot_ui.py", "dolly/graphics_profiles_ui.py"}
        # Every shared UI module, including the redesigned shell as it lands.
        names.update(path.relative_to(ROOT).as_posix() for path in ROOT.glob("dolly/ui/*.py"))
        for name in sorted(names):
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

        with self.subTest(contract='Dolly Blue kit values on shared styles'):
            style = ttk.Style(self.root)
            t = ui_theme.TOKENS
            self.assertEqual(self.root.tk.splitlist(style.lookup(".", "font")),
                             (ui_theme.FONT_FAMILY, str(ui_theme.FONT_SIZES["body"])))
            self.assertNotIn("bold", self.root.tk.splitlist(style.lookup("Primary.TButton", "font")))
            self.assertEqual(style.lookup("Primary.TButton", "foreground"), t["accent_ink"])
            for name in ("TEntry", "TCombobox"):
                self.assertEqual(style.lookup(name, "foreground"), t["muted"])
                self.assertEqual(style.lookup(name, "foreground", ("readonly",)), t["muted"])
                self.assertEqual(style.lookup(name, "insertcolor"), t["text"])
            self.assertEqual(style.lookup("Treeview", "foreground"), t["muted"])
            self.assertEqual(style.lookup("Treeview", "background", ("selected",)), t["selected"])
            self.assertEqual(style.lookup("Treeview.Heading", "background", ("active",)), t["panel"])
            self.assertEqual(style.lookup("Treeview.Heading", "foreground"), t["dim"])
            self.assertEqual(style.lookup("Accent.TLabel", "foreground"), t["text"])
            self.assertEqual(style.lookup("TCheckbutton", "foreground"), t["text"])
            self.assertEqual(tuple(map(int, self.root.tk.splitlist(
                style.lookup("Navigation.TNotebook", "tabmargins")))), (0, 0, 0, 0))

        with self.subTest(contract='replay page styles'):
            from dolly.ui import fonts
            style = ttk.Style(self.root)
            t = ui_theme.TOKENS
            scale = max(1, self.root.winfo_fpixels("1i") / 96)
            px = lambda value: int(value * scale + .5)
            self.assertEqual(style.layout("Library.Treeview.Row"), [("Dolly.tree.row", {"sticky": "nswe"})])
            self.assertEqual(int(style.lookup("Library.Treeview", "rowheight")), px(36))
            self.assertEqual(style.lookup("Library.Treeview", "background", ("selected",)), t["panel"])
            self.assertEqual(style.lookup("Library.Treeview", "foreground", ("selected",)), t["text"])
            self.assertEqual(style.lookup("Library.Treeview", "foreground"), t["muted"])
            self.assertEqual(style.lookup("Library.Treeview.Heading", "foreground"), t["dim"])
            self.assertEqual(style.lookup("Library.Treeview.Heading", "background", ("active",)), t["panel"])
            chip = style.layout("Chip.Card.TLabel")
            self.assertEqual(chip[0][0], "Dolly.field.card")
            self.assertEqual([child[0] for child in chip[0][1]["children"]][-1], "Label.padding")
            self.assertEqual(style.lookup("Chip.Card.TLabel", "background"), t["panel"])
            self.assertEqual(style.lookup("Chip.Card.TLabel", "fieldbackground"), t["field"])
            self.assertEqual(style.lookup("Chip.Card.TLabel", "foreground"), t["muted"])
            self.assertEqual(style.layout("Search.Card.TEntry"), style.layout("Card.TEntry"))
            self.assertEqual(tuple(map(int, self.root.tk.splitlist(style.lookup("Search.Card.TEntry", "padding")))),
                             (px(26), 5, 10, 5))
            self.assertEqual(style.lookup("Search.Card.TEntry", "background", ("focus",)), t["panel"])
            self.assertEqual(style.lookup("Placeholder.TLabel", "background"), t["field"])
            self.assertEqual(style.lookup("Placeholder.TLabel", "foreground"), t["dim"])
            self.assertEqual(style.lookup("CardTitle.TLabel", "font"), fonts.NAMES["card_title"])
            self.assertEqual(style.lookup("CardTitle.TLabel", "foreground"), t["text"])
            self.assertEqual(tuple(map(int, self.root.tk.splitlist(style.lookup("CardTitle.TLabel", "padding")))),
                             (0, 0, 0, fonts.descent_shortfall(self.root, "card_title")))

        with self.subTest(contract='kit styles and focus-less Replay fields'):
            # The five ported pages' Kit.* styles exist once the theme is up (PLAN-5 2.3), and
            # Replay's fields and dropdowns draw the kit's field: no focus or hover face (N4).
            from dolly.ui import kit_styles
            style = ttk.Style(self.root)
            t = ui_theme.TOKENS
            for name in ("Kit.TEntry", "Kit.TCombobox", "Kit.TMenubutton"):
                self.assertEqual(style.layout(name)[0][0], "Kit.combo" if "Combobox" in name else "Kit.field")
            self.assertEqual(style.layout("Kit.Tab.Selected.TButton")[0][0], "Kit.tab")
            self.assertEqual(style.layout("Kit.Keycap.TButton")[0][0], "Kit.keycap")
            self.assertEqual(style.layout("Kit.Treeview"), style.layout("Library.Treeview"))
            self.assertIn("Kit.Checkbutton.indicator", str(style.layout("Kit.TCheckbutton")))
            self.assertLessEqual(set(kit_styles.ELEMENTS), set(style.element_names()))
            for name in ("Stage.Card.TEntry", "Stage.Card.TCombobox"):
                with self.subTest(style=name):
                    self.assertEqual(style.layout(name)[0][0], "Kit.field")
                    self.assertNotIn("Dolly.field", str(style.layout(name)))
                    for option in ("fieldbackground", "bordercolor", "lightcolor", "darkcolor"):
                        self.assertEqual(style.lookup(name, option), t["field"])
                        for state in (("focus",), ("active",), ("active", "focus"), ("readonly", "focus"),
                                      ("readonly", "active", "focus")):
                            self.assertEqual(style.lookup(name, option, state), t["field"], (option, state))
                        self.assertEqual(style.lookup(name, option, ("disabled",)), t["field_disabled"])
                    self.assertEqual(style.lookup(name, "foreground"), t["muted"])
                    self.assertEqual(style.lookup(name, "foreground", ("readonly", "focus")), t["muted"])
                    self.assertEqual(style.lookup(name, "background", ("focus",)), t["panel"])
            # Replay keeps its paddings and chevron; its search field keeps its keyboard ring (Q5).
            self.assertIn("Dolly.Combobox.downarrow", str(style.layout("Stage.Card.TCombobox")))
            self.assertEqual(style.lookup("Search.Card.TEntry", "bordercolor", ("focus",)), t["accent"])

        with self.subTest(contract='named display fonts'):
            from dolly.ui import fonts
            names = set(self.root.tk.splitlist(self.root.tk.call("font", "names")))
            self.assertLessEqual(set(fonts.NAMES.values()), names)
            self.assertEqual(str(fonts.font(self.root, "card_title")), fonts.NAMES["card_title"])
            for key in fonts.NAMES:
                self.assertLess(int(fonts.font(self.root, key).cget("size")), 0, "Display sizes are pixels")

    def test_display_font_changes_re_measure_card_titles(self):
        from tkinter import ttk
        from dolly.ui import fonts
        style = ttk.Style(self.root)
        fonts.install(self.root, 2.0)  # A reconfigured named font notifies the theme.
        self.assertEqual(int(fonts.font(self.root, "card_title").cget("size")), -30)
        self.assertEqual(tuple(map(int, self.root.tk.splitlist(style.lookup("CardTitle.TLabel", "padding")))),
                         (0, 0, 0, fonts.descent_shortfall(self.root, "card_title")))
        self.assertEqual(style.lookup("CardTitle.TLabel", "font"), fonts.NAMES["card_title"])

    def test_styles_take_inter_once_it_resolves(self):
        # UPDATE 23: with the shipped faces listed, text styles draw Inter and value styles
        # (fields, chips, table cells, keycaps, dropdown buttons) Inter Medium, like Figma.
        import os
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from dolly import gui_theme
        from dolly.ui import fonts
        listed = ("Segoe UI", "Segoe UI Semibold", "Inter", "Inter Medium", "Inter SemiBold", "Segoe MDL2 Assets")
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        with patch.object(ui_theme, "FONT_FAMILY", ui_theme.FONT_FAMILY), \
                patch.object(ui_theme, "FONT_VALUE", ui_theme.FONT_VALUE), \
                patch.dict(os.environ, {"DOLLY_UI_FONTS": ""}), \
                patch.object(fonts, "load_bundled", return_value=[]), \
                patch.object(fonts.tkfont, "families", return_value=listed):
            gui_theme.initialize(root)
            style = ttk.Style(root)

            def face(name):
                return root.tk.splitlist(style.lookup(name, "font"))[0]
            for name in (".", "Title.TLabel", "Placeholder.TLabel", "Library.Treeview.Heading"):
                self.assertEqual(face(name), "Inter", name)
            for name in ("FieldValue.TLabel", "Chip.Card.TLabel", "Library.Treeview", "Kit.Treeview",
                         "Kit.Keycap.TButton", "Kit.TMenubutton"):
                self.assertEqual(face(name), "Inter Medium", name)
            self.assertEqual(root.tk.call("font", "configure", "TkTextFont", "-family"), "Inter Medium")
            self.assertEqual(root.tk.call("font", "configure", "TkMenuFont", "-family"), "Inter")

    def test_library_rows_draw_through_the_divider_sublayout(self):
        from tkinter import ttk
        style = ttk.Style(self.root)
        # Tk 8.6 draws each row through "<style>.Row"; its stock layout proves the lookup exists.
        self.assertEqual(style.layout("Treeview.Row")[0][0], "Treeitem.row")
        frame = ttk.Frame(self.root, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("name", "size"), show="headings", height=3, style="Library.Treeview")
        for column, title in (("name", "Replay"), ("size", "Size")):
            tree.heading(column, text=title, anchor="w")
        for index in range(4):
            tree.insert("", "end", iid=str(index), values=(f"{index}.dem", "1.0 MB"))
        tree.selection_set("1")
        tree.pack(fill="both", expand=True)
        self.root.deiconify(); self.root.update()
        self.assertEqual(tree.identify_region(20, tree.winfo_height() // 2), "cell")
        self.assertEqual(tree.selection(), ("1",))

    def test_library_row_divider_is_one_tile_per_row(self):
        # ttk tiles an image element's centre. A divider image of a few pixels cost
        # thousands of image draws per row: every scroll, selection or search in the
        # replay list took over a second at 150 %. The image is a whole row instead.
        from tkinter import ttk
        from dolly.ui.scale import hairline, px, ui_scale
        from dolly.ui.stage_page import StagePage
        t = ui_theme.TOKENS
        scale = ui_scale(self.root)
        height = int(ttk.Style(self.root).lookup("Library.Treeview", "rowheight"))
        line = hairline(scale)
        rows = [image for image in self.root._dolly_theme_images if image.height() == height
                and tuple(image.get(0, 0)) == ui_theme.rgb(t["card_edge"])
                and tuple(image.get(0, line)) == ui_theme.rgb(t["panel"])]
        self.assertEqual(len(rows), 1, "the replay table's divider row image")
        self.assertGreaterEqual(rows[0].width(), px(scale, StagePage.CARD_MAX))

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
        popup = self.root._dolly_combo_popup
        self.assertTrue(popup.top.winfo_ismapped())
        popup._goto(2)
        popup.list.event_generate("<Return>")
        self.root.update()
        self.assertEqual(combo.get(), "2")
        self.assertIsNone(self.root._dolly_combo_popup)
        combo.focus_force(); self.root.update()
        combo.event_generate("<Down>"); self.root.update()
        popup = self.root._dolly_combo_popup
        self.assertIsNotNone(popup)
        popup.list.event_generate("<Escape>"); self.root.update()
        self.assertEqual(combo.get(), "2")
        self.assertIsNone(self.root._dolly_combo_popup)
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
