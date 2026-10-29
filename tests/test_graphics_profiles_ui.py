"""Graphics panel behavior with isolated files, dialogs and process inventory."""
from dolly import game_processes
import gc
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from dolly import graphics_profiles as profiles, launcher
from dolly.graphics_profiles_ui import GraphicsProfiles

VIDEO = b'"video.cfg" { "Version" "20" "setting.r_effects_bloom" "1" "setting.defaultres" "2560" }'


class GraphicsPanelTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.errors = []
        self.addCleanup(self.close_panel)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.video = self.folder / 'video.txt'
        self.video.write_bytes(VIDEO)
        self.library = self.folder / 'profiles.json'
        self.patch(profiles, 'library_path', return_value=self.library)
        self.panel = GraphicsProfiles(ttk.Frame(self.root), root=self.root, on_error=lambda *args: self.errors.append(args))

    def close_panel(self):
        # Registered first so patched methods release their callbacks before Tk.
        self.root.destroy()
        self.panel = None
        self.root = None
        self.errors.clear()  # Exceptions can retain panel frames through tracebacks.
        gc.collect()  # Finalize the unreachable Tk graph on its creating thread.

    def patch(self, obj, name, **kwargs):
        mock = patch.object(obj, name, **kwargs)
        self.addCleanup(mock.stop)
        return mock.start()

    def save_profile(self):
        profile = profiles.profile_from_bytes('Recording', VIDEO)
        profiles.save_library({'format': 1, 'selected': profile['id'], 'profiles': [profile]})
        self.panel.refresh()
        return profile

    def test_preview_precedes_save_and_confirmation_rereads_library(self):
        from dolly.graphics_profiles_ui import simpledialog
        self.patch(simpledialog, 'askstring', return_value=' Recording ')
        preview = self.patch(self.panel, 'show_preview')
        self.panel.add(self.video)
        self.assertFalse(self.library.exists())
        title, text, commit = preview.call_args.args
        self.assertEqual(title, 'Save graphics profile')
        self.assertIn('Effects bloom: 1', text)
        self.assertIn('setting.defaultres', text)
        self.assertIn('version', text)
        external = profiles.profile_from_bytes('Other', VIDEO)
        profiles.save_library({'format': 1, 'selected': '', 'profiles': [external]})
        commit()
        stored = profiles.load_library()
        self.assertEqual([p['name'] for p in stored['profiles']], ['Other', 'Recording'])
        self.assertEqual(stored['selected'], stored['profiles'][1]['id'])
        self.assertEqual(self.panel.selection.get(), 'Recording')
        self.assertEqual(self.video.read_bytes(), VIDEO)

    def test_cancelled_name_creates_no_profile_or_preview(self):
        from dolly.graphics_profiles_ui import simpledialog
        self.patch(simpledialog, 'askstring', return_value=None)
        preview = self.patch(self.panel, 'show_preview')
        self.panel.add(self.video)
        preview.assert_not_called()
        self.assertFalse(self.library.exists())

    def test_unreadable_source_fails_before_name_prompt(self):
        from dolly.graphics_profiles_ui import simpledialog
        prompt = self.patch(simpledialog, 'askstring')
        self.panel.run(lambda: self.panel.add(self.folder / 'missing.txt'))
        prompt.assert_not_called()
        self.assertEqual(self.errors[0][0], 'Graphics profiles')
        self.assertIsInstance(self.errors[0][1], (OSError, profiles.LaunchError))
        self.assertIn('missing.txt', str(self.errors[0][1]))
        self.assertEqual(self.panel.status.get(), str(self.errors[0][1]))

    def test_active_game_refuses_capture_before_resolving_or_reading_video(self):
        self.patch(game_processes, 'running_processes', return_value=['fixture'])
        guard = self.patch(game_processes, '_game_is_running', return_value=True)
        current = self.patch(profiles, 'current_video')
        self.panel.run(self.panel.capture)
        guard.assert_called_once_with(['fixture'])
        current.assert_not_called()
        self.assertEqual(str(self.errors[0][1]), 'Close Deadlock before saving its graphics profile, so the file contains its final saved settings.')
        self.assertFalse(self.library.exists())

    def test_stopped_game_capture_resolves_current_source_then_adds_it(self):
        self.patch(game_processes, 'running_processes', return_value=[])
        self.patch(game_processes, '_game_is_running', return_value=False)
        self.patch(profiles, 'current_video', return_value=self.video)
        add = self.patch(self.panel, 'add')
        self.panel.capture()
        add.assert_called_once_with(self.video)

    def test_removed_choice_is_rejected_after_disk_reread(self):
        self.save_profile()
        profiles.save_library({'format': 1, 'selected': '', 'profiles': []})
        self.panel.run(self.panel.select)
        self.assertEqual(str(self.errors[0][1]), 'Graphics profile changed outside this window. Reopen Settings and choose a profile.')
        self.assertEqual(profiles.load_library()['profiles'], [])

    def test_select_current_preserves_profiles_and_clears_selected_id(self):
        profile = self.save_profile()
        self.panel.selection.set(profiles.CURRENT)
        self.panel.select()
        self.assertEqual(profiles.load_library(), {'format': 1, 'selected': '', 'profiles': [profile]})
        self.assertEqual(self.panel.selection.get(), profiles.CURRENT)

    def test_preview_does_not_modify_source_or_library(self):
        self.save_profile()
        self.patch(profiles, 'current_video', return_value=self.video)
        preview = self.patch(self.panel, 'show_preview')
        before = self.library.read_bytes()
        self.panel.preview()
        self.assertEqual(preview.call_args.args[0], 'Next-launch graphics preview')
        self.assertIn('No game files are changed', preview.call_args.args[1])
        self.assertEqual(len(preview.call_args.args), 2)
        self.assertEqual(self.library.read_bytes(), before)
        self.assertEqual(self.video.read_bytes(), VIDEO)

    def test_failed_save_keeps_disk_and_reports_error_without_refresh(self):
        from dolly.graphics_profiles_ui import simpledialog
        self.save_profile()
        before = self.library.read_bytes()
        self.patch(simpledialog, 'askstring', return_value='Renamed')
        self.patch(profiles, 'save_library', side_effect=OSError('fixture write refusal'))
        refresh = self.patch(self.panel, 'refresh')
        self.panel.run(self.panel.rename)
        refresh.assert_not_called()
        self.assertEqual(str(self.errors[0][1]), 'fixture write refusal')
        self.assertEqual(self.library.read_bytes(), before)

    def test_delete_confirmation_and_cancellation_keep_original_selection_policy(self):
        from dolly.graphics_profiles_ui import messagebox
        self.save_profile()
        before = self.library.read_bytes()
        confirm = self.patch(messagebox, 'askyesno', return_value=False)
        self.panel.delete()
        self.assertEqual(self.library.read_bytes(), before)
        confirm.return_value = True
        self.panel.delete()
        self.assertEqual(profiles.load_library(), {'format': 1, 'selected': '', 'profiles': []})
        self.assertEqual(self.panel.selection.get(), profiles.CURRENT)

    def test_the_frame_layout_keeps_its_help_text(self):
        from dolly.graphics_profiles_ui import PROFILES_INFO

        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        texts = [w.cget('text') for w in walk(self.root) if isinstance(w, ttk.Label)]
        self.assertIn(PROFILES_INFO, texts)
        self.assertTrue(PROFILES_INFO.startswith('Profiles apply when Dolly starts a replay.'))
        self.assertTrue(PROFILES_INFO.endswith('use Display launch options for window size.'))
        buttons = [w.cget('text') for w in walk(self.root) if isinstance(w, ttk.Button)]
        self.assertEqual(buttons, ['Save current graphics...', 'Import video.txt...', 'Preview changes...',
                                   'Rename...', 'Delete...'])


class GraphicsCardTests(unittest.TestCase):
    """``GraphicsProfiles(card=)``: the Settings card's rows (Figma [design reference]) on a kit card."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.errors = []
        self.failures = []
        self.root.report_callback_exception = lambda *args: self.failures.append(args)

    def tearDown(self):
        if hasattr(self, 'failures'):
            self.assertEqual(self.failures, [], 'Tk callback raised an exception')

    def build(self, library=None, error=None):
        from dolly import gui_theme
        from dolly.graphics_profiles_ui import PROFILES_INFO
        from dolly.ui.kit_page import KitPage
        from dolly.ui.scale import px, ui_scale
        gui_theme.initialize(self.root)
        s = ui_scale(self.root)
        self.root.deiconify()
        self.root.geometry(f'{px(s, 700)}x{px(s, 400)}')
        page = KitPage(self.root)
        page.pack(fill='both', expand=True)
        card = page.add_card('Replay Graphics Profiles', info=PROFILES_INFO)
        loaded = {'side_effect': error} if error else {'return_value': library or {'format': 1, 'selected': '', 'profiles': []}}
        with patch.object(profiles, 'load_library', **loaded):
            panel = GraphicsProfiles(None, root=self.root, on_error=lambda *args: self.errors.append(args), card=card)
            self.root.update()
            page.layout_now()
        return page, card, panel

    def test_rows_dropdown_buttons_and_status_without_the_help_text(self):
        from dolly.graphics_profiles_ui import PROFILES_INFO
        from dolly.ui import kit_controls
        page, card, panel = self.build()
        s = page.scale

        def design(rect):
            x0, y0, _x1, _y1 = card.rect
            return tuple(round((value - origin) / s) for value, origin in zip(rect, (x0, y0, x0, y0)))
        self.assertEqual(str(panel.combo.cget('style')), 'Kit.TCombobox')
        self.assertEqual(str(panel.combo.cget('state')), 'readonly')
        self.assertIs(panel.combo.master, card.host)
        self.assertEqual(tuple(panel.combo.cget('values')), (profiles.CURRENT,))
        self.assertEqual(panel.selection.get(), profiles.CURRENT)
        combo = design(page.rect_of(panel.combo))
        for actual, expected in zip(combo, (156, 54, 592, 84)):
            self.assertLessEqual(abs(actual - expected), 1, combo)
        buttons = kit_controls.buttons(card.host)
        self.assertEqual([b.cget('text') for b in buttons],
                         ['Save current graphics...', 'Import video.txt...', 'Preview changes...', 'Rename...', 'Delete...'])
        tops = [design(page.rect_of(b))[1] for b in buttons]
        self.assertEqual(tops, [94, 94, 134, 134, 134])
        for first in (buttons[0], buttons[2]):
            self.assertLessEqual(abs(design(page.rect_of(first))[0] - 156), 1)
        self.assertLessEqual(abs(design(page.rect_of(panel.status_text))[1] - 179), 1)
        self.assertEqual(panel.status_text.cget('text'), '0 local profile(s). Selection applies to the next launch.')
        self.assertLessEqual(abs((card.rect[3] - card.rect[1]) / s - 216), 1)
        texts = [card.host.itemcget(i, 'text') for i in card.host.find_all() if card.host.type(i) == 'text']
        self.assertIn('Next replay session', texts)
        self.assertNotIn(PROFILES_INFO, texts)
        self.assertEqual(card.info.cget('text'), PROFILES_INFO)

    def test_buttons_look_their_action_up_when_pressed(self):
        from dolly.ui import kit_controls
        _page, card, panel = self.build()
        called = []
        for name in ('capture', 'import_file', 'preview', 'rename', 'delete', 'select'):
            setattr(panel, name, lambda name=name: called.append(name))
        for button in kit_controls.buttons(card.host):
            button.invoke()
        panel.combo.event_generate('<<ComboboxSelected>>')
        self.root.update_idletasks()   # the theme clears the dropdown's selection at idle
        self.assertEqual(called, ['capture', 'import_file', 'preview', 'rename', 'delete', 'select'])
        # A failing action reports through the status line and on_error, as before.
        panel.rename = lambda: panel.chosen()
        with patch.object(profiles, 'load_library', return_value={'format': 1, 'selected': '', 'profiles': []}):
            kit_controls.buttons(card.host)[3].invoke()
        self.assertEqual(panel.status.get(), 'Select a named graphics profile first.')
        self.assertEqual(panel.status_text.cget('text'), 'Select a named graphics profile first.')
        self.assertEqual(self.errors[0][0], 'Graphics profiles')

    def test_a_damaged_library_shows_its_error_after_building(self):
        _page, _card, panel = self.build(error=profiles.LaunchError('Damaged profile library'))
        self.assertEqual(panel.status.get(), 'Damaged profile library')
        self.root.update()
        self.assertEqual([(args[0], str(args[1])) for args in self.errors],
                         [('Graphics profiles', 'Damaged profile library')])
