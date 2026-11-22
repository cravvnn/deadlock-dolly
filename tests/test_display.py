"""Game-window focus without changing the window's size."""
import os
import unittest
from unittest.mock import patch

from dolly import display


class Function:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.behaviour(*args)


class FakeUser32:
    def __init__(self, iconic):
        def enum(callback, _):
            callback(7, 0)
            return True

        def owner(_hwnd, pointer):
            pointer._obj.value = 42
            return 1

        def rect(_hwnd, pointer):
            pointer._obj.right, pointer._obj.bottom = 1920, 1080
            return True

        self.EnumWindows = Function(enum)
        self.IsWindowVisible = Function(lambda hwnd: True)
        self.GetWindowThreadProcessId = Function(owner)
        self.GetWindowRect = Function(rect)
        self.IsIconic = Function(lambda hwnd: iconic)
        self.ShowWindow = Function(lambda hwnd, command: True)
        self.SetForegroundWindow = Function(lambda hwnd: True)
        self.AllowSetForegroundWindow = Function(lambda pid: True)


@unittest.skipUnless(os.name == "nt", "Win32 window focus")
class FocusWindow(unittest.TestCase):
    def focus(self, iconic):
        user32 = FakeUser32(iconic)
        with patch.object(display.ctypes, "WinDLL", return_value=user32):
            self.assertTrue(display.focus_window(42))
        return user32

    def test_maximized_window_is_focused_without_restoring(self):
        user32 = self.focus(iconic=0)
        self.assertEqual(user32.ShowWindow.calls, [])
        self.assertEqual(len(user32.SetForegroundWindow.calls), 1)

    def test_minimized_window_is_restored_before_focus(self):
        user32 = self.focus(iconic=1)
        self.assertEqual(user32.ShowWindow.calls, [(7, 9)])
        self.assertEqual(len(user32.SetForegroundWindow.calls), 1)


if __name__ == "__main__":
    unittest.main()
