"""Page contracts using Tk variables and named callbacks, without an app host."""
from dataclasses import fields
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from dolly.ui.library_page import LibraryActions, LibraryPage, LibraryState
from dolly.ui.settings_page import (
    SENSITIVITY_MAX, SENSITIVITY_MIN, SENSITIVITY_TICKS, CameraFeel, SettingsActions, SettingsPage,
    SettingsState, position_to_sensitivity, sensitivity_preset_name, sensitivity_to_position,
)
from dolly.ui.export_page import ExportActions, ExportPage, ExportState


class PageBoundaryTests(unittest.TestCase):
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
        if hasattr(self, 'errors'):
            self.assertEqual(self.errors, [])

    def variables(self, cls, **overrides):
        # Every dependency is declared by the page dataclass, not an app proxy.
        return cls(**{f.name: overrides[f.name] if f.name in overrides else
                      tk.StringVar(self.root, '') for f in fields(cls)})

    def callbacks(self, cls, **overrides):
        def record(name):
            return lambda *args, **kwargs: self.calls.append((name, args, kwargs))
        return cls(**{f.name: overrides.get(f.name, record(f.name)) for f in fields(cls)})

    def button(self, text):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        # ttk buttons first (the legacy CameraFeel), then the canvas-drawn kit buttons (N21).
        from dolly.ui import kit_controls
        found = [w for w in walk(self.root) if isinstance(w, ttk.Button) and w.cget('text') == text]
        found += [b for b in kit_controls.buttons(self.root) if b.cget('text') == text]
        return found[0]

    def test_library_owns_widgets_and_routes_named_operations_without_controller(self):
        def tree(parent, columns, labels, widths, height):
            frame = ttk.Frame(parent)
            widget = ttk.Treeview(frame, columns=columns, height=height)
            widget.pack()
            return frame, widget
        state = self.variables(LibraryState, hotkey_enabled=tk.BooleanVar(self.root, True))
        view = LibraryPage(self.root, state, self.callbacks(LibraryActions, tree=tree))
        self.assertEqual(view.selected_replay_text.get(), 'Choose a replay')
        state.demo_path.set('replays/example.dem')
        self.assertEqual(view.selected_replay_text.get(), 'example.dem')
        state.replay_search.set('example')
        view.play_replay_button.invoke()
        # Stop / restore and Cancel startup are footer stickers; shot files are in the File menu.
        self.assertEqual(str(view.cancel_startup_button.cget('state')), 'disabled')
        view.cancel_startup_button.configure(state='normal')
        view.cancel_startup_button.invoke()
        self.assertEqual([c[0] for c in self.calls], ['filter_replays', 'start_editing_session', 'cancel_startup'])
        self.assertEqual(tuple(view.home_camera_driver_combo.cget('values')), ('Native (experimental)', 'Console (legacy)'))

    def test_settings_owns_preferences_and_routes_to_dedicated_controls(self):
        state = self.variables(SettingsState, auto_updates_initial=True,
                               allow_mod_overrides_initial=False,
                               discord_presence_initial=True,
                               show_log=tk.BooleanVar(self.root, False))
        with patch('dolly.graphics_profiles.load_library', return_value={'format': 1, 'selected': '', 'profiles': []}):
            view = SettingsPage(self.root, state, self.callbacks(SettingsActions), root=self.root,
                                on_error=lambda *args: self.fail(str(args)))
        self.assertEqual(self.calls, [])
        self.assertTrue(view.auto_updates.get())
        self.assertTrue(view.discord_presence.get())
        self.assertFalse(hasattr(view, 'controls_disclosure'))
        self.button('Save paths').invoke()
        self.button('Menu shortcut...').invoke()
        self.button('Browse FX library...').invoke()
        self.assertEqual([c[0] for c in self.calls],
                         ['save_layout_paths', 'show_reshade_keybinds', 'browse_reshade_library'])
        view.discord_presence_checkbox.invoke()
        self.assertFalse(view.discord_presence.get())
        self.assertEqual(self.calls[-1][0], 'save_discord_preference')
        self.assertEqual(str(view.reshade_path_entry.cget('textvariable')), str(state.reshade_runtime_path))
        self.assertEqual(str(view.reshade_disable_button.cget('state')), 'disabled')
        self.assertFalse(hasattr(view.graphics_profiles, 'app'))

    def test_export_uses_supplied_choices_and_never_owns_recording_backend(self):
        booleans = {name: tk.BooleanVar(self.root, False) for name in
                    ('video_depth', 'video_depth_exr', 'video_fixed_step', 'video_game_audio',
                     'video_layer_effects', 'video_layer_players', 'video_layer_world', 'video_reconstructed_audio')}
        state = self.variables(ExportState, codec_choices=('Fixture encoder',),
                               format_choices=('Fixture format',), bitrate_choices=('Fixture rate',), **booleans)
        view = ExportPage(self.root, state, self.callbacks(ExportActions))
        self.assertEqual(tuple(view.video_codec_combo.cget('values')), ('Fixture encoder',))
        self.assertEqual(tuple(view.video_format_combo.cget('values')), ('Fixture format',))
        self.assertEqual(tuple(view.video_bitrate_combo.cget('values')), ('Fixture rate',))
        view.video_source_combo.current(1)
        view.video_source_combo.event_generate('<<ComboboxSelected>>')
        view.video_depth_checkbox.invoke()
        view.video_start_button.invoke()
        self.assertEqual([c[0] for c in self.calls], ['video_source_changed', 'depth_toggled', 'start_video_recording'])
        self.assertEqual(state.video_source.get(), 'Player POV')
        self.assertTrue(state.video_depth.get())
        self.assertEqual(str(view.video_stop_button.cget('state')), 'disabled')
        self.assertEqual(str(view.video_cancel_button.cget('state')), 'disabled')
        self.assertEqual(len(view.video_layer_checkboxes), 3)
        self.assertFalse(hasattr(view, 'video_export'))

    def test_export_gates_the_exr_control_on_the_depth_master(self):
        def build(depth_on):
            booleans = {name: tk.BooleanVar(self.root, False) for name in
                        ('video_depth', 'video_depth_exr', 'video_fixed_step', 'video_game_audio',
                         'video_layer_effects', 'video_layer_players', 'video_layer_world',
                         'video_reconstructed_audio')}
            booleans['video_depth'].set(depth_on)
            state = self.variables(ExportState, codec_choices=('Fixture encoder',),
                                   format_choices=('Fixture format',),
                                   bitrate_choices=('Fixture rate',), **booleans)
            return ExportPage(self.root, state, self.callbacks(ExportActions))

        view = build(False)
        self.assertTrue(view.video_depth_exr_checkbox.instate(['disabled']))
        view.export_page.destroy()
        view = build(True)
        self.assertFalse(view.video_depth_exr_checkbox.instate(['disabled']))

    def test_page_modules_do_not_import_application_controller_or_export_backend(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-c',
            'import sys; from dolly.ui import library_page, settings_page, export_page, controls_page, camera_page, '
            'look_page, playback_card, camera_feel, kit_page, kit_card, kit_controls, kit_fields, kit_styles, fade, tween; '
            'forbidden={"dolly.gui", "dolly.gui_layout", "dolly.controller", "dolly.video_export", "dolly.launcher"}; '
            'assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules))'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_camera_feel_presets_and_state_sync(self):
        sensitivity = tk.StringVar(self.root, '0.12')
        view = CameraFeel(self.root, sensitivity, lambda: self.calls.append(('save_mouse_sensitivity', (), {})))
        self.button('Very slow').invoke()
        self.assertEqual([c[0] for c in self.calls], ['save_mouse_sensitivity'])
        self.assertAlmostEqual(float(sensitivity.get()), 0.02, places=3)
        sensitivity.set('0.25')
        self.assertAlmostEqual(view.sensitivity_position.get(),
                               sensitivity_to_position(0.25), places=3)
        self.assertIn('Fast', view.sensitivity_text.get())


class DepthGateTests(unittest.TestCase):
    """The desktop EXR gate follows the depth variable so both UIs agree."""

    def test_sync_depth_controls_follows_the_variable(self):
        from types import SimpleNamespace
        from dolly.gui import DollyApp
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        stub = SimpleNamespace(video_depth=tk.BooleanVar(root, False),
                               video_depth_exr=tk.BooleanVar(root, False))
        stub.video_depth_exr_checkbox = ttk.Checkbutton(
            root, text="EXR sequence (float)", variable=stub.video_depth_exr)
        DollyApp._sync_depth_controls(stub)
        self.assertTrue(stub.video_depth_exr_checkbox.instate(["disabled"]))
        stub.video_depth.set(True)
        DollyApp._sync_depth_controls(stub)
        self.assertFalse(stub.video_depth_exr_checkbox.instate(["disabled"]))
        stub.video_depth_exr.set(True)
        stub.video_depth.set(False)
        DollyApp._sync_depth_controls(stub)
        self.assertFalse(stub.video_depth_exr.get())
        self.assertTrue(stub.video_depth_exr_checkbox.instate(["disabled"]))


class SensitivityMappingTests(unittest.TestCase):
    def test_mapping_round_trips_across_the_supported_range(self):
        for value in (SENSITIVITY_MIN, 0.02, 0.12, 0.5, SENSITIVITY_MAX):
            self.assertAlmostEqual(position_to_sensitivity(sensitivity_to_position(value)), value, places=9)
        self.assertLess(sensitivity_to_position(0.02), sensitivity_to_position(0.12))
        self.assertEqual(sensitivity_to_position(SENSITIVITY_MIN), 0.0)
        self.assertEqual(sensitivity_to_position(SENSITIVITY_MAX), float(SENSITIVITY_TICKS))

    def test_preset_names_match_the_nearest_choice(self):
        self.assertEqual(sensitivity_preset_name(0.12), 'Normal')
        self.assertEqual(sensitivity_preset_name(0.021), 'Very slow')
        self.assertEqual(sensitivity_preset_name(1.9), 'Custom')
        self.assertEqual(sensitivity_preset_name('not a number'), 'Custom')
