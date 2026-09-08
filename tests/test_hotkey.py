"""Binding edges, modifiers, focus and polling lifecycle without a Windows desktop."""
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dolly.bindings import CaptureBinding, DEFAULT_BINDING
from dolly.hotkey import (CaptureHotkey, _WindowsHotkeyAPI, _VK_CONTROL,
                          _VK_MENU, _VK_SHIFT, _VK_LWIN, _VK_RWIN)


class FakeWindows:
    def __init__(self, binding=DEFAULT_BINDING):
        self.binding = binding
        self.keys = set()
        self.foreground = 5678
        self.failure = None
        self.polls = 0
        self._sample_keys = set()
        self.condition = threading.Condition()

    def key_down(self, vk):
        with self.condition:
            if self.failure is not None:
                raise self.failure
            if vk == self.binding.vk:
                self._sample_keys = set(self.keys)
            down = vk in self._sample_keys
            if vk == _VK_RWIN:
                self.polls += 1
                self.condition.notify_all()
            return down

    def foreground_pid(self):
        return self.foreground

    def state(self, down=False, *, ctrl=None, alt=None, shift=None, win=False):
        with self.condition:
            self.keys = {self.binding.vk} if down else set()
            for vk, value, default in (
                (_VK_CONTROL, ctrl, self.binding.ctrl),
                (_VK_MENU, alt, self.binding.alt),
                (_VK_SHIFT, shift, self.binding.shift),
            ):
                if default if value is None else value:
                    self.keys.add(vk)
            if win:
                self.keys.add(_VK_LWIN)

    def flush(self):
        # Two completed samples guarantee the previous sample's callback finished.
        with self.condition:
            target = self.polls + 2
            if not self.condition.wait_for(lambda: self.polls >= target, timeout=2):
                raise AssertionError("Capture input worker did not poll")

    def press(self, **kwargs):
        self.state(False)
        self.flush()
        self.state(True, **kwargs)
        self.flush()


class HotkeyTests(unittest.TestCase):
    def make_hotkey(self, api=None, callback=None, game_pid=None, binding=None):
        api = api or FakeWindows(binding or DEFAULT_BINDING)
        calls = []
        hotkey = CaptureHotkey(callback or (lambda: calls.append(threading.get_ident())),
                               game_pid or (lambda: 5678), binding=binding)
        native = patch("dolly.hotkey._WindowsHotkeyAPI", return_value=api)
        native.start()
        self.addCleanup(native.stop)
        self.addCleanup(hotkey.stop)
        return hotkey, api, calls

    def test_non_windows_has_actionable_error(self):
        with patch("dolly.hotkey.os.name", "posix"):
            with self.assertRaisesRegex(RuntimeError, "Capture here"):
                _WindowsHotkeyAPI()

    def test_native_key_query_uses_only_the_high_bit(self):
        api = _WindowsHotkeyAPI.__new__(_WindowsHotkeyAPI)
        for state, expected in ((0, False), (1, False), (0x8000, True),
                                (0x8001, True), (-32768, True), (-32767, True)):
            queried = []
            api._user = SimpleNamespace(GetAsyncKeyState=lambda vk: queried.append(vk) or state)
            self.assertIs(api.key_down(5), expected)
            self.assertEqual(queried, [5])

    def test_foreground_game_captures_on_worker_once_per_press(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        self.assertTrue(hotkey.running)
        api.press()
        api.flush()
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.get_ident())
        api.press()
        self.assertEqual(len(calls), 2)

    def test_side_and_middle_mouse_bindings_capture_without_modifiers(self):
        for key in ("Mouse4", "Mouse5", "MiddleMouse"):
            with self.subTest(key=key):
                binding = CaptureBinding(key, False, False, False)
                hotkey, api, calls = self.make_hotkey(binding=binding)
                hotkey.start()
                api.press()
                self.assertEqual(len(calls), 1)
                hotkey.stop()

    def test_different_foreground_window_never_captures(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        for foreground in (77, None):
            api.foreground = foreground
            api.press()
        self.assertEqual(calls, [])

    def test_no_game_or_invalid_pid_never_captures(self):
        pid = [None]
        hotkey, api, calls = self.make_hotkey(game_pid=lambda: pid[0])
        hotkey.start()
        for value in (None, 0, -1, True, "5678"):
            pid[0] = value
            api.press()
        self.assertEqual(calls, [])
        pid[0] = 5678
        api.press()
        self.assertEqual(len(calls), 1)

    def test_held_during_enable_waits_for_release_and_new_press(self):
        api = FakeWindows()
        api.state(True)
        hotkey, api, calls = self.make_hotkey(api)
        hotkey.start()
        api.flush()
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)

    def test_held_through_focus_change_does_not_capture_on_return(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        api.foreground = 99
        api.press()
        api.foreground = 5678
        api.flush()
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)
        api.foreground = 99
        api.flush()
        api.foreground = 5678
        api.flush()
        self.assertEqual(len(calls), 1)

    def test_game_becoming_valid_while_key_held_does_not_capture(self):
        pid = [None]
        hotkey, api, calls = self.make_hotkey(game_pid=lambda: pid[0])
        hotkey.start()
        api.press()
        pid[0] = 5678
        api.flush()
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)

    def test_zero_key_state_outside_game_cannot_create_press_on_focus_return(self):
        binding = CaptureBinding("Mouse4", False, False, False)
        hotkey, api, calls = self.make_hotkey(binding=binding)
        hotkey.start()
        api.press()
        self.assertEqual(len(calls), 1)
        api.foreground = 99
        # Simulate Windows returning zero on another desktop despite the physical
        # button remaining held. It returns a down state again when focus returns.
        api.state(False)
        api.flush()
        api.foreground = 5678
        api.state(True)
        api.flush()
        self.assertEqual(len(calls), 1)
        api.press()
        self.assertEqual(len(calls), 2)

    def test_replacement_game_process_rebaselines_held_button(self):
        pid = [5678]
        hotkey, api, calls = self.make_hotkey(game_pid=lambda: pid[0])
        hotkey.start()
        pid[0] = 8765
        api.foreground = 8765
        api.state(True)
        api.flush()
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)

    def test_checked_modifiers_are_required_and_windows_key_blocks_capture(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        for modifiers in ({"ctrl": False}, {"alt": False}, {"win": True}):
            api.press(**modifiers)
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)
        api.press(shift=True)
        self.assertEqual(len(calls), 2)

    def test_bare_side_mouse_button_allows_freecam_movement_modifiers(self):
        binding = CaptureBinding("Mouse4", False, False, False)
        hotkey, api, calls = self.make_hotkey(binding=binding)
        hotkey.start()
        for modifiers in ({"ctrl": True}, {"shift": True}, {"ctrl": True, "shift": True},
                          {"alt": True}):
            api.press(**modifiers)
        self.assertEqual(len(calls), 4)

    def test_pressing_or_releasing_modifiers_while_key_held_is_not_a_new_press(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        api.press(ctrl=False, alt=False)
        api.state(True)
        api.flush()
        self.assertEqual(calls, [])
        api.press()
        self.assertEqual(len(calls), 1)
        api.state(True, ctrl=False)
        api.flush()
        api.state(True)
        api.flush()
        self.assertEqual(len(calls), 1)

    def test_unrelated_keys_do_not_capture(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        with api.condition:
            api.keys = {ord("L"), _VK_CONTROL, _VK_MENU}
        api.flush()
        self.assertEqual(calls, [])

    def test_startup_failure_is_synchronous(self):
        api = FakeWindows()
        api.failure = RuntimeError("simulated input startup failure")
        hotkey, api, _ = self.make_hotkey(api)
        with self.assertLogs("dolly.hotkey", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "startup failure"):
                hotkey.start()
        self.assertFalse(hotkey.running)
        self.assertIn("startup failure", hotkey.last_error)

    def test_start_is_idempotent(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        thread = hotkey._thread
        hotkey.start()
        self.assertIs(hotkey._thread, thread)
        api.press()
        self.assertEqual(len(calls), 1)

    def test_stop_before_start_is_harmless(self):
        hotkey, api, _ = self.make_hotkey()
        hotkey.stop()
        hotkey.start()
        self.assertTrue(hotkey.running)

    def test_stop_wakes_poll_wait_and_no_later_capture_occurs(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        before = time.monotonic()
        hotkey.stop()
        self.assertLess(time.monotonic() - before, 0.5)
        self.assertFalse(hotkey.running)
        self.assertFalse(hotkey._thread.is_alive())
        api.state(True)
        self.assertEqual(calls, [])

    def test_can_restart_after_stop_with_fresh_baseline(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        api.press()
        hotkey.stop()
        hotkey.start()
        api.flush()
        self.assertEqual(len(calls), 1)
        api.press()
        self.assertEqual(len(calls), 2)

    def test_callback_failure_does_not_kill_polling(self):
        calls = []

        def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("simulated enqueue error")

        hotkey, api, _ = self.make_hotkey(callback=fail_once)
        hotkey.start()
        with self.assertLogs("dolly.hotkey", level="ERROR"):
            api.press()
        api.press()
        self.assertEqual(len(calls), 2)
        self.assertTrue(hotkey.running)

    def test_polling_error_is_reported_and_can_restart(self):
        hotkey, api, calls = self.make_hotkey()
        hotkey.start()
        with self.assertLogs("dolly.hotkey", level="ERROR"):
            api.failure = RuntimeError("simulated GetAsyncKeyState failure")
            hotkey._thread.join(timeout=2)
        self.assertFalse(hotkey.running)
        self.assertIn("GetAsyncKeyState", hotkey.last_error)
        api.failure = None
        hotkey.start()
        api.press()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(hotkey.last_error)

    def test_invalid_binding_fails_before_starting_worker(self):
        with self.assertRaisesRegex(ValueError, "CaptureBinding"):
            CaptureHotkey(lambda: None, lambda: 1, binding={"key": "Mouse4"})

    def test_queued_event_focus_recheck_tracks_current_focus_and_lifecycle(self):
        pid = [5678]
        hotkey, api, _ = self.make_hotkey(game_pid=lambda: pid[0])
        self.assertFalse(hotkey.is_game_focused())
        hotkey.start()
        self.assertTrue(hotkey.is_game_focused())
        api.foreground = 99
        self.assertFalse(hotkey.is_game_focused())
        api.foreground = 5678
        for value in (True, None, -1, "5678"):
            pid[0] = value
            self.assertFalse(hotkey.is_game_focused())
        pid[0] = 5678
        self.assertTrue(hotkey.is_game_focused())
        hotkey.stop()
        self.assertFalse(hotkey.is_game_focused())

    def test_queued_event_focus_recheck_tolerates_session_errors(self):
        hotkey, api, _ = self.make_hotkey()
        hotkey.start()
        with patch.object(api, "foreground_pid", side_effect=RuntimeError("window disappeared")):
            self.assertFalse(hotkey.is_game_focused())
        with patch.object(hotkey, "_game_pid", side_effect=RuntimeError("session disappeared")):
            self.assertFalse(hotkey.is_game_focused())


if __name__ == "__main__":
    unittest.main()
