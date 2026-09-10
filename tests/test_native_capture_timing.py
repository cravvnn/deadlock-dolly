"""Render/console lag and replay-playing capture regressions."""
from collections import deque
import unittest
from unittest.mock import patch

from tests.test_native_flight_controller import FlightBridge, configured_controller


class DelayedViewBridge(FlightBridge):
    def __init__(self, console, views):
        super().__init__(console)
        self.views = deque(views)
        self.view = None

    def status(self):
        result = super().status()
        if self.views:
            self.view = self.views.popleft()
        if self.view is not None:
            result.update(self.view)
        return result


class NativeCaptureTimingTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.console, self.bridge = configured_controller()

    def delayed(self, views):
        bridge = DelayedViewBridge(self.console, views)
        self.controller._session.native = bridge
        self.bridge = bridge
        return bridge

    def test_desktop_playing_capture_waits_for_render_pause_and_keeps_pose_tick_together(self):
        self.console.paused = False
        visible = [11, 22, 33, 4, 5, 0, 1.2]
        self.delayed([
            {"tick": 100, "paused": False},
            {"tick": 101, "paused": False},
            {"tick": 102, "paused": True, "original_pose": visible},
            {"tick": 103, "paused": True, "original_pose": visible},
            {"tick": 103, "paused": True, "original_pose": visible},
        ])
        key = self.controller.capture_at_replay(100, 10)
        self.assertEqual((key.time, key.x, key.z, key.aspect_ratio), (.3, 11, 33, 1.2))
        self.assertEqual(self.controller.status()["tick"], 103)
        self.assertEqual(self.console.events.count("demo_pause"), 1)
        self.assertEqual(self.console.camera_writes, [])

    def test_new_path_start_uses_render_tick_even_if_console_is_behind(self):
        self.delayed([{"tick": 103, "paused": True}])
        key = self.controller.capture_at_replay(None, 64)
        self.assertEqual(key.time, 0)
        self.assertEqual(self.controller.status()["tick"], 103)

    def test_keybind_capture_during_playback_keeps_input_event_time_and_view(self):
        self.console.paused = False
        self.console.tick = 104
        self.bridge.original[0] = 999
        snapshot = {"pose": [11, 22, 33, 4, 5, 0, 1.2], "tick": 100, "paused": False}
        key, tick = self.controller.capture_native_snapshot(snapshot, 1.5)
        self.assertEqual((key.x, key.time, tick), (11, 1.5, 100))
        self.assertEqual(self.controller.status()["tick"], 104)
        self.assertEqual(self.controller.status()["captured_tick"], 100)
        self.assertTrue(self.console.paused)
        self.assertTrue(self.controller.status()["replay_paused"])

    def test_paused_snapshot_rejects_a_seek_while_pause_is_being_processed(self):
        self.delayed([
            {"tick": 100, "paused": True},
            {"tick": 300, "paused": True},
        ])
        with self.assertRaisesRegex(RuntimeError, "replay moved"):
            self.controller.capture_native_snapshot({"pose": self.bridge.pose, "tick": 100, "paused": True})
        self.assertEqual(self.console.camera_writes, [])

    def test_playing_snapshot_rejects_rewind_before_capture(self):
        self.console.tick = 99
        self.console.paused = False
        with self.assertRaisesRegex(RuntimeError, "rewound"):
            self.controller.capture_native_snapshot({"pose": self.bridge.pose, "tick": 100, "paused": False})
        self.assertNotIn("demo_pause", self.console.events)

    def test_stale_paused_render_cannot_be_used_as_a_new_capture(self):
        self.bridge.advance = False
        with patch("dolly.controller.NATIVE_PAUSE_TIMEOUT", .025):
            with self.assertRaisesRegex(RuntimeError, "renderer has not confirmed"):
                self.controller.capture_at_replay(None, 64)
        self.assertEqual(self.console.camera_writes, [])

    def test_capture_after_p_rearms_manual_movement_and_keeps_the_panel_open(self):
        self.controller.begin_paused_camera()
        self.bridge.pose[2] = 733
        self.controller.toggle_replay()
        self.assertFalse(self.console.paused)
        before = self.console.events.count("native.flight")
        key = self.controller.capture_at_replay(None, 64)
        self.assertEqual(key.z, 733)
        self.assertTrue(self.controller.status()["paused_flight"])
        self.assertEqual(self.console.events.count("native.flight"), before + 1)
        self.assertEqual(self.bridge.owner, "panel")
        self.assertEqual(self.bridge.pose[2], 733)
        self.assertNotIn("native.release", self.console.events)

    def test_flight_entry_uses_acknowledged_render_tick_after_pause(self):
        self.delayed([{"tick": 101, "paused": True}])
        result = self.controller.enter_native_flight()
        self.assertEqual(result["z"], 60)
        self.assertEqual(self.controller.status()["paused_tick"], 101)
        self.assertNotIn("native.release", self.console.events)

    def test_flight_entry_still_rejects_external_seek_after_ack(self):
        self.delayed([
            {"tick": 101, "paused": True},  # native acknowledgement
            {"tick": 400, "paused": True},
        ])
        with self.assertRaisesRegex(RuntimeError, "replay moved"):
            self.controller.enter_native_flight()
        self.assertIn("native.release", self.console.events)


if __name__ == "__main__":
    unittest.main()
