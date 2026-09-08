"""Paused-camera controls cannot keep moving across focus/lifecycle changes."""
import unittest
from unittest.mock import patch

from dolly.navigation import CameraMotion
from dolly.navigation_input import CameraInput


class FakeWindows:
    def __init__(self):
        self.keys = set()
        self.pid = 123
        self.queries = 0
        self.failure = None

    def foreground_pid(self):
        return self.pid

    def key_down(self, vk):
        self.queries += 1
        if self.failure:
            raise self.failure
        return vk in self.keys


class CameraInputTests(unittest.TestCase):
    def make_input(self):
        api = FakeWindows()
        pid = [123]
        control = CameraInput(lambda: pid[0])
        with patch("dolly.navigation_input._WindowsHotkeyAPI", return_value=api):
            control.set_game_enabled(True)
        self.addCleanup(control.close)
        return control, api, pid

    def test_gui_keys_need_focus_and_blur_releases_everything(self):
        control = CameraInput(lambda: None)
        control.press("w")
        self.assertEqual(control.sample(), CameraMotion())
        control.set_gui_focus(True)
        control.press("w")
        control.press("Control_L")
        control.press("Left")
        control.press("Shift_R")
        self.assertEqual(control.sample(), CameraMotion(forward=1, up=-1, yaw=1, boost=True))
        control.set_gui_focus(False)
        control.set_gui_focus(True)
        self.assertEqual(control.sample(), CameraMotion())

    def test_direction_mapping_opposites_and_key_release(self):
        control = CameraInput(lambda: None)
        control.set_gui_focus(True)
        for key in ("d", "space", "up", "right"):
            control.press(key)
        self.assertEqual(control.sample(), CameraMotion(right=1, up=1, pitch=-1, yaw=-1))
        for key in ("a", "ctrl", "down", "left"):
            control.press(key)
        self.assertEqual(control.sample(), CameraMotion())
        control.release("space")
        self.assertEqual(control.sample().up, -1)

    def test_releasing_one_modifier_preserves_its_held_counterpart(self):
        control = CameraInput(lambda: None)
        control.set_gui_focus(True)
        for key in ("Control_L", "Control_R", "Shift_L", "Shift_R"):
            control.press(key)
        control.release("Control_L")
        control.release("Shift_R")
        self.assertEqual(control.sample(), CameraMotion(up=-1, boost=True))
        control.release("Control_R")
        control.release("Shift_L")
        self.assertEqual(control.sample(), CameraMotion())

    def test_gui_alt_and_windows_chords_are_neutral(self):
        control = CameraInput(lambda: None)
        control.set_gui_focus(True)
        control.press("w")
        for key in ("Alt_L", "Super_R"):
            control.press(key)
            self.assertEqual(control.sample(), CameraMotion())
            control.release(key)

    def test_game_movement_and_escape_are_live_after_baseline(self):
        control, api, _ = self.make_input()
        self.assertEqual(control.sample(), CameraMotion())
        api.keys = {0x57, 0x20, 0x25, 0x10}
        self.assertEqual(control.sample(), CameraMotion(forward=1, up=1, yaw=1, boost=True))
        api.keys = {0x1B}
        self.assertTrue(control.sample().stop)

    def test_enable_with_held_keys_requires_release(self):
        control, api, _ = self.make_input()
        api.keys = {0x57}
        for _ in range(3):
            self.assertEqual(control.sample(), CameraMotion())
        api.keys.clear()
        control.sample()
        api.keys.add(0x57)
        self.assertEqual(control.sample().forward, 1)

    def test_focus_loss_and_return_with_held_keys_never_jump(self):
        control, api, _ = self.make_input()
        control.sample()
        api.keys = {0x57}
        self.assertEqual(control.sample().forward, 1)
        api.pid = 321
        before = api.queries
        self.assertEqual(control.sample(), CameraMotion())
        self.assertEqual(api.queries, before)
        api.pid = 123
        for _ in range(3):
            self.assertEqual(control.sample(), CameraMotion())
        # A different fresh key works while the stale W key is still blocked.
        api.keys.add(0x44)
        self.assertEqual(control.sample(), CameraMotion(right=1))

    def test_replacement_game_and_invalid_pid_are_ineligible(self):
        control, api, pid = self.make_input()
        control.sample()
        api.keys = {0x57}
        for value in (None, 0, -1, True, "123"):
            pid[0] = value
            self.assertEqual(control.sample(), CameraMotion())
        pid[0] = 555
        api.pid = 555
        self.assertEqual(control.sample(), CameraMotion())
        api.keys.clear()
        control.sample()
        api.keys = {0x57}
        self.assertEqual(control.sample().forward, 1)

    def test_focus_change_mid_sample_discards_motion(self):
        control, api, _ = self.make_input()
        control.sample()
        api.keys = {0x57}
        with patch.object(api, "foreground_pid", side_effect=[123, 321]):
            self.assertEqual(control.sample(), CameraMotion())

    def test_alt_tab_chord_requires_movement_release_after_alt(self):
        control, api, _ = self.make_input()
        control.sample()
        api.keys = {0x57, 0x12}
        self.assertEqual(control.sample(), CameraMotion())
        api.keys.remove(0x12)
        self.assertEqual(control.sample(), CameraMotion())
        api.keys.clear()
        control.sample()
        api.keys = {0x57}
        self.assertEqual(control.sample().forward, 1)

    def test_clear_rearms_game_and_clears_gui_holds(self):
        control, api, _ = self.make_input()
        control.sample()
        api.keys = {0x57}
        self.assertEqual(control.sample().forward, 1)
        control.clear()
        self.assertEqual(control.sample(), CameraMotion())
        control.set_gui_focus(True)
        control.press("w")
        control.clear()
        self.assertEqual(control.sample(), CameraMotion())

    def test_gui_focus_overrides_game_and_return_rearms(self):
        control, api, _ = self.make_input()
        control.sample()
        api.keys = {0x57}
        control.set_gui_focus(True)
        control.press("s")
        self.assertEqual(control.sample(), CameraMotion(forward=-1))
        control.set_gui_focus(False)
        self.assertEqual(control.sample(), CameraMotion())

    def test_disabled_and_closed_inputs_never_poll_or_move(self):
        control, api, _ = self.make_input()
        control.set_game_enabled(False)
        self.assertEqual(control.sample(), CameraMotion())
        self.assertEqual(api.queries, 0)
        control.set_gui_focus(True)
        control.press("w")
        control.close()
        control.set_game_enabled(True)
        control.set_gui_focus(True)
        control.press("w")
        self.assertEqual(control.sample(), CameraMotion())
        self.assertEqual(api.queries, 0)

    def test_native_failure_stops_with_actionable_error(self):
        control, api, _ = self.make_input()
        api.failure = OSError("input unavailable")
        with self.assertRaisesRegex(RuntimeError, "input stopped"):
            control.sample()
        self.assertEqual(control.sample(), CameraMotion())

    def test_native_start_failure_keeps_gui_controls_usable(self):
        control = CameraInput(lambda: None)
        with patch("dolly.navigation_input._WindowsHotkeyAPI", side_effect=RuntimeError("no Windows")):
            with self.assertRaisesRegex(OSError, "movement pad"):
                control.set_game_enabled(True)
        control.set_gui_focus(True)
        control.press("w")
        self.assertEqual(control.sample().forward, 1)

    def test_invalid_controls_are_rejected(self):
        control = CameraInput(lambda: None)
        for key in (None, 12, "mouse1", "r"):
            with self.assertRaises(ValueError):
                control.press(key)
        with self.assertRaises(ValueError):
            control.set_game_enabled("yes")
