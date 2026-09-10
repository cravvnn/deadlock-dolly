import struct
import unittest
from unittest.mock import patch

from dolly import editor_wire as w
from dolly import native_bridge as nb
from dolly.editor_actions import default_action_bindings, EditorBinding


class EditorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.memory = bytearray(nb.MAPPING_BYTES)
        self.time = 0
        def sleep(seconds):
            self.time += seconds
        self.bridge = nb.NativeBridge.create(mapping_factory=lambda *_: self.memory,
            clock=lambda: self.time, sleep=sleep, start_heartbeat=False, editor_pid=100,
            token="a" * 32)
        self.bridge.bind_game(200)
        self.addCleanup(self.bridge.close)

    def publish(self, events=(), *, seq=2, flags=127, owner=1, latest=None):
        latest = max((e[0] for e in events), default=0) if latest is None else latest
        data = bytearray(w.STATUS_BYTES)
        w.HEADER.pack_into(data, 0, w.STATUS_MAGIC, seq, 1, owner, flags, 0, 2,
                           latest, 0, 320, 10, 20, 30, 5, 6, 7, 16/9,
                           b"Ready", 1.5, 42, 0, 17)
        for index, (serial, action, value) in enumerate(events):
            w.EVENT.pack_into(data, w.HEADER.size + index*w.EVENT.size,
                serial, action, value, 10, 20, 30, 5, 6, 7, 16/9, 42, 1)
        self.memory[w.STATUS_OFFSET:w.STATUS_OFFSET + len(data)] = data

    def test_config_does_not_overwrite_path_control_or_payload(self):
        self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET + 32] = b"x"*32
        before = bytes(self.memory[:nb.CONTROL.size])
        bindings = default_action_bindings()
        bindings["capture"] = EditorBinding("Mouse4")
        self.bridge.configure_editor(enabled=True, owner="flight", bindings=bindings,
                                     camera_count=2, selected_camera=1)
        self.assertEqual(bytes(self.memory[:nb.CONTROL.size]), before)
        self.assertEqual(self.memory[nb.PAYLOAD_OFFSET:nb.PAYLOAD_OFFSET+32], b"x"*32)
        packed = w.CONFIG.unpack_from(self.memory, w.CONFIG_OFFSET)
        self.assertEqual(packed[0], w.CONFIG_MAGIC)
        self.assertEqual(packed[1] % 2, 0)
        self.assertEqual(packed[13:15], (5, 0))

    def test_config_refresh_does_not_reopen_console_or_reset_owner(self):
        self.bridge.configure_editor(owner="flight")
        owner_sequence = self.bridge._editor_owner_sequence
        self.bridge.configure_editor(message="Camera captured", camera_count=1)
        self.assertEqual(self.bridge._editor_owner_sequence, owner_sequence)
        self.bridge.configure_editor(owner="panel")
        self.assertEqual(self.bridge._editor_owner_sequence, owner_sequence+1)

    def test_invalid_config_cannot_partially_publish(self):
        self.bridge.configure_editor(enabled=True)
        before = bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size])
        with self.assertRaises(ValueError):
            self.bridge.configure_editor(speed=float("nan"))
        self.assertEqual(bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]), before)

    def test_playback_controls_use_reserved_config_space_and_keep_offsets(self):
        self.assertEqual(w.CONFIG.size, 448)
        self.bridge.configure_editor(playback_speed=.1, playback_rate=120)
        config = self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]
        self.assertEqual(struct.unpack_from("<dI", config, 416), (.1, 120))
        self.assertEqual(config[428:], b"\0"*20)
        before = bytes(config)
        for setting in ({"playback_speed": 0}, {"playback_speed": float("nan")},
                        {"playback_rate": 90}, {"playback_rate": 60.0}, {"playback_rate": True}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.bridge.configure_editor(**setting)
            self.assertEqual(bytes(self.memory[w.CONFIG_OFFSET:w.CONFIG_OFFSET+w.CONFIG.size]), before)

    def test_playback_action_ids_preserve_existing_action_order(self):
        self.publish([(1, 28, 1), (2, 29, .25), (3, 30, 120)])
        events = self.bridge.editor_status()["events"]
        self.assertEqual([(event["action"], event["value"]) for event in events],
                         [("select_view", 1), ("set_playback_speed", .25), ("set_playback_rate", 120)])

    def test_capture_keeps_pose_tick_and_event_order_and_ack_is_exactly_once(self):
        self.publish([(2, 26, 0), (1, 0, 0)])
        status = self.bridge.editor_status()
        self.assertTrue(status["flight_active"])
        self.assertTrue(status["overlay_available"])
        self.assertEqual([e["action"] for e in status["events"]], ["capture", "console"])
        event = status["events"][0]
        self.assertEqual(event["pose"][:3], [10, 20, 30])
        self.assertEqual(event["tick"], 42)
        self.bridge.acknowledge_editor_event(1)
        self.assertEqual([e["sequence"] for e in self.bridge.editor_status()["events"]], [2])
        with self.assertRaises(nb.NativeBridgeError):
            self.bridge.acknowledge_editor_event(1)

    def test_bad_queue_gap_duplicate_action_and_owner_are_rejected(self):
        for events, owner in (([(2,0,0)],1), ([(1,0,0),(1,0,0)],1), ([(1,99,0)],1), ([],9)):
            with self.subTest(events=events,owner=owner):
                self.publish(events,owner=owner)
                with self.assertRaises(nb.NativeBridgeError):
                    self.bridge.editor_status()

    def test_odd_status_writer_cannot_dispatch_actions(self):
        self.publish([(1,0,0)],seq=3)
        with self.assertRaisesRegex(nb.NativeBridgeError,"being updated"):
            self.bridge.editor_status()

    def test_manual_can_start_and_hold_without_a_compiled_shot(self):
        self.publish()
        with patch.object(self.bridge, "_wait", return_value={"state":"armed"}):
            pose = [1,2,3,4,5,6,16/9]
            self.bridge.start_flight("replays/test.dem", pose)
            header = nb.CONTROL.unpack_from(self.memory)
            self.assertEqual(header[4],4)
            self.assertEqual(header[9],56)
            self.assertEqual(struct.unpack_from("<7d",self.memory,nb.PAYLOAD_OFFSET),tuple(pose))
            self.bridge.hold()
            self.assertEqual(nb.CONTROL.unpack_from(self.memory)[4],3)
            self.assertFalse(self.bridge._prepared)
            self.assertTrue(self.bridge._manual)
            with self.assertRaisesRegex(nb.NativeBridgeError,"Prepare"):
                self.bridge.play()
            self.bridge.release()
            self.assertFalse(self.bridge._manual)

    def test_manual_invalid_seed_does_not_publish_command(self):
        before = self.bridge._command
        for pose in ([1,2], [1,2,float("nan"),4,5,6,1], [1,2,3,4,5,6,0]):
            with self.assertRaises(ValueError):
                self.bridge.start_flight("test.dem",pose)
        self.assertEqual(self.bridge._command,before)

    def test_flight_waits_for_dx11_and_input_even_when_launcher_has_focus(self):
        ready = {"enabled": True, "ready": True, "paused": True,
                 "input_available": True, "overlay_available": True, "focused": False}
        waiting = dict(ready, input_available=False, overlay_available=False)
        published = []
        original = self.bridge._publish
        def publish(mode, *args):
            published.append((mode, self.time))
            return original(mode, *args)
        with patch.object(self.bridge, "editor_status", side_effect=[waiting, waiting, ready]), \
             patch.object(self.bridge, "_publish", side_effect=publish), \
             patch.object(self.bridge, "_wait", return_value={"state": "armed"}):
            self.bridge.start_flight("test.dem")
        self.assertEqual(published, [(4, .04)])
        self.assertTrue(self.bridge._manual)

    def test_unavailable_dx11_never_publishes_a_flight_command(self):
        self.publish(flags=1 | 4 | 16 | 64)  # Input/view ready, no swapchain yet.
        modes = []
        original = self.bridge._publish
        with patch.object(self.bridge, "_publish", side_effect=lambda mode, *args: (modes.append(mode), original(mode, *args))[1]):
            with self.assertRaisesRegex(nb.NativeBridgeError, "DX11 panel"):
                self.bridge.start_flight("test.dem", timeout=.05)
        self.assertEqual(modes, [0])
        self.assertFalse(self.bridge._manual)
        self.assertEqual(self.bridge._editor_values["owner"], "panel")

    def test_flight_readiness_can_be_cancelled_without_moving_camera(self):
        with patch.object(self.bridge, "_publish", wraps=self.bridge._publish) as publish:
            with self.assertRaisesRegex(nb.NativeBridgeError, "cancelled"):
                self.bridge.start_flight("test.dem", cancelled=lambda: True)
        publish.assert_called_once_with(0)
        self.assertEqual(self.bridge._editor_values["owner"], "panel")

    def test_authored_path_hides_panel_and_failure_restores_controls(self):
        self.bridge.configure_editor(enabled=True, owner="panel")
        self.bridge._prepared = True
        with patch.object(self.bridge, "_wait", return_value={"state": "playing"}):
            self.bridge.play()
        self.assertEqual(self.bridge._editor_values["owner"], "flight")
        with patch.object(self.bridge, "_wait", side_effect=nb.NativeBridgeError("replay seek")):
            with self.assertRaisesRegex(nb.NativeBridgeError, "replay seek"):
                self.bridge.play()
        self.assertEqual(self.bridge._editor_values["owner"], "panel")


if __name__ == "__main__":
    unittest.main()
