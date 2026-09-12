import struct
import unittest

from dolly import media_wire as wire
from dolly.native_bridge import NativeBridge, NativeBridgeError, MAPPING_BYTES


class Memory(bytearray):
    def close(self):
        pass


class MediaTransportTests(unittest.TestCase):
    def setUp(self):
        self.mappings = {}
        self.now = 0.0
        self.respond = True
        self.state = 0
        self.command_error = 0
        self.observed_commands = []
        self.bridge = NativeBridge.create(mapping_factory=self.factory, clock=lambda: self.now,
                                          sleep=self.sleep, start_heartbeat=False,
                                          editor_pid=11, token="a" * 32)
        self.bridge.bind_game(22)

    def factory(self, name, size):
        self.mappings[name] = Memory(size)
        return self.mappings[name]

    def sleep(self, seconds):
        self.now += seconds
        if not self.respond or self.bridge._media_mapping is None:
            return
        memory = self.bridge._media_mapping
        command = wire.COMMAND.unpack_from(memory)
        self.observed_commands.append(command[3])
        if command[3] == 2 and self.state == 2:
            self.state = 4
        packet = wire.STATUS.pack(b"DLYMDS01", 2, wire.ABI, command[1], self.command_error,
                                  self.state, 60, 1920, 1080, 2, 0, 120, 3, 20000000,
                                  0, 0, b"", b"", "Rejected".encode("utf-16-le"))
        memory[wire.STATUS_OFFSET:wire.STATUS_OFFSET + len(packet)] = packet

    def tearDown(self):
        # Camera release acknowledgement is independent of these media tests.
        self.bridge.release = lambda **_options: None
        self.bridge.close()

    def test_separate_mapping_and_utf16_command(self):
        path = "C:\\Videos\\Café.mp4"
        result = self.bridge.start_video(path, fps=30)
        self.assertEqual(result["ack"], 2)
        self.assertEqual(result["duration"], 2)
        name = "Local\\DeadlockDollyNative_" + "a" * 32 + ".media"
        self.assertEqual(len(self.mappings[name]), wire.MAPPING_BYTES)
        self.assertEqual(len(self.bridge._mapping), MAPPING_BYTES)
        command = wire.COMMAND.unpack_from(self.mappings[name])
        self.assertEqual(command[0:6], (b"DLYMED01", 2, wire.ABI, 1, 30, 20000000))
        self.assertEqual(command[11].decode("utf-16-le").rstrip("\0"), path)

    def test_rejected_command_is_not_reported_as_recording(self):
        self.command_error = 1
        with self.assertRaisesRegex(NativeBridgeError, "Rejected"):
            self.bridge.start_video("C:\\Videos\\one.mp4")
        self.command_error = 0

    def test_unresponsive_media_does_not_change_camera_command(self):
        self.respond = False
        camera = bytes(self.bridge._mapping)
        with self.assertRaisesRegex(NativeBridgeError, "did not acknowledge"):
            self.bridge.start_video("C:\\Videos\\one.mp4")
        self.assertEqual(bytes(self.bridge._mapping), camera)

    def test_torn_status_keeps_previous_valid_snapshot(self):
        result = self.bridge.start_video("C:\\Videos\\one.mp4")
        struct.pack_into("<I", self.bridge._media_mapping, wire.STATUS_OFFSET + 8, 3)
        self.assertEqual(self.bridge.media_status(), result)

    def test_close_finalizes_before_unmapping_and_keeps_diagnostics(self):
        self.state = 2
        self.bridge.start_video("C:\\Videos\\one.mp4")
        self.bridge._close_media()
        self.assertIsNone(self.bridge._media_mapping)
        self.assertEqual(self.bridge.media_status()["state"], "completed")

    def test_120_fps_command_and_status_round_trip(self):
        command = wire.COMMAND.unpack(wire.pack_command(2, "start_video", path="C:\\ok.mp4", fps=120))
        self.assertEqual(command[4], 120)
        self.bridge.start_video("C:\\Videos\\one.mp4", fps=120)
        # Native status must accept the requested rate instead of dropping telemetry.
        struct.pack_into("<I", self.bridge._media_mapping, wire.STATUS_OFFSET + 28, 120)
        self.assertEqual(self.bridge.media_status()["fps"], 120)

    def test_fixed_step_flag_is_encoded_in_the_reserved_field(self):
        flagged = wire.COMMAND.unpack(wire.pack_command(2, "start_video", path="C:\\ok.mp4",
                                                        fixed_step=True))
        plain = wire.COMMAND.unpack(wire.pack_command(2, "start_video", path="C:\\ok.mp4"))
        self.assertEqual(flagged[6], 1)
        self.assertEqual(plain[6], 0)

    def test_protocol_layout_and_invalid_paths(self):
        self.assertEqual(wire.COMMAND.size, 6192)
        self.assertEqual(wire.STATUS.size, 2384)
        for path in ("relative.mp4", "C:relative.mp4", "\\root-only.mp4", "C:\\bad\0.mp4", "C:\\" + "x" * 1024):
            with self.subTest(path=path), self.assertRaises(ValueError):
                wire.pack_command(2, "start_video", path=path)
        for fps in (True, 0, 24, 240):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                wire.pack_command(2, "start_video", path="C:\\ok.mp4", fps=fps)

    def test_close_supersedes_an_unacknowledged_start(self):
        self.respond = False
        with self.assertRaises(NativeBridgeError):
            self.bridge.start_video("C:\\Videos\\late.mp4")
        self.respond = True
        self.bridge._close_media()
        self.assertIn(2, self.observed_commands)
        self.assertNotIn(1, self.observed_commands)


if __name__ == "__main__":
    unittest.main()
