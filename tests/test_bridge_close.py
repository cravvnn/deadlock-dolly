"""Bridge close, busy snapshot and control-refresh boundaries."""
import logging
import unittest

from dolly import media_wire
from dolly import native_bridge as nb


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 1e-4)


class Mapping(bytearray):
    closed = False

    def close(self):
        self.closed = True


def make_bridge(clock=None):
    clock = clock or FakeClock()
    mappings = []

    def factory(name, size):
        mappings.append(Mapping(size))
        return mappings[-1]

    bridge = nb.NativeBridge.create(mapping_factory=factory, clock=clock, sleep=clock.sleep,
                                    start_heartbeat=False, editor_pid=100, token="0" * 32)
    return bridge, clock, mappings


def media_status(seq, ack, video_state, reshade_state):
    return media_wire.STATUS.pack(b"DLYMDS01", seq, media_wire.ABI, ack, 0, video_state, 60,
                                  1920, 1080, reshade_state, 1, 10, 0, 0, 0, 0, b"", b"", b"", 0, 0)


class MediaCloseAfterGameExitTests(unittest.TestCase):
    def test_close_after_game_exit_skips_media_waits_and_unmaps(self):
        bridge, clock, mappings = make_bridge()
        dead = {"value": False}
        bridge.bind_game(1234, exited=lambda: dead["value"])
        bridge._ensure_media()
        media = bridge._media_mapping
        data = media_status(2, 2, 2, 1)  # recording, ReShade enabled, start_video acknowledged
        media[media_wire.STATUS_OFFSET:media_wire.STATUS_OFFSET + len(data)] = data
        bridge._media_sequence = 2
        bridge._media_last_command = "start_video"
        bridge._source_audio_recording = True
        self.assertEqual(bridge.media_status()["state"], "recording")
        dead["value"] = True
        logging.disable(logging.CRITICAL)
        try:
            bridge.close()
        finally:
            logging.disable(logging.NOTSET)
        self.assertLess(clock.now, 0.5)
        self.assertIsNone(bridge._media_mapping)
        self.assertTrue(media.closed)

    def test_media_command_ends_its_wait_when_the_game_exits(self):
        bridge, clock, _ = make_bridge()
        dead = {"value": False}
        bridge.bind_game(1234, exited=lambda: dead["value"])
        bridge._ensure_media()
        dead["value"] = True
        with self.assertRaisesRegex(nb.NativeBridgeError, "exited"):
            bridge.disable_reshade()
        self.assertLess(clock.now, 0.5)


if __name__ == "__main__":
    unittest.main()
