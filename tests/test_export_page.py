"""Export page (Figma [design reference]): cards, actions, info texts and the Recording card last (PLAN-5 5.2).

One column like Replay's (USER-ADDITIONS UPDATE 13).
"""
import ast
from dataclasses import fields
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly import gui_theme, screenshot, screenshot_ui
from dolly.ui import export_page, kit_controls
from dolly.ui.export_page import ExportActions, ExportPage, ExportState
from dolly.ui.kit_controls import CanvasButton, CanvasInfoIcon
from dolly.ui.scale import px, ui_scale

ROOT = Path(__file__).resolve().parents[1]

# The old page's help labels, verbatim: each is an info bubble now (N14).
OLD_HELP = {
    "POV_INFO": "Select a hero with Game Follow or F9, then pause at the start. F8: return to Export. "
                "HUD hides automatically.",
    "AUDIO_INFO": "Reconstructed audio installs a verified helper automatically on first use.",
    "PROCESSING_INFO": "Background leaves more CPU time for editing while building the Players MOV. "
                       "Faster modes use more CPU.",
    "SCRATCH_INFO": "Blank uses the output folder. Raw Players files need a fast drive with free space. "
                    "Failed or cancelled captures are kept in their own folders.",
    "SCREENSHOT_INFO": "Captures the current paused view: plate.png, hero_alpha.png, hero_rgba.png, "
                       "hero_isolated.png and depth.exr in one folder beside the output file. "
                       "Hero = every player and their equipment, no NPCs.",
    "PASSES_INFO": "Color video always records first. Ticked passes are extra takes recorded automatically after it.",
    "DEPTH_INFO": "Depth and its extra passes use 100% render scale; your scale is restored afterward. "
                  "This can make export slower.",
    "RECORDING_INFO": "With passes ticked: Record video records the color take. When it finishes, Dolly records "
                      "each ticked pass in turn; you do not need to press Finish again.",
}
# Card rects in design px relative to the page: one column like Replay's (UPDATE 13: x 0, 613 wide, 15 apart,
# Recording last). Heights are the frame's ([design reference]), except FFmpeg Runtime: its three buttons fit one row now.
DESIGN_CARDS = {
    "Camera Source": (0, 0, 613, 133),
    "Destination": (0, 148, 613, 321),
    "Encoder & Quality": (0, 336, 613, 561),
    "Capture": (0, 576, 613, 749),
    "Output Passes": (0, 764, 613, 974),
    "Screenshot (High-Res Still)": (0, 989, 613, 1160),
    "FFmpeg Runtime": (0, 1175, 613, 1320),
    "Recording": (0, 1335, 613, 1491),
}
# In creation order: screenshot_ui.build adds "Take screenshot" after the page is built.
BUTTONS = ("Browse...", "Open output folder", "Browse scratch folder...", "Browse FFmpeg...", "Save path",
           "Use bundled FFmpeg", "Play shot", "Record video", "Finish recording", "Discard recording", "Take screenshot")


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


class ExportPageCase(unittest.TestCase):
    """A standalone Export page on its own root, with the Screenshot card filled like the app does."""

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

    def state(self, *, depth=False, status="Launch a replay to record video."):
        var = lambda value="": tk.StringVar(self.root, value)  # noqa: E731
        flag = lambda value=False: tk.BooleanVar(self.root, value)  # noqa: E731
        values = dict(
            ffmpeg_path=var(r"C:\Tools\Deadlock Dolly\tools\ffmpeg\bin\ffmpeg.exe"), video_bitrate=var("20 Mbps"), video_format=var("MP4"), format_choices=("MP4", "MOV", "MKV"),
            video_codec=var("Auto (hardware when available)"), player_processing=var("Background"),
            players_scratch=var(), video_depth=flag(depth), video_depth_exr=flag(), video_export_speed=var("1"),
            video_fixed_step=flag(), video_fps=var("60"), video_game_audio=flag(), video_layer_effects=flag(),
            video_layer_players=flag(), video_layer_world=flag(),
            video_path=var(r"C:\Users\Example\Videos\Deadlock Dolly\Dolly_Example.mp4"), video_pov_duration=var("10"),
            video_reconstructed_audio=flag(), video_source=var("Camera path"), video_status_text=var(status),
            video_ae_camera=flag(), video_usd_camera=flag(),
            codec_choices=("Auto (hardware when available)", "H.264 (CPU)"),
            bitrate_choices=("10 Mbps", "20 Mbps", "40 Mbps"))
        self.assertEqual(set(values), {f.name for f in fields(ExportState)})
        return ExportState(**values)

    def app_stub(self):
        """What screenshot_ui.init gives the app (the three variables), plus what refresh() reads."""
        return SimpleNamespace(
            screenshot_depth=tk.BooleanVar(self.root, True),
            screenshot_render_size=tk.StringVar(self.root, screenshot.RENDER_SIZE_LABELS[0]),
            screenshot_size_text=tk.StringVar(self.root, "Game render size: launch a replay to see it."),
            _still_run=None, _layer_queue=[], controller=SimpleNamespace(game_pid=lambda: 1234))

    def build(self, *, size=(948, 591), theme=True, still=True, **state):
        if theme:
            gui_theme.initialize(self.root)
        s = ui_scale(self.root)
        if size:
            self.root.deiconify()
            self.root.geometry(f"{px(s, size[0])}x{px(s, size[1])}")
        self.view_state = self.state(**state)
        view = ExportPage(self.root, self.view_state, ExportActions(
            **{f.name: self.record(f.name) for f in fields(ExportActions)}))
        self.app = None
        if still:
            self.app = self.app_stub()
            screenshot_ui.build(self.app, card=view.screenshot_card)
        self.settle(view)
        return view

    def settle(self, view):
        if self.root.state() != "withdrawn":
            self.root.update()
        else:
            self.root.update_idletasks()
        view.export_page.layout_now()
        self.root.update_idletasks()

    def resize(self, view, width, height):
        s = view.export_page.scale
        self.root.geometry(f"{px(s, width)}x{px(s, height)}")
        self.root.update()
        self.settle(view)

    def design(self, page, rect):
        return tuple(round(value / page.scale, 1) for value in rect)

    def assertRect(self, page, rect, expected, tolerance=1, msg=None):
        actual = self.design(page, rect)
        for a, e in zip(actual, expected):
            self.assertLessEqual(abs(a - e), tolerance, msg or (actual, expected))

    def button(self, view, text):
        return next(b for b in kit_controls.buttons(view.export_page) if b.cget("text") == text)


class ExportPageTests(ExportPageCase):
    def test_cards_follow_the_design_titles_in_one_column(self):
        view = self.build()
        page = view.export_page
        self.assertFalse(page.two_columns)
        # UPDATE 13: every card in one column (no side column), Recording last.
        self.assertEqual([(card.title, card.column) for card in page.cards],
                         [(title, None) for title in ("Camera Source", "Destination", "Encoder & Quality", "Capture",
                                                      "Output Passes", "Screenshot (High-Res Still)", "FFmpeg Runtime",
                                                      "Recording")])
        self.assertIs(page.cards[-1], view.recording_card)
        self.assertEqual((view.camera_source_card, view.video_destination_card, view.encoder_card,
                          view.export_capture_card, view.export_passes_card, view.screenshot_card, view.ffmpeg_card),
                         tuple(page.cards[:7]))
        self.assertEqual((export_page.TITLE, export_page.SUBTITLE),
                         ("Export", "Set the camera source, output file and passes, then record the shot as video "
                                    "or take a high-res still."))
        self.assertEqual(page.hosts, [])
        self.assertIsNotNone(page.fade)  # every scrolling page fades at its cut edges (UPDATE 9.2)
        self.assertIsNone(page.dock)     # nothing pinned (N1)

    def check_design_geometry(self, view, tolerance):
        page = view.export_page
        self.assertEqual(round(page.content_width / page.scale), 613)
        # Replay's column: every card at x 0, exactly as wide as the column, exactly 15 apart; each card as
        # tall as in the design. (Tops add up the cards above: at 150 % their rounding may drift a px or two.)
        rects = [card.rect for card in page.cards]
        self.assertEqual({(r[0], r[2]) for r in rects}, {(0, page.content_width)})
        self.assertEqual(rects[0][1], 0)
        self.assertEqual({b[1] - a[3] for a, b in zip(rects, rects[1:])}, {px(page.scale, 15)})
        for card in page.cards:
            x0, y0, x1, y1 = DESIGN_CARDS[card.title]
            self.assertRect(page, (card.rect[0], card.rect[2], card.rect[3] - card.rect[1]), (x0, x1, y1 - y0),
                            tolerance, msg=card.title)
            if page.scale == 1:
                self.assertRect(page, card.rect, DESIGN_CARDS[card.title], tolerance, msg=card.title)
        self.assertEqual(page.content_height, rects[-1][3])
        self.assertLessEqual(abs(page.content_height / page.scale - 1491), 2 * tolerance)

        def local(widget, card):     # px from the card's top-left
            x0, y0, x1, y1 = page.rect_of(widget)
            return (x0 - card.rect[0], y0 - card.rect[1], x1 - card.rect[0], y1 - card.rect[1])
        # Fixed label cells: the Encoder controls start at 176, the Capture ones at 111.
        for combo in (view.video_codec_combo, view.video_bitrate_combo, view.player_processing_combo):
            self.assertRect(page, page.rect_of(combo)[::2], (176, 592), tolerance)
        self.assertRect(page, page.rect_of(view.players_scratch_entry)[:1], (176,), tolerance)
        for combo in (view.video_fps_combo, view.video_speed_combo):
            self.assertRect(page, page.rect_of(combo)[::2], (111, 592), tolerance)
        # Rows: 10 apart below the title gap (the Encoder rows 54/94/134/174 below the card's top).
        rows = (view.video_codec_combo, view.video_bitrate_combo, view.player_processing_combo,
                view.players_scratch_entry)
        tops = [self.design(page, local(w, view.encoder_card))[1] for w in rows]
        for top, expected in zip(tops, (54, 94, 134, 174)):
            self.assertLessEqual(abs(top - expected), tolerance, tops)
        # The EXR option sits 17 px in under the depth master; the record row starts the Recording card.
        exr, depth = page.rect_of(view.video_depth_exr_checkbox), page.rect_of(view.video_depth_checkbox)
        self.assertLessEqual(abs((exr[0] - depth[0]) / page.scale - 17), tolerance)
        start = local(view.video_start_button, view.recording_card)
        self.assertRect(page, (start[1], start[3]), (54, 84), tolerance)
        # Screenshot card (filled by screenshot_ui): the button ends on the content edge, right-aligned.
        shot = local(self.app.screenshot_button, view.screenshot_card)
        self.assertRect(page, (shot[1], shot[2], shot[3]), (120, 592, 150), tolerance)

    def test_cards_keep_the_design_geometry_at_the_design_width(self):
        view = self.build()
        self.check_design_geometry(view, 1)

    def test_cards_keep_the_design_geometry_at_150_percent(self):
        # Tk keeps the scaling per display, shared by every root of the process: put it back
        # (cleanups run last-in first-out, so this runs while the root still exists).
        self.addCleanup(self.root.tk.call, "tk", "scaling", self.root.tk.call("tk", "scaling"))
        self.root.tk.call("tk", "scaling", 1.5 * 96 / 72)    # before anything reads the scale
        view = self.build()
        self.assertEqual(view.export_page.scale, 1.5)
        self.check_design_geometry(view, 2)

    def test_every_action_is_reachable_from_a_control(self):
        view = self.build()
        view.video_source_combo.current(1)
        view.video_source_combo.event_generate("<<ComboboxSelected>>")
        self.root.update_idletasks()    # the theme's selection clear runs at idle
        view.video_depth_checkbox.invoke()
        for box in view.video_layer_checkboxes:
            box.invoke()
        view.video_stop_button.configure(state="normal")
        view.video_cancel_button.configure(state="normal")
        for text in BUTTONS:
            if text != "Take screenshot":
                self.button(view, text).invoke()
        self.assertEqual(self.calls, [
            "video_source_changed", "depth_toggled", "layer_toggled", "layer_toggled", "layer_toggled",
            "browse_video", "open_output_folder", "browse_players_scratch", "browse_ffmpeg", "save_ffmpeg_preference",
            "use_bundled_ffmpeg", "play", "start_video_recording", "stop_video_recording", "discard_recording"])
        self.assertEqual({name for name in self.calls}, {f.name for f in fields(ExportActions)})
        self.assertEqual(self.view_state.video_source.get(), "Player POV")
        self.assertIs(view.video_browse_button, self.button(view, "Browse..."))
        self.assertIs(view.players_scratch_browse, self.button(view, "Browse scratch folder..."))
        self.assertIs(view.ffmpeg_browse_button, self.button(view, "Browse FFmpeg..."))

    def test_buttons_are_canvas_drawn_kit_buttons_with_the_ttk_surface(self):
        view = self.build()
        page = view.export_page
        found = kit_controls.buttons(page)
        self.assertEqual([b.cget("text") for b in found], list(BUTTONS))
        self.assertTrue(all(isinstance(b, CanvasButton) for b in found))
        self.assertFalse([w for w in walk(page) if isinstance(w, (ttk.Button, tk.Button))])
        self.assertEqual([b.cget("text") for b in found if b.kind == "primary"], ["Record video", "Take screenshot"])
        self.assertEqual({b.kind for b in found if b.kind != "primary"}, {"secondary"})
        # Created disabled, as the old page did; the app's state rule reads and writes them like ttk buttons.
        self.assertEqual(str(view.video_stop_button.cget("state")), "disabled")
        self.assertEqual(str(view.video_cancel_button.cget("state")), "disabled")
        self.assertTrue(view.video_cancel_button.instate(["disabled"]))
        self.assertEqual(view.video_cancel_button.invoke(), "")
        self.assertEqual(self.calls, [])
        # "Record POV" / "Cancel processing" re-hug and the row lays out again.
        stop = page.rect_of(view.video_stop_button)
        view.video_start_button.configure(text="Record POV")
        view.video_cancel_button.configure(text="Cancel processing")
        self.settle(view)
        self.assertEqual(view.video_start_button.cget("text"), "Record POV")
        self.assertNotEqual(page.rect_of(view.video_stop_button)[0], stop[0])
        self.assertEqual(view.video_start_button.winfo_reqwidth(),
                         kit_controls.fonts.hug_width(page, "body", "Record POV") + 2 * px(page.scale, 13))
        self.assertGreater(view.video_start_button.winfo_width(), 1)
        page.reveal(view.video_start_button)     # (without window regions a card out of view is unmapped)
        self.root.update()
        self.assertTrue(view.video_start_button.winfo_ismapped())

    def test_info_bubbles_keep_the_old_help_texts(self):
        view = self.build()
        for name, text in OLD_HELP.items():
            self.assertEqual(getattr(export_page, name), text, name)
        icons = [control for card in view.export_page.cards for control in getattr(card.host, "_kit_controls", ())
                 if isinstance(control, CanvasInfoIcon)]
        self.assertEqual(sorted(icon.text for icon in icons), sorted(OLD_HELP.values()))
        # Static text is canvas items (PR1): no label widget on a card, except the field covers in entries.
        for card in view.export_page.cards:
            for widget in walk(card.host):
                if isinstance(widget, (ttk.Label, tk.Label)):
                    self.assertIsInstance(widget.master, ttk.Entry, widget)
        self.assertEqual(view.export_camera_note.cget("text"), "Camera: Free path")
        view.export_camera_note.configure(text="Camera: Deadlock's selected spectator view.")
        self.assertEqual(view.export_camera_note.cget("text"), "Camera: Deadlock's selected spectator view.")

    def test_scratch_placeholder_never_writes_the_variable(self):
        view = self.build()
        entry, variable = view.players_scratch_entry, self.view_state.players_scratch
        hint = next(w for w in entry.winfo_children() if str(w.cget("style")) == "Placeholder.TLabel")
        self.assertEqual(hint.cget("text"), "Output folder")
        self.assertEqual(hint.winfo_manager(), "place")
        entry.event_generate("<FocusIn>")
        self.root.update_idletasks()
        self.assertEqual(hint.winfo_manager(), "")
        entry.event_generate("<FocusOut>")
        self.root.update_idletasks()
        self.assertEqual(hint.winfo_manager(), "place")
        self.assertEqual(variable.get(), "")
        variable.set(r"D:\Scratch")
        self.assertEqual(hint.winfo_manager(), "")
        variable.set("")
        self.assertEqual(hint.winfo_manager(), "place")
        self.assertEqual(variable.get(), "")

    def test_long_paths_are_elided_at_rest_and_handed_over_on_focus(self):
        view = self.build()
        # Longer than either field, now that the column gives FFmpeg's field the card's width.
        self.view_state.ffmpeg_path.set(r"C:\Tools\Deadlock Dolly\tools\ffmpeg\builds\2026-10\full_build"
                                        r"\ffmpeg-n7.1-latest-win64-gpl-shared\bin\ffmpeg.exe")
        self.settle(view)
        for entry, variable in ((view.video_path_entry, self.view_state.video_path),
                                (view.ffmpeg_path_entry, self.view_state.ffmpeg_path)):
            cover = next(w for w in entry.winfo_children() if str(w.cget("style")) == "FieldValue.TLabel")
            self.assertEqual(cover.winfo_manager(), "place", entry)
            self.assertTrue(str(cover.cget("text")).endswith("…"))
            self.assertTrue(variable.get().startswith(str(cover.cget("text"))[:-1]))
            entry.event_generate("<FocusIn>")
            self.root.update_idletasks()
            self.assertEqual(cover.winfo_manager(), "")
            entry.event_generate("<FocusOut>")
            self.root.update_idletasks()
            self.assertEqual(cover.winfo_manager(), "place")
            self.assertFalse(variable.get().endswith("…"))
        # A short value needs no cover.
        self.view_state.video_path.set(r"C:\a.mp4")
        self.root.update_idletasks()
        cover = next(w for w in view.video_path_entry.winfo_children() if str(w.cget("style")) == "FieldValue.TLabel")
        self.assertEqual(cover.winfo_manager(), "")

    def test_pov_row_follows_pack_and_pack_forget(self):
        view = self.build()
        page = view.export_page
        source, destination = view.camera_source_card, view.video_destination_card
        self.assertFalse(view.video_pov_controls.visible)
        self.assertEqual(view.video_pov_controls.winfo_manager(), "")
        self.assertEqual(view.video_pov_duration_combo.winfo_manager(), "")
        # gui._video_source_changed / _update_export_camera_note use the packed-frame calls.
        view.video_pov_controls.pack(fill="x", before=view.video_camera_options)
        self.settle(view)
        self.assertEqual(view.video_pov_controls.winfo_manager(), "canvas")
        self.assertRect(page, source.rect, (0, 0, 613, 173))
        self.assertRect(page, destination.rect, (0, 188, 613, 361))
        combo = page.rect_of(view.video_pov_duration_combo)
        self.assertLessEqual(abs((combo[2] - combo[0]) / page.scale - 80), 1)
        self.assertRect(page, (combo[1],), (94,))
        self.assertLess(combo[3], page.rect_of(view.video_ae_camera_check)[1])
        view.video_pov_controls.pack_forget()
        self.settle(view)
        self.assertRect(page, source.rect, (0, 0, 613, 133))
        self.assertRect(page, destination.rect, (0, 148, 613, 321))
        self.assertEqual(view.video_pov_duration_combo.winfo_manager(), "")

    def test_one_column_at_every_width(self):
        view = self.build()
        page = view.export_page
        for width in (1400, 948, 888 + 18, 700, 560):
            self.resize(view, width, 591)
            self.assertFalse(page.two_columns, width)
            column = min(px(page.scale, 613), page.winfo_width() - px(page.scale, page.GUTTER))
            self.assertEqual(page.content_width, column, width)
            rects = [card.rect for card in page.cards]
            self.assertEqual({(r[0], r[2]) for r in rects}, {(0, column)}, width)   # never a side column
            self.assertEqual(sorted(rects, key=lambda r: r[1]), rects, width)      # in the page's order
            self.assertIs(page.cards[-1], view.recording_card)

    def test_recording_is_last_and_reveal_scrolls_to_the_record_buttons(self):
        view = self.build(size=(948, 400))
        page, canvas = view.export_page, view.export_page.canvas
        self.assertEqual(canvas.canvasy(0), 0)
        self.assertTrue(page.scrollbar.winfo_ismapped())
        for button in (view.video_start_button, view.video_stop_button, view.video_cancel_button):
            page.reveal(button)
            top, viewport = canvas.canvasy(0), canvas.winfo_height()
            rect = page.rect_of(button)
            self.assertGreater(top, 0)
            self.assertGreaterEqual(rect[1], top)
            self.assertLessEqual(rect[3], top + viewport)
            # The geometry surface the desktop self-test reads: inside the window after the reveal.
            y = button.winfo_rooty() - self.root.winfo_rooty()
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y + button.winfo_height(), self.root.winfo_height())
        page.reveal(view.video_path_entry)
        self.assertLessEqual(page.rect_of(view.video_path_entry)[1], canvas.canvasy(0) + canvas.winfo_height())
        self.assertGreaterEqual(page.rect_of(view.video_path_entry)[1], canvas.canvasy(0))

    def test_wrapped_status_grows_the_recording_card(self):
        view = self.build()
        page = view.export_page
        before = view.recording_card.rect
        self.view_state.video_status_text.set("Recording failed: " + "the encoder stopped answering " * 12)
        self.settle(view)
        after = view.recording_card.rect
        lines = round((after[3] - after[1] - (before[3] - before[1])) / px(page.scale, 16))
        self.assertGreaterEqual(lines, 1)
        self.assertEqual(after[1], before[1])
        self.assertLessEqual(abs(page.content_height - after[3]), 1)

    def test_window_counts_stay_within_the_budgets(self):
        view = self.build()
        page = view.export_page
        counts = page.window_counts()
        if page.sheet is not None:
            self.assertEqual(counts["page"], 1)       # the sheet (PR2)
            self.assertEqual(counts["moving"], 1)
        else:
            self.assertEqual(counts["page"], len(page.cards))   # the per-card fallback, never per widget
            self.assertEqual(counts["moving"], len(page.cards))
        self.assertEqual(counts["inputs"], 23)        # PR3: Export <= 26
        for card in page.cards:
            self.assertFalse([item for item in card.host.find_all() if card.host.type(item) == "window"])
        for widget in (view.video_source_combo, view.video_path_entry, view.ffmpeg_path_entry,
                       self.app.screenshot_size_combo, self.app.screenshot_depth_check):
            self.assertEqual(widget.winfo_manager(), "place")
            self.assertIn(widget.master, [card.host for card in page.cards])
        self.assertFalse([w for w in walk(page) if isinstance(w, ttk.Frame)], "containers are tk.Frames (PR4)")

    def test_tab_follows_reading_order_and_skips_hidden_and_disabled(self):
        view = self.build()
        app = self.app
        # Top to bottom in the one column (UPDATE 13).
        expected = [view.video_source_combo, view.video_ae_camera_check, view.video_usd_camera_check,
                    view.video_format_combo, view.video_path_entry, view.video_game_audio_check, view.video_reconstructed_audio_check,
                    view.video_codec_combo, view.video_bitrate_combo, view.player_processing_combo,
                    view.players_scratch_entry, view.video_fps_combo, view.video_speed_combo,
                    view.video_fixed_checkbox, view.video_depth_checkbox, *view.video_layer_checkboxes,
                    app.screenshot_size_combo, app.screenshot_depth_check, view.ffmpeg_path_entry]
        order, widget = [], view.video_source_combo
        for _ in range(60):
            widget = widget.tk_focusNext()
            if widget is None or widget is view.video_source_combo:
                break
            order.append(widget)
        self.assertEqual([w for w in order if w in expected], expected[1:])
        self.assertNotIn(view.video_pov_duration_combo, order)    # hidden row
        self.assertNotIn(view.video_depth_exr_checkbox, order)    # disabled while depth is off
        # The record buttons are reached through their focus group after the FFmpeg buttons' group.
        groups = [w for w in order if getattr(w, "_kit_group", None) is not None]
        self.assertIs(groups[-1]._kit_group, view.video_start_button.group)


class ScreenshotCardTests(ExportPageCase):
    def test_build_fills_the_kit_card_and_keeps_the_app_names(self):
        view = self.build()
        app, card = self.app, view.screenshot_card
        for widget in (app.screenshot_size_combo, app.screenshot_depth_check):
            self.assertIs(widget.master, card.host)
        self.assertEqual(str(app.screenshot_size_combo.cget("style")), "Kit.TCombobox")
        self.assertEqual(tuple(app.screenshot_size_combo.cget("values")), tuple(screenshot.RENDER_SIZE_LABELS))
        self.assertEqual(str(app.screenshot_size_combo.cget("state")), "readonly")
        self.assertEqual(str(app.screenshot_depth_check.cget("variable")), str(app.screenshot_depth))
        self.assertIsInstance(app.screenshot_button, CanvasButton)
        self.assertEqual((app.screenshot_button.kind, app.screenshot_button.canvas), ("primary", card.host))
        self.assertEqual(card.info.text, export_page.SCREENSHOT_INFO)
        texts = [element for element in card.elements if hasattr(element, "cget") and not hasattr(element, "core")]
        self.assertEqual([t.cget("text") for t in texts], ["Game render size: launch a replay to see it."])
        app.screenshot_size_text.set("Game render size: 2560 x 1440. Stills are saved at this size.")
        self.assertEqual(texts[0].cget("text"), "Game render size: 2560 x 1440. Stills are saved at this size.")
        with patch.object(screenshot_ui, "start") as start, patch.object(screenshot_ui, "render_size_changed") as size:
            app.screenshot_button.invoke()
            app.screenshot_size_combo.event_generate("<<ComboboxSelected>>")
            self.root.update_idletasks()
        start.assert_called_once_with(app)
        size.assert_called_once_with(app)
        # Tab reaches the rows added after the page was built.
        self.assertNotIn(str(app.screenshot_size_combo.cget("takefocus")), ("", "ttk::takefocus"))

    def test_refresh_drives_the_canvas_button_like_the_ttk_one(self):
        view = self.build()
        app = self.app
        screenshot_ui.refresh(app, False)
        self.assertEqual(str(app.screenshot_button.cget("state")), "disabled")
        self.assertEqual(str(app.screenshot_size_combo.cget("state")), "readonly")
        app._screenshot_size_checked = 0.0
        with patch.object(screenshot_ui, "client_size", return_value=(1920, 1080)):
            screenshot_ui.refresh(app, True)
        self.assertEqual(str(app.screenshot_button.cget("state")), "normal")
        self.assertEqual(app.screenshot_size_text.get(), "Game render size: 1920 x 1080. Stills are saved at this size.")
        app._layer_queue = [("players", "capture")]
        screenshot_ui.refresh(app, True)
        self.assertEqual(str(app.screenshot_button.cget("state")), "disabled")
        self.assertTrue(app.screenshot_depth_check.instate(["disabled"]))
        self.assertEqual(str(app.screenshot_size_combo.cget("state")), "disabled")
        self.assertIsNotNone(view)

    def test_gui_layout_call_with_the_page_body_fills_the_same_card(self):
        # Until integration passes card=, gui_layout hands over export_page.body and a "before" card.
        view = self.build(still=False)
        self.assertIs(view.export_page.body, view.screenshot_card)
        app = self.app_stub()
        screenshot_ui.build(app, view.export_page.body, before=view.video_destination_card)
        self.settle(view)
        self.assertIs(app.screenshot_size_combo.master, view.screenshot_card.host)
        self.assertIn(app.screenshot_button, kit_controls.buttons(view.screenshot_card.host))
        self.assertRect(view.export_page, view.screenshot_card.rect, DESIGN_CARDS["Screenshot (High-Res Still)"])
        self.assertEqual(view.export_page.window_counts()["inputs"], 23)

    def test_legacy_frame_build_still_makes_its_own_card(self):
        gui_theme.initialize(self.root)
        frame = ttk.Frame(self.root)
        frame.pack()
        app = self.app_stub()
        screenshot_ui.build(app, frame)
        self.assertIsInstance(app.screenshot_button, ttk.Button)
        self.assertEqual(app.screenshot_button.cget("text"), "Take screenshot")
        labels = [w.cget("text") for w in walk(frame) if isinstance(w, ttk.Label)]
        self.assertIn(export_page.SCREENSHOT_INFO, labels)
        with patch.object(screenshot_ui, "start") as start:
            app.screenshot_button.invoke()
        start.assert_called_once_with(app)


class ExportModuleTests(unittest.TestCase):
    def test_page_module_imports_no_application_or_export_backend(self):
        tree = ast.parse((ROOT / "dolly" / "ui" / "export_page.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        forbidden = {"dolly.gui", "dolly.gui_layout", "dolly.controller", "dolly.video_export", "dolly.launcher",
                     "dolly.screenshot_ui", "..gui", "..gui_layout", "..controller", "..video_export", "..launcher",
                     "..screenshot_ui"}
        self.assertFalse(forbidden & imported, imported)

    def test_export_files_have_no_hardcoded_colours(self):
        import re
        for name in ("dolly/ui/export_page.py", "dolly/screenshot_ui.py", "tests/test_export_page.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}", text), [], name)


if __name__ == "__main__":
    unittest.main()
