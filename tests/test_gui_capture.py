"""Capture authoring and playback intent without a display or a running game.

Use the real DollyApp capture handlers; replace only Tk widgets and the game
boundary. Worker completion is explicit so failed captures and stale results
can be checked without racing threads.
"""
import copy
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dolly.gui import DollyApp, FIELDS
from dolly.editor_session import dispatch as dispatch_editor_action
from dolly.bindings import CaptureBinding, DEFAULT_BINDING
from dolly.settings import AppSettings
from dolly.path import STANDARD_ASPECT, Keyframe, Project, CvarTrack, TrackKey


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeController:
    def __init__(self):
        self.tick = None
        self.failure = None
        self.calls = []
        self.replay_time = 0.0
        self.standard_aspect = STANDARD_ASPECT

    def capture(self, time):
        self.calls.append(("capture", time))
        if self.failure:
            raise self.failure
        return Keyframe(time, len(self.calls) * 100.0, 20.0, 30.0, 4.0, 5.0, 6.0, 90.0)

    def capture_at_replay(self, start_tick, rate):
        self.calls.append(("replay", start_tick, rate))
        if self.failure:
            raise self.failure
        return Keyframe(self.replay_time, 100.0, 20.0, 30.0, 4.0, 5.0, 6.0, 90.0)

    def status(self):
        return {"tick": self.tick}


class CaptureHarness:
    def __init__(self):
        self.app = app = DollyApp.__new__(DollyApp)
        app.project = Project(name="Shot")
        app.busy = False
        app.playing = False
        app.closed = False
        app.start_tick = Var("0")
        app.tick_rate = Var("64")
        app.interpolation = Var("smooth")
        app.rotation = Var("shortest")
        app.standard_aspect = Var(str(STANDARD_ASPECT))
        app.lens_interpolation = Var("smooth")
        app.capture_mode = Var("Timed shot")
        app.segment_seconds = Var("3")
        app.frozen = Var(False)
        app.hide_hud = Var(True)
        app.speed = Var("1")
        app.rate = Var("60")
        app.smoothing = Var("Balanced")
        app.status_text = Var("")
        app.app_settings = AppSettings()
        app.capture_binding = DEFAULT_BINDING
        app.hotkey_enabled = Var(False)
        app.hotkey_label = Var("In-game capture · Ctrl+Alt+K")
        app.capture_generation = 0
        app.capture_hotkey = None
        app.events = queue.Queue()
        app.controller = FakeController()
        app.controller.game_pid = lambda: 123
        self.selected = None
        app.camera_tree = SimpleNamespace(selection=lambda: () if self.selected is None else (str(self.selected),))
        app.root = SimpleNamespace(grab_current=lambda: None)
        app.notebook = SimpleNamespace(select=lambda _tab: None)
        app.camera_tab = object()
        app._mark_dirty = Mock()
        app._refresh_keys = Mock()
        app._set_time = Mock()
        app._draw_path = Mock()
        self.errors = []
        self.pending = None
        app._error = lambda _label, error: self.errors.append(error)
        app._submit = self.submit

    def submit(self, label, function, callback=None):
        if self.app.busy:
            return False
        self.app.busy = True
        self.pending = (label, function, callback)
        return True

    def finish(self):
        self.assert_pending()
        label, function, callback = self.pending
        self.pending = None
        try:
            result = function()
        except Exception as exc:
            self.app.busy = False
            self.app._error(label, exc)
            return
        self.app.busy = False
        if callback:
            try:
                callback(result)
            except Exception as exc:
                self.app._error(label, exc)

    def assert_pending(self):
        if self.pending is None:
            raise AssertionError("No capture was submitted")

    def capture(self):
        self.app.capture_here()
        self.finish()


class GuiCaptureTests(unittest.TestCase):
    def setUp(self):
        self.harness = CaptureHarness()
        self.app = self.harness.app

    def test_timed_paused_views_append_zero_three_six_and_form_smooth_path(self):
        for _ in range(3):
            self.harness.capture()
        self.assertEqual([key.time for key in self.app.project.keyframes], [0.0, 3.0, 6.0])
        self.assertEqual([key.x for key in self.app.project.keyframes], [100.0, 200.0, 300.0])
        self.assertEqual(self.app.project.interpolation, "smooth")
        self.assertFalse(self.app.frozen.get())
        self.assertEqual(self.harness.errors, [])

    def test_missing_capture_tick_does_not_silently_select_frozen_playback(self):
        self.harness.capture()
        self.assertFalse(self.app.frozen.get())
        self.assertIn("start tick is unavailable", self.app.status_text.get())
        self.assertIn("select Frozen preview", self.app.status_text.get())

    def test_capture_preserves_explicit_frozen_preview_choice(self):
        self.app.frozen.set(True)
        for tick in (None, 256):
            with self.subTest(tick=tick):
                self.app.controller.tick = tick
                self.harness.capture()
                self.assertTrue(self.app.frozen.get())

    def test_first_timed_capture_records_known_replay_start_without_replay_timing(self):
        self.app.controller.tick = 256
        self.harness.capture()
        self.assertEqual(self.app.project.start_tick, 256)
        self.assertEqual(self.app.project.keyframes[0].time, 0.0)
        self.assertEqual(self.app.start_tick.get(), "256")

    def test_capture_passes_selected_standard_aspect_stably_to_controller(self):
        self.app.standard_aspect.set("4:3")
        self.app.capture_here()
        self.app.standard_aspect.set("21:9")
        self.harness.finish()
        self.assertEqual(self.app.controller.standard_aspect, 4 / 3)
        self.assertEqual(self.app.project.standard_aspect, 4 / 3)
        self.assertEqual(self.harness.errors, [])

    def test_failed_capture_does_not_clear_existing_path_or_tracks(self):
        self.harness.capture()
        self.app.project.tracks = [CvarTrack("r_depth_of_field", [TrackKey(0, 1)], "step")]
        before = copy.deepcopy(self.app.project)
        self.app.controller.failure = RuntimeError("Camera unavailable")
        with patch("dolly.gui.messagebox.askyesno") as ask:
            self.app._start_path_here()
            self.harness.finish()
            ask.assert_not_called()
        self.assertEqual(self.app.project, before)
        self.assertIn("Camera unavailable", str(self.harness.errors[-1]))

    def test_start_replacement_prompt_waits_for_success_and_cancel_keeps_existing(self):
        self.harness.capture()
        before = copy.deepcopy(self.app.project)
        with patch("dolly.gui.messagebox.askyesno", return_value=False) as ask:
            self.app._start_path_here()
            ask.assert_not_called()
            self.assertEqual(self.app.project, before)
            self.harness.finish()
            ask.assert_called_once()
        self.assertEqual(len(self.app.controller.calls), 2)
        self.assertEqual(self.app.project, before)

    def test_confirmed_new_path_preserves_camera_variable_tracks(self):
        self.harness.capture()
        self.harness.capture()
        self.app.project.tracks = [CvarTrack("r_depth_of_field", [TrackKey(0, 1)], "step")]
        tracks = copy.deepcopy(self.app.project.tracks)
        with patch("dolly.gui.messagebox.askyesno", return_value=True):
            self.app._start_path_here()
            self.harness.finish()
        self.assertEqual(len(self.app.project.keyframes), 1)
        self.assertEqual(self.app.project.keyframes[0].time, 0.0)
        self.assertEqual(self.app.project.tracks, tracks)

    def test_replace_selected_updates_pose_but_preserves_its_arrival_time(self):
        self.harness.capture()
        self.harness.capture()
        self.harness.selected = 0
        self.app._replace_camera_here()
        self.harness.finish()
        self.assertEqual([key.time for key in self.app.project.keyframes], [0.0, 3.0])
        self.assertEqual([key.x for key in self.app.project.keyframes], [300.0, 200.0])

    def test_duplicate_replay_capture_does_not_overwrite_existing_view(self):
        self.harness.capture()
        before = copy.deepcopy(self.app.project)
        self.app.capture_mode.set("Replay timing")
        self.app.controller.replay_time = 0.0
        self.app.capture_here()
        self.harness.finish()
        self.assertEqual(self.app.project, before)
        self.assertIn("already has a view", str(self.harness.errors[-1]))

    def test_replay_timing_uses_engine_spacing_instead_of_segment_field(self):
        self.app.capture_mode.set("Replay timing")
        self.app.controller.tick = 640
        self.harness.capture()
        self.app.segment_seconds.set("not used")
        self.app.controller.replay_time = 1.25
        self.harness.capture()
        self.assertEqual([key.time for key in self.app.project.keyframes], [0, 1.25])
        self.assertEqual(self.app.controller.calls[-1], ("replay", 640, 64.0))

    def test_nonpositive_interval_cannot_create_duplicate_timed_view(self):
        self.harness.capture()
        self.app.segment_seconds.set("0")
        self.app.capture_here()
        self.assertIsNone(self.harness.pending)
        self.assertEqual(len(self.app.project.keyframes), 1)
        self.assertIn("greater than zero", str(self.harness.errors[-1]))

    def test_result_cannot_replace_project_changed_during_capture(self):
        self.app.capture_here()
        replacement = Project(name="Different shot")
        self.app.project = replacement
        self.harness.finish()
        self.assertIs(self.app.project, replacement)
        self.assertEqual(self.app.project.keyframes, [])
        self.assertIn("changed during capture", str(self.harness.errors[-1]))

    def test_busy_or_path_playback_blocks_button_capture(self):
        for flag in ("busy", "playing"):
            with self.subTest(flag=flag):
                setattr(self.app, flag, True)
                self.app.capture_here()
                self.assertIsNone(self.harness.pending)
                setattr(self.app, flag, False)

    def test_play_shot_uses_start_from_middle_end_or_invalid_preview_cursor(self):
        self.harness.capture()
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        self.app._current_time = Mock(side_effect=AssertionError("Play must ignore the preview cursor"))
        for preview_time in (1.5, self.app.project.duration, "invalid"):
            with self.subTest(preview_time=preview_time):
                self.app.time_text = Var(preview_time)
                self.app._play()
                self.harness.finish()
                self.assertEqual(self.app.controller.play.call_args.kwargs["time"], 0.0)
                self.assertFalse(self.app.controller.play.call_args.kwargs["frozen"])
                self.assertTrue(self.app.controller.play.call_args.kwargs["hide_hud"])
                self.assertEqual(self.app.controller.play.call_args.kwargs["smoothing"], "balanced")
                self.app._set_time.assert_called_with(0.0)
        self.app._current_time.assert_not_called()
        self.assertEqual(self.harness.errors, [])

    def test_play_shot_passes_explicit_preview_hud_and_speed_choices(self):
        self.harness.capture()
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        self.app.frozen.set(True)
        self.app.hide_hud.set(False)
        self.app.speed.set("0.5")
        self.app.rate.set("30")
        self.app.smoothing.set("Strong")
        self.app._play()
        # Worker inputs are read on the UI thread and kept stable even if a
        # checkbox changes before the worker starts.
        self.app.frozen.set(False)
        self.app.hide_hud.set(True)
        self.app.smoothing.set("Off")
        self.harness.finish()
        self.app.controller.play.assert_called_once_with(
            self.app.project, time=0.0, speed=0.5, rate=30, frozen=True, hide_hud=False,
            smoothing="strong")

    def test_play_shot_accepts_each_smoothing_choice_without_editing_project(self):
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        original = copy.deepcopy(self.app.project)
        for choice in ("Off", "Light", "Balanced", "Strong"):
            with self.subTest(choice=choice):
                self.app.smoothing.set(choice)
                self.app._play()
                self.harness.finish()
                self.assertEqual(self.app.controller.play.call_args.kwargs["smoothing"], choice.lower())
                self.assertEqual(self.app.project, original)
        self.assertEqual(self.harness.errors, [])

    def test_in_game_playback_choices_are_used_by_the_next_desktop_play_shot(self):
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        self.app.controller._native_bridge = lambda: None
        for action, value in (("set_playback_speed", .1), ("set_playback_rate", 120)):
            self.assertTrue(dispatch_editor_action(self.app, {"action": action, "value": value}, Mock()))
        self.app._play()
        self.harness.finish()
        settings = self.app.controller.play.call_args.kwargs
        self.assertEqual((settings["speed"], settings["rate"]), (.1, 120))
        self.assertEqual(self.harness.errors, [])

    def test_invalid_smoothing_keeps_paused_controls_and_does_not_queue_playback(self):
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        self.app._close_paused_camera = Mock()
        self.app._set_time.reset_mock()
        self.app.smoothing.set("Unsupported")
        self.app._play()
        self.assertIsNone(self.harness.pending)
        self.app.controller.play.assert_not_called()
        self.app._close_paused_camera.assert_not_called()
        self.app._set_time.assert_not_called()
        self.assertEqual(len(self.harness.errors), 1)

    def test_launch_captures_native_driver_choice_before_worker_runs(self):
        self.app.game_path = Var("B:/Deadlock/game/bin/win64/citadel.exe")
        self.app.demo_path = Var("B:/replays/example.dem")
        self.app.protocol = Var("Netconsole")
        self.app.camera_driver = Var("Native (experimental)")
        self.app.controller.launch = Mock(return_value=None)
        self.app._session_result = Mock()
        self.app._launch()
        self.app.camera_driver.set("Console (legacy)")
        self.app.protocol.set("VConsole")
        self.harness.finish()
        self.app.controller.launch.assert_called_once_with(
            "B:/Deadlock/game/bin/win64/citadel.exe", "B:/replays/example.dem",
            protocol="netcon", native=True)

    def test_launch_allows_console_fallback_before_startup(self):
        self.app.game_path = Var("game")
        self.app.demo_path = Var("example.dem")
        self.app.protocol = Var("VConsole")
        self.app.camera_driver = Var("Console (legacy)")
        self.app.controller.launch = Mock(return_value=None)
        self.app._session_result = Mock()
        self.app._launch()
        self.harness.finish()
        self.app.controller.launch.assert_called_once_with("game", "example.dem", protocol="vconsole", native=False)

    def test_edited_aspect_reaches_alternating_previews_and_keeps_legacy_fov(self):
        self.app.project = Project(name="Edited lens", keyframes=[
            Keyframe(0.0, 240.1, 3816.2, 421.3, -10.5, 317.4, 0.0, 75.0),
            Keyframe(3.0, 735.6, 4675.9, 574.8, 1.0, 27.5, 0.0, 40.0),
        ])
        original = copy.deepcopy(self.app.project)
        self.app.key_vars = {field: Var("") for field in FIELDS}
        self.app.shot_time = Var(0.0)
        self.app.time_text = Var("0")
        self.app.slider = SimpleNamespace(configure=Mock())
        self.app._set_time = DollyApp._set_time.__get__(self.app)
        self.app.controller.apply = Mock()

        self.harness.selected = 1
        self.app._select_key()
        self.app.key_vars["aspect_ratio"].set("1.2")
        self.app._update_key()
        self.assertEqual([key.aspect_ratio for key in self.app.project.keyframes], [STANDARD_ASPECT, 1.2])
        self.assertEqual([key.fov for key in self.app.project.keyframes], [75.0, 40.0])
        self.app._refresh_keys.assert_called_with(3.0)

        for index in (0, 1, 0, 1):
            with self.subTest(selected=index):
                self.harness.selected = index
                self.app._select_key()
                self.app._apply_selected()
                self.harness.finish()
                snapshot, time = self.app.controller.apply.call_args.args
                self.assertIsNot(snapshot, self.app.project)
                self.assertEqual(time, self.app.project.keyframes[index].time)
                frame = snapshot.evaluate(time)
                self.assertEqual(frame["aspect_ratio"], (STANDARD_ASPECT, 1.2)[index])
                for field in ("x", "y", "z", "pitch"):
                    self.assertEqual(frame[field], getattr(original.keyframes[index], field))

        self.assertEqual(self.harness.errors, [])
        self.assertEqual([key.fov for key in self.app.project.keyframes], [75.0, 40.0])

    def test_invalid_aspect_edit_leaves_saved_key_and_other_channels_untouched(self):
        self.app.project = Project(keyframes=[
            Keyframe(0, 12, 34, 56, 7, 8, 9, 75),
            Keyframe(3, 120, 340, 560, 70, 80, 90, 40),
        ])
        original = copy.deepcopy(self.app.project)
        self.app.key_vars = {field: Var("") for field in FIELDS}
        self.harness.selected = 1
        self.app._select_key()
        for value in ("0", "nan", "4.1", "oops"):
            self.app.key_vars["aspect_ratio"].set(value)
            self.app._update_key()
            self.assertEqual(self.app.project, original)
        self.assertEqual(len(self.harness.errors), 4)

    def test_lens_mode_and_standard_aspect_persist_independently_in_snapshot(self):
        self.harness.capture()
        self.harness.capture()
        self.app.lens_interpolation.set("step")
        self.app.standard_aspect.set(str(4 / 3))
        original = copy.deepcopy(self.app.project.keyframes)
        snapshot = self.app._snapshot()
        self.assertEqual(snapshot.lens_interpolation, "step")
        self.assertEqual(snapshot.standard_aspect, 4 / 3)
        self.assertEqual(snapshot.interpolation, "smooth")
        self.assertEqual(snapshot.keyframes, original)
        self.assertIsNot(snapshot, self.app.project)
        self.assertEqual(Project.from_dict(snapshot.to_dict()), snapshot)

    def test_dragging_graph_node_changes_only_aspect_and_keeps_arrival_time(self):
        self.app.project = Project(keyframes=[
            Keyframe(0, 12, 34, 56, 7, 8, 9, 75),
            Keyframe(3, 120, 340, 560, 70, 80, 90, 40),
        ])
        original = copy.deepcopy(self.app.project)
        self.app._change_curve_key(1, 8.25, 1.2)
        expected = copy.deepcopy(original)
        expected.keyframes[1].aspect_ratio = 1.2
        self.assertEqual(self.app.project, expected)
        self.app._refresh_keys.assert_called_with(3)
        self.assertEqual(self.app.project.evaluate(3)["aspect_ratio"], 1.2)
        self.assertEqual(self.harness.errors, [])

    def test_reset_selected_aspect_uses_custom_standard_without_changing_other_keys(self):
        self.app.project = Project(keyframes=[
            Keyframe(0, 12, 34, 56, 7, 8, 9, 75, 1.2),
            Keyframe(3, 120, 340, 560, 70, 80, 90, 40, 1.4),
        ])
        self.app.key_vars = {field: Var("") for field in FIELDS}
        self.harness.selected = 1
        self.app._select_key()
        self.app.standard_aspect.set("4:3")
        original = copy.deepcopy(self.app.project.keyframes)
        self.app._reset_aspect()
        self.assertEqual(self.app.project.keyframes[0], original[0])
        expected = copy.deepcopy(original[1])
        expected.aspect_ratio = 4 / 3
        self.assertEqual(self.app.project.keyframes[1], expected)
        self.assertEqual(self.harness.errors, [])

    def test_invalid_standard_aspect_cannot_overwrite_project_settings(self):
        self.harness.capture()
        original = copy.deepcopy(self.app.project)
        for value in ("16:0", "0", "nan", "invalid"):
            self.app.standard_aspect.set(value)
            self.app._apply_options()
            self.assertEqual(self.app.project, original)
        self.assertEqual(len(self.harness.errors), 4)

    def test_empty_shot_rejects_invalid_lens_mode_before_settings_are_committed(self):
        original = copy.deepcopy(self.app.project)
        self.app.lens_interpolation.set("unknown")
        self.app._apply_options()
        self.assertEqual(self.app.project, original)
        self.assertEqual(len(self.harness.errors), 1)
        self.assertIn("Zoom interpolation", str(self.harness.errors[-1]))

    def test_invalid_playback_speed_does_not_start_or_move_preview_cursor(self):
        self.harness.capture()
        self.app._snapshot = lambda: self.app.project
        self.app.controller.play = Mock()
        self.app._set_time.reset_mock()
        self.app.speed.set("0")
        self.app._play()
        self.assertIsNone(self.harness.pending)
        self.app.controller.play.assert_not_called()
        self.app._set_time.assert_not_called()
        self.assertIn("Playback speed", str(self.harness.errors[-1]))

    def test_hotkey_does_not_capture_while_busy_playing_or_modal(self):
        self.app.capture_here = Mock()
        self.app.hotkey_enabled.set(True)
        self.app.capture_hotkey = Mock(running=True, is_game_focused=Mock(return_value=True))
        for flag in ("busy", "playing", "closed"):
            with self.subTest(flag=flag):
                setattr(self.app, flag, True)
                self.app._handle_capture_hotkey(self.app.capture_generation)
                self.app.capture_here.assert_not_called()
                setattr(self.app, flag, False)
        self.app.root.grab_current = lambda: object()
        self.app._handle_capture_hotkey(self.app.capture_generation)
        self.app.capture_here.assert_not_called()
        self.app.root.grab_current = lambda: None
        self.app._handle_capture_hotkey(self.app.capture_generation)
        self.app.capture_here.assert_called_once()


class FakeCaptureListener:
    def __init__(self, callback=None, game_pid=None, binding=None, *, running=False):
        self.callback = callback
        self.game_pid = game_pid
        self.binding = binding
        self.running = running
        self.focused = True
        self.last_error = None
        self.start_failure = None
        self.stop_failure = None
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1
        if self.start_failure:
            raise self.start_failure
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False
        if self.stop_failure:
            raise self.stop_failure

    def is_game_focused(self):
        return self.focused


class GuiCaptureBindingTests(unittest.TestCase):
    def setUp(self):
        self.harness = CaptureHarness()
        self.app = self.harness.app
        self.new_binding = CaptureBinding("Mouse4", False, False)
        self.listeners = []
        self.factory = patch("dolly.gui.CaptureHotkey", side_effect=self.make_listener)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.saving = patch("dolly.gui.save_settings")
        self.save = self.saving.start()
        self.addCleanup(self.saving.stop)

    def make_listener(self, callback, game_pid, binding):
        listener = FakeCaptureListener(callback, game_pid, binding)
        self.listeners.append(listener)
        return listener

    def active_listener(self):
        self.app.capture_hotkey = listener = FakeCaptureListener(binding=DEFAULT_BINDING, running=True)
        self.app.hotkey_enabled.set(True)
        return listener

    def test_disabled_binding_save_persists_and_updates_label_without_starting_input(self):
        done = Mock()
        self.app._apply_capture_binding(self.new_binding, done)
        self.harness.finish()
        self.save.assert_called_once_with(AppSettings(capture_binding=self.new_binding))
        self.assertEqual(self.app.capture_binding, self.new_binding)
        self.assertEqual(self.app.hotkey_label.get(), "In-game capture · Mouse4")
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertEqual(self.listeners, [])
        done.assert_called_once_with()

    def test_enabling_uses_saved_mouse_binding_without_resaving_preferences(self):
        self.app.app_settings = AppSettings(capture_binding=self.new_binding)
        self.app.capture_binding = self.new_binding
        self.app.hotkey_enabled.set(True)
        self.app._toggle_capture_hotkey()
        self.harness.finish()
        self.assertTrue(self.app.hotkey_enabled.get())
        listener = self.listeners[-1]
        self.assertEqual(listener.binding, self.new_binding)
        self.assertTrue(listener.running)
        listener.callback()
        self.assertEqual(self.app.events.get_nowait(), ("capture_hotkey", "", self.app.capture_generation))
        self.save.assert_not_called()

    def test_live_rebind_stops_old_listener_and_restarts_new_binding(self):
        old = self.active_listener()
        self.app._apply_capture_binding(self.new_binding)
        self.assertFalse(self.app.hotkey_enabled.get())
        self.harness.finish()
        self.assertEqual(old.stops, 1)
        self.assertFalse(old.running)
        self.assertTrue(self.app.hotkey_enabled.get())
        self.assertIs(self.app.capture_hotkey, self.listeners[-1])
        self.assertEqual(self.app.capture_hotkey.binding, self.new_binding)
        self.save.assert_called_once_with(AppSettings(capture_binding=self.new_binding))

    def test_queued_old_press_cannot_capture_after_rebind_or_disable(self):
        self.active_listener()
        self.app.capture_here = Mock()
        previous_generation = self.app.capture_generation
        self.app._apply_capture_binding(self.new_binding)
        self.app._handle_capture_hotkey(previous_generation)
        self.app.capture_here.assert_not_called()
        self.harness.finish()
        self.app._handle_capture_hotkey(previous_generation)
        self.app.capture_here.assert_not_called()
        current_generation = self.app.capture_generation
        self.app._handle_capture_hotkey(current_generation)
        self.app.capture_here.assert_called_once()
        self.app.capture_here.reset_mock()
        self.app.hotkey_enabled.set(False)
        self.app._toggle_capture_hotkey()
        self.app._handle_capture_hotkey(current_generation)
        self.app.capture_here.assert_not_called()
        self.harness.finish()
        self.app._handle_capture_hotkey(current_generation)
        self.app.capture_here.assert_not_called()

    def test_queued_press_is_discarded_when_focus_is_lost_or_listener_stops(self):
        listener = self.active_listener()
        self.app.capture_here = Mock()
        listener.focused = False
        self.app._handle_capture_hotkey(self.app.capture_generation)
        listener.focused = True
        listener.running = False
        self.app._handle_capture_hotkey(self.app.capture_generation)
        self.app.capture_here.assert_not_called()

    def test_save_failure_preserves_old_binding_and_disables_capture(self):
        old = self.active_listener()
        self.save.side_effect = OSError("Disk full")
        self.app._apply_capture_binding(self.new_binding)
        self.harness.finish()
        self.assertEqual(old.stops, 1)
        self.assertEqual(self.app.capture_binding, DEFAULT_BINDING)
        self.assertEqual(self.app.app_settings, AppSettings())
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertIsNone(self.app.capture_hotkey)
        self.assertEqual(self.listeners, [])
        self.assertIn("Disk full", str(self.harness.errors[-1]))
        self.assertNotIn("Binding saved", str(self.harness.errors[-1]))

    def test_new_listener_failure_keeps_saved_binding_but_turns_capture_off(self):
        self.active_listener()
        failed = FakeCaptureListener()
        failed.start_failure = RuntimeError("Input unavailable")
        with patch("dolly.gui.CaptureHotkey", return_value=failed):
            self.app._apply_capture_binding(self.new_binding)
            self.harness.finish()
        self.assertEqual(failed.stops, 1)
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertEqual(self.app.capture_binding, self.new_binding)
        self.assertIsNone(self.app.capture_hotkey)
        self.assertIn("Binding saved, but in-game capture is disabled", str(self.harness.errors[-1]))

    def test_stop_failure_keeps_old_binding_and_does_not_save_or_start_another_worker(self):
        old = self.active_listener()
        old.stop_failure = RuntimeError("Worker is still stopping")
        self.app._apply_capture_binding(self.new_binding)
        self.harness.finish()
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertIs(self.app.capture_hotkey, old)  # Retain for a later cleanup attempt.
        self.assertEqual(self.app.capture_binding, DEFAULT_BINDING)
        self.save.assert_not_called()
        self.assertEqual(self.listeners, [])
        self.assertIn("still stopping", str(self.harness.errors[-1]))

    def test_busy_rebind_is_rejected_without_invalidating_the_active_listener(self):
        old = self.active_listener()
        generation = self.app.capture_generation
        self.app.busy = True
        self.assertFalse(self.app._apply_capture_binding(self.new_binding))
        self.assertEqual(self.app.capture_generation, generation)
        self.assertTrue(self.app.hotkey_enabled.get())
        self.assertIs(self.app.capture_hotkey, old)
        self.assertIsNone(self.harness.pending)
        self.save.assert_not_called()

    def test_playback_rebind_is_rejected(self):
        self.active_listener()
        self.app.playing = True
        self.assertFalse(self.app._apply_capture_binding(self.new_binding))
        self.assertIsNone(self.harness.pending)
        self.save.assert_not_called()

    def test_unexpected_listener_exit_disables_capture_and_reports_once(self):
        listener = self.active_listener()
        listener.running = False
        listener.last_error = "Native input polling failed"
        self.app._log = Mock()
        previous_generation = self.app.capture_generation
        with self.assertLogs("dolly", level="WARNING") as logged:
            self.app._check_capture_listener()
        self.assertFalse(self.app.hotkey_enabled.get())
        self.assertIs(self.app.capture_hotkey, listener)
        self.assertEqual(self.app.capture_generation, previous_generation + 1)
        self.assertIn("Native input polling failed", self.app.status_text.get())
        self.assertIn("Enable it again", self.app.status_text.get())
        self.assertEqual(len(logged.output), 1)
        self.app._check_capture_listener()
        self.app._log.assert_called_once()
        self.assertEqual(self.app.capture_generation, previous_generation + 1)

    def test_listener_health_waits_for_a_busy_transition_to_finish(self):
        listener = self.active_listener()
        listener.running = False
        self.app.busy = True
        self.app._log = Mock()
        self.app._check_capture_listener()
        self.assertTrue(self.app.hotkey_enabled.get())
        self.assertEqual(self.app.capture_generation, 0)
        self.app._log.assert_not_called()
        self.app.busy = False
        with self.assertLogs("dolly", level="WARNING"):
            self.app._check_capture_listener()
        self.assertFalse(self.app.hotkey_enabled.get())

    def test_invalid_preferences_use_defaults_and_explain_without_overwriting_file(self):
        self.app.root.after = Mock()
        self.app._enqueue_log = Mock()
        with patch("dolly.gui.load_settings", side_effect=ValueError("Invalid JSON")):
            settings = self.app._load_app_settings()
        self.assertEqual(settings, AppSettings())
        self.save.assert_not_called()
        self.app._enqueue_log.assert_called_once()
        delayed_warning = self.app.root.after.call_args.args[1]
        with patch("dolly.gui.messagebox.showwarning") as warning:
            delayed_warning()
        self.assertIn("Ctrl+Alt+K", warning.call_args.args[1])
        self.assertIn("Invalid JSON", warning.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
