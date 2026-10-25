"""Actual Controls page widgets and the application's shared recorder route."""
from dataclasses import fields
import json
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from dolly.editor_actions import ACTION_LABELS, BINDABLE_ORDER, EditorBinding
from dolly.settings import AppSettings
from dolly.ui.controls_page import ControlsActions, ControlsPage


class ControlsPageTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.calls = []
        self.saved_sensitivity_values = []
        def record(name, args):
            self.calls.append((name, args))
            if name == "save_sensitivity":
                self.saved_sensitivity_values.append(self.sensitivity.get())
        commands = ControlsActions(**{field.name: lambda *args, name=field.name:
                                     record(name, args) for field in fields(ControlsActions)})
        self.records = (("capture", "Capture camera", "Camera"), ("replace", "Replace selected camera", "Camera"),
                        ("panel", "Toggle Dolly panel", "Interface"), ("reshade", "Toggle ReShade menu", "Interface"),
                        ("move_forward", "Move forward", "Movement"))
        self.sensitivity = tk.StringVar(self.root, "0.12")
        self.view = ControlsPage(self.root, self.records, commands,
            movement_speed=tk.StringVar(self.root, "320"), mouse_sensitivity=self.sensitivity,
            feedback=tk.StringVar(self.root, "Fixture controls"))
        self.settings = AppSettings()
        self.view.refresh({**self.settings.action_bindings, "reshade": self.settings.reshade_binding})

    def tearDown(self):
        self.assertEqual(getattr(self, "errors", []), [])

    def visible(self, mapping):
        return [key for key, widget in mapping.items() if widget.winfo_manager()]

    def widgets(self):
        def walk(parent):
            for child in parent.winfo_children():
                yield child
                yield from walk(child)
        return tuple(walk(self.root))

    def test_shortcut_clear_reset_and_reset_all_route_exact_action(self):
        for action, _label, _group in self.records:
            self.view.binding_buttons[action].invoke()
            self.view.rows[action].clear_button.invoke()
            self.view.rows[action].reset_button.invoke()
        self.view.reset_all_button.invoke()
        expected = [(name, (action,)) for action, _label, _group in self.records
                    for name in ("change_binding", "clear_binding", "reset_binding")]
        self.assertEqual(self.calls, expected + [("reset_all", ())])
        self.assertFalse(any(isinstance(widget, ttk.Combobox) for widget in self.widgets()))

    def test_search_matches_action_shortcut_and_group_hides_empty_cards(self):
        for query, rows, groups in ((" CAPTURE ", ["capture"], ["Camera"]),
                                   ("f8", ["panel"], ["Interface"]),
                                   ("movement", ["move_forward"], ["Movement"]),
                                   ("no such shortcut", [], [])):
            self.view.search.set(query)
            self.assertEqual(self.visible(self.view.rows), rows)
            self.assertEqual(self.visible(self.view.groups), groups)
            self.assertEqual(bool(self.view.empty_label.winfo_manager()), not rows)
        self.view.search.set("")
        self.assertEqual(self.visible(self.view.rows), [row[0] for row in self.records])
        self.assertEqual(self.visible(self.view.groups), ["Camera", "Interface", "Movement"])
        self.assertEqual(tuple(self.view.list_body.pack_slaves()), tuple(self.view.groups.values()))

    def test_refresh_updates_live_search_and_unbound_clear_state(self):
        self.view.search.set("mouse5")
        self.assertEqual(self.visible(self.view.rows), [])
        bindings = {**self.settings.action_bindings, "reshade": None, "capture": EditorBinding("Mouse5")}
        self.view.refresh(bindings)
        self.assertEqual(self.visible(self.view.rows), ["capture"])
        self.assertEqual(self.view.binding_buttons["capture"].cget("text"), "Mouse5")
        self.assertEqual(str(self.view.rows["reshade"].clear_button.cget("state")), "disabled")
        self.view.rows["reshade"].clear_button.invoke()
        self.assertEqual(self.calls, [])
        self.view.search.set("Unbound")
        self.assertEqual(self.visible(self.view.rows), ["reshade"])

    def test_reveal_clears_filter_and_reaches_last_action_in_scroll_region(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        self.view.search.set("Capture")
        self.view.reveal_action("move_forward")
        self.root.update()
        self.assertEqual(self.view.search.get(), "")
        button = self.view.binding_buttons["move_forward"]
        canvas = self.view.page.canvas
        self.assertGreaterEqual(button.winfo_rooty(), canvas.winfo_rooty())
        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), canvas.winfo_rooty() + canvas.winfo_height())

    def test_camera_feel_and_movement_settings_are_preserved_on_controls_page(self):
        next(widget for widget in self.widgets() if isinstance(widget, ttk.Button)
             and widget.cget("text") == "Very slow").invoke()
        self.assertAlmostEqual(float(self.sensitivity.get()), .02)
        next(widget for widget in self.widgets() if isinstance(widget, ttk.Button)
             and widget.cget("text") == "Save movement settings").invoke()
        self.assertEqual(self.calls, [("save_sensitivity", ()), ("save_movement", ())])

    def test_exact_sensitivity_entry_and_save_return_preserve_authored_precision(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        entry = self.view.camera_feel.sensitivity_entry
        self.assertEqual(str(entry.cget("textvariable")), str(self.sensitivity))
        save = next(widget for widget in self.widgets() if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "Save sensitivity")
        for value in ("0.123456789", "0.001", "10"):
            entry.delete(0, "end")
            entry.insert(0, value)
            self.root.update_idletasks()
            self.assertEqual(self.sensitivity.get(), value)
            save.invoke()
            self.assertEqual(self.saved_sensitivity_values[-1], value)
        self.view.page.reveal(entry)
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.saved_sensitivity_values, ["0.123456789", "0.001", "10", "10"])

    def test_keyboard_traversal_reveals_offscreen_actions_and_exact_preferences(self):
        self.root.deiconify()
        self.root.geometry("950x460+0+0")
        self.root.update()
        self.view.page.canvas.yview_moveto(0)
        self.view.search_entry.focus_force()
        self.root.update()
        targets = (self.view.binding_buttons["move_forward"], self.view.camera_feel.sensitivity_entry)
        visited = set()
        route = []
        chain = []
        current = self.view.search_entry
        for _ in range(len(self.widgets())+1):
            chain.append(current)
            current = current.tk_focusNext()
            if current is self.view.search_entry:
                break
            self.assertNotIn(current, chain, "Tab traversal must form one finite cycle.")
        self.assertIs(current, self.view.search_entry)
        self.assertFalse([str(widget) for widget in chain if isinstance(widget, (ttk.Frame, ttk.Label))],
                         "Focus reveal must not turn structural cards or labels into Tab stops.")
        self.assertTrue(set(targets).issubset(chain))
        def visible(widget):
            canvas = self.view.page.canvas
            self.assertGreaterEqual(widget.winfo_rooty(), canvas.winfo_rooty())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), canvas.winfo_rooty()+canvas.winfo_height())
        for _ in range(len(chain)+1):
            focused = self.root.focus_get()
            route.append((str(focused), focused.winfo_class() if focused else None,
                          self.view.page.canvas.yview()))
            if focused in targets:
                visible(focused)
                visited.add(focused)
                if focused is targets[-1]:
                    break
            self.assertIsNotNone(focused)
            focused.event_generate("<KeyPress>", keysym="Tab")
            self.root.update()
        self.assertEqual(visited, set(targets), route)
        self.assertGreater(self.view.page.canvas.yview()[0], 0)
        for _ in range(len(chain)+1):
            focused = self.root.focus_get()
            if focused is self.view.search_entry:
                break
            focused.event_generate("<KeyPress>", keysym="Tab", state=0x1)
            self.root.update()
        self.assertIs(self.root.focus_get(), self.view.search_entry)
        visible(self.view.search_entry)
        self.assertEqual(self.calls, [])

    def test_destroying_controls_cancels_pending_filter_geometry_refresh(self):
        self.root.update_idletasks()
        self.view.search.set("capture")
        token = self.view._filter_after
        self.assertIsNotNone(token)
        self.view.search.set("panel")
        self.assertEqual(self.view._filter_after, token)
        self.assertIn(token, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.view.page.destroy()
        self.assertNotIn(token, self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.root.update()


class ControlsAppFixture(unittest.TestCase):
    def setUp(self):
        from dolly.gui import DollyApp
        self.DollyApp = DollyApp
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        if hasattr(self, "fixture_scale"):
            self.root.tk.call("tk", "scaling", self.fixture_scale*96/72)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.submit = patch.object(DollyApp, "_submit").start()
        patch.object(DollyApp, "_poll").start()
        patch.object(DollyApp, "_load_app_settings", return_value=AppSettings(
            auto_updates=False, full_editor=getattr(self, "fixture_full_editor", False))).start()
        patch("dolly.graphics_profiles.load_library", return_value={"format": 1, "selected": "", "profiles": []}).start()
        patch("dolly.ui.binding_capture.SideMouseCapture.start", return_value=False).start()
        self.addCleanup(patch.stopall)
        self.app = DollyApp(self.root)
        self.addCleanup(self.close_app)

    def close_app(self):
        if self.app.binding_dialog is not None and self.app.binding_dialog.winfo_exists():
            self.app.binding_dialog.event_generate("<Escape>")
            self.app.binding_dialog.destroy() if self.app.binding_dialog is not None else None
        self.app.closed = True
        self.app.jobs.put(None)
        for timer in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.tk.call("after", "cancel", timer)
        self.root.destroy()

    def tearDown(self):
        self.assertEqual(getattr(self, "errors", []), [])

    def open_recorder(self, action="panel"):
        self.root.deiconify()
        self.app._record_binding(action)
        self.root.update()
        dialog = self.app.binding_dialog
        dialog.focus_force()  # Own isolated Tk fixture only; never a user's app.
        self.root.update()
        return dialog

    def button(self, text):
        def walk(parent):
            for child in parent.winfo_children():
                yield child
                yield from walk(child)
        return next(widget for widget in walk(self.app.binding_dialog)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == text)


class ControlsRecorderIntegrationTests(ControlsAppFixture):
    def test_all_existing_actions_present_once_in_central_controls_page(self):
        # The standard desktop hides the panorama shortcut; the environment
        # edition shows it (see test_environment_action).
        expected = set(BINDABLE_ORDER) - {"object_picker", "capture_environment"} | {"reshade"}
        actual = [record[0] for record in self.app.controls_view.records]
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(expected))
        self.app._show_keybinds("reshade")
        self.assertEqual(self.app.notebook.select(), str(self.app.controls_tab))
        self.assertTrue(self.app.full_editor.get())

    def test_filter_after_reaching_all_controls_keeps_the_search_result_visible(self):
        self._check_filter_after_reaching_all_controls()

    def _check_filter_after_reaching_all_controls(self):
        with patch.object(self.root, "winfo_screenwidth", return_value=1280), \
             patch.object(self.root, "winfo_screenheight", return_value=720):
            self.root.minsize(*self.app._window_dimensions(1000, 700))
        self.root.deiconify()
        self.root.geometry("1280x720+0+0")
        self.root.update()
        self.app._show_keybinds("capture")
        self.root.update()
        controls = self.app.controls_view
        controls.page.canvas.yview_moveto(0)
        self.root.update()
        for action in controls.binding_buttons:
            controls.page.reveal(controls.rows[action])
            self.root.update_idletasks()
            target, host = controls.binding_buttons[action], controls.page.canvas
            for _ in range(4):
                top = target.winfo_rooty()-host.winfo_rooty()
                if 0 <= top and top+target.winfo_height() <= host.winfo_height():
                    break
                host.yview_scroll(-1 if top < 0 else 1, "units")
                self.root.update_idletasks()
        controls.page.reveal(controls.camera_feel.sensitivity_entry)
        self.root.update()
        canvas, body = controls.page.canvas, controls.page.body
        previous_top = canvas.canvasy(0)
        self.app._refresh_bindings()
        self.root.update()
        self.assertEqual(canvas.canvasy(0), previous_top, "Refreshing shortcuts must preserve the user's scroll position.")
        def geometry():
            def widget(value):
                return {"manager": value.winfo_manager(), "mapped": bool(value.winfo_ismapped()),
                        "x": value.winfo_rootx(), "y": value.winfo_rooty(),
                        "width": value.winfo_width(), "height": value.winfo_height(),
                        "requested_height": value.winfo_reqheight()}
            return {"canvas": widget(canvas), "body": widget(body),
                    "bbox": canvas.bbox("all"), "yview": canvas.yview(),
                    "cached_region": controls.page._scrollregion,
                    "window_height": canvas.itemcget(controls.page.window, "height"),
                    "top": canvas.canvasy(0), "list": widget(controls.list_body),
                    "interface": widget(controls.groups["Interface"]),
                    "reshade": widget(controls.binding_buttons["reshade"])}
        before = geometry()
        self.assertGreater(canvas.yview()[0], 0)
        controls.search.set("reshade")
        for _ in range(5):
            self.root.update_idletasks()
            self.root.update()
        after = geometry()
        recovery = None
        if not after["reshade"]["mapped"]:
            controls.page._resize()
            self.root.update()
            recovery = geometry()
        proof = json.dumps({"before": before, "after": after, "explicit_resize_recovery": recovery}, sort_keys=True)
        print("FILTER_GEOMETRY " + proof)
        self.assertFalse(controls.groups["Movement"].winfo_ismapped(), proof)
        target = after["reshade"]
        self.assertTrue(target["mapped"], proof)
        self.assertGreaterEqual(target["y"], after["canvas"]["y"], proof)
        self.assertLessEqual(target["y"]+target["height"], after["canvas"]["y"]+after["canvas"]["height"], proof)
        self.assertEqual(canvas.canvasy(0), 0, "A new normalized query must start at the first result.")
        canvas.yview_moveto(1)
        self.root.update()
        unchanged_top = canvas.canvasy(0)
        controls.search.set(" ReShade ")
        self.root.update()
        self.assertEqual(canvas.canvasy(0), unchanged_top, "Equivalent normalized queries must preserve the viewport.")

    def test_real_keypress_review_requires_save_and_blocks_app_shortcuts(self):
        dialog = self.open_recorder()
        fallthrough = Mock()
        dialog.bind("<Control-s>", fallthrough)
        self.app._persist_preferences = Mock(return_value=True)
        dialog.event_generate("<KeyPress>", keysym="F24", state=0x8)  # Windows NumLock is not Alt.
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("F24"))
        self.app._persist_preferences.assert_not_called()
        dialog.event_generate("<KeyPress>", keysym="s", state=0x4)
        self.root.update()
        fallthrough.assert_not_called()
        self.button("Save shortcut").invoke()
        result = self.app._persist_preferences.call_args.args[0]
        self.assertEqual(result.action_bindings["panel"], EditorBinding("F24"))
        self.assertIsNone(self.app.binding_dialog)

    def test_f7_conflict_cancel_and_modifier_release_do_not_mutate_preferences(self):
        dialog = self.open_recorder("move_down")
        self.app._persist_preferences = Mock()
        for symbol in ("F7", "F8"):
            dialog.event_generate("<KeyPress>", keysym=symbol)
            self.root.update()
            self.assertFalse(self.app._binding_capture.ready)
            self.assertTrue(self.app._binding_capture.error)
        dialog.event_generate("<KeyPress>", keysym="Control_L")
        dialog.event_generate("<KeyPress>", keysym="semicolon", state=0x4)
        dialog.event_generate("<KeyRelease>", keysym="Control_L", state=0x4)
        self.root.update()
        self.assertFalse(self.app._binding_capture.ready)
        dialog.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.app.binding_dialog)
        self.app._persist_preferences.assert_not_called()

    def test_stale_review_cannot_save_then_record_again_uses_current_bindings(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.app.app_settings = self.app.app_settings.with_reshade_binding(None)
        self.button("Save shortcut").invoke()
        self.app._persist_preferences.assert_not_called()
        self.assertEqual(str(self.button("Save shortcut").cget("state")), "disabled")
        self.button("Record again").invoke()
        dialog.event_generate("<KeyPress>", keysym="F11")
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.button("Save shortcut").invoke()
        settings = self.app._persist_preferences.call_args.args[0]
        self.assertIsNone(settings.reshade_binding)
        self.assertEqual(settings.action_bindings["panel"].key, "F11")

    def test_explicit_modified_escape_and_middle_mouse_share_record_review_save(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        escape = self.button("Use Escape as the shortcut")
        escape.event_generate("<ButtonPress-1>", state=0x4, x=10, y=10)
        escape.event_generate("<ButtonRelease-1>", state=0x4, x=10, y=10)
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape", ctrl=True))
        self.button("Save shortcut").invoke()
        self.assertEqual(self.app._persist_preferences.call_args.args[0].action_bindings["panel"], EditorBinding("Escape", ctrl=True))
        dialog = self.open_recorder()
        dialog.event_generate("<ButtonPress-2>", state=0x20000 | 0x1)
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("MiddleMouse", alt=True, shift=True))
        self.button("Cancel").invoke()

    def test_dialog_key_handler_refuses_win_modifier_held_before_opening(self):
        dialog = self.open_recorder()
        with patch("dolly.ui.binding_capture.SideMouseCapture.keyboard_blocked", return_value=True):
            dialog.event_generate("<KeyPress>", keysym="F24")
            self.root.update()
        self.assertFalse(self.app._binding_capture.ready)
        self.assertIn("Windows", self.app._binding_capture.error)
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.assertTrue(self.app._binding_capture.ready)

    def test_review_escape_keyboard_choice_drops_modifiers_from_previous_attempt(self):
        dialog = self.open_recorder()
        self.app._persist_preferences = Mock(return_value=True)
        escape = self.button("Use Escape as the shortcut")
        escape.event_generate("<ButtonPress-1>", state=0x4, x=10, y=10)
        escape.event_generate("<ButtonRelease-1>", state=0x4, x=10, y=10)
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape", ctrl=True))
        self.button("Record again").invoke()
        dialog.event_generate("<KeyPress>", keysym="F24")
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("F24"))
        for _ in range(8):
            focused = dialog.focus_get()
            if focused is escape:
                break
            (focused or dialog).event_generate("<KeyPress>", keysym="Tab")
            self.root.update()
        self.assertIs(dialog.focus_get(), escape, "The Escape option must be reachable from review by Tab.")
        escape.event_generate("<KeyPress>", keysym="space")
        escape.event_generate("<KeyRelease>", keysym="space")
        self.root.update()
        self.assertEqual(self.app._binding_capture.candidate, EditorBinding("Escape"))
        self.app._persist_preferences.assert_not_called()
        self.button("Save shortcut").invoke()
        self.assertEqual(self.app._persist_preferences.call_args.args[0].action_bindings["panel"], EditorBinding("Escape"))

    def test_busy_and_playback_block_recording_before_modal_or_listener(self):
        for attribute in ("busy", "playing"):
            setattr(self.app, attribute, True)
            self.app._record_binding("capture")
            self.assertIsNone(self.app.binding_dialog)
            setattr(self.app, attribute, False)

    def test_capture_save_failures_keep_settings_and_invalidate_queued_old_presses(self):
        from dolly.ui.binding_capture import binding_settings
        original = self.app.app_settings
        proposed = binding_settings(original, "capture", EditorBinding("Mouse5"))
        for phase in ("stop", "save"):
            with self.subTest(phase=phase):
                helper = Mock(running=True, is_game_focused=Mock(return_value=True))
                helper.stop.side_effect = OSError("listener failed") if phase == "stop" else lambda: setattr(helper, "running", False)
                self.app.capture_hotkey = helper
                self.app.hotkey_enabled.set(True)
                old_generation = self.app.capture_generation
                self.app.capture_here = Mock()
                self.submit.reset_mock()
                with patch("dolly.gui.save_settings", side_effect=OSError("disk unavailable")) as save:
                    self.app._persist_preferences(proposed, "Fixture saved")
                    self.assertFalse(self.app.hotkey_enabled.get())
                    self.assertEqual(self.app.capture_generation, old_generation + 1)
                    worker = self.submit.call_args.args[1]
                    with self.assertRaises(OSError):
                        worker()
                    if phase == "stop":
                        save.assert_not_called()
                    else:
                        save.assert_called_once()
                self.assertIs(self.app.app_settings, original)
                self.assertEqual(self.app.capture_binding, original.capture_binding)
                self.app._handle_capture_hotkey(old_generation)
                self.app.capture_here.assert_not_called()

    def test_clearing_capture_disables_listener_and_keeps_unbound_action(self):
        from dolly.ui.binding_capture import binding_settings
        helper = Mock(running=True)
        self.app.capture_hotkey = helper
        self.app.hotkey_enabled.set(True)
        proposed = binding_settings(self.app.app_settings, "capture", None)
        self.submit.reset_mock()
        self.app._persist_preferences(proposed, "Fixture unbound")
        worker, complete = self.submit.call_args.args[1:3]
        with patch("dolly.gui.save_settings") as save, patch("dolly.gui.editor_session.configure"):
            worker()
            complete(None)
        helper.stop.assert_called_once()
        save.assert_called_once()
        self.assertIsNone(self.app.capture_hotkey)
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertIsNone(self.app.app_settings.action_bindings["capture"])

    def test_unrelated_bind_change_keeps_running_capture_listener(self):
        from dolly.ui.binding_capture import binding_settings
        helper = Mock(running=True)
        self.app.capture_hotkey = helper
        self.app.hotkey_enabled.set(True)
        generation = self.app.capture_generation
        proposed = binding_settings(self.app.app_settings, "panel", EditorBinding("F24"))
        self.submit.reset_mock()
        self.app._persist_preferences(proposed, "Fixture changed")
        worker, complete = self.submit.call_args.args[1:3]
        with patch("dolly.gui.save_settings"), patch("dolly.gui.editor_session.configure"):
            worker()
            complete(None)
        helper.stop.assert_not_called()
        self.assertIs(self.app.capture_hotkey, helper)
        self.assertTrue(self.app.hotkey_enabled.get())
        self.assertEqual(self.app.capture_generation, generation)
        self.assertEqual(self.app.app_settings.action_bindings["panel"].key, "F24")

    def test_reset_conflict_is_visible_in_status_without_popup_or_displacement(self):
        bindings = dict(self.app.app_settings.action_bindings)
        bindings["move_fast"] = EditorBinding("F24")
        bindings["move_up"] = EditorBinding("Shift")
        self.app.app_settings = self.app.app_settings.with_action_bindings(bindings)
        before = self.app.app_settings
        self.app._refresh_bindings()
        self.app._persist_preferences = Mock()
        self.app._error = Mock()
        self.app.controls_view.rows["move_fast"].reset_button.invoke()
        self.assertIs(self.app.app_settings, before)
        self.app._persist_preferences.assert_not_called()
        self.app._error.assert_not_called()
        self.assertEqual(self.app.status_text.get(), self.app.binding_feedback.get())
        self.assertIn("Move up", self.app.status_text.get())
        self.assertIn("Move faster", self.app.status_text.get())
        self.assertEqual(self.app.app_settings.action_bindings["move_up"], EditorBinding("Shift"))
        self.assertEqual(self.app.app_settings.action_bindings["move_fast"], EditorBinding("F24"))


class ControlsHighDpiFilterTests(ControlsAppFixture):
    fixture_scale = 1.5
    fixture_full_editor = True

    def test_720p_150_percent_filter_after_all_rows_and_exact_sensitivity(self):
        self.assertAlmostEqual(self.app.ui_scale, 1.5, delta=.02)
        ControlsRecorderIntegrationTests._check_filter_after_reaching_all_controls(self)


if __name__ == "__main__":
    unittest.main()
